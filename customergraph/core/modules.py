"""Protected backend modules decided on Day 1."""

PROTECTED_MODULES = [
    {
        "key": "auth",
        "name": "Authentication",
        "description": "Login, token refresh, logout, current user profile.",
    },
    {
        "key": "customers",
        "name": "Customers",
        "description": "Customer list, customer profile, customer graph summary.",
    },
    {
        "key": "dashboard",
        "name": "Dashboard",
        "description": "Manager summary metrics and revenue/risk overview.",
    },
    {
        "key": "health_risk",
        "name": "Health and Risk",
        "description": "Health score, risk reasons, churn/support/payment risk.",
    },
    {
        "key": "support",
        "name": "Support",
        "description": "Tickets, support priority, escalation reason.",
    },
    {
        "key": "sales_renewal",
        "name": "Sales and Renewal",
        "description": "Sales opportunity, renewal follow-up, upsell logic.",
    },
    {
        "key": "agents",
        "name": "AI Agents",
        "description": "Customer summary, risk, support, sales, renewal, next-best-action agents.",
    },
    {
        "key": "chatbot",
        "name": "Graph Chatbot",
        "description": "Natural language questions over safe graph-backed answers.",
    },
    {
        "key": "admin_audit",
        "name": "Admin and Audit",
        "description": "User management, roles, permissions, audit logs.",
    },
]


def get_module_keys() -> list[str]:
    """Return valid module keys."""
    return [module["key"] for module in PROTECTED_MODULES]