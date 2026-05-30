"""Day 1 MVP scope for CustomerGraph AI.

Day 1 does not implement full authentication yet.
It freezes what authentication and access control must protect from Day 2 onward.
"""

MVP_SCOPE = {
    "project_name": "CustomerGraph AI",
    "project_type": "Customer 360 Agentic AI System",
    "development_rule": "Backend first, Auth first, React later",
    "day_1_goal": "Freeze MVP scope, roles, permissions, protected modules, and access rules.",
    "backend_stack": [
        "FastAPI",
        "Python",
        "JWT Auth",
        "RBAC",
        "Neo4j",
        "Agentic AI backend",
    ],
    "frontend_stack_later": [
        "React",
        "React Router",
        "Axios or Fetch",
        "Protected dashboard UI",
    ],
    "mvp_features": [
        "Secure login and protected APIs",
        "Customer 360 profile",
        "Customer health score",
        "Risk detection",
        "Next best action",
        "Support priority",
        "Upsell and renewal intelligence",
        "Manager dashboard",
        "Graph chatbot",
        "Audit logs",
    ],
}