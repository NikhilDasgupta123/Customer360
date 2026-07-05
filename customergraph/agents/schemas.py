"""API and LLM contracts for CustomerGraph's background AI agents."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class AgentType(str, Enum):
    """Supported agent types. The first release enables health/churn only."""

    CUSTOMER_HEALTH_CHURN = "customer_health_churn"


class AgentRunStatus(str, Enum):
    """Persisted lifecycle for an asynchronous agent run."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class AgentRunAcceptedResponse(BaseModel):
    """Immediate response after a one-click analysis request is queued."""

    ok: bool = True
    run_id: str
    agent_type: AgentType
    status: AgentRunStatus = AgentRunStatus.QUEUED
    message: str


class AgentRunStatusResponse(BaseModel):
    """Pollable status used later by the Dashboard loading UI."""

    ok: bool = True
    run_id: str
    agent_type: AgentType
    status: AgentRunStatus
    scope: Literal["portfolio", "customer"]
    requested_by_user_id: str
    customer_id: str | None = None
    total_customers: int = Field(default=0, ge=0)
    processed_customers: int = Field(default=0, ge=0)
    successful_customers: int = Field(default=0, ge=0)
    failed_customers: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    model: str | None = None
    total_llm_duration_ms: int | None = Field(default=None, ge=0)


class CustomerHealthChurnInsight(BaseModel):
    """Strict LLM result stored as the latest insight for one customer."""

    risk_level: Literal["low", "medium", "high", "critical"]
    churn_probability: int = Field(..., ge=0, le=100)
    renewal_confidence: int = Field(..., ge=0, le=100)
    upsell_likelihood: int = Field(..., ge=0, le=100)
    confidence: int = Field(..., ge=0, le=100)
    executive_summary: str = Field(..., min_length=1, max_length=900)
    key_risk_drivers: list[str] = Field(default_factory=list, max_length=4)
    positive_signals: list[str] = Field(default_factory=list, max_length=3)
    recommended_action: str = Field(..., min_length=1, max_length=600)
    priority: Literal["low", "normal", "high", "urgent"]
    requires_human_review: bool


class CustomerAIInsightResponse(CustomerHealthChurnInsight):
    """Latest persisted one-customer insight exposed to future Customer UI."""

    ok: bool = True
    customer_id: str
    customer_name: str
    agent_type: AgentType = AgentType.CUSTOMER_HEALTH_CHURN
    generated_at: datetime
    source_run_id: str
    model: str
    analysis_input_version: str = "v1"


class PortfolioPriorityAction(BaseModel):
    """One LLM-selected action inside a portfolio summary."""

    customer_id: str
    customer_name: str
    priority: Literal["high", "urgent"]
    title: str = Field(..., min_length=1, max_length=160)
    recommendation: str = Field(..., min_length=1, max_length=500)


class PortfolioHealthChurnInsight(BaseModel):
    """Structured LLM summary of all customer health/churn analyses."""

    portfolio_status: Literal["healthy", "watch", "needs_attention", "critical"]
    executive_summary: str = Field(..., min_length=1, max_length=1200)
    key_observations: list[str] = Field(default_factory=list, max_length=5)
    priority_actions: list[PortfolioPriorityAction] = Field(default_factory=list, max_length=5)
    requires_human_review: bool


class PortfolioAIInsightResponse(PortfolioHealthChurnInsight):
    """Latest portfolio insight consumed by the future Dashboard UI."""

    ok: bool = True
    agent_type: AgentType = AgentType.CUSTOMER_HEALTH_CHURN
    generated_at: datetime
    source_run_id: str
    model: str
    total_customers_analyzed: int = Field(default=0, ge=0)
    high_or_critical_customers: int = Field(default=0, ge=0)
    estimated_revenue_at_risk: float = Field(default=0, ge=0)


class OllamaAgentHealthResponse(BaseModel):
    """Safe configuration/availability check for the future Admin UI."""

    ok: bool
    enabled: bool
    base_url: str
    model: str
    model_available: bool
    message: str
    available_models: list[str] = Field(default_factory=list)


class SimpleCustomerAnalysisResponse(BaseModel):
    """Compact one-click customer analysis response for the Customers UI."""

    success: bool = True
    message: str
    customer_id: str
    customer_name: str
    risk_level: Literal["low", "medium", "high", "critical"]
    churn_probability: int = Field(..., ge=0, le=100)
    renewal_confidence: int = Field(..., ge=0, le=100)
    summary: str
    reasons: list[str] = Field(default_factory=list)
    recommended_action: str
    priority: Literal["low", "normal", "high", "urgent"]
    generated_at: datetime


class SimplePortfolioAction(BaseModel):
    """Compact priority action displayed in the Dashboard AI section."""

    customer_id: str
    customer_name: str
    priority: Literal["high", "urgent"]
    action: str


class SimplePortfolioAnalysisResponse(BaseModel):
    """Compact one-click portfolio analysis response for the Dashboard UI."""

    success: bool = True
    message: str
    portfolio_status: Literal["healthy", "watch", "needs_attention", "critical"]
    total_customers_analyzed: int = Field(default=0, ge=0)
    high_risk_customers: int = Field(default=0, ge=0)
    revenue_at_risk: float = Field(default=0, ge=0)
    summary: str
    key_observations: list[str] = Field(default_factory=list)
    priority_actions: list[SimplePortfolioAction] = Field(default_factory=list)
    generated_at: datetime


class DashboardAnalysisSection(str, Enum):
    """The one dashboard area selected by a sparkle button."""

    TOTAL_CUSTOMERS = "total_customers"
    HIGH_RISK_CUSTOMERS = "high_risk_customers"
    UPCOMING_RENEWALS = "upcoming_renewals"
    OPEN_CRITICAL_TICKETS = "open_critical_tickets"
    DELAYED_INVOICES = "delayed_invoices"
    UPSELL_OPPORTUNITIES = "upsell_opportunities"
    REVENUE_AT_RISK = "revenue_at_risk"
    HEALTH_SCORE_TREND = "health_score_trend"
    TOP_HIGH_RISK_CUSTOMERS = "top_high_risk_customers"


class DashboardWidgetAnalysisResponse(BaseModel):
    """Compact LLM result for exactly one clicked Dashboard widget."""

    success: bool = True
    message: str
    section: DashboardAnalysisSection
    title: str = Field(..., min_length=1, max_length=140)
    status: Literal["stable", "attention", "critical", "opportunity", "info"]
    summary: str = Field(..., min_length=1, max_length=520)
    evidence: list[str] = Field(default_factory=list, max_length=3)
    recommended_action: str = Field(..., min_length=1, max_length=340)
    generated_at: datetime
