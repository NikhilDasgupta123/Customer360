"""Neo4j-backed aggregation service for the CustomerGraph Main Dashboard.

This is deliberately the first Dashboard backend API. It does not create demo
numbers. Until customer graph data is loaded it returns a valid summary with
zero counts and empty chart/table lists.

Canonical graph contract used by this service:
- (:Customer {id, name/company_name, owner_user_id, health_score, risk_level,
  risk_reason, annual_contract_value})
- (:Customer)-[:HAS_CONTRACT]->(:Contract {renewal_date, status})
- (:Customer)-[:HAS_TICKET]->(:Ticket {status, severity})
- (:Customer)-[:HAS_INVOICE]->(:Invoice {status, due_date, outstanding_amount})
- (:Customer)-[:HAS_OPPORTUNITY]->(:Opportunity {status, opportunity_type,
  estimated_revenue})
- (:HealthSnapshot {month, average_health_score, owner_user_id})

All dates should be stored as Neo4j date values or ISO-8601 YYYY-MM-DD strings.
All money values must be numeric values in the organisation's selected currency.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired

from customergraph.auth.schemas import CurrentUserResponse
from customergraph.core.config import get_settings
from customergraph.core.logging import get_logger
from customergraph.dashboard.schemas import (
    DashboardHealthTrendPoint,
    DashboardHighRiskCustomer,
    DashboardSummaryResponse,
)
from customergraph.db.neo4j_client import get_neo4j_driver
from customergraph.models.user import UserRole

logger = get_logger("customergraph.dashboard")


_SCOPE_WHERE = "($is_admin = true OR customer.owner_user_id = $owner_user_id)"
_HIGH_RISK_LEVELS = ["high", "critical"]
_OPEN_TICKET_STATUSES = ["open", "in_progress", "pending"]
_CLOSED_OPPORTUNITY_STATUSES = ["won", "lost", "closed", "rejected"]


def _scope_params(current_user: CurrentUserResponse) -> tuple[dict[str, Any], str]:
    """Return safe graph parameters and a user-visible dashboard scope label."""
    is_admin = current_user.role is UserRole.ADMIN
    return (
        {
            "is_admin": is_admin,
            "owner_user_id": current_user.id,
            "high_risk_levels": _HIGH_RISK_LEVELS,
            "open_ticket_statuses": _OPEN_TICKET_STATUSES,
            "closed_opportunity_statuses": _CLOSED_OPPORTUNITY_STATUSES,
        },
        "all_customers" if is_admin else "assigned_customers",
    )


def _number(value: Any) -> float:
    """Convert Neo4j numeric values safely for the JSON response."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _count(value: Any) -> int:
    """Convert Neo4j count values safely for the JSON response."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _record_value(record: Any, key: str, default: Any = None) -> Any:
    """Read a Neo4j record key without leaking driver-specific behaviour."""
    if record is None:
        return default
    value = record.get(key, default)
    return default if value is None else value


def _load_dashboard_records_once(driver: Any, params: dict[str, Any]) -> tuple[Any, Any, Any, Any, Any, list[Any], list[Any]]:
    """Read all dashboard result sets in one short-lived Neo4j session."""
    with driver.session(database=get_settings().neo4j_database) as session:
        base_record = session.run(
            f"""
            MATCH (customer:Customer)
            WHERE {_SCOPE_WHERE}
            WITH customer, toLower(trim(coalesce(customer.risk_level, ""))) AS risk_level
            RETURN
                count(customer) AS total_customers,
                sum(CASE WHEN risk_level IN $high_risk_levels THEN 1 ELSE 0 END) AS high_risk_customers,
                sum(
                    CASE
                        WHEN risk_level IN $high_risk_levels
                        THEN coalesce(
                            customer.revenue_at_risk,
                            customer.annual_contract_value,
                            customer.contract_value,
                            0
                        )
                        ELSE 0
                    END
                ) AS revenue_at_risk
            """,
            **params,
        ).single()

        renewals_record = session.run(
            f"""
            MATCH (customer:Customer)-[:HAS_CONTRACT]->(contract:Contract)
            WHERE {_SCOPE_WHERE}
              AND coalesce(toLower(contract.status), "active") <> "cancelled"
              AND contract.renewal_date IS NOT NULL
              AND date(contract.renewal_date) >= date()
              AND date(contract.renewal_date) <= date() + duration({{days: 30}})
            RETURN count(DISTINCT customer) AS upcoming_renewals
            """,
            **params,
        ).single()

        critical_tickets_record = session.run(
            f"""
            MATCH (customer:Customer)-[:HAS_TICKET]->(ticket:Ticket)
            WHERE {_SCOPE_WHERE}
              AND toLower(coalesce(ticket.status, "open")) IN $open_ticket_statuses
              AND toLower(coalesce(ticket.severity, "")) = "critical"
            RETURN count(ticket) AS open_critical_tickets
            """,
            **params,
        ).single()

        invoices_record = session.run(
            f"""
            MATCH (customer:Customer)-[:HAS_INVOICE]->(invoice:Invoice)
            WHERE {_SCOPE_WHERE}
              AND toLower(coalesce(invoice.status, "open")) <> "paid"
              AND (
                toLower(coalesce(invoice.status, "")) IN ["overdue", "delayed"]
                OR (
                    invoice.due_date IS NOT NULL
                    AND date(invoice.due_date) < date()
                )
              )
            RETURN
                count(invoice) AS delayed_invoices_count,
                sum(coalesce(invoice.outstanding_amount, invoice.amount_due, invoice.amount, 0))
                    AS delayed_invoices_amount
            """,
            **params,
        ).single()

        opportunities_record = session.run(
            f"""
            MATCH (customer:Customer)-[:HAS_OPPORTUNITY]->(opportunity:Opportunity)
            WHERE {_SCOPE_WHERE}
              AND toLower(coalesce(opportunity.opportunity_type, opportunity.type, ""))
                    IN ["upsell", "cross_sell"]
              AND NOT (
                    toLower(coalesce(opportunity.status, "open"))
                    IN $closed_opportunity_statuses
              )
            RETURN
                count(opportunity) AS upsell_opportunities_count,
                sum(coalesce(opportunity.estimated_revenue, opportunity.potential_revenue, 0))
                    AS upsell_potential_revenue
            """,
            **params,
        ).single()

        trend_records = list(
            session.run(
                """
                MATCH (snapshot:HealthSnapshot)
                WHERE ($is_admin = true OR snapshot.owner_user_id = $owner_user_id)
                RETURN
                    toString(snapshot.month) AS month,
                    coalesce(snapshot.average_health_score, 0) AS average_health_score
                ORDER BY snapshot.month DESC
                LIMIT 6
                """,
                **params,
            )
        )

        high_risk_records = list(
            session.run(
                f"""
                MATCH (customer:Customer)
                WHERE {_SCOPE_WHERE}
                  AND toLower(trim(coalesce(customer.risk_level, ""))) IN $high_risk_levels
                RETURN
                    coalesce(customer.id, elementId(customer)) AS customer_id,
                    coalesce(customer.company_name, customer.name, "Unnamed Customer") AS customer_name,
                    customer.health_score AS health_score,
                    customer.risk_level AS risk_level,
                    customer.risk_reason AS risk_reason,
                    toString(customer.renewal_date) AS renewal_date,
                    coalesce(
                        customer.revenue_at_risk,
                        customer.annual_contract_value,
                        customer.contract_value,
                        0
                    ) AS revenue_at_risk
                ORDER BY coalesce(customer.health_score, 100) ASC, revenue_at_risk DESC
                LIMIT 5
                """,
                **params,
            )
        )

    return (
        base_record,
        renewals_record,
        critical_tickets_record,
        invoices_record,
        opportunities_record,
        trend_records,
        high_risk_records,
    )


def _load_dashboard_records_with_retry(driver: Any, params: dict[str, Any]) -> tuple[Any, Any, Any, Any, Any, list[Any], list[Any]]:
    """Retry once when Aura drops an idle/defunct Bolt connection.

    The Neo4j driver discards a defunct connection after ``SessionExpired`` or
    ``ServiceUnavailable``. A fresh session on the same driver can then obtain
    a healthy pooled connection without changing business data or auth rules.
    """
    transient_errors = (SessionExpired, ServiceUnavailable, OSError)

    for attempt in range(2):
        try:
            return _load_dashboard_records_once(driver, params)
        except transient_errors as exc:
            if attempt == 1:
                logger.exception(
                    "dashboard_summary_transient_connection_failed_after_retry error_type=%s",
                    type(exc).__name__,
                )
                raise

            logger.warning(
                "dashboard_summary_transient_connection_retry error_type=%s",
                type(exc).__name__,
            )

    raise RuntimeError("Dashboard graph retry loop exited unexpectedly.")


def get_dashboard_summary(current_user: CurrentUserResponse) -> DashboardSummaryResponse:
    """Return live graph-derived dashboard metrics for the authenticated user.

    Admin sees the full portfolio. Account Manager sees only Customer nodes
    whose immutable ``owner_user_id`` matches the verified JWT user. This
    keeps the future React dashboard from bypassing customer ownership rules.
    """
    params, scope = _scope_params(current_user)
    driver = get_neo4j_driver()

    try:
        (
            base_record,
            renewals_record,
            critical_tickets_record,
            invoices_record,
            opportunities_record,
            trend_records,
            high_risk_records,
        ) = _load_dashboard_records_with_retry(driver, params)
    except (Neo4jError, OSError):
        logger.exception("dashboard_summary_graph_query_failed user_id=%s", current_user.id)
        raise

    health_score_trend = [
        DashboardHealthTrendPoint(
            month=str(_record_value(record, "month", "")),
            average_health_score=_number(_record_value(record, "average_health_score")),
        )
        for record in reversed(trend_records)
        if _record_value(record, "month", "")
    ]

    top_high_risk_customers = [
        DashboardHighRiskCustomer(
            customer_id=str(_record_value(record, "customer_id", "")) or None,
            customer_name=str(_record_value(record, "customer_name", "Unnamed Customer")),
            health_score=(
                _number(_record_value(record, "health_score"))
                if _record_value(record, "health_score") is not None
                else None
            ),
            risk_level=str(_record_value(record, "risk_level", "High")),
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
            revenue_at_risk=_number(_record_value(record, "revenue_at_risk")),
        )
        for record in high_risk_records
    ]

    return DashboardSummaryResponse(
        generated_at=datetime.now(timezone.utc),
        scope=scope,
        total_customers=_count(_record_value(base_record, "total_customers")),
        high_risk_customers=_count(_record_value(base_record, "high_risk_customers")),
        upcoming_renewals_next_30_days=_count(_record_value(renewals_record, "upcoming_renewals")),
        open_critical_tickets=_count(_record_value(critical_tickets_record, "open_critical_tickets")),
        delayed_invoices_count=_count(_record_value(invoices_record, "delayed_invoices_count")),
        delayed_invoices_amount=_number(_record_value(invoices_record, "delayed_invoices_amount")),
        upsell_opportunities_count=_count(_record_value(opportunities_record, "upsell_opportunities_count")),
        upsell_potential_revenue=_number(_record_value(opportunities_record, "upsell_potential_revenue")),
        revenue_at_risk=_number(_record_value(base_record, "revenue_at_risk")),
        health_score_trend=health_score_trend,
        top_high_risk_customers=top_high_risk_customers,
    )
