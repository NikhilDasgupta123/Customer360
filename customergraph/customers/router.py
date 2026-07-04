"""Protected Customer List API route."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from neo4j.exceptions import Neo4jError

from customergraph.auth.dependencies import CurrentUser, require_module
from customergraph.core.logging import get_logger
from customergraph.customers.schemas import CustomerListResponse
from customergraph.customers.service import (
    CustomerListFilters,
    CustomerScopeError,
    list_customers,
)

router = APIRouter(prefix="/customers", tags=["Customers"])
logger = get_logger("customergraph.customers")


@router.get(
    "",
    response_model=CustomerListResponse,
    summary="List CustomerGraph customers",
    dependencies=[Depends(require_module("customers"))],
)
def get_customers(
    current_user: CurrentUser,
    search: str = Query(default="", max_length=120, description="Search company name, industry, or location."),
    risk_level: str | None = Query(default=None, max_length=32, description="Optional exact risk level, for example high or critical."),
    renewal_within_days: int | None = Query(
        default=None,
        ge=0,
        le=365,
        description="Only customers whose renewal falls within this many calendar days.",
    ),
    owner_user_id: str | None = Query(
        default=None,
        max_length=128,
        description="Optional owner filter. Account Managers may only use their own user ID.",
    ),
    limit: int = Query(default=25, ge=1, le=100, description="Maximum records to return."),
    offset: int = Query(default=0, ge=0, description="Records to skip for pagination."),
    sort_by: Literal["company_name", "health_score", "renewal_date", "annual_contract_value"] = Query(
        default="company_name",
        description="Safe server-side sort field.",
    ),
    sort_direction: Literal["asc", "desc"] = Query(default="asc", description="Sort direction."),
) -> CustomerListResponse:
    """Return role-scoped Customer List rows backed by Neo4j.

    Admin, Sales Executive and Support Agent can browse the graph portfolio.
    Account Manager is automatically restricted to Customer nodes assigned to
    the verified user ID in the access token.
    """
    try:
        return list_customers(
            current_user,
            CustomerListFilters(
                search=search,
                risk_level=risk_level,
                renewal_within_days=renewal_within_days,
                owner_user_id=owner_user_id,
                limit=limit,
                offset=offset,
                sort_by=sort_by,
                sort_direction=sort_direction,
            ),
        )
    except CustomerScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except (Neo4jError, OSError) as exc:
        logger.exception("customer_list_unavailable user_id=%s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Customer data is unavailable. Check Neo4j connectivity and graph data.",
        ) from exc
    except Exception as exc:
        logger.exception("customer_list_failed user_id=%s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Customer list could not be generated.",
        ) from exc
