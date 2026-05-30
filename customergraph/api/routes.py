from fastapi import APIRouter, Query

from ..core.modules import PROTECTED_MODULES
from ..core.permissions import PERMISSION_MATRIX, get_permissions_for_role
from ..core.project_scope import MVP_SCOPE
from ..core.rbac import can_access, normalize_key, validate_rbac_matrix
from ..core.roles import ROLES

router = APIRouter(tags=["Day 1 - Planning and Auth Freeze"])


@router.get("/day1/scope")
def get_day1_scope() -> dict:
    """Return frozen Day 1 MVP scope."""
    return {"ok": True, "data": MVP_SCOPE}


@router.get("/day1/roles")
def get_roles() -> dict:
    """Return MVP user roles."""
    return {"ok": True, "count": len(ROLES), "data": ROLES}


@router.get("/day1/protected-modules")
def get_protected_modules() -> dict:
    """Return protected backend modules."""
    return {"ok": True, "count": len(PROTECTED_MODULES), "data": PROTECTED_MODULES}


@router.get("/day1/permissions")
def get_permissions(role: str | None = Query(default=None, description="Optional role key")) -> dict:
    """Return full permission matrix or one role's permissions."""
    if role:
        role_key = normalize_key(role)
        return {
            "ok": True,
            "role": role_key,
            "allowed_modules": get_permissions_for_role(role_key),
        }

    return {"ok": True, "data": PERMISSION_MATRIX}


@router.get("/day1/access-check")
def check_access(
    role: str = Query(..., description="Example: admin, support_agent, manager"),
    module: str = Query(..., description="Example: customers, admin_audit, chatbot"),
) -> dict:
    """Check if a role can access one module."""
    role_key = normalize_key(role)
    module_key = normalize_key(module)
    allowed = can_access(role_key, module_key)

    return {
        "ok": True,
        "role": role_key,
        "module": module_key,
        "allowed": allowed,
        "message": "Access allowed" if allowed else "Access blocked",
    }


@router.get("/day1/rbac-validation")
def rbac_validation() -> dict:
    """Validate permission matrix consistency."""
    return validate_rbac_matrix()