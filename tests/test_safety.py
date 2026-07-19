from datetime import datetime, timezone

from bumblewick_support.config import Settings
from bumblewick_support.models import Order, OrderStatus
from bumblewick_support.safety import evaluate_refund

S = Settings(auto_refund_limit_cents=2000, refund_rate_limit=3, refund_rate_window_hours=24)


def make_order(status=OrderStatus.DELIVERED, total=2400) -> Order:
    return Order(
        id="ord_x",
        customer_id="cus_x",
        item="Test Candle",
        status=status,
        total_cents=total,
        currency="USD",
        created_at=datetime.now(timezone.utc),
    )


def _eval(order, amount, refunded=0, recent=0, confirm=False):
    return evaluate_refund(
        order=order,
        amount_cents=amount,
        already_refunded_cents=refunded,
        recent_refund_count=recent,
        confirm=confirm,
        settings=S,
    )


def test_zero_or_negative_amount_rejected():
    assert _eval(make_order(), 0).outcome == "rejected"
    assert _eval(make_order(), -100).outcome == "rejected"


def test_non_refundable_status_rejected():
    for status in (OrderStatus.PLACED, OrderStatus.CANCELLED):
        d = _eval(make_order(status=status), 500)
        assert d.outcome == "rejected"
        assert "not eligible" in d.reason


def test_over_refund_rejected():
    d = _eval(make_order(total=2400), amount=1000, refunded=2000)
    assert d.outcome == "rejected"
    assert d.max_refundable_cents == 400


def test_small_refund_ok():
    d = _eval(make_order(), amount=1500)
    assert d.outcome == "ok"


def test_large_refund_needs_confirmation():
    d = _eval(make_order(total=2400), amount=2400)
    assert d.outcome == "needs_confirmation"


def test_large_refund_with_confirm_ok():
    d = _eval(make_order(total=2400), amount=2400, confirm=True)
    assert d.outcome == "ok"


def test_rate_limited_rejected():
    d = _eval(make_order(), amount=500, recent=3)
    assert d.outcome == "rejected"
    assert "rate limit" in d.reason.lower()


def test_over_refund_checked_before_rate_limit():
    # An impossible amount is rejected for the amount, regardless of rate state.
    d = _eval(make_order(total=2400), amount=99999, recent=3)
    assert d.outcome == "rejected"
    assert "exceeds" in d.reason
