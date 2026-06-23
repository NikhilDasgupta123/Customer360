"""Authentication endpoints for CustomerGraph AI."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from customergraph.schemas.auth import (
    AccessRequestResponse,
    AuthSuccessResponse,
    CurrentUserResponse,
    FirstAdminCreateRequest,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshTokenRequest,
    RequestAccessRequest,
    TokenResponse,
)
from customergraph.services.auth_service import (
    authenticate_user,
    bootstrap_first_admin,
    get_current_user_from_access_token,
    logout,
    refresh_tokens,
    request_access,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _extract_bearer_token(authorization: str | None) -> str:
    """Extract the access token from an Authorization bearer header."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header. Use: Bearer <access_token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


@router.post("/bootstrap-admin", response_model=AuthSuccessResponse, status_code=status.HTTP_201_CREATED)
def create_first_admin(payload: FirstAdminCreateRequest) -> AuthSuccessResponse:
    """Create the first Admin user. Works only when no users exist."""
    return bootstrap_first_admin(payload)


@router.post("/login", response_model=AuthSuccessResponse)
def login(payload: LoginRequest) -> AuthSuccessResponse:
    """Login using email and password to receive access and refresh tokens."""
    return authenticate_user(payload)


@router.post("/request-access", response_model=AccessRequestResponse, status_code=status.HTTP_201_CREATED)
def submit_access_request(payload: RequestAccessRequest) -> AccessRequestResponse:
    """Submit a user access request. The user is initially pending."""
    return request_access(payload)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshTokenRequest) -> TokenResponse:
    """Rotate the refresh token and issue a new token pair."""
    return refresh_tokens(payload.refresh_token)


@router.post("/logout", response_model=MessageResponse)
def logout_user(payload: LogoutRequest) -> dict:
    """Revoke a refresh token. The access token expires naturally."""
    return logout(payload.refresh_token)


@router.get("/me", response_model=CurrentUserResponse)
def get_me(authorization: Annotated[str | None, Header(alias="Authorization")] = None) -> CurrentUserResponse:
    """Return the current user for a valid bearer access token."""
    token = _extract_bearer_token(authorization)
    return get_current_user_from_access_token(token)
