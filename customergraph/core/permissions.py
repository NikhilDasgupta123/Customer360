"""Permission matrix for CustomerGraph RBAC."""

PERMISSION_MATRIX = {
    "admin": [
        "auth",
        "customers",
        "dashboard",
        "health_risk",
        "support",
        "sales_renewal",
        "agents",
        "chatbot",
        "admin_audit",
    ],
    "sales_executive": [
        "auth",
        "customers",
        "sales_renewal",
        "agents",
        "chatbot",
    ],
    "account_manager": [
        "auth",
        "customers",
        "dashboard",
        "health_risk",
        "support",
        "sales_renewal",
        "agents",
        "chatbot",
    ],
    "support_agent": [
        "auth",
        "customers",
        "support",
        "health_risk",
        "agents",
        "chatbot",
    ],
}


def get_permissions_for_role(role_key: str) -> list[str]:
    """Return protected module keys allowed for a role."""
    return PERMISSION_MATRIX.get(role_key, [])
