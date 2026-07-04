"""Authentication endpoints for CustomerGraph AI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from customergraph.api.dependencies import CurrentUser, require_initial_admin_bootstrap_key
from customergraph.schemas.auth import (
    AccessRequestResponse,
    AuthSuccessResponse,
    CurrentUserResponse,
    FirstAdminCreateRequest,
    LoginRequest,
    MessageResponse,
    RefreshTokenRequest,
    RequestAccessRequest,
    TokenResponse,
)
from customergraph.services.auth_service import (
    authenticate_user,
    bootstrap_first_admin,
    logout,
    refresh_tokens,
    request_access,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/bootstrap-admin",
    response_model=AuthSuccessResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_initial_admin_bootstrap_key)],
)
def create_first_admin(payload: FirstAdminCreateRequest) -> AuthSuccessResponse:
    """Create the first Admin user.

    This is a one-time onboarding exception: no bearer token exists yet, so it
    requires the ``X-Bootstrap-Key`` request header. As soon as the first user
    exists, the service rejects every additional bootstrap attempt.
    """
    return bootstrap_first_admin(payload)


@router.post("/login", response_model=AuthSuccessResponse)
def login(payload: LoginRequest) -> AuthSuccessResponse:
    """Login using email and password to receive access and refresh tokens."""
    return authenticate_user(payload)


@router.post("/request-access", response_model=AccessRequestResponse, status_code=status.HTTP_201_CREATED)
def submit_access_request(payload: RequestAccessRequest) -> AccessRequestResponse:
    """Submit a user access request. The new account remains pending."""
    return request_access(payload)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshTokenRequest) -> TokenResponse:
    """Rotate the refresh token and issue a new token pair."""
    return refresh_tokens(payload.refresh_token)


@router.post("/logout", response_model=MessageResponse)
def logout_user(current_user: CurrentUser) -> dict:
    """Log out using only the Bearer access token.

    No request body is required. This ends every active session for the
    authenticated user and makes the current access token unusable immediately.
    """
    return logout(current_user.id)


@router.get("/me", response_model=CurrentUserResponse)
def get_me(current_user: CurrentUser) -> CurrentUserResponse:
    """Return the active database user for a valid bearer access token."""
    return current_user
