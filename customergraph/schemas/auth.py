"""Day 3 Pydantic schemas for upcoming auth APIs.

Day 3 defines the request/response contracts only. Day 4 will attach these
schemas to real login, token creation, refresh, and logout endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from customergraph.models.user import UserRole, UserStatus


class FirstAdminCreateRequest(BaseModel):
    """Request body for the first admin bootstrap endpoint."""

    email: str = Field(..., examples=["admin@customergraph.local"])
    full_name: str = Field(..., min_length=2, examples=["Admin User"])
    password: str = Field(..., min_length=8, examples=["ChangeMe123!"])


class LoginRequest(BaseModel):
    """Request body for login."""

    email: str = Field(..., examples=["admin@customergraph.local"])
    password: str = Field(..., min_length=1, examples=["ChangeMe123!"])


class RefreshTokenRequest(BaseModel):
    """Request body for refresh-token exchange."""

    refresh_token: str = Field(..., min_length=20)


class LogoutRequest(BaseModel):
    """Request body for logout/revoke refresh token."""

    refresh_token: str = Field(..., min_length=20)


class TokenResponse(BaseModel):
    """Response body returned after successful login/refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class CurrentUserResponse(BaseModel):
    """Safe current-user response for /auth/me."""

    id: str
    email: str
    full_name: str
    role: UserRole
    status: UserStatus
    allowed_modules: list[str]


class AuthDesignValidationResponse(BaseModel):
    """Small validation response used by Day 3 setup checks."""

    ok: bool
    errors: list[str]
    user_roles: list[str]
    planned_auth_endpoints: list[str]
