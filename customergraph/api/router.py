"""Main router for the active CustomerGraph API surface."""

from fastapi import APIRouter

from .auth_routes import router as auth_router
from .graph_routes import router as graph_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(graph_router)
