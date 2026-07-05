"""Response contracts for the CustomerGraph main dashboard."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DashboardHealthTrendPoint(BaseModel):
    """One monthly average health-score point for the dashboard chart."""

    month: str = Field(..., description="Month label from the HealthSnapshot node, for example 2026-07.")
    average_health_score: float = Field(..., ge=0, le=100)


class DashboardHighRiskCustomer(BaseModel):
    """Compact high-risk customer record used by the dashboard table."""

    customer_id: str | None = None
    customer_name: str
    health_score: float | None = Field(default=None, ge=0, le=100)
    risk_level: str
    risk_reason: str | None = None
    renewal_date: str | None = None
    revenue_at_risk: float = Field(default=0, ge=0)


class DashboardAIAction(BaseModel):
    """One saved AI recommended action for the existing Dashboard GET response."""

    customer_id: str
    customer_name: str
    priority: str
    action: str


class DashboardAISummary(BaseModel):
    """Latest saved dashboard AI summary; null until Analyse Portfolio is used."""

    portfolio_status: str
    summary: str
    key_observations: list[str] = Field(default_factory=list)
    priority_actions: list[DashboardAIAction] = Field(default_factory=list)
    generated_at: datetime


class DashboardSummaryResponse(BaseModel):
    """Single API response consumed by the React Main Dashboard screen."""

    ok: bool = True
    generated_at: datetime
    scope: str = Field(
        ...,
        description="all_customers for Admin, assigned_customers for Account Manager.",
    )
    total_customers: int = Field(default=0, ge=0)
    high_risk_customers: int = Field(default=0, ge=0)
    upcoming_renewals_next_30_days: int = Field(default=0, ge=0)
    open_critical_tickets: int = Field(default=0, ge=0)
    delayed_invoices_count: int = Field(default=0, ge=0)
    delayed_invoices_amount: float = Field(default=0, ge=0)
    upsell_opportunities_count: int = Field(default=0, ge=0)
    upsell_potential_revenue: float = Field(default=0, ge=0)
    revenue_at_risk: float = Field(default=0, ge=0)
    health_score_trend: list[DashboardHealthTrendPoint] = Field(default_factory=list)
    top_high_risk_customers: list[DashboardHighRiskCustomer] = Field(default_factory=list)
    ai_summary: DashboardAISummary | None = None
