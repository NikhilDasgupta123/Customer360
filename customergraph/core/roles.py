"""Supported CustomerGraph user roles."""

# CustomerGraph now has one administrator role and three operational roles.
# Keep this list as the single source of truth for role configuration used by
# RBAC validation and future user-management screens.
ROLES = [
    {
        "key": "admin",
        "name": "Admin",
        "purpose": "Full platform access, user approval, and audit control.",
    },
    {
        "key": "sales_executive",
        "name": "Sales Executive",
        "purpose": "Manage pipeline, accounts, renewals, and sales opportunities.",
    },
    {
        "key": "account_manager",
        "name": "Account Manager",
        "purpose": "Manage customers, relationship health, and churn prevention.",
    },
    {
        "key": "support_agent",
        "name": "Support Agent",
        "purpose": "Manage customer support, ticket priority, and escalations.",
    },
]


def get_role_keys() -> list[str]:
    """Return valid role keys."""
    return [role["key"] for role in ROLES]
