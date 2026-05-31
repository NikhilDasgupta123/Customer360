"""Day 3 auth design endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from customergraph.schemas.auth import AuthDesignValidationResponse
from customergraph.services.auth_design_service import (
    LOGIN_FLOW_STEPS,
    PASSWORD_HASHING_PLAN,
    get_full_auth_design,
    get_token_design,
    get_user_model_design,
    validate_auth_design,
)

router = APIRouter(prefix="/day3", tags=["Day 3 - Auth Design"])


@router.get("/ping")
def ping_day3() -> dict:
    """Small endpoint to verify the Day 3 router is loaded."""
    return {"ok": True, "message": "Day 3 auth design router is working"}


@router.get("/auth-design")
def get_day3_auth_design() -> dict:
    """Return the complete Day 3 auth design."""
    return get_full_auth_design()


@router.get("/user-model")
def get_day3_user_model() -> dict:
    """Return planned user model, roles, and DB table design."""
    return {"ok": True, "day": 3, "data": get_user_model_design()}


@router.get("/password-plan")
def get_day3_password_plan() -> dict:
    """Return password hashing rules for upcoming auth implementation."""
    return {"ok": True, "day": 3, "data": PASSWORD_HASHING_PLAN}


@router.get("/token-flow")
def get_day3_token_flow() -> dict:
    """Return access-token and refresh-token design."""
    return {"ok": True, "day": 3, "data": get_token_design()}


@router.get("/login-flow")
def get_day3_login_flow() -> dict:
    """Return planned login flow steps."""
    return {"ok": True, "day": 3, "steps": LOGIN_FLOW_STEPS}


@router.get("/schema-validation", response_model=AuthDesignValidationResponse)
def get_day3_schema_validation() -> dict:
    """Validate Day 3 auth design against Day 1 role freeze."""
    return validate_auth_design()
