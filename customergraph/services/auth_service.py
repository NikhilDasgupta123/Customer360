"""Day 4 real authentication service.

Implements first admin bootstrap, login, JWT access token creation, refresh-token
rotation, logout/revoke, and current-user lookup using local SQLite.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status

from customergraph.core.config import get_settings
from customergraph.core.permissions import get_permissions_for_role
from customergraph.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    get_password_backend_name,
    hash_password,
    hash_token,
    parse_utc,
    utc_iso,
    utc_now,
    verify_password,
)
from customergraph.db.sqlite import get_connection, get_sqlite_path, init_auth_db
from customergraph.models.user import UserRole, UserStatus
from customergraph.schemas.auth import (
    AuthSuccessResponse,
    CurrentUserResponse,
    FirstAdminCreateRequest,
    LoginRequest,
    TokenResponse,
)


AUTH_ENDPOINTS_DAY4 = [
    "POST /api/v1/auth/bootstrap-admin",
    "POST /api/v1/auth/login",
    "POST /api/v1/auth/refresh",
    "POST /api/v1/auth/logout",
    "GET /api/v1/auth/me",
]


def normalize_email(email: str) -> str:
    """Normalize email for unique login lookup."""
    return email.strip().lower()


def _row_to_user(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _user_response(user: dict[str, Any]) -> CurrentUserResponse:
    role = UserRole(user["role"])
    status_value = UserStatus(user["status"])
    return CurrentUserResponse(
        id=user["id"],
        email=user["email"],
        full_name=user["full_name"],
        role=role,
        status=status_value,
        allowed_modules=get_permissions_for_role(role.value),
    )


def _create_refresh_token_for_user(conn: sqlite3.Connection, user_id: str) -> str:
    settings = get_settings()
    refresh_token = create_refresh_token()
    now = utc_now()
    expires_at = now + timedelta(days=settings.refresh_token_expire_days)
    conn.execute(
        """
        INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, revoked_at, created_at)
        VALUES (?, ?, ?, ?, NULL, ?)
        """,
        (
            str(uuid.uuid4()),
            user_id,
            hash_token(refresh_token),
            expires_at.isoformat(),
            now.isoformat(),
        ),
    )
    return refresh_token


def _create_tokens(conn: sqlite3.Connection, user: dict[str, Any]) -> TokenResponse:
    settings = get_settings()
    access_token = create_access_token(user_id=user["id"], email=user["email"], role=user["role"])
    refresh_token = _create_refresh_token_for_user(conn, user["id"])
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in_minutes=settings.access_token_expire_minutes,
    )


def count_users() -> int:
    """Return number of auth users."""
    init_auth_db()
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()
    return int(row["total"] if row else 0)


def bootstrap_first_admin(payload: FirstAdminCreateRequest) -> AuthSuccessResponse:
    """Create the first Admin user and return login tokens.

    This endpoint is allowed only when the users table is empty.
    """
    init_auth_db()
    email = normalize_email(payload.email)

    with get_connection() as conn:
        existing_count = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
        if existing_count > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="First admin already exists. Use /auth/login instead.",
            )

        now = utc_iso()
        user_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO users (
                id, email, full_name, hashed_password, role, status,
                is_first_admin, created_at, updated_at, last_login_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                user_id,
                email,
                payload.full_name.strip(),
                hash_password(payload.password),
                UserRole.ADMIN.value,
                UserStatus.ACTIVE.value,
                now,
                now,
                now,
            ),
        )
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        if user is None:
            raise HTTPException(status_code=500, detail="Admin user could not be created")
        tokens = _create_tokens(conn, user)
        conn.commit()

    return AuthSuccessResponse(
        ok=True,
        message="First admin created successfully",
        user=_user_response(user),
        tokens=tokens,
    )


def authenticate_user(payload: LoginRequest) -> AuthSuccessResponse:
    """Verify email/password and return auth tokens."""
    init_auth_db()
    email = normalize_email(payload.email)

    with get_connection() as conn:
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())
        if user is None or not verify_password(payload.password, user["hashed_password"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if user["status"] != UserStatus.ACTIVE.value:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not active")

        now = utc_iso()
        conn.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (now, now, user["id"]))
        refreshed_user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone())
        if refreshed_user is None:
            raise HTTPException(status_code=500, detail="User could not be loaded")
        tokens = _create_tokens(conn, refreshed_user)
        conn.commit()

    return AuthSuccessResponse(
        ok=True,
        message="Login successful",
        user=_user_response(refreshed_user),
        tokens=tokens,
    )


def refresh_tokens(refresh_token: str) -> TokenResponse:
    """Rotate refresh token and return a new access/refresh token pair."""
    init_auth_db()
    token_digest = hash_token(refresh_token)

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT rt.*, u.email, u.role, u.status
            FROM refresh_tokens rt
            JOIN users u ON u.id = rt.user_id
            WHERE rt.token_hash = ?
            """,
            (token_digest,),
        ).fetchone()

        if row is None or row["revoked_at"] is not None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
        if parse_utc(row["expires_at"]) <= utc_now():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")
        if row["status"] != UserStatus.ACTIVE.value:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not active")

        now = utc_iso()
        conn.execute("UPDATE refresh_tokens SET revoked_at = ? WHERE id = ?", (now, row["id"]))
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone())
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        tokens = _create_tokens(conn, user)
        conn.commit()

    return tokens


def logout(refresh_token: str) -> dict[str, Any]:
    """Revoke a refresh token. Access token expires naturally."""
    init_auth_db()
    token_digest = hash_token(refresh_token)
    now = utc_iso()

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = ?
            WHERE token_hash = ? AND revoked_at IS NULL
            """,
            (now, token_digest),
        )
        conn.commit()

    return {"ok": True, "message": "Logout successful. Refresh token revoked if it existed."}


def get_current_user_from_access_token(access_token: str) -> CurrentUserResponse:
    """Return current user from a verified access token."""
    init_auth_db()
    payload = decode_access_token(access_token)
    user_id = payload["sub"]

    with get_connection() as conn:
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user["status"] != UserStatus.ACTIVE.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not active")
    return _user_response(user)


def validate_auth_runtime() -> dict[str, Any]:
    """Return Day 4 auth runtime health information without secrets."""
    db_status = init_auth_db()
    user_count = count_users()
    return {
        "ok": True,
        "day": 4,
        "database_path": str(get_sqlite_path()),
        "tables": db_status["tables"],
        "user_count": user_count,
        "admin_bootstrap_available": user_count == 0,
        "password_backend": get_password_backend_name(),
        "auth_endpoints": AUTH_ENDPOINTS_DAY4,
    }
