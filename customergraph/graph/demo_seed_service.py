"""Safe Neo4j schema and deterministic demo data seed for the Main Dashboard.

The seed is deliberately Admin-only at the API layer. It creates a small,
repeatable dataset that exercises the existing dashboard summary queries:
customers, high-risk accounts, renewals, critical tickets, delayed invoices,
upsell opportunities, revenue at risk, and a six-month health trend.

No auth users are created or modified. Demo graph nodes are tagged with a
single dataset marker so reset only removes data that this service created.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from neo4j.exceptions import Neo4jError

from customergraph.auth.schemas import CurrentUserResponse
from customergraph.core.logging import get_logger
from customergraph.db.neo4j_client import get_neo4j_driver
from customergraph.db.sqlite import get_connection, init_auth_db
from customergraph.graph.schemas import GraphDemoSeedResponse
from customergraph.models.user import UserRole, UserStatus

logger = get_logger("customergraph.graph.demo_seed")

DEMO_DATASET = "customergraph-dashboard-demo-v1"
PORTFOLIO_ID = "customergraph-main-dashboard-demo"


# Neo4j Aura supports these standard schema statements. They make the seed
# idempotent and provide stable lookup keys for future CustomerGraph APIs.
_SCHEMA_STATEMENTS = (
    "CREATE CONSTRAINT customergraph_customer_id IF NOT EXISTS FOR (node:Customer) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT customergraph_employee_id IF NOT EXISTS FOR (node:Employee) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT customergraph_product_id IF NOT EXISTS FOR (node:Product) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT customergraph_ticket_id IF NOT EXISTS FOR (node:Ticket) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT customergraph_invoice_id IF NOT EXISTS FOR (node:Invoice) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT customergraph_contract_id IF NOT EXISTS FOR (node:Contract) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT customergraph_opportunity_id IF NOT EXISTS FOR (node:Opportunity) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT customergraph_usage_id IF NOT EXISTS FOR (node:Usage) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT customergraph_health_snapshot_id IF NOT EXISTS FOR (node:HealthSnapshot) REQUIRE node.id IS UNIQUE",
    "CREATE CONSTRAINT customergraph_portfolio_id IF NOT EXISTS FOR (node:DashboardPortfolio) REQUIRE node.id IS UNIQUE",
)


def _month_label(reference: date, months_back: int) -> str:
    """Return a YYYY-MM label without third-party date dependencies."""
    index = (reference.year * 12 + reference.month - 1) - months_back
    year, month_zero_index = divmod(index, 12)
    return f"{year:04d}-{month_zero_index + 1:02d}"


def _active_account_owners(current_user: CurrentUserResponse) -> list[dict[str, str]]:
    """Use real active Account Manager user IDs where available.

    Fresh local projects usually only have an Admin at this stage. In that
    case the authenticated Admin becomes the temporary owner for the demo
    graph so the data remains visible and no fake auth account is created.
    """
    init_auth_db()
    owners: list[dict[str, str]] = []

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, full_name, email
            FROM users
            WHERE role = ? AND status = ?
            ORDER BY created_at ASC
            """,
            (UserRole.ACCOUNT_MANAGER.value, UserStatus.ACTIVE.value),
        ).fetchall()

    for row in rows:
        owners.append(
            {
                "id": str(row["id"]),
                "full_name": str(row["full_name"]),
                "email": str(row["email"]),
            }
        )

    if owners:
        return owners

    return [
        {
            "id": current_user.id,
            "full_name": current_user.full_name,
            "email": current_user.email,
        }
    ]


