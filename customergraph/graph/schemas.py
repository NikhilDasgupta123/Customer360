"""Request and response contracts for the CustomerGraph demo graph seed."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GraphDemoSeedRequest(BaseModel):
    """Control whether previously-created CustomerGraph demo nodes are rebuilt.

    When ``replace_existing_demo`` is true, only graph nodes tagged with the
    CustomerGraph demo dataset marker are removed. Real, untagged graph data
    is never deleted by this endpoint.
    """

    replace_existing_demo: bool = Field(
        default=False,
        description=(
            "When true, delete and recreate only the CustomerGraph dashboard demo dataset. "
            "Existing real graph data is not deleted."
        ),
    )


class GraphDemoSeedResponse(BaseModel):
    """Result returned after the Admin seeds the graph for dashboard testing."""

    ok: bool = True
    message: str
    dataset: str
    reset_performed: bool
    customers_created_or_updated: int = Field(ge=0)
    employees_created_or_updated: int = Field(ge=0)
    products_created_or_updated: int = Field(ge=0)
    tickets_created_or_updated: int = Field(ge=0)
    invoices_created_or_updated: int = Field(ge=0)
    contracts_created_or_updated: int = Field(ge=0)
    opportunities_created_or_updated: int = Field(ge=0)
    usage_records_created_or_updated: int = Field(ge=0)
    health_snapshots_created_or_updated: int = Field(ge=0)
    seeded_by_user_id: str
