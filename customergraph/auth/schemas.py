"""Authentication and administrator user-management schemas for CustomerGraph AI."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from customergraph.models.user import UserRole, UserStatus


class FirstAdminCreateRequest(BaseModel):
    """Request body for the first admin bootstrap endpoint."""

    email: str = Field(..., description="Administrator work email.")
    full_name: str = Field(..., min_length=2, description="Administrator display name.")
    password: str = Field(
        ...,
        min_length=8,
        description="Administrator password. Enter it manually; no demo password is exposed in Swagger.",
        json_schema_extra={"format": "password", "writeOnly": True},
    )


class LoginRequest(BaseModel):
    """Login request. The database, not the browser, decides the user's role."""

    email: str = Field(..., description="Registered work email.")
    password: str = Field(
        ...,
        min_length=1,
        description="Account password.",
        json_schema_extra={"format": "password", "writeOnly": True},
    )


class RequestAccessRequest(BaseModel):
    """Public operational-role request. Created accounts stay pending until an Admin approves them."""

    full_name: str = Field(..., min_length=2, max_length=120, description="Full name.")
    email: str = Field(..., description="Work email address.")
    password: str = Field(
        ...,
        min_length=8,
        description="New password.",
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    confirm_password: str = Field(
        ...,
        min_length=8,
        description="Repeat the new password.",
        json_schema_extra={"format": "password", "writeOnly": True},
    )
    company_team: str | None = Field(default=None, max_length=120, description="Optional company or team name.")
    role: UserRole = Field(..., description="Requested product role.")

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
        if self.role is UserRole.ADMIN:
            raise ValueError("Admin role cannot be requested from the signup form")
        return self


class AccessRequestUserResponse(BaseModel):
    """Safe user details returned for a submitted or reviewed access request."""

    id: str
    email: str
    full_name: str
    role: UserRole
    status: UserStatus
    company_team: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AccessRequestResponse(BaseModel):
    ok: bool
    message: str
    user: AccessRequestUserResponse


class AccessRequestListResponse(BaseModel):
    ok: bool = True
    requests: list[AccessRequestUserResponse]


class AccessRequestActionResponse(BaseModel):
    ok: bool
    message: str
    user: AccessRequestUserResponse


class AdminUserResponse(BaseModel):
    """User record safe for the Admin user-management screen."""

    id: str
    email: str
    full_name: str
    role: UserRole
    status: UserStatus
    company_team: str | None = None
    is_first_admin: bool = False
    created_at: str | None = None
    updated_at: str | None = None
    last_login_at: str | None = None


class AdminUserListResponse(BaseModel):
    ok: bool = True
    users: list[AdminUserResponse]
    total: int


class AdminUserSummaryResponse(BaseModel):
    ok: bool = True
    total_users: int
    active_users: int
    pending_users: int
    disabled_users: int
    admin_users: int


class AdminCreateUserRequest(BaseModel):
    """Admin-only creation request used by the React Add User modal.

    Email delivery is not implemented in the current project, so an Admin must
    set a temporary password and communicate it through their approved channel.
    """

    full_name: str = Field(..., min_length=2, max_length=120)
    email: str = Field(...)
    password: str = Field(..., min_length=8, json_schema_extra={"format": "password", "writeOnly": True})
    role: UserRole
    status: UserStatus = UserStatus.ACTIVE
    company_team: str | None = Field(default=None, max_length=120)

    @field_validator("full_name")
    @classmethod
    def clean_admin_full_name(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("Full name must contain at least 2 characters")
        return cleaned

    @field_validator("email")
    @classmethod
    def clean_admin_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if "@" not in cleaned or "." not in cleaned.split("@")[-1]:
            raise ValueError("Enter a valid work email address")
        return cleaned

    @field_validator("company_team")
    @classmethod
    def clean_admin_team(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None

    @field_validator("role", mode="before")
    @classmethod
    def normalize_admin_role(cls, value: str | UserRole) -> str | UserRole:
        return value.strip().lower().replace("-", "_") if isinstance(value, str) else value


class AdminUpdateUserRequest(BaseModel):
    """Editable profile fields from the Admin screen. Email and password are intentionally excluded."""

    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    role: UserRole | None = None
    status: UserStatus | None = None
    company_team: str | None = Field(default=None, max_length=120)

    @field_validator("full_name")
    @classmethod
    def clean_update_full_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("company_team")
    @classmethod
    def clean_update_team(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None

    @field_validator("role", mode="before")
    @classmethod
    def normalize_update_role(cls, value: str | UserRole | None) -> str | UserRole | None:
        return value.strip().lower().replace("-", "_") if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_one_change(self) -> "AdminUpdateUserRequest":
        if self.full_name is None and self.role is None and self.status is None and self.company_team is None:
            raise ValueError("Provide at least one field to update")
        return self


class AdminUserActionResponse(BaseModel):
    ok: bool
    message: str
    user: AdminUserResponse | None = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(
        ...,
        min_length=20,
        description="Refresh token returned by login or a previous refresh call.",
        json_schema_extra={"writeOnly": True},
    )


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class CurrentUserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: UserRole
    status: UserStatus
    allowed_modules: list[str]


class AuthSuccessResponse(BaseModel):
    ok: bool
    message: str
    user: CurrentUserResponse
    tokens: TokenResponse


class MessageResponse(BaseModel):
    ok: bool
    message: str


class AuthDesignValidationResponse(BaseModel):
    ok: bool
    errors: list[str]
    user_roles: list[str]
    planned_auth_endpoints: list[str]