def _build_demo_payload(current_user: CurrentUserResponse) -> dict[str, list[dict[str, Any]] | dict[str, Any]]:
    """Build deterministic dashboard data with dates relative to today."""
    today = date.today()
    seeded_at = datetime.now(timezone.utc).isoformat()
    owners = _active_account_owners(current_user)

    employees = [
        {
            "id": owner["id"],
            "full_name": owner["full_name"],
            "email": owner["email"],
            "role": UserRole.ACCOUNT_MANAGER.value,
            "demo_dataset": DEMO_DATASET,
            "seeded_at": seeded_at,
        }
        for owner in owners
    ]

    products = [
        {
            "id": "demo-product-cx-core",
            "name": "CustomerGraph Core",
            "category": "Customer Intelligence",
            "demo_dataset": DEMO_DATASET,
            "seeded_at": seeded_at,
        },
        {
            "id": "demo-product-risk-analytics",
            "name": "Risk Analytics",
            "category": "AI Add-on",
            "demo_dataset": DEMO_DATASET,
            "seeded_at": seeded_at,
        },
        {
            "id": "demo-product-support-plus",
            "name": "Support Plus",
            "category": "Support",
            "demo_dataset": DEMO_DATASET,
            "seeded_at": seeded_at,
        },
        {
            "id": "demo-product-revenue-suite",
            "name": "Revenue Suite",
            "category": "Sales",
            "demo_dataset": DEMO_DATASET,
            "seeded_at": seeded_at,
        },
    ]

    # Each entry deliberately has the canonical properties read by the current
    # dashboard API: owner_user_id, health_score, risk_level, risk_reason,
    # annual_contract_value, revenue_at_risk, and renewal_date.
    raw_customers = [
        ("aurora-retail", "Aurora Retail Pvt Ltd", "Retail", "Mumbai", 28, "critical", "Usage dropped 52%, two critical tickets remain open, renewal is near, and an invoice is overdue.", 3_200_000, 3_200_000, 12, "demo-product-cx-core"),
        ("bharat-logistics", "Bharat Logistics Ltd", "Logistics", "Pune", 36, "high", "A critical integration ticket, delayed payment, and a renewal in the next month need attention.", 2_400_000, 2_400_000, 18, "demo-product-support-plus"),
        ("crest-healthcare", "Crest Healthcare Systems", "Healthcare", "Bengaluru", 43, "high", "Low adoption and renewal risk indicate the account needs an executive follow-up.", 1_800_000, 1_500_000, 25, "demo-product-cx-core"),
        ("delta-manufacturing", "Delta Manufacturing", "Manufacturing", "Chennai", 48, "high", "Payment is overdue and product usage has reduced for three consecutive months.", 1_400_000, 1_100_000, 46, "demo-product-risk-analytics"),
        ("evergreen-education", "Evergreen Education", "Education", "Hyderabad", 61, "medium", "Renewal discussion is due soon; current product adoption is stable but needs follow-up.", 950_000, 250_000, 9, "demo-product-cx-core"),
        ("flux-finance", "Flux Finance", "Financial Services", "Delhi", 82, "low", "Healthy usage and recent successful renewal engagement.", 2_100_000, 0, 72, "demo-product-risk-analytics"),
        ("greengrid-energy", "GreenGrid Energy", "Energy", "Ahmedabad", 66, "medium", "Usage is growing, but the renewal window is approaching.", 1_250_000, 300_000, 21, "demo-product-cx-core"),
        ("horizon-telecom", "Horizon Telecom", "Telecom", "Kolkata", 39, "high", "Critical support escalation and negative feedback put the upcoming renewal at risk.", 2_700_000, 2_200_000, 28, "demo-product-support-plus"),
        ("indigo-foods", "Indigo Foods", "Food & Beverage", "Jaipur", 84, "low", "Strong adoption and no unresolved service issues.", 800_000, 0, 88, "demo-product-revenue-suite"),
        ("jade-consulting", "Jade Consulting", "Consulting", "Gurugram", 59, "medium", "Good cross-sell potential before the renewal follow-up.", 1_050_000, 210_000, 14, "demo-product-cx-core"),
        ("kaveri-textiles", "Kaveri Textiles", "Textiles", "Surat", 46, "high", "Declining usage and a long-running support issue need an account manager review.", 1_600_000, 1_100_000, 36, "demo-product-risk-analytics"),
        ("lumina-labs", "Lumina Labs", "Technology", "Noida", 68, "medium", "Usage is stable; a targeted training session can improve feature adoption.", 1_300_000, 260_000, 54, "demo-product-cx-core"),
    ]

    customers: list[dict[str, Any]] = []
    customer_product_links: list[dict[str, str]] = []
    customer_owner_links: list[dict[str, str]] = []
    for index, raw in enumerate(raw_customers):
        (
            customer_key,
            company_name,
            industry,
            location,
            health_score,
            risk_level,
            risk_reason,
            annual_contract_value,
            revenue_at_risk,
            renewal_days,
            product_id,
        ) = raw
        owner = owners[index % len(owners)]
        customer_id = f"demo-customer-{customer_key}"
        customers.append(
            {
                "id": customer_id,
                "company_name": company_name,
                "name": company_name,
                "industry": industry,
                "location": location,
                "owner_user_id": owner["id"],
                "health_score": health_score,
                "risk_level": risk_level,
                "risk_reason": risk_reason,
                "annual_contract_value": annual_contract_value,
                "revenue_at_risk": revenue_at_risk,
                "renewal_date": (today + timedelta(days=renewal_days)).isoformat(),
                # Canonical Customer List field. It represents the latest
                # business interaction date in this deterministic demo graph,
                # not the graph seed timestamp.
                "last_activity_date": (today - timedelta(days=(index + 1) * 2)).isoformat(),
                "demo_dataset": DEMO_DATASET,
                "seeded_at": seeded_at,
            }
        )
        customer_product_links.append({"customer_id": customer_id, "product_id": product_id})
        customer_owner_links.append({"customer_id": customer_id, "employee_id": owner["id"]})

    contracts = [
        {
            "id": f"demo-contract-{customer['id'].removeprefix('demo-customer-')}",
            "customer_id": customer["id"],
            "status": "active",
            "renewal_date": customer["renewal_date"],
            "annual_value": customer["annual_contract_value"],
            "demo_dataset": DEMO_DATASET,
            "seeded_at": seeded_at,
        }
        for customer in customers
    ]

    tickets = [
        {"id": "demo-ticket-aurora-1", "customer_id": "demo-customer-aurora-retail", "title": "POS integration outage", "status": "open", "severity": "critical", "age_days": 8},
        {"id": "demo-ticket-aurora-2", "customer_id": "demo-customer-aurora-retail", "title": "Data sync delay", "status": "in_progress", "severity": "critical", "age_days": 5},
        {"id": "demo-ticket-bharat-1", "customer_id": "demo-customer-bharat-logistics", "title": "Route planning API failure", "status": "open", "severity": "critical", "age_days": 6},
        {"id": "demo-ticket-crest-1", "customer_id": "demo-customer-crest-healthcare", "title": "Usage reporting mismatch", "status": "pending", "severity": "high", "age_days": 4},
        {"id": "demo-ticket-horizon-1", "customer_id": "demo-customer-horizon-telecom", "title": "Call center integration outage", "status": "open", "severity": "critical", "age_days": 11},
        {"id": "demo-ticket-kaveri-1", "customer_id": "demo-customer-kaveri-textiles", "title": "Dashboard export issue", "status": "in_progress", "severity": "high", "age_days": 13},
        {"id": "demo-ticket-lumina-1", "customer_id": "demo-customer-lumina-labs", "title": "Feature onboarding request", "status": "resolved", "severity": "medium", "age_days": 2},
    ]
    for ticket in tickets:
        ticket.update({"demo_dataset": DEMO_DATASET, "seeded_at": seeded_at})

    invoices = [
        {"id": "demo-invoice-aurora-1", "customer_id": "demo-customer-aurora-retail", "status": "overdue", "due_date": (today - timedelta(days=14)).isoformat(), "outstanding_amount": 410_000},
        {"id": "demo-invoice-bharat-1", "customer_id": "demo-customer-bharat-logistics", "status": "overdue", "due_date": (today - timedelta(days=9)).isoformat(), "outstanding_amount": 285_000},
        {"id": "demo-invoice-delta-1", "customer_id": "demo-customer-delta-manufacturing", "status": "delayed", "due_date": (today - timedelta(days=21)).isoformat(), "outstanding_amount": 330_000},
        {"id": "demo-invoice-horizon-1", "customer_id": "demo-customer-horizon-telecom", "status": "open", "due_date": (today - timedelta(days=3)).isoformat(), "outstanding_amount": 520_000},
        {"id": "demo-invoice-flux-1", "customer_id": "demo-customer-flux-finance", "status": "paid", "due_date": (today - timedelta(days=30)).isoformat(), "outstanding_amount": 0},
    ]
    for invoice in invoices:
        invoice.update({"demo_dataset": DEMO_DATASET, "seeded_at": seeded_at})

    opportunities = [
        {"id": "demo-opportunity-aurora-1", "customer_id": "demo-customer-aurora-retail", "opportunity_type": "upsell", "status": "qualified", "estimated_revenue": 650_000, "reason": "High transaction volume needs advanced analytics."},
        {"id": "demo-opportunity-crest-1", "customer_id": "demo-customer-crest-healthcare", "opportunity_type": "cross_sell", "status": "proposal", "estimated_revenue": 480_000, "reason": "Support workflow can benefit from Support Plus."},
        {"id": "demo-opportunity-evergreen-1", "customer_id": "demo-customer-evergreen-education", "opportunity_type": "upsell", "status": "open", "estimated_revenue": 300_000, "reason": "Feature adoption indicates analytics upgrade potential."},
        {"id": "demo-opportunity-greengrid-1", "customer_id": "demo-customer-greengrid-energy", "opportunity_type": "cross_sell", "status": "qualified", "estimated_revenue": 550_000, "reason": "Growing usage supports a Revenue Suite discussion."},
        {"id": "demo-opportunity-jade-1", "customer_id": "demo-customer-jade-consulting", "opportunity_type": "upsell", "status": "open", "estimated_revenue": 410_000, "reason": "Consulting team is consistently at plan capacity."},
        {"id": "demo-opportunity-indigo-closed", "customer_id": "demo-customer-indigo-foods", "opportunity_type": "upsell", "status": "won", "estimated_revenue": 200_000, "reason": "Closed opportunity must not count in open upsell dashboard metric."},
    ]
    for opportunity in opportunities:
        opportunity.update({"demo_dataset": DEMO_DATASET, "seeded_at": seeded_at})

    usages = [
        {"id": f"demo-usage-{customer['id'].removeprefix('demo-customer-')}", "customer_id": customer["id"], "monthly_active_users": max(10, int(customer["health_score"] * 3)), "usage_change_percent": {-1: 0}.get(-1, 0), "demo_dataset": DEMO_DATASET, "seeded_at": seeded_at}
        for customer in customers
    ]
    # Explicit usage changes make risk reasons inspectable in later Customer 360 work.
    usage_changes = [-52, -36, -31, -28, 4, 16, 9, -41, 18, 6, -24, 7]
    for usage, change in zip(usages, usage_changes, strict=True):
        usage["usage_change_percent"] = change

    health_scores = [72, 74, 70, 76, 79, 82]
    health_snapshots = [
        {
            "id": f"demo-health-{_month_label(today, months_back)}",
            "month": _month_label(today, months_back),
            "average_health_score": health_scores[index],
            "owner_user_id": current_user.id,
            "scope": "portfolio",
            "demo_dataset": DEMO_DATASET,
            "seeded_at": seeded_at,
        }
        for index, months_back in enumerate(range(5, -1, -1))
    ]

    portfolio = {
        "id": PORTFOLIO_ID,
        "name": "CustomerGraph Main Dashboard Demo Portfolio",
        "demo_dataset": DEMO_DATASET,
        "seeded_at": seeded_at,
    }

    return {
        "employees": employees,
        "products": products,
        "customers": customers,
        "contracts": contracts,
        "tickets": tickets,
        "invoices": invoices,
        "opportunities": opportunities,
        "usages": usages,
        "health_snapshots": health_snapshots,
        "portfolio": portfolio,
        "customer_product_links": customer_product_links,
        "customer_owner_links": customer_owner_links,
    }


