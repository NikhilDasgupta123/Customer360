"""Neo4j Aura client helpers for CustomerGraph AI."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from neo4j import Driver, GraphDatabase

from customergraph.core.config import get_settings


@lru_cache
def get_neo4j_driver() -> Driver:
    """Create and reuse one Neo4j driver for the FastAPI application."""
    settings = get_settings()

    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )


def verify_neo4j_connection() -> dict[str, Any]:
    """Verify the configured Neo4j database connection with a safe read query."""
    settings = get_settings()
    driver = get_neo4j_driver()

    # Confirms URI, credentials, TLS, and server reachability.
    driver.verify_connectivity()

    # Do not call currentDatabase(): it is unavailable in this Aura/Cypher runtime.
    # The selected database is already explicitly provided to the session.
    with driver.session(database=settings.neo4j_database) as session:
        record = session.run(
            """
            RETURN
                1 AS ok,
                datetime() AS checked_at
            """
        ).single()

    if record is None:
        raise RuntimeError("Neo4j connection check returned no result.")

    return {
        "ok": record["ok"] == 1,
        "database": settings.neo4j_database,
        "checked_at": str(record["checked_at"]),
    }


def close_neo4j_driver() -> None:
    """Close the cached Neo4j driver safely during application shutdown."""
    try:
        get_neo4j_driver().close()
    finally:
        get_neo4j_driver.cache_clear()
