"""FastAPI entrypoint for CustomerGraph AI."""

from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from customergraph.api.router import api_router
from customergraph.core.config import get_settings
from customergraph.core.logging import RequestLoggingMiddleware, configure_logging, get_logger
from customergraph.db.neo4j_client import close_neo4j_driver, verify_neo4j_connection

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger("customergraph.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and safely close shared application resources."""
    logger.info(
        "startup app=%s version=%s environment=%s api_prefix=%s",
        settings.app_name,
        settings.app_version,
        settings.environment,
        settings.api_v1_prefix,
    )

    try:
        connection = verify_neo4j_connection()
        logger.info(
            "neo4j_connected database=%s checked_at=%s",
            connection["database"],
            connection["checked_at"],
        )
    except Exception:
        # CustomerGraph depends on Neo4j for customer, health, risk,
        # recommendation, agent, and chatbot services.
        logger.exception("neo4j_startup_connection_failed")
        close_neo4j_driver()
        raise

    try:
        yield
    finally:
        close_neo4j_driver()
        logger.info("shutdown app=%s neo4j_driver_closed=true", settings.app_name)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="CustomerGraph AI backend for authentication and Neo4j graph services.",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        lifespan=lifespan,
    )

    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.backend_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", tags=["System"], summary="Service information")
    def root() -> dict:
        """Return the main service links."""
        return {
            "ok": True,
            "service": "customergraph-ai-backend",
            "docs": "/docs" if settings.docs_enabled else None,
            "health": "/health",
            "graph_health": f"{settings.api_v1_prefix}/graph/health",
        }

    @app.get("/health", tags=["System"], summary="Check backend health")
    def health() -> dict:
        """Return the basic backend health status."""
        return {
            "ok": True,
            "service": "customergraph-ai-backend",
            "status": "healthy",
            "version": settings.app_version,
            "environment": settings.environment,
        }

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.is_development,
    )
