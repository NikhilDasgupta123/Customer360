"""Day 4 auth implementation status endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from customergraph.services.auth_service import validate_auth_runtime

router = APIRouter(prefix="/day4", tags=["Day 4 - Auth Implementation"])


@router.get("/ping")
def ping_day4() -> dict:
    """Small endpoint to verify the Day 4 router is loaded."""
    return {"ok": True, "message": "Day 4 auth implementation router is working"}


@router.get("/auth-runtime-status")
def auth_runtime_status() -> dict:
    """Check auth database, tables, admin bootstrap availability, and auth endpoints."""
    return validate_auth_runtime()
