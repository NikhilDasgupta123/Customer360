"""Response contracts for the CustomerGraph customer-list API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CustomerListItem(BaseModel):
    """One safe, compact Customer 360 list row.

    This is intentionally a list-screen contract, not the future full
    Customer 360 detail response. It contains only the fields required to
    find, filter, and open a customer record.
    """

    id: str
    company_name: str
    industry: str | None = None
    location: str | None = None
    owner_user_id: str | None = None
    owner_name: str | None = None
    health_score: float | None = Field(default=None, ge=0, le=100)
    risk_level: str = "unknown"
    risk_reason: str | None = None
    renewal_date: str | None = None
    open_ticket_count: int = Field(default=0, ge=0)
    annual_contract_value: float = Field(default=0, ge=0)
    last_activity_date: str | None = None
    ai_risk_level: str | None = None
    ai_summary: str | None = None
    ai_recommended_action: str | None = None
    ai_generated_at: str | None = None


class CustomerListResponse(BaseModel):
    """Paginated response for ``GET /api/v1/customers``."""

    ok: bool = True
    scope: str = Field(
        ...,
        description="all_customers for Admin, Sales Executive and Support Agent; assigned_customers for Account Manager.",
    )
    total: int = Field(default=0, ge=0)
    limit: int = Field(..., ge=1, le=100)
    offset: int = Field(..., ge=0)
    has_more: bool = False
    customers: list[CustomerListItem] = Field(default_factory=list)
