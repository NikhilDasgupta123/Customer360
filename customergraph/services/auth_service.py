"""CustomerGraph authentication service.

Implements first-admin bootstrap, login, JWT access token creation, refresh-token
rotation, one-click logout, and current-user lookup using local SQLite.
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
    AccessRequestResponse,
    AccessRequestUserResponse,
    AuthSuccessResponse,
    CurrentUserResponse,
    FirstAdminCreateRequest,
    LoginRequest,
    RequestAccessRequest,
    TokenResponse,
)


AUTH_ENDPOINTS = [
    "POST /api/v1/auth/bootstrap-admin",
    "POST /api/v1/auth/login",
    "POST /api/v1/auth/request-access",
    "POST /api/v1/auth/refresh",
    "POST /api/v1/auth/logout",
    "GET /api/v1/auth/me",
]

ADMIN_ACCESS_REQUEST_ENDPOINTS = [
    "GET /api/v1/admin/access-requests?status=pending",
    "PATCH /api/v1/admin/access-requests/{user_id}/approve",
    "PATCH /api/v1/admin/access-requests/{user_id}/reject",
]


def normalize_email(email: str) -> str:
    """Normalize email for unique login lookup."""
    return email.strip().lower()


def _row_to_user(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _supported_user_role(user: dict[str, Any]) -> UserRole:
    """Return a valid role or block legacy roles without issuing a token.

    Previous development builds exposed two extra roles. Existing records with
    those retired values are not silently remapped or elevated. They must be
    updated by an administrator before they can sign in again.
    """
    try:
        return UserRole(user["role"])
    except (KeyError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has a retired role. Ask an administrator to assign one of the four supported roles.",
        ) from error


def _user_response(user: dict[str, Any]) -> CurrentUserResponse:
    role = _supported_user_role(user)
    status_value = UserStatus(user["status"])
    return CurrentUserResponse(
        id=user["id"],
        email=user["email"],
        full_name=user["full_name"],
        role=role,
        status=status_value,
        allowed_modules=get_permissions_for_role(role.value),
    )


def _access_request_user_response(user: dict[str, Any]) -> AccessRequestUserResponse:
    """Return safe details for a submitted or reviewed access request."""
    return AccessRequestUserResponse(
        id=user["id"],
        email=user["email"],
        full_name=user["full_name"],
        role=_supported_user_role(user),
        status=UserStatus(user["status"]),
        company_team=user.get("company_team"),
        created_at=user.get("created_at"),
        updated_at=user.get("updated_at"),
    )


# Admin creation is restricted to the guarded bootstrap endpoint. The three
# operational roles can enter the pending approval queue.
ALLOWED_SELF_SERVICE_ROLES = {
    UserRole.SALES_EXECUTIVE.value,
    UserRole.ACCOUNT_MANAGER.value,
    UserRole.SUPPORT_AGENT.value,
}


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
    role = _supported_user_role(user)
    access_token = create_access_token(
        user_id=user["id"],
        email=user["email"],
        role=role.value,
        token_version=int(user.get("token_version", 0)),
    )
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
                is_first_admin, token_version, created_at, updated_at, last_login_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
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


def request_access(payload: RequestAccessRequest) -> AccessRequestResponse:
    """Create a pending user from the public Request Access form.

    The user cannot login until an admin changes the status from pending to
    active. The public flow never creates an Admin account.
    """
    init_auth_db()
    email = normalize_email(payload.email)
    role = payload.role.value

    if role not in ALLOWED_SELF_SERVICE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This role cannot be requested from the signup form",
        )

    with get_connection() as conn:
        existing_user = conn.execute("SELECT id, status FROM users WHERE email = ?", (email,)).fetchone()
        if existing_user is not None:
            existing_status = existing_user["status"]
            if existing_status == UserStatus.PENDING.value:
                detail = "Access request already exists. Please wait for admin approval."
            elif existing_status == UserStatus.ACTIVE.value:
                detail = "Account already exists. Please login."
            else:
                detail = "Account exists but is not active. Contact your admin."
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

        now = utc_iso()
        user_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO users (
                id, email, full_name, hashed_password, role, status, company_team,
                is_first_admin, token_version, created_at, updated_at, last_login_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, NULL)
            """,
            (
                user_id,
                email,
                payload.full_name.strip(),
                hash_password(payload.password),
                role,
                UserStatus.PENDING.value,
                payload.company_team,
                now,
                now,
            ),
        )
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        if user is None:
            raise HTTPException(status_code=500, detail="Access request could not be created")
        conn.commit()

    return AccessRequestResponse(
        ok=True,
        message="Access request submitted. Please wait for admin approval.",
        user=_access_request_user_response(user),
    )


def authenticate_user(payload: LoginRequest) -> AuthSuccessResponse:
    """Verify email/password and return tokens for the role stored in SQLite."""
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

        # Do not trust a client-provided role. The role is loaded from the
        # database and validated before the JWT is issued.
        _supported_user_role(user)

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
        _supported_user_role(user)
        tokens = _create_tokens(conn, user)
        conn.commit()

    return tokens


def logout(current_user_id: str) -> dict[str, Any]:
    """Log out the authenticated user without accepting a request body.

    The call revokes every refresh token owned by the current user and increments
    ``token_version``. All existing access tokens immediately fail at the next
    protected API call, including the token used to make this logout request.
    """
    init_auth_db()
    now = utc_iso()

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = ?
            WHERE user_id = ? AND revoked_at IS NULL
            """,
            (now, current_user_id),
        )
        result = conn.execute(
            """
            UPDATE users
            SET token_version = token_version + 1, updated_at = ?
            WHERE id = ?
            """,
            (now, current_user_id),
        )
        if result.rowcount != 1:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        conn.commit()

    return {"ok": True, "message": "Logout successful. All active sessions were ended."}


def get_current_user_from_access_token(access_token: str) -> CurrentUserResponse:
    """Return current user from a verified, non-revoked access token."""
    init_auth_db()
    payload = decode_access_token(access_token)
    user_id = payload["sub"]
    token_version = payload["token_version"]

    with get_connection() as conn:
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user["status"] != UserStatus.ACTIVE.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not active")
    if int(user.get("token_version", 0)) != token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This session has been logged out. Please login again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _user_response(user)


def validate_auth_runtime() -> dict[str, Any]:
    """Return auth runtime health information without secrets."""
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
        "auth_endpoints": AUTH_ENDPOINTS,
        "admin_access_request_endpoints": ADMIN_ACCESS_REQUEST_ENDPOINTS,
    }
