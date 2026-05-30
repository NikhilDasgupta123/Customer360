"""User roles for Day 1 auth planning."""

ROLES = [
    {
        "key": "admin",
        "name": "Admin",
        "purpose": "System owner and project demo control.",
    },
    {
        "key": "sales_executive",
        "name": "Sales Executive",
        "purpose": "Prepare for calls and identify upsell or renewal actions.",
    },
    {
        "key": "account_manager",
        "name": "Account Manager",
        "purpose": "Protect customer relationship and prevent churn.",
    },
    {
        "key": "support_agent",
        "name": "Support Agent",
        "purpose": "Prioritize important support tickets with full customer context.",
    },
    {
        "key": "customer_success_manager",
        "name": "Customer Success Manager",
        "purpose": "Track customer value, engagement, renewals, and health.",
    },
    {
        "key": "manager",
        "name": "Sales Manager / Leadership",
        "purpose": "View portfolio-level customer risk and business impact.",
    },
]


def get_role_keys() -> list[str]:
    """Return valid role keys."""
    return [role["key"] for role in ROLES]