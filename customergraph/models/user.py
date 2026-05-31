"""Day 3 auth domain model design.

These are lightweight Python models for planning. Day 4/Day 5 can connect the
same fields to a real database table and protected FastAPI dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class UserRole(str, Enum):
    """Valid CustomerGraph AI user roles."""

    ADMIN = "admin"
    SALES_EXECUTIVE = "sales_executive"
    ACCOUNT_MANAGER = "account_manager"
    SUPPORT_AGENT = "support_agent"
    CUSTOMER_SUCCESS_MANAGER = "customer_success_manager"
    MANAGER = "manager"


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
