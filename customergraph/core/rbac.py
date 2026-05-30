"""Simple Day 1 RBAC helper.

This is not final JWT auth. It is a testable access-rule helper so we can freeze
permissions before building real auth APIs on Day 3 and Day 4.
"""

from customergraph.core.modules import get_module_keys
from customergraph.core.permissions import get_permissions_for_role
from customergraph.core.roles import get_role_keys


def normalize_key(value: str) -> str:
    """Normalize user input into a clean key."""
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def can_access(role_key: str, module_key: str) -> bool:
    """Return True if role can access the protected module."""
    role_key = normalize_key(role_key)
    module_key = normalize_key(module_key)
    return module_key in get_permissions_for_role(role_key)


def validate_rbac_matrix() -> dict:
    """Validate that all permission keys point to real roles and modules."""
    roles = set(get_role_keys())
    modules = set(get_module_keys())
    errors: list[str] = []

    for role in roles:
        for module in get_permissions_for_role(role):
            if module not in modules:
                errors.append(f"Unknown module '{module}' in role '{role}'")

    for role in get_role_keys():
        if role not in roles:
            errors.append(f"Unknown role '{role}'")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "role_count": len(roles),
        "module_count": len(modules),
    }