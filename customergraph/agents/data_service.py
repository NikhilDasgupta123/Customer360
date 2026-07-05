"""Authorized Neo4j reads used by CustomerGraph LLM agents.

This module owns graph aggregation for AI prompts. It deliberately returns
compact factual metrics instead of Cypher text, credentials, or whole customer
records. The LLM can explain a recommendation, but it never selects data that
the authenticated backend user was not allowed to read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from customergraph.auth.schemas import CurrentUserResponse
from customergraph.core.config import get_settings
from customergraph.db.neo4j_client import get_neo4j_driver
from customergraph.models.user import UserRole

_OPEN_TICKET_STATUSES = {"open", "in_progress", "pending"}
_CLOSED_OPPORTUNITY_STATUSES = {"won", "lost", "closed", "rejected"}


class CustomerAnalysisAccessError(RuntimeError):
    """Raised when a user asks an agent to inspect an unavailable customer."""


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value)
    return default if text == "None" else text


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _count(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _iso_date(value: Any) -> str | None:
    if value is None:
        return None
    text = _text(value)
    return text if text else None


def _days_until(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return (date.fromisoformat(value[:10]) - date.today()).days
    except ValueError:
        return None


def _days_overdue(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return max(0, (date.today() - date.fromisoformat(value[:10])).days)
    except ValueError:
        return None


@dataclass(frozen=True)
class CustomerAnalysisContext:
    """Minimal factual customer context passed to the health/churn agent."""

    customer_id: str
    customer_name: str
    owner_user_id: str | None
    industry: str | None
    location: str | None
    health_score: float | None
    current_risk_level: str | None
    current_risk_reason: str | None
    annual_contract_value: float
    revenue_at_risk: float
    renewal_date: str | None
    days_until_renewal: int | None
    last_activity_date: str | None
    open_ticket_count: int
    critical_ticket_count: int
    open_tickets: list[dict[str, Any]]
    overdue_invoice_count: int
    overdue_invoice_amount: float
    max_invoice_days_overdue: int
    overdue_invoices: list[dict[str, Any]]
    usage_change_percent: float | None
    monthly_active_users: int | None
    open_upsell_opportunity_count: int
    open_upsell_potential_revenue: float

    def to_llm_payload(self) -> dict[str, Any]:
        return {
            "customer": {
                "id": self.customer_id,
                "name": self.customer_name,
                "industry": self.industry,
                "location": self.location,
                "health_score": self.health_score,
                "current_system_risk_level": self.current_risk_level,
                "current_system_risk_reason": self.current_risk_reason,
                "annual_contract_value": self.annual_contract_value,
                "revenue_at_risk": self.revenue_at_risk,
                "renewal_date": self.renewal_date,
                "days_until_renewal": self.days_until_renewal,
                "last_activity_date": self.last_activity_date,
            },
            "support": {
                "open_ticket_count": self.open_ticket_count,
                "critical_ticket_count": self.critical_ticket_count,
                "open_tickets": self.open_tickets[:5],
            },
            "billing": {
                "overdue_invoice_count": self.overdue_invoice_count,
                "overdue_invoice_amount": self.overdue_invoice_amount,
                "max_invoice_days_overdue": self.max_invoice_days_overdue,
                "overdue_invoices": self.overdue_invoices[:5],
            },
            "adoption": {
                "usage_change_percent": self.usage_change_percent,
                "monthly_active_users": self.monthly_active_users,
            },
            "growth": {
                "open_upsell_opportunity_count": self.open_upsell_opportunity_count,
                "open_upsell_potential_revenue": self.open_upsell_potential_revenue,
            },
        }


def _is_owner_restricted(current_user: CurrentUserResponse) -> bool:
    return current_user.role is UserRole.ACCOUNT_MANAGER


def _read_customer_core(session: Any, customer_id: str, current_user: CurrentUserResponse) -> dict[str, Any] | None:
    record = session.run(
        """
        MATCH (customer:Customer)
        WHERE coalesce(customer.id, elementId(customer)) = $customer_id
          AND (NOT $owner_restricted OR customer.owner_user_id = $owner_user_id)
        RETURN
            coalesce(customer.id, elementId(customer)) AS customer_id,
            coalesce(customer.company_name, customer.name, "Unnamed Customer") AS customer_name,
            customer.owner_user_id AS owner_user_id,
            customer.industry AS industry,
            customer.location AS location,
            customer.health_score AS health_score,
            customer.risk_level AS current_risk_level,
            customer.risk_reason AS current_risk_reason,
            coalesce(customer.annual_contract_value, 0) AS annual_contract_value,
            coalesce(customer.revenue_at_risk, 0) AS revenue_at_risk,
            toString(customer.renewal_date) AS renewal_date,
            toString(customer.last_activity_date) AS last_activity_date
        """,
        customer_id=customer_id,
        owner_restricted=_is_owner_restricted(current_user),
        owner_user_id=current_user.id,
    ).single()
    return dict(record) if record is not None else None


def _load_context_in_session(session: Any, customer_id: str, current_user: CurrentUserResponse) -> CustomerAnalysisContext:
    core = _read_customer_core(session, customer_id, current_user)
    if core is None:
        raise CustomerAnalysisAccessError("Customer was not found or is not available in your assigned scope.")

    tickets = list(
        session.run(
            """
            MATCH (customer:Customer)
            WHERE coalesce(customer.id, elementId(customer)) = $customer_id
            OPTIONAL MATCH (customer)-[:HAS_TICKET]->(ticket:Ticket)
            RETURN collect({
                id: ticket.id,
                title: ticket.title,
                status: ticket.status,
                severity: ticket.severity,
                age_days: ticket.age_days
            }) AS tickets
            """,
            customer_id=customer_id,
        ).single()["tickets"]
        or []
    )
    open_tickets: list[dict[str, Any]] = []
    critical_ticket_count = 0
    for ticket in tickets:
        if not isinstance(ticket, dict) or not ticket.get("id"):
            continue
        status = _text(ticket.get("status"), "open").lower()
        severity = _text(ticket.get("severity")).lower()
        if status in _OPEN_TICKET_STATUSES:
            clean_ticket = {
                "title": _text(ticket.get("title"), "Untitled ticket"),
                "status": status,
                "severity": severity or "unknown",
                "age_days": _count(ticket.get("age_days")),
            }
            open_tickets.append(clean_ticket)
            if severity == "critical":
                critical_ticket_count += 1
    open_tickets.sort(key=lambda item: (item["severity"] != "critical", -item["age_days"]))

    invoices = list(
        session.run(
            """
            MATCH (customer:Customer)
            WHERE coalesce(customer.id, elementId(customer)) = $customer_id
            OPTIONAL MATCH (customer)-[:HAS_INVOICE]->(invoice:Invoice)
            RETURN collect({
                id: invoice.id,
                status: invoice.status,
                due_date: toString(invoice.due_date),
                outstanding_amount: invoice.outstanding_amount
            }) AS invoices
            """,
            customer_id=customer_id,
        ).single()["invoices"]
        or []
    )
    overdue_invoices: list[dict[str, Any]] = []
    overdue_invoice_amount = 0.0
    max_invoice_days_overdue = 0
    for invoice in invoices:
        if not isinstance(invoice, dict) or not invoice.get("id"):
            continue
        status = _text(invoice.get("status"), "open").lower()
        due_date = _iso_date(invoice.get("due_date"))
        days_overdue = _days_overdue(due_date)
        is_overdue = status in {"overdue", "delayed"} or (status != "paid" and (days_overdue or 0) > 0)
        if is_overdue:
            amount = _number(invoice.get("outstanding_amount"))
            overdue_invoice_amount += amount
            max_invoice_days_overdue = max(max_invoice_days_overdue, days_overdue or 0)
            overdue_invoices.append(
                {"status": status, "due_date": due_date, "outstanding_amount": amount, "days_overdue": days_overdue or 0}
            )
    overdue_invoices.sort(key=lambda item: (-item["days_overdue"], -item["outstanding_amount"]))

    # The current graph does not have updated_at / created_at on Usage nodes.
    # Reading one available Usage node avoids repeated Neo4j warnings without
    # pretending that a non-existent timestamp defines a latest record.
    usage_record = session.run(
        """
        MATCH (customer:Customer)
        WHERE coalesce(customer.id, elementId(customer)) = $customer_id
        OPTIONAL MATCH (customer)-[:HAS_USAGE]->(usage:Usage)
        RETURN head(collect(usage)) AS latest_usage
        """,
        customer_id=customer_id,
    ).single()
    latest_usage = usage_record["latest_usage"] if usage_record is not None else None
    usage_change_percent = _number(latest_usage.get("usage_change_percent")) if latest_usage else None
    monthly_active_users = _count(latest_usage.get("monthly_active_users")) if latest_usage else None

    opportunity_record = session.run(
        """
        MATCH (customer:Customer)
        WHERE coalesce(customer.id, elementId(customer)) = $customer_id
        OPTIONAL MATCH (customer)-[:HAS_OPPORTUNITY]->(opportunity:Opportunity)
        WITH collect(opportunity) AS opportunities
        RETURN
            size([opportunity IN opportunities
                WHERE opportunity IS NOT NULL
                  AND toLower(coalesce(opportunity.opportunity_type, "")) IN ["upsell", "cross_sell"]
                  AND NOT (toLower(coalesce(opportunity.status, "open")) IN $closed_statuses)
            ]) AS open_upsell_opportunity_count,
            reduce(total = 0.0, opportunity IN opportunities |
                CASE
                    WHEN opportunity IS NOT NULL
                      AND toLower(coalesce(opportunity.opportunity_type, "")) IN ["upsell", "cross_sell"]
                      AND NOT (toLower(coalesce(opportunity.status, "open")) IN $closed_statuses)
                    THEN total + coalesce(toFloat(opportunity.estimated_revenue), 0.0)
                    ELSE total
                END
            ) AS open_upsell_potential_revenue
        """,
        customer_id=customer_id,
        closed_statuses=sorted(_CLOSED_OPPORTUNITY_STATUSES),
    ).single()

    renewal_date = _iso_date(core.get("renewal_date"))
    return CustomerAnalysisContext(
        customer_id=_text(core.get("customer_id")),
        customer_name=_text(core.get("customer_name"), "Unnamed Customer"),
        owner_user_id=_iso_date(core.get("owner_user_id")),
        industry=_iso_date(core.get("industry")),
        location=_iso_date(core.get("location")),
        health_score=_number(core.get("health_score")) if core.get("health_score") is not None else None,
        current_risk_level=_iso_date(core.get("current_risk_level")),
        current_risk_reason=_iso_date(core.get("current_risk_reason")),
        annual_contract_value=_number(core.get("annual_contract_value")),
        revenue_at_risk=_number(core.get("revenue_at_risk")),
        renewal_date=renewal_date,
        days_until_renewal=_days_until(renewal_date),
        last_activity_date=_iso_date(core.get("last_activity_date")),
        open_ticket_count=len(open_tickets),
        critical_ticket_count=critical_ticket_count,
        open_tickets=open_tickets,
        overdue_invoice_count=len(overdue_invoices),
        overdue_invoice_amount=overdue_invoice_amount,
        max_invoice_days_overdue=max_invoice_days_overdue,
        overdue_invoices=overdue_invoices,
        usage_change_percent=usage_change_percent,
        monthly_active_users=monthly_active_users,
        open_upsell_opportunity_count=_count(opportunity_record.get("open_upsell_opportunity_count")) if opportunity_record else 0,
        open_upsell_potential_revenue=_number(opportunity_record.get("open_upsell_potential_revenue")) if opportunity_record else 0.0,
    )


def load_customer_context(customer_id: str, current_user: CurrentUserResponse) -> CustomerAnalysisContext:
    """Load one role-scoped customer record and its evidence metrics."""
    driver = get_neo4j_driver()
    with driver.session(database=get_settings().neo4j_database) as session:
        return _load_context_in_session(session, customer_id, current_user)


def load_portfolio_contexts(current_user: CurrentUserResponse, *, limit: int | None = None) -> list[CustomerAnalysisContext]:
    """Load every allowed customer context for a portfolio agent run."""
    settings = get_settings()
    bounded_limit = min(max(limit or settings.ai_agent_max_customers_per_portfolio_run, 1), settings.ai_agent_max_customers_per_portfolio_run)
    driver = get_neo4j_driver()
    with driver.session(database=settings.neo4j_database) as session:
        records = list(
            session.run(
                """
                MATCH (customer:Customer)
                WHERE (NOT $owner_restricted OR customer.owner_user_id = $owner_user_id)
                RETURN coalesce(customer.id, elementId(customer)) AS customer_id
                ORDER BY coalesce(customer.health_score, 100) ASC, coalesce(customer.company_name, customer.name, "") ASC
                LIMIT $limit
                """,
                owner_restricted=_is_owner_restricted(current_user),
                owner_user_id=current_user.id,
                limit=bounded_limit,
            )
        )
        customer_ids = [_text(record.get("customer_id")) for record in records if _text(record.get("customer_id"))]
        return [_load_context_in_session(session, customer_id, current_user) for customer_id in customer_ids]