def _create_schema(session: Any) -> None:
    for statement in _SCHEMA_STATEMENTS:
        session.run(statement).consume()


def _reset_demo_data(tx: Any) -> None:
    tx.run(
        "MATCH (node {demo_dataset: $demo_dataset}) DETACH DELETE node",
        demo_dataset=DEMO_DATASET,
    ).consume()


def _upsert_demo_data(tx: Any, payload: dict[str, Any]) -> None:
    """Write all nodes/relationships in a single idempotent transaction."""
    node_batches = (
        ("Customer", "customers"),
        ("Employee", "employees"),
        ("Product", "products"),
        ("Contract", "contracts"),
        ("Ticket", "tickets"),
        ("Invoice", "invoices"),
        ("Opportunity", "opportunities"),
        ("Usage", "usages"),
        ("HealthSnapshot", "health_snapshots"),
    )

    for label, key in node_batches:
        tx.run(
            f"""
            UNWIND $rows AS row
            MERGE (node:{label} {{id: row.id}})
            SET node += row
            """,
            rows=payload[key],
        ).consume()

    tx.run(
        """
        MERGE (portfolio:DashboardPortfolio {id: $portfolio.id})
        SET portfolio += $portfolio
        """,
        portfolio=payload["portfolio"],
    ).consume()

    tx.run(
        """
        UNWIND $rows AS row
        MATCH (customer:Customer {id: row.customer_id})
        MATCH (employee:Employee {id: row.employee_id})
        MERGE (customer)-[:ASSIGNED_TO]->(employee)
        """,
        rows=payload["customer_owner_links"],
    ).consume()

    tx.run(
        """
        UNWIND $rows AS row
        MATCH (customer:Customer {id: row.customer_id})
        MATCH (product:Product {id: row.product_id})
        MERGE (customer)-[:USES_PRODUCT]->(product)
        """,
        rows=payload["customer_product_links"],
    ).consume()

    relationship_batches = (
        ("contracts", "Contract", "HAS_CONTRACT"),
        ("tickets", "Ticket", "HAS_TICKET"),
        ("invoices", "Invoice", "HAS_INVOICE"),
        ("opportunities", "Opportunity", "HAS_OPPORTUNITY"),
        ("usages", "Usage", "HAS_USAGE"),
    )
    for key, target_label, relationship_type in relationship_batches:
        tx.run(
            f"""
            UNWIND $rows AS row
            MATCH (customer:Customer {{id: row.customer_id}})
            MATCH (target:{target_label} {{id: row.id}})
            MERGE (customer)-[:{relationship_type}]->(target)
            """,
            rows=payload[key],
        ).consume()

    tx.run(
        """
        UNWIND $rows AS row
        MATCH (portfolio:DashboardPortfolio {id: $portfolio_id})
        MATCH (snapshot:HealthSnapshot {id: row.id})
        MERGE (portfolio)-[:HAS_HEALTH_SNAPSHOT]->(snapshot)
        """,
        rows=payload["health_snapshots"],
        portfolio_id=PORTFOLIO_ID,
    ).consume()


