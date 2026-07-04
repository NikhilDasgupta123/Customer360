"""Strict user-ownership helpers for CustomerGraph business data.

Rule:
- Standard user APIs never accept owner_user_id from the frontend.
- The backend derives owner_user_id from the verified access token.
- A resource can only be read, changed, or deleted when its stored owner id
  matches the authenticated user's id.
- Administrative cross-user access must use a separate explicitly-admin-only
  endpoint. It must never silently bypass this default scope.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException, status

from customergraph.auth.schemas import CurrentUserResponse


_SAFE_CYPHER_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def current_owner_user_id(current_user: CurrentUserResponse) -> str:
    """Return the only owner id that standard user-facing routes may use."""
    return current_user.id


def owned_create_properties(current_user: CurrentUserResponse) -> dict[str, str]:
    """Return immutable ownership values for a new graph or database record."""
    return {
        "owner_user_id": current_user.id,
        "created_by_user_id": current_user.id,
    }


def enforce_resource_owner(
    *,
    stored_owner_user_id: str | None,
    current_user: CurrentUserResponse,
    resource_name: str = "resource",
) -> None:
    """Block cross-user access for a loaded resource.

    Do not bypass this function inside normal user routes. If support staff or
    an administrator need cross-user access, create a separate admin-only
    route and record that access in audit logs.
    """
    if not stored_owner_user_id or stored_owner_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You are not allowed to access this {resource_name}.",
        )


def build_user_scoped_cypher(
    *,
    label: str,
    alias: str = "resource",
    extra_where: str | None = None,
) -> str:
    """Build a Cypher MATCH whose owner filter is mandatory.

    The returned statement uses ``$owner_user_id``. Call it with:
        session.run(
            build_user_scoped_cypher(label="Customer", alias="customer"),
            owner_user_id=current_owner_user_id(current_user),
        )

    ``label`` and ``alias`` are developer-controlled identifiers only and are
    validated before being inserted into Cypher. User-provided values must
    always be passed as query parameters, never string-formatted into Cypher.
    """
    if not _SAFE_CYPHER_IDENTIFIER.fullmatch(label):
        raise ValueError("Cypher label must be a safe identifier")
    if not _SAFE_CYPHER_IDENTIFIER.fullmatch(alias):
        raise ValueError("Cypher alias must be a safe identifier")

    clauses = [f"{alias}.owner_user_id = $owner_user_id"]
    if extra_where:
        clauses.append(f"({extra_where})")
    return f"MATCH ({alias}:{label}) WHERE {' AND '.join(clauses)} RETURN {alias}"


def user_scoped_cypher_params(
    current_user: CurrentUserResponse,
    **additional_params: Any,
) -> dict[str, Any]:
    """Return query parameters with the owner id injected from the JWT user."""
    return {
        "owner_user_id": current_owner_user_id(current_user),
        **additional_params,
    }
