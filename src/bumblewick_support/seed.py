"""Deterministic seed data for Bumblewick & Co.

Fake customers, candle orders, and help articles so the server (and the tests,
and the reader) always have something to poke at. No real people, no real money.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import db
from .models import Customer, HelpArticle, Order, OrderStatus

# A fixed reference time keeps seeded timestamps stable across runs/tests.
_T0 = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)

CUSTOMERS = [
    Customer(id="cus_ivy", email="ivy@example.com", name="Ivy Ashford", is_vip=True, created_at=_T0),
    Customer(id="cus_bram", email="bram@example.com", name="Bram Kettle", created_at=_T0),
    Customer(id="cus_opal", email="opal@example.com", name="Opal Wren", created_at=_T0),
]

ORDERS = [
    # Ivy: one delivered (refundable), one cancelled (not refundable).
    Order(id="ord_1001", customer_id="cus_ivy", item="Vanilla Doom (Large)", status=OrderStatus.DELIVERED, total_cents=2400, currency="USD", created_at=_T0),
    Order(id="ord_1002", customer_id="cus_ivy", item="Midnight Bergamot Trio", status=OrderStatus.CANCELLED, total_cents=5400, currency="USD", created_at=_T0),
    # Bram: shipped (refundable), and a big-ticket delivered order (needs confirmation to refund fully).
    Order(id="ord_1003", customer_id="cus_bram", item="Existential Pine", status=OrderStatus.SHIPPED, total_cents=1800, currency="USD", created_at=_T0),
    Order(id="ord_1004", customer_id="cus_bram", item="The Whole Cabin (Gift Set)", status=OrderStatus.DELIVERED, total_cents=9900, currency="USD", created_at=_T0),
    # Opal: still just placed (not yet refundable).
    Order(id="ord_1005", customer_id="cus_opal", item="Petrichor No. 5", status=OrderStatus.PLACED, total_cents=3200, currency="USD", created_at=_T0),
]

HELP_ARTICLES = [
    HelpArticle(id="kb_burn", title="Getting the best burn", body="Trim the wick to 5mm before each light and let the first burn reach a full melt pool.", tags=["care", "wick", "burn"]),
    HelpArticle(id="kb_refund", title="Refunds and returns", body="Shipped or delivered orders can be refunded within 30 days. Cancelled orders are refunded automatically at cancellation.", tags=["refund", "returns", "policy"]),
    HelpArticle(id="kb_ship", title="Shipping times", body="Candles ship in 2-3 business days. The Whole Cabin gift set is made to order and ships in a week.", tags=["shipping", "delivery"]),
]

REFUND_POLICY = """\
Bumblewick & Co. — Refund Policy (support desk)

- Only SHIPPED or DELIVERED orders are eligible for a refund.
- Never refund more than the order total minus any refunds already issued.
- Refunds at or under the auto-approve limit may be issued directly.
- Larger refunds require explicit human confirmation (confirm=true).
- Each customer is limited to a small number of refunds per rolling window.
- Every refund should carry an idempotency key so retries never double-refund.
"""


def seed(path: str) -> None:
    """Create the schema and load the sample data (idempotent-ish: INSERT OR REPLACE)."""
    db.init_db(path)
    with db.connect(path) as conn:
        for c in CUSTOMERS:
            conn.execute(
                "INSERT OR REPLACE INTO customers (id, email, name, is_vip, created_at) VALUES (?,?,?,?,?)",
                (c.id, c.email, c.name, int(c.is_vip), c.created_at.isoformat()),
            )
        for o in ORDERS:
            conn.execute(
                "INSERT OR REPLACE INTO orders (id, customer_id, item, status, total_cents, currency, created_at) VALUES (?,?,?,?,?,?,?)",
                (o.id, o.customer_id, o.item, o.status.value, o.total_cents, o.currency, o.created_at.isoformat()),
            )
        for a in HELP_ARTICLES:
            conn.execute(
                "INSERT OR REPLACE INTO help_articles (id, title, body, tags) VALUES (?,?,?,?)",
                (a.id, a.title, a.body, ",".join(a.tags)),
            )
