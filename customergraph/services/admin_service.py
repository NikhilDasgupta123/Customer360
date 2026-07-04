"""Admin-only user-management services for CustomerGraph."""

from __future__ import annotations

from typing import Any, Literal
import uuid

from fastapi import HTTPException, status

from customergraph.core.roles import get_role_keys
from customergraph.core.security import hash_password, utc_iso
from customergraph.db.sqlite import get_connection, init_auth_db
from customergraph.models.user import UserRole, UserStatus
from customergraph.schemas.auth import (
    AccessRequestActionResponse,
    AccessRequestListResponse,
    AccessRequestUserResponse,
    AdminCreateUserRequest,
    AdminUpdateUserRequest,
    AdminUserActionResponse,
    AdminUserListResponse,
    AdminUserResponse,
    AdminUserSummaryResponse,
)
from customergraph.services.auth_service import _access_request_user_response, _row_to_user, _supported_user_role, normalize_email


AccessRequestAction = Literal["approve", "reject"]


def _supported_role_values() -> tuple[str, ...]:
    return tuple(get_role_keys())


def _admin_user_response(user: dict[str, Any]) -> AdminUserResponse:
    return AdminUserResponse(
        id=user["id"],
        email=user["email"],
        full_name=user["full_name"],
        role=_supported_user_role(user),
        status=UserStatus(user["status"]),
        company_team=user.get("company_team"),
        is_first_admin=bool(user.get("is_first_admin", 0)),
        created_at=user.get("created_at"),
        updated_at=user.get("updated_at"),
        last_login_at=user.get("last_login_at"),
    )


def _valid_role_placeholders() -> tuple[str, str]:
    roles = _supported_role_values()
    return ", ".join("?" for _ in roles), roles


def list_access_requests(request_status: UserStatus = UserStatus.PENDING) -> AccessRequestListResponse:
    """Return access requests for supported roles, newest first."""
    init_auth_db()
    placeholders, roles = _valid_role_placeholders()

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM users
            WHERE status = ? AND role IN ({placeholders})
            ORDER BY created_at DESC, email ASC
            """,
            (request_status.value, *roles),
        ).fetchall()

    requests: list[AccessRequestUserResponse] = []
    for row in rows:
        user = _row_to_user(row)
        if user is not None:
            requests.append(_access_request_user_response(user))
    return AccessRequestListResponse(ok=True, requests=requests)


def review_access_request(user_id: str, action: AccessRequestAction) -> AccessRequestActionResponse:
    """Approve or reject one pending non-admin access request."""
    init_auth_db()
    if action not in {"approve", "reject"}:
        raise ValueError("Unsupported access-request action")

    with get_connection() as conn:
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access request not found")

        if user["role"] not in _supported_role_values() or user["role"] == UserRole.ADMIN.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only pending Sales Executive, Account Manager, or Support Agent requests can be reviewed.",
            )
        if user["status"] != UserStatus.PENDING.value:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This access request has already been reviewed.")

        next_status = UserStatus.ACTIVE if action == "approve" else UserStatus.DISABLED
        now = utc_iso()
        conn.execute(
            """
            UPDATE users
            SET status = ?, updated_at = ?, token_version = token_version + 1
            WHERE id = ?
            """,
            (next_status.value, now, user_id),
        )
        updated_user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        if updated_user is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Access request could not be updated")
        conn.commit()

    verb = "approved" if action == "approve" else "rejected"
    return AccessRequestActionResponse(
        ok=True,
        message=f"Access request {verb} successfully.",
        user=_access_request_user_response(updated_user),
    )


def list_admin_users(
    search: str = "",
    role: UserRole | None = None,
    user_status: UserStatus | None = None,
) -> AdminUserListResponse:
    """Return supported-role users for the React Admin > Users & Access screen."""
    init_auth_db()
    placeholders, roles = _valid_role_placeholders()
    clauses = [f"role IN ({placeholders})"]
    params: list[Any] = list(roles)

    normalized_search = search.strip().lower()
    if normalized_search:
        clauses.append("(LOWER(full_name) LIKE ? OR LOWER(email) LIKE ? OR LOWER(COALESCE(company_team, '')) LIKE ?)")
        token = f"%{normalized_search}%"
        params.extend([token, token, token])
    if role is not None:
        clauses.append("role = ?")
        params.append(role.value)
    if user_status is not None:
        clauses.append("status = ?")
        params.append(user_status.value)

    where_clause = " AND ".join(clauses)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM users
            WHERE {where_clause}
            ORDER BY
                CASE role WHEN 'admin' THEN 0 ELSE 1 END,
                CASE status WHEN 'active' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                full_name COLLATE NOCASE ASC, email COLLATE NOCASE ASC
            """,
            params,
        ).fetchall()

    users = [_admin_user_response(user) for row in rows if (user := _row_to_user(row)) is not None]
    return AdminUserListResponse(ok=True, users=users, total=len(users))


