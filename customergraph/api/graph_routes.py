"""Neo4j graph health endpoints for CustomerGraph AI."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from neo4j.exceptions import Neo4jError

from customergraph.core.logging import get_logger
from customergraph.db.neo4j_client import verify_neo4j_connection

router = APIRouter(prefix="/graph", tags=["Graph"])
logger = get_logger("customergraph.graph")


@router.get("/health", summary="Check Neo4j graph connection")
def graph_health() -> dict:
    """Verify that CustomerGraph can reach the configured Neo4j database."""
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
