"""Small, UI-focused CustomerGraph AI analysis API.

Only two endpoints are intentionally exposed:
* Dashboard one-click portfolio summary
* Customers one-click individual analysis

The existing dashboard/customer GET APIs remain the read APIs. There are no
public health, queue, run-status, or latest-insight endpoints in this version.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from neo4j.exceptions import Neo4jError

from customergraph.agents.data_service import CustomerAnalysisAccessError
from customergraph.agents.schemas import (
    SimpleCustomerAnalysisResponse,
    SimplePortfolioAnalysisResponse,
)
from customergraph.agents.service import (
    AgentActor,
    AgentRunConflictError,
    AgentUnavailableError,
    analyse_customer_now,
    analyse_portfolio_now,
)
from customergraph.auth.dependencies import CurrentUser, require_module, require_roles
from customergraph.core.logging import get_logger
from customergraph.models.user import UserRole

router = APIRouter(prefix="/ai", tags=["AI Analysis"])
logger = get_logger("customergraph.ai.api")
admin_only = [Depends(require_roles(UserRole.ADMIN))]
customer_ai_access = [Depends(require_module("customers"))]


def _service_unavailable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


@router.post(
    "/analyse-dashboard",
    response_model=SimplePortfolioAnalysisResponse,
    summary="Analyse the dashboard portfolio with AI",
    dependencies=admin_only,
)
def analyse_dashboard(current_user: CurrentUser) -> SimplePortfolioAnalysisResponse:
    """One Dashboard button: current portfolio facts -> one LLM executive summary."""
    try:
        return analyse_portfolio_now(AgentActor.from_current_user(current_user))
    except AgentRunConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An AI analysis is already running. Please wait for it to finish.",
        ) from exc
    except AgentUnavailableError as exc:
        raise _service_unavailable(str(exc)) from exc
    except (Neo4jError, OSError) as exc:
        logger.exception("dashboard_ai_analysis_graph_failed user_id=%s", current_user.id)
        raise _service_unavailable("AI analysis is unavailable. Check Neo4j and Ollama.") from exc
    except Exception as exc:
        logger.exception("dashboard_ai_analysis_failed user_id=%s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Dashboard AI analysis could not be completed. Check the server logs.",
        ) from exc


@router.post(
    "/customers/{customer_id}/analyse",
    response_model=SimpleCustomerAnalysisResponse,
    summary="Analyse one customer with AI",
    dependencies=customer_ai_access,
)
def analyse_customer(customer_id: str, current_user: CurrentUser) -> SimpleCustomerAnalysisResponse:
    """One Customers button: actual customer data -> LLM summary and next action."""
    try:
        return analyse_customer_now(customer_id, AgentActor.from_current_user(current_user))
    except CustomerAnalysisAccessError as exc:
        # Keep customer ownership private.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer was not found.") from exc
    except AgentRunConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="AI analysis is already running for this customer. Please wait for it to finish.",
        ) from exc
    except AgentUnavailableError as exc:
        raise _service_unavailable(str(exc)) from exc
    except (Neo4jError, OSError) as exc:
        logger.exception("customer_ai_analysis_graph_failed user_id=%s customer_id=%s", current_user.id, customer_id)
        raise _service_unavailable("AI analysis is unavailable. Check Neo4j and Ollama.") from exc
    except Exception as exc:
        logger.exception("customer_ai_analysis_failed user_id=%s customer_id=%s", current_user.id, customer_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Customer AI analysis could not be completed. Check the server logs.",
        ) from exc
