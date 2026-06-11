"""Auth request and response schemas for Day 4 real auth APIs."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

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


class RequestAccessRequest(BaseModel):
    """Request body for public access request/signup form.

    This does not activate the user immediately. The account is created with
    pending status and can login only after admin approval.
    """

    full_name: str = Field(..., min_length=2, max_length=120, examples=["John Carter"])
    email: str = Field(..., examples=["john@company.com"])
    password: str = Field(..., min_length=8, examples=["ChangeMe123!"])
    confirm_password: str = Field(..., min_length=8, examples=["ChangeMe123!"])
    company_team: str | None = Field(default=None, max_length=120, examples=["Sales Team"])
    role: UserRole = Field(..., examples=[UserRole.ACCOUNT_MANAGER.value])

    @field_validator("full_name")
    @classmethod
    def clean_full_name(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("Full name must contain at least 2 characters")
        return cleaned

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if "@" not in cleaned or "." not in cleaned.split("@")[-1]:
            raise ValueError("Enter a valid work email address")
        return cleaned

    @field_validator("company_team")
    @classmethod
    def clean_company_team(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, value: str | UserRole) -> str | UserRole:
        if isinstance(value, str):
            return value.strip().lower().replace("-", "_")
        return value

    @model_validator(mode="after")
    def validate_request(self) -> "RequestAccessRequest":
        if self.password != self.confirm_password:
            raise ValueError("Password and confirm password do not match")
        if self.role in {UserRole.ADMIN, UserRole.MANAGER}:
            raise ValueError("This role cannot be requested from the signup form")
        return self


class AccessRequestUserResponse(BaseModel):
    """Safe user details returned after access request submission."""

    id: str
    email: str
    full_name: str
    role: UserRole
    status: UserStatus
    company_team: str | None = None


class AccessRequestResponse(BaseModel):
    """Response returned by /auth/request-access."""

    ok: bool
    message: str
    user: AccessRequestUserResponse


class RefreshTokenRequest(BaseModel):
    """Request body for refresh-token exchange."""

    refresh_token: str = Field(..., min_length=20)


class LogoutRequest(BaseModel):
    """Request body for logout/revoke refresh token."""

    refresh_token: str = Field(..., min_length=20)


class TokenResponse(BaseModel):
    """Tokens returned after successful login/refresh."""

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


class AuthSuccessResponse(BaseModel):
    """Response returned by bootstrap-admin and login."""

    ok: bool
    message: str
    user: CurrentUserResponse
    tokens: TokenResponse


class MessageResponse(BaseModel):
    """Generic success message."""

    ok: bool
    message: str


class AuthDesignValidationResponse(BaseModel):
    """Small validation response used by Day 3 setup checks."""

    ok: bool
    errors: list[str]
    user_roles: list[str]
    planned_auth_endpoints: list[str]
