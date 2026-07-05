"""Background-run orchestration and Neo4j persistence for CustomerGraph AI agents.

The first agent is intentionally read-only from a business perspective:
* it reads authorised customer facts,
* asks the local model for a structured recommendation,
* saves reviewable AI insight nodes, and
* never sends email, changes customer records, or triggers external actions.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from customergraph.agents.data_service import (
    CustomerAnalysisAccessError,
    CustomerAnalysisContext,
    load_customer_context,
    load_portfolio_contexts,
)
from customergraph.agents.dashboard_widget_agent import (
    DashboardWidgetInsight,
    analyse_dashboard_widget,
)
from customergraph.agents.health_churn_agent import analyse_customer_health_and_churn
from customergraph.agents.portfolio_agent import summarise_portfolio_health_and_churn
from customergraph.agents.schemas import (
    AgentRunAcceptedResponse,
    AgentRunStatus,
    AgentRunStatusResponse,
    AgentType,
    CustomerAIInsightResponse,
    CustomerHealthChurnInsight,
    DashboardAnalysisSection,
    DashboardWidgetAnalysisResponse,
    PortfolioAIInsightResponse,
    SimpleCustomerAnalysisResponse,
    SimplePortfolioAction,
    SimplePortfolioAnalysisResponse,
)
from customergraph.auth.schemas import CurrentUserResponse
from customergraph.core.config import get_settings
from customergraph.core.logging import get_logger
from customergraph.dashboard.service import get_dashboard_summary
from customergraph.db.neo4j_client import get_neo4j_driver
from customergraph.llm import get_ollama_health

logger = get_logger("customergraph.agents")

_AGENT_TYPE = AgentType.CUSTOMER_HEALTH_CHURN.value
_PORTFOLIO_INSIGHT_ID = f"portfolio_ai_insight:{_AGENT_TYPE}"


class AgentUnavailableError(RuntimeError):
    """Raised before queueing when AI-agent execution is not ready."""


class AgentRunConflictError(RuntimeError):
    """Raised if a same-scope agent run is already queued/running."""

    def __init__(self, run_id: str) -> None:
        super().__init__("An equivalent AI analysis is already queued or running.")
        self.run_id = run_id


class AgentRunNotFoundError(RuntimeError):
    """Raised for missing/inaccessible agent run records."""


@dataclass(frozen=True)
class AgentActor:
    """Minimal user context safe to send into a FastAPI background task."""

    id: str
    email: str
    full_name: str
    role: Any
    status: Any
    allowed_modules: list[str]

    @classmethod
    def from_current_user(cls, user: CurrentUserResponse) -> "AgentActor":
        return cls(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            status=user.status,
            allowed_modules=list(user.allowed_modules),
        )

    def as_current_user(self) -> CurrentUserResponse:
        return CurrentUserResponse(
            id=self.id,
            email=self.email,
            full_name=self.full_name,
            role=self.role,
            status=self.status,
            allowed_modules=self.allowed_modules,
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_param(value: datetime) -> str:
    return value.isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    native = getattr(value, "to_native", None)
    if callable(native):
        return _parse_datetime(native())
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _record_value(record: Any, key: str, default: Any = None) -> Any:
    if record is None:
        return default
    value = record.get(key, default)
    return default if value is None else value


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _clean_error(error: Exception | str) -> str:
    """Store actionable but bounded errors; never save tracebacks or prompts."""
    text = " ".join(str(error).split())
    return text[:500] or "AI analysis did not complete."


def _assert_agents_ready() -> None:
    settings = get_settings()
    if not settings.ai_agents_enabled:
        raise AgentUnavailableError("AI agents are disabled. Set AI_AGENTS_ENABLED=true to enable them.")
    health = get_ollama_health()
    if not health.get("ok"):
        raise AgentUnavailableError(str(health.get("message") or "Ollama is not ready."))


def _find_active_run(scope: str, customer_id: str | None = None) -> str | None:
    driver = get_neo4j_driver()
    with driver.session(database=get_settings().neo4j_database) as session:
        record = session.run(
            """
            MATCH (run:AgentRun {agent_type: $agent_type, scope: $scope})
            WHERE run.status IN ["queued", "running"]
              AND coalesce(run.customer_id, "") = coalesce($customer_id, "")
            RETURN run.id AS run_id
            ORDER BY run.requested_at DESC
            LIMIT 1
            """,
            agent_type=_AGENT_TYPE,
            scope=scope,
            customer_id=customer_id,
        ).single()
    return str(_record_value(record, "run_id", "")) or None


def _create_run(*, scope: str, actor: AgentActor, customer_id: str | None = None) -> str:
    active = _find_active_run(scope, customer_id)
    if active:
        raise AgentRunConflictError(active)

    run_id = f"airun-{uuid.uuid4()}"
    requested_at = _dt_param(_now())
    driver = get_neo4j_driver()
    with driver.session(database=get_settings().neo4j_database) as session:
        session.run(
            """
            CREATE (run:AgentRun {
                id: $run_id,
                agent_type: $agent_type,
                scope: $scope,
                customer_id: $customer_id,
                status: "queued",
                requested_by_user_id: $requested_by_user_id,
                requested_at: datetime($requested_at),
                total_customers: 0,
                processed_customers: 0,
                successful_customers: 0,
                failed_customers: 0,
                total_llm_duration_ms: 0
            })
            """,
            run_id=run_id,
            agent_type=_AGENT_TYPE,
            scope=scope,
            customer_id=customer_id or "",
            requested_by_user_id=actor.id,
            requested_at=requested_at,
        ).consume()
    return run_id


def _update_run(run_id: str, **fields: Any) -> None:
    """Safely update known agent-run fields using one parameterised Cypher query."""
    if not fields:
        return
    allowed = {
        "status",
        "total_customers",
        "processed_customers",
        "successful_customers",
        "failed_customers",
        "model",
        "error_message",
        "total_llm_duration_ms",
        "started_at",
        "completed_at",
    }
    update_fields = {key: value for key, value in fields.items() if key in allowed}
    if not update_fields:
        return

    properties: dict[str, Any] = {}
    for key, value in update_fields.items():
        if key in {"started_at", "completed_at"} and isinstance(value, datetime):
            properties[key] = _dt_param(value)
        else:
            properties[key] = value

    driver = get_neo4j_driver()
    with driver.session(database=get_settings().neo4j_database) as session:
        session.run(
            """
            MATCH (run:AgentRun {id: $run_id})
            SET run.status = coalesce($status, run.status),
                run.total_customers = coalesce($total_customers, run.total_customers),
                run.processed_customers = coalesce($processed_customers, run.processed_customers),
                run.successful_customers = coalesce($successful_customers, run.successful_customers),
                run.failed_customers = coalesce($failed_customers, run.failed_customers),
                run.model = coalesce($model, run.model),
                run.error_message = $error_message,
                run.total_llm_duration_ms = coalesce($total_llm_duration_ms, run.total_llm_duration_ms),
                run.started_at = CASE WHEN $started_at IS NULL THEN run.started_at ELSE datetime($started_at) END,
                run.completed_at = CASE WHEN $completed_at IS NULL THEN run.completed_at ELSE datetime($completed_at) END
            """,
            run_id=run_id,
            status=properties.get("status"),
            total_customers=properties.get("total_customers"),
            processed_customers=properties.get("processed_customers"),
            successful_customers=properties.get("successful_customers"),
            failed_customers=properties.get("failed_customers"),
            model=properties.get("model"),
            error_message=properties.get("error_message"),
            total_llm_duration_ms=properties.get("total_llm_duration_ms"),
            started_at=properties.get("started_at"),
            completed_at=properties.get("completed_at"),
        ).consume()


def _save_customer_insight(
    *,
    context: CustomerAnalysisContext,
    insight: CustomerHealthChurnInsight,
    run_id: str,
    model: str,
) -> None:
    """Upsert only the newest health/churn insight for a customer."""
    insight_id = f"customer_ai_insight:{context.customer_id}:{_AGENT_TYPE}"
    generated_at = _dt_param(_now())
    properties = insight.dict()
    driver = get_neo4j_driver()
    with driver.session(database=get_settings().neo4j_database) as session:
        session.run(
            """
            MATCH (customer:Customer)
            WHERE coalesce(customer.id, elementId(customer)) = $customer_id
            MERGE (insight:CustomerAIInsight {id: $insight_id})
            SET insight += $properties,
                insight.agent_type = $agent_type,
                insight.customer_id = $customer_id,
                insight.customer_name = $customer_name,
                insight.source_run_id = $run_id,
                insight.model = $model,
                insight.analysis_input_version = "v1",
                insight.generated_at = datetime($generated_at)
            MERGE (customer)-[:HAS_AI_INSIGHT]->(insight)
            WITH insight
            MATCH (run:AgentRun {id: $run_id})
            MERGE (run)-[:PRODUCED_LATEST]->(insight)
            """,
            insight_id=insight_id,
            properties=properties,
            agent_type=_AGENT_TYPE,
            customer_id=context.customer_id,
            customer_name=context.customer_name,
            run_id=run_id,
            model=model,
            generated_at=generated_at,
        ).consume()


def _save_portfolio_insight(
    *,
    run_id: str,
    model: str,
    total_customers_analyzed: int,
    high_or_critical_customers: int,
    estimated_revenue_at_risk: float,
    portfolio_data: Any,
) -> None:
    """Persist the newest portfolio summary plus run linkage for the dashboard."""
    generated_at = _dt_param(_now())
    properties = portfolio_data.insight.dict(exclude={"priority_actions"})
    priority_actions_json = json.dumps(
        [action.dict() for action in portfolio_data.insight.priority_actions],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    driver = get_neo4j_driver()
    with driver.session(database=get_settings().neo4j_database) as session:
        session.run(
            """
            MERGE (insight:PortfolioAIInsight {id: $insight_id})
            SET insight += $properties,
                insight.agent_type = $agent_type,
                insight.source_run_id = $run_id,
                insight.model = $model,
                insight.generated_at = datetime($generated_at),
                insight.total_customers_analyzed = $total_customers_analyzed,
                insight.high_or_critical_customers = $high_or_critical_customers,
                insight.estimated_revenue_at_risk = $estimated_revenue_at_risk,
                insight.priority_actions_json = $priority_actions_json
            WITH insight
            MATCH (run:AgentRun {id: $run_id})
            MERGE (run)-[:PRODUCED_LATEST]->(insight)
            """,
            insight_id=_PORTFOLIO_INSIGHT_ID,
            properties=properties,
            agent_type=_AGENT_TYPE,
            run_id=run_id,
            model=model,
            generated_at=generated_at,
            total_customers_analyzed=total_customers_analyzed,
            high_or_critical_customers=high_or_critical_customers,
            estimated_revenue_at_risk=estimated_revenue_at_risk,
            priority_actions_json=priority_actions_json,
        ).consume()


def queue_portfolio_analysis(actor: AgentActor) -> AgentRunAcceptedResponse:
    """Create a portfolio agent run; the API layer schedules the background task."""
    _assert_agents_ready()
    run_id = _create_run(scope="portfolio", actor=actor)
    return AgentRunAcceptedResponse(
        run_id=run_id,
        agent_type=AgentType.CUSTOMER_HEALTH_CHURN,
        message="Portfolio analysis queued. Poll the run status while the Customer Health & Churn Agent works in the background.",
    )


def queue_customer_analysis(customer_id: str, actor: AgentActor) -> AgentRunAcceptedResponse:
    """Create a one-customer agent run after verifying customer scope."""
    _assert_agents_ready()
    load_customer_context(customer_id, actor.as_current_user())
    run_id = _create_run(scope="customer", actor=actor, customer_id=customer_id)
    return AgentRunAcceptedResponse(
        run_id=run_id,
        agent_type=AgentType.CUSTOMER_HEALTH_CHURN,
        message="Customer analysis queued. Poll the run status while the AI insight is being prepared.",
    )


def run_portfolio_analysis(run_id: str, actor: AgentActor) -> None:
    """Background task: analyse all allowed customers then create one portfolio summary."""
    current_user = actor.as_current_user()
    successful_rows: list[tuple[CustomerAnalysisContext, CustomerHealthChurnInsight]] = []
    processed = successful = failed = 0
    total_duration_ms = 0
    last_model: str | None = None

    try:
        _update_run(run_id, status=AgentRunStatus.RUNNING.value, started_at=_now(), error_message=None)
        contexts = load_portfolio_contexts(current_user)
        _update_run(run_id, total_customers=len(contexts))

        if not contexts:
            _update_run(
                run_id,
                status=AgentRunStatus.COMPLETED.value,
                completed_at=_now(),
                error_message="No customers are available in the selected portfolio scope.",
            )
            return

        for context in contexts:
            processed += 1
            try:
                result = analyse_customer_health_and_churn(context)
                _save_customer_insight(
                    context=context,
                    insight=result.insight,
                    run_id=run_id,
                    model=result.model,
                )
                successful_rows.append((context, result.insight))
                successful += 1
                last_model = result.model
                total_duration_ms += result.total_duration_ms or 0
            except Exception as exc:  # Individual customer failures must not stop the whole portfolio.
                failed += 1
                logger.warning(
                    "customer_agent_analysis_failed run_id=%s customer_id=%s error=%s",
                    run_id,
                    context.customer_id,
                    _clean_error(exc),
                )
            finally:
                _update_run(
                    run_id,
                    processed_customers=processed,
                    successful_customers=successful,
                    failed_customers=failed,
                    total_llm_duration_ms=total_duration_ms,
                    model=last_model,
                )

        if successful_rows:
            portfolio_result = summarise_portfolio_health_and_churn(successful_rows)
            total_duration_ms += portfolio_result.total_duration_ms or 0
            last_model = portfolio_result.model
            _save_portfolio_insight(
                run_id=run_id,
                model=portfolio_result.model,
                total_customers_analyzed=len(successful_rows),
                high_or_critical_customers=sum(
                    1 for _, insight in successful_rows if insight.risk_level in {"high", "critical"}
                ),
                estimated_revenue_at_risk=sum(context.revenue_at_risk for context, _ in successful_rows),
                portfolio_data=portfolio_result,
            )

        final_status = (
            AgentRunStatus.COMPLETED.value
            if failed == 0
            else AgentRunStatus.COMPLETED_WITH_ERRORS.value
        )
        _update_run(
            run_id,
            status=final_status,
            completed_at=_now(),
            processed_customers=processed,
            successful_customers=successful,
            failed_customers=failed,
            total_llm_duration_ms=total_duration_ms,
            model=last_model,
            error_message=("Some customers could not be analysed. Review run counts and server logs." if failed else None),
        )
    except Exception as exc:
        logger.exception("portfolio_agent_run_failed run_id=%s", run_id)
        _update_run(
            run_id,
            status=AgentRunStatus.FAILED.value,
            completed_at=_now(),
            processed_customers=processed,
            successful_customers=successful,
            failed_customers=max(failed, 1 if processed == 0 else failed),
            total_llm_duration_ms=total_duration_ms,
            model=last_model,
            error_message=_clean_error(exc),
        )


def run_customer_analysis(run_id: str, customer_id: str, actor: AgentActor) -> None:
    """Background task: run the first agent for exactly one authorised customer."""
    current_user = actor.as_current_user()
    total_duration_ms = 0
    last_model: str | None = None
    try:
        _update_run(
            run_id,
            status=AgentRunStatus.RUNNING.value,
            started_at=_now(),
            total_customers=1,
            error_message=None,
        )
        context = load_customer_context(customer_id, current_user)
        result = analyse_customer_health_and_churn(context)
        total_duration_ms = result.total_duration_ms or 0
        last_model = result.model
        _save_customer_insight(
            context=context,
            insight=result.insight,
            run_id=run_id,
            model=result.model,
        )
        _update_run(
            run_id,
            status=AgentRunStatus.COMPLETED.value,
            completed_at=_now(),
            processed_customers=1,
            successful_customers=1,
            failed_customers=0,
            total_llm_duration_ms=total_duration_ms,
            model=last_model,
            error_message=None,
        )
    except Exception as exc:
        logger.exception("customer_agent_run_failed run_id=%s customer_id=%s", run_id, customer_id)
        _update_run(
            run_id,
            status=AgentRunStatus.FAILED.value,
            completed_at=_now(),
            processed_customers=1,
            successful_customers=0,
            failed_customers=1,
            total_llm_duration_ms=total_duration_ms,
            model=last_model,
            error_message=_clean_error(exc),
        )


def get_run_status(run_id: str, current_user: CurrentUserResponse) -> AgentRunStatusResponse:
    """Return a role-safe status. Only Admin may inspect portfolio runs initially."""
    driver = get_neo4j_driver()
    with driver.session(database=get_settings().neo4j_database) as session:
        record = session.run(
            """
            MATCH (run:AgentRun {id: $run_id})
            WHERE run.scope <> "portfolio" OR $is_admin = true
            RETURN run
            """,
            run_id=run_id,
            is_admin=current_user.role.value == "admin",
        ).single()
    if record is None:
        raise AgentRunNotFoundError("AI analysis run was not found or is unavailable for your role.")
    run = record["run"]
    # Account Managers must not infer another manager's queued/running activity
    # from a run ID. Other roles retain the same customer visibility policy as
    # the existing Customer Directory API.
    if str(run.get("scope") or "") == "customer" and run.get("customer_id"):
        try:
            load_customer_context(str(run.get("customer_id")), current_user)
        except CustomerAnalysisAccessError as exc:
            raise AgentRunNotFoundError("AI analysis run was not found or is unavailable for your role.") from exc
    return AgentRunStatusResponse(
        run_id=str(run.get("id")),
        agent_type=AgentType(str(run.get("agent_type") or _AGENT_TYPE)),
        status=AgentRunStatus(str(run.get("status") or AgentRunStatus.FAILED.value)),
        scope=str(run.get("scope") or "customer"),
        requested_by_user_id=str(run.get("requested_by_user_id") or ""),
        customer_id=(str(run.get("customer_id")) if run.get("customer_id") else None),
        total_customers=_to_int(run.get("total_customers")),
        processed_customers=_to_int(run.get("processed_customers")),
        successful_customers=_to_int(run.get("successful_customers")),
        failed_customers=_to_int(run.get("failed_customers")),
        started_at=_parse_datetime(run.get("started_at")),
        completed_at=_parse_datetime(run.get("completed_at")),
        error_message=(str(run.get("error_message")) if run.get("error_message") else None),
        model=(str(run.get("model")) if run.get("model") else None),
        total_llm_duration_ms=_to_int(run.get("total_llm_duration_ms")) or None,
    )


def get_latest_customer_insight(customer_id: str, current_user: CurrentUserResponse) -> CustomerAIInsightResponse | None:
    """Fetch latest visible insight after verifying underlying customer scope."""
    # Scope check first: no insight metadata should reveal an unavailable customer.
    load_customer_context(customer_id, current_user)
    driver = get_neo4j_driver()
    with driver.session(database=get_settings().neo4j_database) as session:
        record = session.run(
            """
            MATCH (customer:Customer)
            WHERE coalesce(customer.id, elementId(customer)) = $customer_id
            MATCH (customer)-[:HAS_AI_INSIGHT]->(insight:CustomerAIInsight {agent_type: $agent_type})
            RETURN insight
            """,
            customer_id=customer_id,
            agent_type=_AGENT_TYPE,
        ).single()
    if record is None:
        return None
    insight = record["insight"]
    return CustomerAIInsightResponse(
        customer_id=str(insight.get("customer_id") or customer_id),
        customer_name=str(insight.get("customer_name") or "Unnamed Customer"),
        risk_level=str(insight.get("risk_level") or "medium"),
        churn_probability=_to_int(insight.get("churn_probability")),
        renewal_confidence=_to_int(insight.get("renewal_confidence")),
        upsell_likelihood=_to_int(insight.get("upsell_likelihood")),
        confidence=_to_int(insight.get("confidence")),
        executive_summary=str(insight.get("executive_summary") or ""),
        key_risk_drivers=list(insight.get("key_risk_drivers") or []),
        positive_signals=list(insight.get("positive_signals") or []),
        recommended_action=str(insight.get("recommended_action") or ""),
        priority=str(insight.get("priority") or "normal"),
        requires_human_review=bool(insight.get("requires_human_review")),
        generated_at=_parse_datetime(insight.get("generated_at")) or _now(),
        source_run_id=str(insight.get("source_run_id") or ""),
        model=str(insight.get("model") or ""),
        analysis_input_version=str(insight.get("analysis_input_version") or "v1"),
    )


def get_latest_portfolio_insight() -> PortfolioAIInsightResponse | None:
    """Fetch latest Admin-only portfolio summary."""
    driver = get_neo4j_driver()
    with driver.session(database=get_settings().neo4j_database) as session:
        record = session.run(
            """
            MATCH (insight:PortfolioAIInsight {id: $insight_id})
            RETURN insight
            """,
            insight_id=_PORTFOLIO_INSIGHT_ID,
        ).single()
    if record is None:
        return None
    insight = record["insight"]
    try:
        priority_actions = json.loads(str(insight.get("priority_actions_json") or "[]"))
    except json.JSONDecodeError:
        priority_actions = []
    return PortfolioAIInsightResponse(
        portfolio_status=str(insight.get("portfolio_status") or "watch"),
        executive_summary=str(insight.get("executive_summary") or ""),
        key_observations=list(insight.get("key_observations") or []),
        priority_actions=priority_actions,
        requires_human_review=bool(insight.get("requires_human_review")),
        generated_at=_parse_datetime(insight.get("generated_at")) or _now(),
        source_run_id=str(insight.get("source_run_id") or ""),
        model=str(insight.get("model") or ""),
        total_customers_analyzed=_to_int(insight.get("total_customers_analyzed")),
        high_or_critical_customers=_to_int(insight.get("high_or_critical_customers")),
        estimated_revenue_at_risk=_to_float(insight.get("estimated_revenue_at_risk")),
    )


def _portfolio_candidate_insight(context: CustomerAnalysisContext) -> CustomerHealthChurnInsight:
    """Create bounded factual candidate scores for one dashboard-level LLM summary.

    The Dashboard button intentionally makes one portfolio LLM call, not one LLM
    call per customer. These deterministic scores are only used to rank the
    provided facts before the LLM writes a concise executive summary.
    """
    risk_points = 0
    reasons: list[str] = []

    if context.health_score is not None:
        if context.health_score <= 40:
            risk_points += 45
            reasons.append(f"Health score is {round(context.health_score)}.")
        elif context.health_score <= 60:
            risk_points += 30
            reasons.append(f"Health score is {round(context.health_score)}.")
        elif context.health_score <= 75:
            risk_points += 12
            reasons.append(f"Health score is {round(context.health_score)}.")

    if context.critical_ticket_count:
        risk_points += min(25, context.critical_ticket_count * 12)
        reasons.append(f"{context.critical_ticket_count} critical support ticket(s) are open.")
    elif context.open_ticket_count >= 3:
        risk_points += 10
        reasons.append(f"{context.open_ticket_count} support tickets are open.")

    if context.overdue_invoice_count:
        risk_points += min(18, 6 + context.max_invoice_days_overdue // 7)
        reasons.append(f"{context.overdue_invoice_count} invoice(s) are overdue.")

    if context.days_until_renewal is not None and 0 <= context.days_until_renewal <= 30:
        risk_points += 18
        reasons.append(f"Renewal is due in {context.days_until_renewal} day(s).")
    elif context.days_until_renewal is not None and 0 <= context.days_until_renewal <= 60:
        risk_points += 8
        reasons.append(f"Renewal is due in {context.days_until_renewal} day(s).")

    if context.usage_change_percent is not None and context.usage_change_percent <= -20:
        risk_points += 15
        reasons.append(f"Usage decreased by {abs(round(context.usage_change_percent))}%.")

    churn_probability = max(5, min(95, risk_points))
    if risk_points >= 65:
        risk_level = "critical"
        priority = "urgent"
    elif risk_points >= 40:
        risk_level = "high"
        priority = "urgent" if context.critical_ticket_count or (context.days_until_renewal if context.days_until_renewal is not None else 999) <= 30 else "high"
    elif risk_points >= 18:
        risk_level = "medium"
        priority = "normal"
    else:
        risk_level = "low"
        priority = "low"

    if not reasons:
        reasons = ["No major negative signal was found in the currently available customer data."]
    positive_signals: list[str] = []
    if context.health_score is not None and context.health_score >= 80:
        positive_signals.append("Health score is stable.")
    if context.open_upsell_opportunity_count > 0:
        positive_signals.append("An open expansion opportunity exists.")

    if priority == "urgent":
        action = "Assign an account owner review within 48 hours."
    elif priority == "high":
        action = "Review account health and confirm the renewal or support recovery plan this week."
    elif risk_level == "medium":
        action = "Monitor the account and review the highlighted indicators in the next account check-in."
    else:
        action = "Continue routine account monitoring."

    return CustomerHealthChurnInsight(
        risk_level=risk_level,
        churn_probability=churn_probability,
        renewal_confidence=max(5, min(95, 100 - churn_probability + (5 if context.open_upsell_opportunity_count else 0))),
        upsell_likelihood=70 if context.open_upsell_opportunity_count else 20,
        confidence=80,
        executive_summary=" ".join(reasons[:2]),
        key_risk_drivers=reasons[:4],
        positive_signals=positive_signals[:3],
        recommended_action=action,
        priority=priority,
        requires_human_review=risk_level in {"high", "critical"},
    )


def analyse_customer_now(customer_id: str, actor: AgentActor) -> SimpleCustomerAnalysisResponse:
    """Run one customer LLM analysis synchronously for the Customers tab button."""
    _assert_agents_ready()
    current_user = actor.as_current_user()
    # Scope is verified before creating any persisted run record.
    context = load_customer_context(customer_id, current_user)
    run_id = _create_run(scope="customer", actor=actor, customer_id=customer_id)
    started_at = _now()
    try:
        _update_run(
            run_id,
            status=AgentRunStatus.RUNNING.value,
            started_at=started_at,
            total_customers=1,
            error_message=None,
        )
        result = analyse_customer_health_and_churn(context)
        _save_customer_insight(
            context=context,
            insight=result.insight,
            run_id=run_id,
            model=result.model,
        )
        completed_at = _now()
        _update_run(
            run_id,
            status=AgentRunStatus.COMPLETED.value,
            completed_at=completed_at,
            processed_customers=1,
            successful_customers=1,
            failed_customers=0,
            total_llm_duration_ms=result.total_duration_ms or 0,
            model=result.model,
            error_message=None,
        )
        return SimpleCustomerAnalysisResponse(
            message="AI analysis completed.",
            customer_id=context.customer_id,
            customer_name=context.customer_name,
            risk_level=result.insight.risk_level,
            churn_probability=result.insight.churn_probability,
            renewal_confidence=result.insight.renewal_confidence,
            summary=result.insight.executive_summary,
            reasons=result.insight.key_risk_drivers,
            recommended_action=result.insight.recommended_action,
            priority=result.insight.priority,
            generated_at=completed_at,
        )
    except Exception as exc:
        logger.exception("customer_ai_analysis_failed customer_id=%s", customer_id)
        _update_run(
            run_id,
            status=AgentRunStatus.FAILED.value,
            completed_at=_now(),
            processed_customers=1,
            successful_customers=0,
            failed_customers=1,
            error_message=_clean_error(exc),
        )
        raise


def analyse_portfolio_now(actor: AgentActor) -> SimplePortfolioAnalysisResponse:
    """Run one dashboard LLM summary from current portfolio facts.

    This endpoint deliberately avoids calling the LLM once per customer. The
    backend ranks customer facts first and calls the LLM only once to write the
    executive summary and top human-review actions.
    """
    _assert_agents_ready()
    current_user = actor.as_current_user()
    run_id = _create_run(scope="portfolio", actor=actor)
    try:
        _update_run(run_id, status=AgentRunStatus.RUNNING.value, started_at=_now(), error_message=None)
        contexts = load_portfolio_contexts(current_user)
        if not contexts:
            _update_run(
                run_id,
                status=AgentRunStatus.COMPLETED.value,
                completed_at=_now(),
                total_customers=0,
                processed_customers=0,
                successful_customers=0,
                failed_customers=0,
                error_message="No customers are available for analysis.",
            )
            return SimplePortfolioAnalysisResponse(
                message="No customers are available for AI analysis.",
                portfolio_status="healthy",
                total_customers_analyzed=0,
                high_risk_customers=0,
                revenue_at_risk=0,
                summary="No customers are available in the current portfolio scope.",
                key_observations=[],
                priority_actions=[],
                generated_at=_now(),
            )

        candidate_rows = [(context, _portfolio_candidate_insight(context)) for context in contexts]
        portfolio_result = summarise_portfolio_health_and_churn(candidate_rows)
        completed_at = _now()
        high_risk_customers = sum(
            1 for _, insight in candidate_rows if insight.risk_level in {"high", "critical"}
        )
        revenue_at_risk = sum(context.revenue_at_risk for context, _ in candidate_rows)
        _save_portfolio_insight(
            run_id=run_id,
            model=portfolio_result.model,
            total_customers_analyzed=len(candidate_rows),
            high_or_critical_customers=high_risk_customers,
            estimated_revenue_at_risk=revenue_at_risk,
            portfolio_data=portfolio_result,
        )
        _update_run(
            run_id,
            status=AgentRunStatus.COMPLETED.value,
            completed_at=completed_at,
            total_customers=len(candidate_rows),
            processed_customers=len(candidate_rows),
            successful_customers=len(candidate_rows),
            failed_customers=0,
            total_llm_duration_ms=portfolio_result.total_duration_ms or 0,
            model=portfolio_result.model,
            error_message=None,
        )
        return SimplePortfolioAnalysisResponse(
            message="Portfolio AI summary completed.",
            portfolio_status=portfolio_result.insight.portfolio_status,
            total_customers_analyzed=len(candidate_rows),
            high_risk_customers=high_risk_customers,
            revenue_at_risk=revenue_at_risk,
            summary=portfolio_result.insight.executive_summary,
            key_observations=portfolio_result.insight.key_observations,
            priority_actions=[
                SimplePortfolioAction(
                    customer_id=action.customer_id,
                    customer_name=action.customer_name,
                    priority=action.priority,
                    action=action.recommendation,
                )
                for action in portfolio_result.insight.priority_actions
            ],
            generated_at=completed_at,
        )
    except Exception as exc:
        logger.exception("portfolio_ai_analysis_failed")
        _update_run(
            run_id,
            status=AgentRunStatus.FAILED.value,
            completed_at=_now(),
            error_message=_clean_error(exc),
        )
        raise


_DASHBOARD_WIDGET_TITLES: dict[str, str] = {
    DashboardAnalysisSection.TOTAL_CUSTOMERS.value: "Customer base overview",
    DashboardAnalysisSection.HIGH_RISK_CUSTOMERS.value: "High-risk customer review",
    DashboardAnalysisSection.UPCOMING_RENEWALS.value: "Upcoming renewal review",
    DashboardAnalysisSection.OPEN_CRITICAL_TICKETS.value: "Critical support review",
    DashboardAnalysisSection.DELAYED_INVOICES.value: "Delayed invoice review",
    DashboardAnalysisSection.UPSELL_OPPORTUNITIES.value: "Upsell opportunity review",
    DashboardAnalysisSection.REVENUE_AT_RISK.value: "Revenue-at-risk review",
    DashboardAnalysisSection.HEALTH_SCORE_TREND.value: "Health score trend review",
    DashboardAnalysisSection.TOP_HIGH_RISK_CUSTOMERS.value: "Top high-risk accounts",
}


def _compact_amount(value: float) -> str:
    """Human-readable money for factual fallback copy only."""
    amount = max(0.0, float(value or 0.0))
    if amount >= 10_000_000:
        return f"₹{amount / 10_000_000:.2f}".rstrip("0").rstrip(".") + " Cr"
    if amount >= 100_000:
        return f"₹{amount / 100_000:.1f}".rstrip("0").rstrip(".") + " L"
    return f"₹{amount:,.0f}"


def _dashboard_widget_customer_rows(contexts: list[CustomerAnalysisContext]) -> list[tuple[CustomerAnalysisContext, CustomerHealthChurnInsight]]:
    """Use deterministic, factual candidates to rank per-widget customer detail."""
    return [(context, _portfolio_candidate_insight(context)) for context in contexts]


def _customer_row_payload(context: CustomerAnalysisContext, insight: CustomerHealthChurnInsight) -> dict[str, Any]:
    return {
        "customer_id": context.customer_id,
        "customer_name": context.customer_name,
        "health_score": context.health_score,
        "risk_level": insight.risk_level,
        "priority": insight.priority,
        "days_until_renewal": context.days_until_renewal,
        "critical_ticket_count": context.critical_ticket_count,
        "overdue_invoice_count": context.overdue_invoice_count,
        "overdue_invoice_amount": context.overdue_invoice_amount,
        "open_upsell_opportunity_count": context.open_upsell_opportunity_count,
        "open_upsell_potential_revenue": context.open_upsell_potential_revenue,
        "revenue_at_risk": context.revenue_at_risk,
    }


def _rank_rows(rows: list[tuple[CustomerAnalysisContext, CustomerHealthChurnInsight]]) -> list[tuple[CustomerAnalysisContext, CustomerHealthChurnInsight]]:
    return sorted(
        rows,
        key=lambda row: (
            row[1].priority != "urgent",
            row[1].risk_level not in {"critical", "high"},
            -row[1].churn_probability,
            -(row[0].revenue_at_risk or row[0].annual_contract_value),
        ),
    )


def _build_dashboard_widget_input(
    section: DashboardAnalysisSection,
    *,
    dashboard: Any,
    rows: list[tuple[CustomerAnalysisContext, CustomerHealthChurnInsight]],
) -> tuple[dict[str, Any], DashboardWidgetInsight]:
    """Build one bounded factual prompt and a transparent fallback per widget."""
    section_key = section.value
    base = {
        "scope": dashboard.scope,
        "total_customers": dashboard.total_customers,
        "high_risk_customers": dashboard.high_risk_customers,
        "upcoming_renewals_next_30_days": dashboard.upcoming_renewals_next_30_days,
        "open_critical_tickets": dashboard.open_critical_tickets,
        "delayed_invoices_count": dashboard.delayed_invoices_count,
        "delayed_invoices_amount": dashboard.delayed_invoices_amount,
        "upsell_opportunities_count": dashboard.upsell_opportunities_count,
        "upsell_potential_revenue": dashboard.upsell_potential_revenue,
        "revenue_at_risk": dashboard.revenue_at_risk,
        "currency": {
            "code": "INR",
            "symbol": "₹",
            "format": "Use ₹ with L (lakh) or Cr (crore); all monetary values are already INR.",
        },
    }
    ranked = _rank_rows(rows)
    high_rows = [row for row in ranked if row[1].risk_level in {"high", "critical"}]

    if section_key == DashboardAnalysisSection.TOTAL_CUSTOMERS.value:
        status = "attention" if dashboard.high_risk_customers else "stable"
        return (
            {
                **base,
                "risk_distribution": {
                    "high_or_critical": len(high_rows),
                    "urgent": sum(1 for _, insight in rows if insight.priority == "urgent"),
                },
            },
            DashboardWidgetInsight(
                status=status,
                summary=(
                    f"{dashboard.total_customers} active customer(s) are currently in scope. "
                    f"{dashboard.high_risk_customers} account(s) need elevated attention."
                ),
                evidence=[
                    f"{dashboard.total_customers} active customer(s) are in the current portfolio scope.",
                    f"{dashboard.high_risk_customers} customer(s) are marked high or critical risk.",
                ],
                recommended_action="Review ownership and recovery coverage for the high-risk accounts before the next portfolio review.",
            ),
        )

    if section_key in {DashboardAnalysisSection.HIGH_RISK_CUSTOMERS.value, DashboardAnalysisSection.TOP_HIGH_RISK_CUSTOMERS.value}:
        candidates = [_customer_row_payload(context, insight) for context, insight in high_rows[:5]]
        status = "critical" if any(insight.risk_level == "critical" for _, insight in high_rows) else ("attention" if high_rows else "stable")
        title = "top 5" if section_key == DashboardAnalysisSection.TOP_HIGH_RISK_CUSTOMERS.value else "high-risk"
        return (
            {**base, "selected_group": title, "customers": candidates},
            DashboardWidgetInsight(
                status=status,
                summary=(
                    f"{dashboard.high_risk_customers} high-risk customer(s) are currently flagged, with "
                    f"{_compact_amount(dashboard.revenue_at_risk)} in estimated revenue at risk. "
                    f"The highest-priority accounts should receive a named owner review."
                    if high_rows
                    else "No high-risk customers are currently flagged in the available portfolio data."
                ),
                evidence=(
                    [f"Revenue at risk across high and critical accounts: {_compact_amount(dashboard.revenue_at_risk)}."]
                    + ([f"Open critical support tickets: {dashboard.open_critical_tickets}."] if dashboard.open_critical_tickets else [])
                    + [
                        f"{context.customer_name}: {insight.risk_level.title()} risk"
                        + (f", health score {round(context.health_score)}" if context.health_score is not None else "")
                        for context, insight in high_rows[:3]
                    ]
                )[:3] or ["No high-risk customer evidence is currently available."],
                recommended_action=(
                    "Assign or confirm an accountable owner for the highest-risk accounts and review their recovery plans."
                    if high_rows
                    else "Continue routine account health monitoring."
                ),
            ),
        )

    if section_key == DashboardAnalysisSection.UPCOMING_RENEWALS.value:
        candidates = sorted(
            [row for row in rows if row[0].days_until_renewal is not None and 0 <= row[0].days_until_renewal <= 30],
            key=lambda row: row[0].days_until_renewal or 999,
        )[:5]
        return (
            {**base, "renewal_candidates": [_customer_row_payload(context, insight) for context, insight in candidates]},
            DashboardWidgetInsight(
                status="attention" if dashboard.upcoming_renewals_next_30_days else "stable",
                summary=(
                    f"{dashboard.upcoming_renewals_next_30_days} renewal(s) are due within the next 30 days. "
                    "Prioritise accounts that also show support, billing, or health risk signals."
                    if dashboard.upcoming_renewals_next_30_days
                    else "No renewals are due within the next 30 days in the current portfolio scope."
                ),
                evidence=[
                    *[
                        f"{context.customer_name}: renewal due in {context.days_until_renewal} day(s)."
                        for context, _ in candidates[:3]
                    ],
                ] or ["No renewal candidate is due within 30 days."],
                recommended_action=(
                    "Confirm account ownership and renewal plans for the nearest due customers this week."
                    if candidates
                    else "Continue monitoring the renewal calendar."
                ),
            ),
        )

    if section_key == DashboardAnalysisSection.OPEN_CRITICAL_TICKETS.value:
        candidates = sorted(
            [row for row in rows if row[0].critical_ticket_count > 0],
            key=lambda row: (-row[0].critical_ticket_count, row[0].days_until_renewal or 999),
        )[:5]
        return (
            {**base, "critical_ticket_customers": [_customer_row_payload(context, insight) for context, insight in candidates]},
            DashboardWidgetInsight(
                status="critical" if dashboard.open_critical_tickets else "stable",
                summary=(
                    f"{dashboard.open_critical_tickets} critical support ticket(s) are open right now. "
                    "Affected customer accounts should have a recovery owner and escalation path."
                    if dashboard.open_critical_tickets
                    else "No critical support tickets are currently open in the available portfolio data."
                ),
                evidence=[
                    *[
                        f"{context.customer_name}: {context.critical_ticket_count} critical ticket(s) open."
                        for context, _ in candidates[:3]
                    ],
                ] or ["No customer currently has an open critical support ticket."],
                recommended_action=(
                    "Confirm an owner and resolution plan for each critical ticket before the next customer update."
                    if candidates
                    else "Continue routine support monitoring."
                ),
            ),
        )

    if section_key == DashboardAnalysisSection.DELAYED_INVOICES.value:
        candidates = sorted(
            [row for row in rows if row[0].overdue_invoice_count > 0],
            key=lambda row: -row[0].overdue_invoice_amount,
        )[:5]
        return (
            {**base, "overdue_invoice_customers": [_customer_row_payload(context, insight) for context, insight in candidates]},
            DashboardWidgetInsight(
                status="attention" if dashboard.delayed_invoices_count else "stable",
                summary=(
                    f"{dashboard.delayed_invoices_count} delayed invoice(s) total {_compact_amount(dashboard.delayed_invoices_amount)}. "
                    "Billing follow-up should be coordinated with account risk and renewal context."
                    if dashboard.delayed_invoices_count
                    else "No delayed invoices are currently recorded in the available portfolio data."
                ),
                evidence=[
                    *[
                        f"{context.customer_name}: {_compact_amount(context.overdue_invoice_amount)} overdue across {context.overdue_invoice_count} invoice(s)."
                        for context, _ in candidates[:3]
                    ],
                ] or ["No overdue invoice customer is currently available."],
                recommended_action=(
                    "Review the largest overdue balances with the account owner and confirm a follow-up plan."
                    if candidates
                    else "Continue routine billing monitoring."
                ),
            ),
        )

    if section_key == DashboardAnalysisSection.UPSELL_OPPORTUNITIES.value:
        candidates = sorted(
            [row for row in rows if row[0].open_upsell_opportunity_count > 0],
            key=lambda row: -row[0].open_upsell_potential_revenue,
        )[:5]
        return (
            {**base, "upsell_customers": [_customer_row_payload(context, insight) for context, insight in candidates]},
            DashboardWidgetInsight(
                status="opportunity" if dashboard.upsell_opportunities_count else "info",
                summary=(
                    f"{dashboard.upsell_opportunities_count} open expansion opportunity(s) represent "
                    f"{_compact_amount(dashboard.upsell_potential_revenue)} in potential revenue."
                    if dashboard.upsell_opportunities_count
                    else "No open upsell opportunity is currently available in the portfolio data."
                ),
                evidence=[
                    *[
                        f"{context.customer_name}: {_compact_amount(context.open_upsell_potential_revenue)} potential across {context.open_upsell_opportunity_count} opportunity(s)."
                        for context, _ in candidates[:3]
                    ],
                ] or ["No open expansion opportunity is currently available."],
                recommended_action=(
                    "Validate the highest-value expansion opportunities with the account owners before adding them to the sales plan."
                    if candidates
                    else "Continue monitoring customer expansion signals."
                ),
            ),
        )

    if section_key == DashboardAnalysisSection.REVENUE_AT_RISK.value:
        candidates = sorted(rows, key=lambda row: -row[0].revenue_at_risk)[:5]
        return (
            {**base, "revenue_risk_customers": [_customer_row_payload(context, insight) for context, insight in candidates]},
            DashboardWidgetInsight(
                status="attention" if dashboard.revenue_at_risk else "stable",
                summary=(
                    f"Estimated revenue at risk is {_compact_amount(dashboard.revenue_at_risk)} across high and critical portfolio accounts. "
                    "Focus recovery coverage on the customers with the largest exposure."
                    if dashboard.revenue_at_risk
                    else "No revenue-at-risk amount is currently recorded in the available portfolio data."
                ),
                evidence=[
                    *[
                        f"{context.customer_name}: {_compact_amount(context.revenue_at_risk)} revenue at risk."
                        for context, _ in candidates[:3] if context.revenue_at_risk > 0
                    ],
                ] or ["No customer-level revenue-at-risk value is currently available."],
                recommended_action=(
                    "Review the largest revenue exposures with the accountable customer owners and confirm recovery actions."
                    if dashboard.revenue_at_risk
                    else "Continue routine portfolio risk monitoring."
                ),
            ),
        )

    if section_key == DashboardAnalysisSection.HEALTH_SCORE_TREND.value:
        trend = [
            {"month": point.month, "average_health_score": point.average_health_score}
            for point in dashboard.health_score_trend
        ]
        scores = [point["average_health_score"] for point in trend]
        change = (scores[-1] - scores[0]) if len(scores) >= 2 else 0
        status = "critical" if change <= -10 else ("attention" if change <= -3 else "stable")
        direction = "declined" if change < 0 else ("improved" if change > 0 else "remained stable")
        return (
            {**base, "health_score_trend": trend, "change_from_first_to_latest": change},
            DashboardWidgetInsight(
                status=status,
                summary=(
                    f"Average portfolio health has {direction} by {abs(round(change, 1))} point(s) across the available trend period."
                    if len(scores) >= 2
                    else "Not enough health-score trend data is available to assess movement over time."
                ),
                evidence=[
                    *[
                        f"{point['month']}: average health score {round(point['average_health_score'], 1)}."
                        for point in trend[-3:]
                    ],
                ] or ["No monthly health-score trend point is currently available."],
                recommended_action=(
                    "Review the accounts contributing to the recent health-score movement and confirm an owner action for material declines."
                    if len(scores) >= 2 and change < 0
                    else "Continue monitoring the health-score trend as new monthly snapshots are recorded."
                ),
            ),
        )

    raise ValueError(f"Unsupported dashboard AI analysis section: {section_key}")


DashboardProgressCallback = Callable[[str, str, int], None]


def _emit_dashboard_progress(
    callback: DashboardProgressCallback | None,
    stage: str,
    message: str,
    progress: int,
) -> None:
    """Emit a safe, user-facing lifecycle update for the SSE response."""
    if callback is None:
        return
    try:
        callback(stage, message, max(0, min(100, int(progress))))
    except Exception:
        logger.debug("dashboard_widget_progress_callback_failed stage=%s", stage, exc_info=True)


def analyse_dashboard_widget_now(
    section: DashboardAnalysisSection,
    actor: AgentActor,
    progress_callback: DashboardProgressCallback | None = None,
) -> DashboardWidgetAnalysisResponse:
    """Run one selected Dashboard widget analysis through the single AI API.

    ``progress_callback`` is used only by the ``stream=true`` SSE flow. Normal JSON
    requests keep the same response contract.
    """
    _emit_dashboard_progress(progress_callback, "starting", "Starting the focused dashboard analysis.", 6)
    _assert_agents_ready()
    current_user = actor.as_current_user()
    section_key = section.value
    run_id = _create_run(scope="dashboard_widget", actor=actor, customer_id=section_key)
    try:
        _update_run(
            run_id,
            status=AgentRunStatus.RUNNING.value,
            started_at=_now(),
            total_customers=0,
            error_message=None,
        )
        _emit_dashboard_progress(progress_callback, "dashboard_data", "Reading the current dashboard values.", 20)
        dashboard = get_dashboard_summary(current_user)
        # A widget needs only the highest-priority sample for evidence. Live KPI totals
        # still come from get_dashboard_summary(), so one sparkle click remains quick.
        _emit_dashboard_progress(progress_callback, "customer_signals", "Checking the relevant customer signals.", 42)
        contexts = load_portfolio_contexts(current_user, limit=25)
        rows = _dashboard_widget_customer_rows(contexts)
        facts, fallback = _build_dashboard_widget_input(section, dashboard=dashboard, rows=rows)
        _emit_dashboard_progress(progress_callback, "ai_brief", "Generating a validated AI brief.", 70)
        result = analyse_dashboard_widget(section=section_key, facts=facts, fallback=fallback)
        _emit_dashboard_progress(progress_callback, "validation", "Validating the AI response against the dashboard facts.", 90)
        completed_at = _now()
        _update_run(
            run_id,
            status=AgentRunStatus.COMPLETED.value,
            completed_at=completed_at,
            total_customers=len(contexts),
            processed_customers=len(contexts),
            successful_customers=len(contexts),
            failed_customers=0,
            total_llm_duration_ms=result.total_duration_ms or 0,
            model=result.model,
            error_message=None,
        )
        response = DashboardWidgetAnalysisResponse(
            message="AI widget analysis completed.",
            section=section,
            title=_DASHBOARD_WIDGET_TITLES[section_key],
            status=result.insight.status,
            summary=result.insight.summary,
            evidence=result.insight.evidence,
            recommended_action=result.insight.recommended_action,
            generated_at=completed_at,
        )
        _emit_dashboard_progress(progress_callback, "completed", "Validated insight is ready.", 100)
        return response
    except Exception as exc:
        logger.exception("dashboard_widget_ai_analysis_failed section=%s", section_key)
        _update_run(
            run_id,
            status=AgentRunStatus.FAILED.value,
            completed_at=_now(),
            error_message=_clean_error(exc),
        )
        raise
