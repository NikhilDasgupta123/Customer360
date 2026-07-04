"""Admin-only access-request and user-management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from customergraph.auth.dependencies import CurrentUser, require_roles
from customergraph.models.user import UserRole, UserStatus
from customergraph.auth.schemas import (
    AccessRequestActionResponse,
    AccessRequestListResponse,
    AdminCreateUserRequest,
    AdminUpdateUserRequest,
    AdminUserActionResponse,
    AdminUserListResponse,
    AdminUserSummaryResponse,
)
from customergraph.services.admin_service import (
    create_admin_user,
    delete_admin_user,
    get_admin_user_summary,
    list_access_requests,
    list_admin_users,
    review_access_request,
    update_admin_user,
)

router = APIRouter(prefix="/admin", tags=["Admin"])
admin_only = [Depends(require_roles(UserRole.ADMIN))]


@router.get("/access-requests", response_model=AccessRequestListResponse, dependencies=admin_only)
def get_access_requests(
    request_status: UserStatus = Query(default=UserStatus.PENDING, alias="status"),
) -> AccessRequestListResponse:
    """Existing approval API retained for compatibility."""
    return list_access_requests(request_status)


@router.patch(
    "/access-requests/{user_id}/approve",
    response_model=AccessRequestActionResponse,
    dependencies=admin_only,
)
def approve_access_request(user_id: str) -> AccessRequestActionResponse:
    return review_access_request(user_id, "approve")


@router.patch(
    "/access-requests/{user_id}/reject",
    response_model=AccessRequestActionResponse,
    dependencies=admin_only,
)
def reject_access_request(user_id: str) -> AccessRequestActionResponse:
    return review_access_request(user_id, "reject")


@router.get("/users", response_model=AdminUserListResponse, dependencies=admin_only)
def get_users(
    search: str = Query(default="", max_length=120),
    role: UserRole | None = Query(default=None),
    user_status: UserStatus | None = Query(default=None, alias="status"),
) -> AdminUserListResponse:
    """List real Admin, Sales Executive, Account Manager and Support Agent accounts."""
    return list_admin_users(search=search, role=role, user_status=user_status)


@router.get("/users/summary", response_model=AdminUserSummaryResponse, dependencies=admin_only)
def get_users_summary() -> AdminUserSummaryResponse:
    """Summary cards for the Admin React screen."""
    return get_admin_user_summary()


@router.post(
    "/users",
    response_model=AdminUserActionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=admin_only,
)
def create_user(payload: AdminCreateUserRequest) -> AdminUserActionResponse:
    """Create a user from the Add User modal with a temporary password."""
    return create_admin_user(payload)


@router.patch("/users/{user_id}", response_model=AdminUserActionResponse, dependencies=admin_only)
def patch_user(user_id: str, payload: AdminUpdateUserRequest, current_user: CurrentUser) -> AdminUserActionResponse:
    return update_admin_user(user_id, payload, current_user.id)


@router.delete("/users/{user_id}", response_model=AdminUserActionResponse, dependencies=admin_only)
def remove_user(user_id: str, current_user: CurrentUser) -> AdminUserActionResponse:
    return delete_admin_user(user_id, current_user.id)
