"""User domain models and enum values for CustomerGraph auth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class UserRole(str, Enum):
    """The four supported CustomerGraph user roles."""

    ADMIN = "admin"
    SALES_EXECUTIVE = "sales_executive"
    ACCOUNT_MANAGER = "account_manager"
    SUPPORT_AGENT = "support_agent"


class UserStatus(str, Enum):
    """User lifecycle state."""

    ACTIVE = "active"
    DISABLED = "disabled"
    PENDING = "pending"


class TokenType(str, Enum):
    """JWT token purpose."""

    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True)
class UserRecord:
    """Runtime user record loaded from SQLite."""

    id: str
    email: str
    full_name: str
    hashed_password: str
    role: UserRole
    status: UserStatus
    is_first_admin: bool
    created_at: str
    updated_at: str
    last_login_at: Optional[str] = None


@dataclass(frozen=True)
class RefreshTokenRecord:
    """Runtime refresh-token record loaded from SQLite."""

    id: str
    user_id: str
    token_hash: str
    expires_at: str
    revoked_at: Optional[str]
    created_at: str


@dataclass(frozen=True)
class UserModelDesign:
    """Planned user record shape for database-backed auth."""

    id: str
    email: str
    full_name: str
    hashed_password: str
    role: UserRole
    status: UserStatus
    is_first_admin: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: Optional[datetime] = None


@dataclass(frozen=True)
class RefreshTokenModelDesign:
    """Planned refresh token table shape."""

    id: str
    user_id: str
    token_hash: str
    expires_at: datetime
    revoked_at: Optional[datetime]
    created_at: datetime


USER_TABLE_COLUMNS = [
    {"name": "id", "type": "string/uuid", "required": True, "unique": True},
    {"name": "email", "type": "string", "required": True, "unique": True},
    {"name": "full_name", "type": "string", "required": True, "unique": False},
    {"name": "hashed_password", "type": "string", "required": True, "unique": False},
    {"name": "role", "type": "enum", "required": True, "unique": False},
    {"name": "status", "type": "enum", "required": True, "unique": False},
    {"name": "is_first_admin", "type": "boolean", "required": True, "unique": False},
    {"name": "created_at", "type": "datetime", "required": True, "unique": False},
    {"name": "updated_at", "type": "datetime", "required": True, "unique": False},
    {"name": "last_login_at", "type": "datetime/null", "required": False, "unique": False},
]


REFRESH_TOKEN_TABLE_COLUMNS = [
    {"name": "id", "type": "string/uuid", "required": True, "unique": True},
    {"name": "user_id", "type": "string/uuid", "required": True, "unique": False},
    {"name": "token_hash", "type": "string", "required": True, "unique": True},
    {"name": "expires_at", "type": "datetime", "required": True, "unique": False},
    {"name": "revoked_at", "type": "datetime/null", "required": False, "unique": False},
    {"name": "created_at", "type": "datetime", "required": True, "unique": False},
]
