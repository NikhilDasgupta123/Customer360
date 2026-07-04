"""Day 3 auth design service.

This service returns the planned auth architecture in a testable way. It does
not create real users yet. Day 4 will implement the real APIs using this plan.
"""

from __future__ import annotations

from customergraph.core.config import get_settings
from customergraph.core.permissions import PERMISSION_MATRIX
from customergraph.core.roles import get_role_keys
from customergraph.models.user import REFRESH_TOKEN_TABLE_COLUMNS, USER_TABLE_COLUMNS, UserRole


PLANNED_AUTH_ENDPOINTS = [
    "POST /api/v1/auth/bootstrap-admin",
    "POST /api/v1/auth/login",
    "POST /api/v1/auth/request-access",
    "POST /api/v1/auth/refresh",
    "POST /api/v1/auth/logout",
    "GET /api/v1/auth/me",
    "GET /api/v1/admin/access-requests?status=pending",
    "PATCH /api/v1/admin/access-requests/{user_id}/approve",
    "PATCH /api/v1/admin/access-requests/{user_id}/reject",
    "GET /api/v1/admin/users",
    "GET /api/v1/admin/users/summary",
    "POST /api/v1/admin/users",
    "PATCH /api/v1/admin/users/{user_id}",
    "DELETE /api/v1/admin/users/{user_id}",
]


PASSWORD_HASHING_PLAN = {
    "algorithm_family": "bcrypt",
    "library_to_add_on_day4": "passlib[bcrypt]",
    "storage_rule": "Never store plain password. Store only hashed_password.",
    "verification_rule": "When user logs in, verify raw password against stored hashed_password.",
    "minimum_password_length": 8,
    "production_note": "Use a strong JWT secret and rotate secrets carefully in production.",
}


LOGIN_FLOW_STEPS = [
    {"step": 1, "name": "User submits email and password", "api": "POST /auth/login"},
    {"step": 2, "name": "Backend finds user by email", "failure": "Return 401 if not found"},
    {"step": 3, "name": "Backend loads the stored role", "purpose": "The browser cannot choose a role at login"},
    {"step": 4, "name": "Backend checks user status", "failure": "Block pending or disabled users"},
    {"step": 5, "name": "Backend verifies password hash", "failure": "Return 401 if password is wrong"},
    {"step": 6, "name": "Backend creates access token", "purpose": "Short-lived API access"},
    {"step": 7, "name": "Backend creates refresh token", "purpose": "Get a new access token later"},
    {"step": 8, "name": "Frontend stores token safely", "purpose": "Call protected APIs"},
]


DATABASE_DESIGN = {
    "users_table": {
        "name": "users",
        "primary_key": "id",
        "unique_indexes": ["email"],
        "columns": USER_TABLE_COLUMNS,
    },
    "refresh_tokens_table": {
        "name": "refresh_tokens",
        "primary_key": "id",
        "unique_indexes": ["token_hash"],
        "foreign_keys": ["user_id -> users.id"],
        "columns": REFRESH_TOKEN_TABLE_COLUMNS,
    },
}


def get_token_design() -> dict:
    """Return safe JWT token design details."""
    settings = get_settings()
    return {
        "access_token": {
            "type": "JWT",
            "expires_in_minutes": settings.access_token_expire_minutes,
            "contains": ["sub/user_id", "email", "role", "token_type", "exp"],
            "used_for": "Calling protected backend APIs",
        },
        "refresh_token": {
            "type": "Opaque random token or JWT refresh token",
            "expires_in_days": settings.refresh_token_expire_days,
            "stored_as": "hash in refresh_tokens table",
            "used_for": "Getting a new access token without logging in again",
        },
        "jwt_algorithm": settings.jwt_algorithm,
        "secret_storage_rule": "Secret comes from .env. Never return it in API responses.",
    }


def get_user_model_design() -> dict:
    """Return planned user model and role mapping."""
    return {
        "roles": [role.value for role in UserRole],
        "status_values": ["active", "disabled", "pending"],
        "permission_matrix": PERMISSION_MATRIX,
        "database_design": DATABASE_DESIGN,
    }


def validate_auth_design() -> dict:
    """Validate that auth design roles match the frozen Day 1 RBAC roles."""
    errors: list[str] = []
    day1_roles = set(get_role_keys())
    day3_roles = {role.value for role in UserRole}

    missing_from_day3 = sorted(day1_roles - day3_roles)
    extra_in_day3 = sorted(day3_roles - day1_roles)

    if missing_from_day3:
        errors.append(f"Roles missing from Day 3 UserRole enum: {missing_from_day3}")
    if extra_in_day3:
        errors.append(f"Extra roles in Day 3 UserRole enum: {extra_in_day3}")

    required_endpoints = {"POST /api/v1/auth/login", "GET /api/v1/auth/me"}
    planned = set(PLANNED_AUTH_ENDPOINTS)
    missing_endpoints = sorted(required_endpoints - planned)
    if missing_endpoints:
        errors.append(f"Required auth endpoints missing from plan: {missing_endpoints}")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "user_roles": sorted(day3_roles),
        "planned_auth_endpoints": PLANNED_AUTH_ENDPOINTS,
    }


def get_full_auth_design() -> dict:
    """Return the complete Day 3 auth design."""
    validation = validate_auth_design()
    return {
        "ok": validation["ok"],
        "day": 3,
        "title": "Auth Design",
        "message": "User model, password hashing plan, JWT token plan, refresh token flow, and auth endpoints are designed.",
        "planned_auth_endpoints": PLANNED_AUTH_ENDPOINTS,
        "user_model_design": get_user_model_design(),
        "password_hashing_plan": PASSWORD_HASHING_PLAN,
        "token_design": get_token_design(),
        "login_flow_steps": LOGIN_FLOW_STEPS,
        "validation": validation,
        "next_day": "Day 4 will implement real auth APIs using this design.",
    }