def seed_dashboard_demo_data(
    current_user: CurrentUserResponse,
    *,
    replace_existing_demo: bool,
) -> GraphDemoSeedResponse:
    """Create/update only the deterministic CustomerGraph dashboard demo graph."""
    payload = _build_demo_payload(current_user)
    driver = get_neo4j_driver()

    try:
        from customergraph.core.config import get_settings

        with driver.session(database=get_settings().neo4j_database) as session:
            _create_schema(session)
            if replace_existing_demo:
                session.execute_write(_reset_demo_data)
            session.execute_write(_upsert_demo_data, payload)
    except Neo4jError:
        logger.exception("demo_graph_seed_failed user_id=%s", current_user.id)
        raise

    logger.info(
        "demo_graph_seed_completed user_id=%s replace_existing_demo=%s customers=%s",
        current_user.id,
        replace_existing_demo,
        len(payload["customers"]),
    )

    return GraphDemoSeedResponse(
        message="CustomerGraph dashboard demo data is ready in Neo4j.",
        dataset=DEMO_DATASET,
        reset_performed=replace_existing_demo,
        customers_created_or_updated=len(payload["customers"]),
        employees_created_or_updated=len(payload["employees"]),
        products_created_or_updated=len(payload["products"]),
        tickets_created_or_updated=len(payload["tickets"]),
        invoices_created_or_updated=len(payload["invoices"]),
        contracts_created_or_updated=len(payload["contracts"]),
        opportunities_created_or_updated=len(payload["opportunities"]),
        usage_records_created_or_updated=len(payload["usages"]),
        health_snapshots_created_or_updated=len(payload["health_snapshots"]),
        seeded_by_user_id=current_user.id,
    )
