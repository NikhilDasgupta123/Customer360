"""FastAPI entrypoint for CustomerGraph AI."""

from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from customergraph.api.router import api_router
from customergraph.core.config import get_settings
from customergraph.core.logging import RequestLoggingMiddleware, configure_logging, get_logger
from customergraph.core.rbac import validate_rbac_matrix
from customergraph.services.auth_design_service import validate_auth_design

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger("customergraph.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Log application startup and shutdown."""
    logger.info(
        "startup app=%s version=%s environment=%s api_prefix=%s",
        settings.app_name,
        settings.app_version,
        settings.environment,
        settings.api_v1_prefix,
    )
    yield
    logger.info("shutdown app=%s", settings.app_name)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="CustomerGraph AI backend - Day 3 auth design with user model, JWT plan, and login flow.",
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

    @app.get("/", tags=["Root"])
    def root() -> dict:
        return {
            "ok": True,
            "message": "CustomerGraph AI backend is running",
            "day": 3,
            "docs": "http://127.0.0.1:8000/docs" if settings.docs_enabled else None,
            "health": "http://127.0.0.1:8000/health",
        }

    @app.get("/health", tags=["Health"])
    def health() -> dict:
        rbac_status = validate_rbac_matrix()
        auth_design_status = validate_auth_design()
        return {
            "ok": True,
            "service": "customergraph-ai-backend",
            "status": "healthy",
            "day": 3,
            "version": settings.app_version,
            "environment": settings.environment,
            "api_prefix": settings.api_v1_prefix,
            "rbac_ok": rbac_status["ok"],
            "auth_design_ok": auth_design_status["ok"],
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
