"""Typed domain models.

Pydantic models are the contract between the data layer, the service layer, and
the MCP tools. They also drive input validation — the model *is* the schema the
AI sees, which is why getting them right matters.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class OrderStatus(str, Enum):
    PLACED = "placed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class RefundStatus(str, Enum):
    SUCCEEDED = "succeeded"


class Customer(BaseModel):
    id: str
    email: str
    name: str
    is_vip: bool = False
    created_at: datetime


class Order(BaseModel):
    id: str
    customer_id: str
    item: str
    status: OrderStatus
    total_cents: int
    currency: str
    created_at: datetime


class Refund(BaseModel):
    id: str
    order_id: str
    amount_cents: int
    reason: str
    status: RefundStatus
    idempotency_key: str | None = None
    created_at: datetime


class HelpArticle(BaseModel):
    id: str
    title: str
    body: str
    tags: list[str] = Field(default_factory=list)


class RefundDecision(BaseModel):
    """The output of the refund safety engine (see ``safety.py``)."""

    outcome: Literal["ok", "needs_confirmation", "rejected"]
    reason: str
    max_refundable_cents: int
