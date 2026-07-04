"""Protected Main Dashboard API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j.exceptions import Neo4jError

from customergraph.auth.dependencies import CurrentUser, require_module
from customergraph.core.logging import get_logger
from customergraph.dashboard.schemas import DashboardSummaryResponse
from customergraph.dashboard.service import get_dashboard_summary

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
logger = get_logger("customergraph.dashboard")


@router.get(
    "/summary",
    response_model=DashboardSummaryResponse,
    summary="Get main dashboard metrics",
    dependencies=[Depends(require_module("dashboard"))],
)
def dashboard_summary(current_user: CurrentUser) -> DashboardSummaryResponse:
    """Return real Neo4j-derived Main Dashboard data; never demo/hard-coded values."""
    try:
        return get_dashboard_summary(current_user)
    except (Neo4jError, OSError) as exc:
        logger.exception("dashboard_summary_unavailable user_id=%s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard data is unavailable. Check Neo4j connectivity and graph data.",
        ) from exc
    except Exception as exc:
        logger.exception("dashboard_summary_failed user_id=%s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Dashboard summary could not be generated.",
        ) from exc
