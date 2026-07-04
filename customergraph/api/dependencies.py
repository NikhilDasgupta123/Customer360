"""Reusable authentication, RBAC, and request-scope dependencies.

All business routers must be mounted through ``protected_api_router`` in
customergraph.api.router. This makes a verified Bearer access token mandatory
before a business endpoint executes.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import Header, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from customergraph.core.config import get_settings
from customergraph.core.permissions import get_permissions_for_role
from customergraph.models.user import UserRole
from customergraph.schemas.auth import CurrentUserResponse
from customergraph.services.auth_service import get_current_user_from_access_token


# This is intentionally the only Swagger/OpenAPI authorization scheme.
# Swagger's Authorize dialog shows one field where the user pastes only the
# access_token. Swagger adds the "Bearer " prefix automatically.
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Paste only the access_token returned by /auth/login. Do not type the word Bearer.",
)


def _credentials_error(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> CurrentUserResponse:
    """Return the active database user represented by a verified JWT.

    The token supplies only the user identifier. Role, status, and permitted
    modules are always reloaded from SQLite, so a disabled user, changed role,
    or logged-out user is enforced immediately without waiting for expiry.
    """
    if credentials is None:
        raise _credentials_error("Missing Authorization header. Use: Bearer <access_token>")
    if credentials.scheme.lower() != "bearer" or not credentials.credentials.strip():
        raise _credentials_error("Invalid Authorization header. Use: Bearer <access_token>")

    current_user = get_current_user_from_access_token(credentials.credentials.strip())
    request.state.current_user_id = current_user.id
    request.state.current_user_role = current_user.role.value
    return current_user


CurrentUser = Annotated[CurrentUserResponse, Security(get_current_user)]


def require_roles(*allowed_roles: UserRole | str) -> Callable[..., CurrentUserResponse]:
    """Create a dependency that allows only the listed roles."""
    allowed_role_keys = {
        role.value if isinstance(role, UserRole) else str(role).strip().lower()
        for role in allowed_roles
    }

    def dependency(current_user: CurrentUser) -> CurrentUserResponse:
        if current_user.role.value not in allowed_role_keys:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your role is not allowed to access this endpoint.",
            )
        return current_user

    return dependency


def require_module(module_key: str) -> Callable[..., CurrentUserResponse]:
    """Create a dependency for a protected CustomerGraph product module.

    Example:
        @router.get("/customers", dependencies=[Depends(require_module("customers"))])
    """
    normalized_module_key = module_key.strip().lower().replace("-", "_").replace(" ", "_")

    def dependency(current_user: CurrentUser) -> CurrentUserResponse:
        allowed_modules = set(get_permissions_for_role(current_user.role.value))
        if normalized_module_key not in allowed_modules:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your role does not have access to the '{normalized_module_key}' module.",
            )
        return current_user

    return dependency


def require_initial_admin_bootstrap_key(
    bootstrap_key: Annotated[
        str | None,
        Header(
            alias="X-Bootstrap-Key",
            description="One-time secret for creating the first admin. This is an endpoint header, not a Swagger authorization method.",
        ),
    ] = None,
) -> None:
    """Protect the one-time first-admin endpoint before any bearer token exists.

    ``Header`` is used deliberately instead of ``APIKeyHeader`` so the Swagger
    "Available authorizations" popup contains only BearerAuth.
    """
    configured_key = get_settings().bootstrap_admin_key
    if not configured_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Initial admin bootstrap is disabled. Configure BOOTSTRAP_ADMIN_KEY first.",
        )
    if bootstrap_key is None or not hmac.compare_digest(bootstrap_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Bootstrap-Key.",
        )
