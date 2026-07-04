"""Neo4j graph health and admin-only demo-data endpoints for CustomerGraph AI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j.exceptions import Neo4jError

from customergraph.auth.dependencies import require_roles
from customergraph.auth.schemas import CurrentUserResponse
from customergraph.core.logging import get_logger
from customergraph.db.neo4j_client import verify_neo4j_connection
from customergraph.graph.demo_seed_service import seed_dashboard_demo_data
from customergraph.graph.schemas import GraphDemoSeedRequest, GraphDemoSeedResponse
from customergraph.models.user import UserRole

router = APIRouter(prefix="/graph", tags=["Graph"])
logger = get_logger("customergraph.graph")


@router.get(
    "/health",
    summary="Check Neo4j graph connection",
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
)
def graph_health() -> dict:
    """Verify that an Admin can reach the configured Neo4j database."""
    try:
        connection = verify_neo4j_connection()
    except Neo4jError as exc:
        logger.exception("neo4j_health_failed error_type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Neo4j graph service is unavailable. Check Neo4j Aura configuration and connectivity.",
        ) from exc
    except Exception as exc:
        logger.exception("neo4j_health_failed error_type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Neo4j graph health check failed.",
        ) from exc

    return {
        "ok": True,
        "service": "customergraph-neo4j",
        "status": "healthy",
        "database": connection["database"],
        "checked_at": connection["checked_at"],
    }


@router.post(
    "/seed-demo-data",
    response_model=GraphDemoSeedResponse,
    summary="Create the safe CustomerGraph dashboard demo graph",
)
def seed_demo_data(
    request: GraphDemoSeedRequest,
    current_user: CurrentUserResponse = Depends(require_roles(UserRole.ADMIN)),
) -> GraphDemoSeedResponse:
    """Seed only tagged demo nodes; never delete untagged graph data.

    Use an empty JSON object (``{}``) for the normal first seed. Set
    ``replace_existing_demo`` to true only when you want to reset the
    CustomerGraph dashboard demo dataset during local testing.
    """
    try:
        return seed_dashboard_demo_data(
            current_user,
            replace_existing_demo=request.replace_existing_demo,
        )
    except Neo4jError as exc:
        logger.exception("neo4j_demo_seed_failed user_id=%s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Neo4j demo graph data could not be created. Check Neo4j Aura connectivity and schema permissions.",
        ) from exc
    except Exception as exc:
        logger.exception("neo4j_demo_seed_unexpected_failure user_id=%s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Demo graph data could not be created.",
        ) from exc
