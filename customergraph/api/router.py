"""Main API router for versioned backend endpoints."""

from fastapi import APIRouter

from .auth_design_routes import router as auth_design_router
from .auth_routes import router as auth_router
from .day4_routes import router as day4_router
from .routes import router as day1_router
from .setup_routes import router as setup_router

api_router = APIRouter()
api_router.include_router(day1_router)
api_router.include_router(setup_router)
api_router.include_router(auth_design_router)
api_router.include_router(day4_router)
api_router.include_router(auth_router)
