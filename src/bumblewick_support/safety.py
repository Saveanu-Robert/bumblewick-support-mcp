"""The refund safety engine — a.k.a. "don't give the robot a chainsaw".

This is deliberately a *pure* function: it takes plain numbers and an order and
returns a decision. No database, no I/O, no MCP. That makes every rule trivially
unit-testable and keeps the dangerous logic in one auditable place.
"""

from __future__ import annotations

from .config import Settings
from .models import Order, OrderStatus, RefundDecision

#: Only orders that actually shipped can be refunded.
REFUNDABLE_STATUSES = {OrderStatus.SHIPPED, OrderStatus.DELIVERED}


def evaluate_refund(
    *,
    order: Order,
    amount_cents: int,
    already_refunded_cents: int,
    recent_refund_count: int,
    confirm: bool,
    settings: Settings,
) -> RefundDecision:
    """Decide whether a refund may proceed.

    Rules, in order:
      1. Amount must be positive.
      2. Order must be in a refundable status.
      3. Amount must not exceed the remaining refundable balance (no over-refund).
      4. Customer must be within the rolling refund rate limit.
      5. Amounts above the auto-approve limit need explicit ``confirm=True``
         (human-in-the-loop) instead of silently executing.
    """
    max_refundable = max(order.total_cents - already_refunded_cents, 0)

    if amount_cents <= 0:
        return RefundDecision(
            outcome="rejected",
            reason="Refund amount must be a positive number of cents.",
            max_refundable_cents=max_refundable,
        )

    if order.status not in REFUNDABLE_STATUSES:
        return RefundDecision(
            outcome="rejected",
            reason=(
                f"Order status '{order.status.value}' is not eligible for a "
                "refund; only shipped or delivered orders can be refunded."
            ),
            max_refundable_cents=max_refundable,
        )

    if amount_cents > max_refundable:
        return RefundDecision(
            outcome="rejected",
            reason=(
                f"Refund of {amount_cents} cents exceeds the refundable balance "
                f"of {max_refundable} cents."
            ),
            max_refundable_cents=max_refundable,
        )

    if recent_refund_count >= settings.refund_rate_limit:
        return RefundDecision(
            outcome="rejected",
            reason=(
                f"Customer has reached the refund rate limit "
                f"({settings.refund_rate_limit} per "
                f"{settings.refund_rate_window_hours}h)."
            ),
            max_refundable_cents=max_refundable,
        )

    if amount_cents > settings.auto_refund_limit_cents and not confirm:
        return RefundDecision(
            outcome="needs_confirmation",
            reason=(
                f"Refund of {amount_cents} cents exceeds the auto-approve limit "
                f"of {settings.auto_refund_limit_cents} cents. Re-issue the same "
                "request with confirm=true to proceed."
            ),
            max_refundable_cents=max_refundable,
        )

    return RefundDecision(
        outcome="ok", reason="Refund approved.", max_refundable_cents=max_refundable
    )
