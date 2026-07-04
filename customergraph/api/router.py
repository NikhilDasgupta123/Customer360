"""Main router for the active CustomerGraph API surface.

Public authentication endpoints are intentionally limited to the minimum
required for onboarding. Every business router is included through
``protected_api_router`` and therefore requires a valid Bearer access token.
"""

from fastapi import APIRouter, Depends

from .admin_routes import router as admin_router
from customergraph.auth.dependencies import get_current_user
from customergraph.auth.router import router as auth_router
from .graph_routes import router as graph_router
from customergraph.dashboard.router import router as dashboard_router
from customergraph.customers.router import router as customers_router


api_router = APIRouter()

# These routes must remain public because users need a way to obtain or
# rotate a token. The bootstrap endpoint has an additional one-time API key.
api_router.include_router(auth_router)

# Add every future business router here. Do not include business routers
# directly on api_router, or they could accidentally become public.
protected_api_router = APIRouter(dependencies=[Depends(get_current_user)])
protected_api_router.include_router(graph_router)
protected_api_router.include_router(admin_router)
protected_api_router.include_router(dashboard_router)
protected_api_router.include_router(customers_router)

api_router.include_router(protected_api_router)