def get_admin_user_summary() -> AdminUserSummaryResponse:
    """Return real counts used by the summary cards; no demo numbers are fabricated."""
    init_auth_db()
    placeholders, roles = _valid_role_placeholders()
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total_users,
                COALESCE(SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END), 0) AS active_users,
                COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_users,
                COALESCE(SUM(CASE WHEN status = 'disabled' THEN 1 ELSE 0 END), 0) AS disabled_users,
                COALESCE(SUM(CASE WHEN role = 'admin' THEN 1 ELSE 0 END), 0) AS admin_users
            FROM users
            WHERE role IN ({placeholders})
            """,
            roles,
        ).fetchone()

    return AdminUserSummaryResponse(
        ok=True,
        total_users=int(row["total_users"] or 0),
        active_users=int(row["active_users"] or 0),
        pending_users=int(row["pending_users"] or 0),
        disabled_users=int(row["disabled_users"] or 0),
        admin_users=int(row["admin_users"] or 0),
    )


def create_admin_user(payload: AdminCreateUserRequest) -> AdminUserActionResponse:
    """Create a user from the Admin UI using a temporary password supplied by the Admin."""
    init_auth_db()
    email = normalize_email(payload.email)
    now = utc_iso()

    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account already exists for this email")

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
                payload.role.value,
                payload.status.value,
                payload.company_team,
                now,
                now,
            ),
        )
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        if user is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User could not be created")
        conn.commit()

    state_text = "active" if payload.status is UserStatus.ACTIVE else payload.status.value
    return AdminUserActionResponse(
        ok=True,
        message=f"User created successfully with {state_text} status.",
        user=_admin_user_response(user),
    )


def _active_admin_count(conn: Any) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM users WHERE role = ? AND status = ?",
        (UserRole.ADMIN.value, UserStatus.ACTIVE.value),
    ).fetchone()
    return int(row["total"] if row else 0)


def update_admin_user(user_id: str, payload: AdminUpdateUserRequest, current_admin_id: str) -> AdminUserActionResponse:
    """Edit role/status/name/team while preserving a usable active Administrator account."""
    init_auth_db()
    with get_connection() as conn:
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        if user is None or user.get("role") not in _supported_role_values():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        next_role = payload.role.value if payload.role is not None else user["role"]
        next_status = payload.status.value if payload.status is not None else user["status"]

        if user_id == current_admin_id and (next_role != UserRole.ADMIN.value or next_status != UserStatus.ACTIVE.value):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot remove or suspend your own Admin access")
        if bool(user.get("is_first_admin", 0)) and (next_role != UserRole.ADMIN.value or next_status != UserStatus.ACTIVE.value):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The first Admin account cannot be removed or suspended")

        is_losing_last_admin = (
            user["role"] == UserRole.ADMIN.value
            and user["status"] == UserStatus.ACTIVE.value
            and (next_role != UserRole.ADMIN.value or next_status != UserStatus.ACTIVE.value)
        )
        if is_losing_last_admin and _active_admin_count(conn) <= 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="At least one active Admin account must remain")

        fields: list[str] = []
        values: list[Any] = []
        if payload.full_name is not None:
            fields.append("full_name = ?")
            values.append(payload.full_name)
        if payload.company_team is not None:
            fields.append("company_team = ?")
            values.append(payload.company_team)
        if payload.role is not None:
            fields.append("role = ?")
            values.append(next_role)
        if payload.status is not None:
            fields.append("status = ?")
            values.append(next_status)
        if payload.role is not None or payload.status is not None:
            fields.append("token_version = token_version + 1")

        fields.append("updated_at = ?")
        values.append(utc_iso())
        values.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
        updated_user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        if updated_user is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="User could not be updated")
        conn.commit()

    return AdminUserActionResponse(ok=True, message="User updated successfully.", user=_admin_user_response(updated_user))


def delete_admin_user(user_id: str, current_admin_id: str) -> AdminUserActionResponse:
    """Delete a non-first, non-current user and revoke their refresh tokens."""
    init_auth_db()
    with get_connection() as conn:
        user = _row_to_user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        if user is None or user.get("role") not in _supported_role_values():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        if user_id == current_admin_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You cannot delete your own account")
        if bool(user.get("is_first_admin", 0)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The first Admin account cannot be deleted")
        if user["role"] == UserRole.ADMIN.value and user["status"] == UserStatus.ACTIVE.value and _active_admin_count(conn) <= 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="At least one active Admin account must remain")

        conn.execute("DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()

    return AdminUserActionResponse(ok=True, message="User deleted successfully.", user=None)
