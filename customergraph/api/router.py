"""Main API router for versioned backend endpoints."""

from fastapi import APIRouter

from .setup_routes import router as setup_router
from .routes import router as day1_router

api_router = APIRouter()
api_router.include_router(day1_router)
api_router.include_router(setup_router)
