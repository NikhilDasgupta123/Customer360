"""Neo4j-backed Customer List service.

The API uses verified JWT user data to scope Account Manager requests. Query
parameters are always passed as Cypher parameters; only a fixed sort-expression
map is interpolated into Cypher, so callers cannot inject Cypher through
search/sort/filter values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neo4j.exceptions import ServiceUnavailable, SessionExpired

from customergraph.auth.schemas import CurrentUserResponse
from customergraph.core.config import get_settings
from customergraph.core.logging import get_logger
from customergraph.customers.schemas import CustomerListItem, CustomerListResponse
from customergraph.db.neo4j_client import get_neo4j_driver
from customergraph.models.user import UserRole

logger = get_logger("customergraph.customers")

_OPEN_TICKET_STATUSES = ["open", "in_progress", "pending"]
_CUSTOMER_AI_AGENT_TYPE = "customer_health_churn"
_SORT_EXPRESSIONS = {
    "company_name": "toLower(coalesce(customer.company_name, customer.name, ''))",
    "health_score": "coalesce(customer.health_score, -1)",
    "renewal_date": "coalesce(toString(customer.renewal_date), '9999-12-31')",
    "annual_contract_value": "coalesce(customer.annual_contract_value, 0)",
}


@dataclass(frozen=True)
class CustomerListFilters:
    """Validated, normalized list filters passed to the graph query."""

    search: str = ""
    risk_level: str | None = None
    renewal_within_days: int | None = None
    owner_user_id: str | None = None
    limit: int = 25
    offset: int = 0
    sort_by: str = "company_name"
    sort_direction: str = "asc"


class CustomerScopeError(ValueError):
    """Raised when a role attempts to bypass its customer ownership scope."""


def _number(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _count(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _record_value(record: Any, key: str, default: Any = None) -> Any:
    if record is None:
        return default
    value = record.get(key, default)
    return default if value is None else value


def _normalise_filters(filters: CustomerListFilters) -> CustomerListFilters:
    """Normalise caller input while retaining router-level range validation."""
    sort_by = filters.sort_by.strip().lower()
    if sort_by not in _SORT_EXPRESSIONS:
        sort_by = "company_name"

    sort_direction = filters.sort_direction.strip().lower()
    if sort_direction not in {"asc", "desc"}:
        sort_direction = "asc"

    risk_level = filters.risk_level.strip().lower() if filters.risk_level else None
    owner_user_id = filters.owner_user_id.strip() if filters.owner_user_id else None

    return CustomerListFilters(
        search=filters.search.strip().lower(),
        risk_level=risk_level or None,
        renewal_within_days=filters.renewal_within_days,
        owner_user_id=owner_user_id or None,
        limit=filters.limit,
        offset=filters.offset,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )


def _scope_params(current_user: CurrentUserResponse, filters: CustomerListFilters) -> tuple[dict[str, Any], str]:
    """Build graph parameters and enforce Account Manager ownership safely."""
    restrict_to_current_owner = current_user.role is UserRole.ACCOUNT_MANAGER

    if restrict_to_current_owner and filters.owner_user_id and filters.owner_user_id != current_user.id:
        raise CustomerScopeError("Account Managers can only filter their own assigned customers.")

    effective_owner_filter = current_user.id if restrict_to_current_owner else (filters.owner_user_id or "")

    return (
        {
            "restrict_to_current_owner": restrict_to_current_owner,
            "current_owner_user_id": current_user.id,
            "owner_user_id_filter": effective_owner_filter,
            "search": filters.search,
            "risk_level": filters.risk_level or "",
            "renewal_within_days": filters.renewal_within_days,
            "open_ticket_statuses": _OPEN_TICKET_STATUSES,
            "customer_ai_agent_type": _CUSTOMER_AI_AGENT_TYPE,
            "limit": filters.limit,
            "offset": filters.offset,
        },
        "assigned_customers" if restrict_to_current_owner else "all_customers",
    )


def _where_clause() -> str:
    """Return one reusable parameterised filter clause for count and list queries."""
    return """
        WHERE (NOT $restrict_to_current_owner OR customer.owner_user_id = $current_owner_user_id)
          AND ($owner_user_id_filter = "" OR customer.owner_user_id = $owner_user_id_filter)
          AND (
              $search = ""
              OR toLower(coalesce(customer.company_name, customer.name, "")) CONTAINS $search
              OR toLower(coalesce(customer.industry, "")) CONTAINS $search
              OR toLower(coalesce(customer.location, "")) CONTAINS $search
          )
          AND ($risk_level = "" OR toLower(coalesce(customer.risk_level, "")) = $risk_level)
          AND (
              $renewal_within_days IS NULL
              OR (
                  customer.renewal_date IS NOT NULL
                  AND date(customer.renewal_date) >= date()
                  AND date(customer.renewal_date) <= date() + duration({days: $renewal_within_days})
              )
          )
    """


def _load_customer_list_once(
    driver: Any,
    params: dict[str, Any],
    *,
    sort_by: str,
    sort_direction: str,
) -> tuple[Any, list[Any]]:
    """Read count + one page through a short-lived Neo4j session."""
    sort_expression = _SORT_EXPRESSIONS[sort_by]
    direction = "DESC" if sort_direction == "desc" else "ASC"
    where_clause = _where_clause()

    with driver.session(database=get_settings().neo4j_database) as session:
        total_record = session.run(
            f"""
            MATCH (customer:Customer)
            {where_clause}
            RETURN count(customer) AS total
            """,
            **params,
        ).single()

        records = list(
            session.run(
                f"""
                MATCH (customer:Customer)
                {where_clause}
                OPTIONAL MATCH (customer)-[:ASSIGNED_TO]->(owner:Employee)
                WITH customer, head(collect(owner)) AS owner
                OPTIONAL MATCH (customer)-[:HAS_TICKET]->(ticket:Ticket)
                WITH
                    customer,
                    owner,
                    sum(
                        CASE
                            WHEN toLower(coalesce(ticket.status, "")) IN $open_ticket_statuses
                            THEN 1
                            ELSE 0
                        END
                    ) AS open_ticket_count
                // Do not put a Cypher map literal inside this Python f-string.
                // The agent type is passed as a Cypher parameter, avoiding an inline map literal.
                // as a missing Python variable and works before AI insight nodes exist.
                OPTIONAL MATCH (customer)-[:HAS_AI_INSIGHT]->(ai_insight:CustomerAIInsight)
                WHERE ai_insight.agent_type = $customer_ai_agent_type
                WITH customer, owner, open_ticket_count, head(collect(ai_insight)) AS ai_insight
                RETURN
                    coalesce(customer.id, elementId(customer)) AS customer_id,
                    coalesce(customer.company_name, customer.name, "Unnamed Customer") AS company_name,
                    customer.industry AS industry,
                    customer.location AS location,
                    customer.owner_user_id AS owner_user_id,
                    coalesce(owner.full_name, owner.name) AS owner_name,
                    customer.health_score AS health_score,
                    coalesce(customer.risk_level, "unknown") AS risk_level,
                    customer.risk_reason AS risk_reason,
                    toString(customer.renewal_date) AS renewal_date,
                    open_ticket_count,
                    coalesce(customer.annual_contract_value, 0) AS annual_contract_value,
                    toString(customer.last_activity_date) AS last_activity_date,
                    ai_insight.risk_level AS ai_risk_level,
                    ai_insight.executive_summary AS ai_summary,
                    ai_insight.recommended_action AS ai_recommended_action,
                    toString(ai_insight.generated_at) AS ai_generated_at,
                    {sort_expression} AS sort_value
                ORDER BY sort_value {direction}, company_name ASC
                SKIP $offset
                LIMIT $limit
                """,
                **params,
            )
        )

    return total_record, records


def _load_customer_list_with_retry(
    driver: Any,
    params: dict[str, Any],
    *,
    sort_by: str,
    sort_direction: str,
) -> tuple[Any, list[Any]]:
    """Retry once if Neo4j Aura has discarded an idle pooled connection."""
    transient_errors = (SessionExpired, ServiceUnavailable, OSError)

    for attempt in range(2):
        try:
            return _load_customer_list_once(
                driver,
                params,
                sort_by=sort_by,
                sort_direction=sort_direction,
            )
        except transient_errors as exc:
            if attempt == 1:
                logger.exception(
                    "customer_list_transient_connection_failed_after_retry error_type=%s",
                    type(exc).__name__,
                )
                raise
            logger.warning(
                "customer_list_transient_connection_retry error_type=%s",
                type(exc).__name__,
            )

    raise RuntimeError("Customer list graph retry loop exited unexpectedly.")


def list_customers(
    current_user: CurrentUserResponse,
    filters: CustomerListFilters,
) -> CustomerListResponse:
    """Return a role-scoped, paginated Customer List from Neo4j."""
    normalised = _normalise_filters(filters)
    params, scope = _scope_params(current_user, normalised)

    total_record, records = _load_customer_list_with_retry(
        get_neo4j_driver(),
        params,
        sort_by=normalised.sort_by,
        sort_direction=normalised.sort_direction,
    )

    customers = [
        CustomerListItem(
            id=str(_record_value(record, "customer_id", "")),
            company_name=str(_record_value(record, "company_name", "Unnamed Customer")),
            industry=(
                str(_record_value(record, "industry"))
                if _record_value(record, "industry") is not None
                else None
            ),
            location=(
                str(_record_value(record, "location"))
                if _record_value(record, "location") is not None
                else None
            ),
            owner_user_id=(
                str(_record_value(record, "owner_user_id"))
                if _record_value(record, "owner_user_id") is not None
                else None
            ),
            owner_name=(
                str(_record_value(record, "owner_name"))
                if _record_value(record, "owner_name") is not None
                else None
            ),
            health_score=(
                _number(_record_value(record, "health_score"))
                if _record_value(record, "health_score") is not None
                else None
            ),
            risk_level=str(_record_value(record, "risk_level", "unknown")),
            risk_reason=(
                str(_record_value(record, "risk_reason"))
                if _record_value(record, "risk_reason") is not None
                else None
            ),
            renewal_date=(
                str(_record_value(record, "renewal_date"))
                if _record_value(record, "renewal_date") not in {None, "None"}
                else None
            ),
            open_ticket_count=_count(_record_value(record, "open_ticket_count")),
            annual_contract_value=_number(_record_value(record, "annual_contract_value")),
            last_activity_date=(
                str(_record_value(record, "last_activity_date"))
                if _record_value(record, "last_activity_date") not in {None, "None"}
                else None
            ),
            ai_risk_level=(
                str(_record_value(record, "ai_risk_level"))
                if _record_value(record, "ai_risk_level") is not None
                else None
            ),
            ai_summary=(
                str(_record_value(record, "ai_summary"))
                if _record_value(record, "ai_summary") is not None
                else None
            ),
            ai_recommended_action=(
                str(_record_value(record, "ai_recommended_action"))
                if _record_value(record, "ai_recommended_action") is not None
                else None
            ),
            ai_generated_at=(
                str(_record_value(record, "ai_generated_at"))
                if _record_value(record, "ai_generated_at") not in {None, "None"}
                else None
            ),
        )
        for record in records
    ]

    total = _count(_record_value(total_record, "total"))
    return CustomerListResponse(
        scope=scope,
        total=total,
        limit=normalised.limit,
        offset=normalised.offset,
        has_more=(normalised.offset + len(customers)) < total,
        customers=customers,
    )
