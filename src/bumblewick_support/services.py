"""The SupportDesk service layer.

All business logic lives here, decoupled from MCP. The server (server.py) is a
thin wrapper that calls these methods; the tests call them directly. Methods
return plain JSON-serialisable dicts — exactly what an MCP tool should hand back.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from . import db
from .config import Settings, get_settings
from .models import Refund, RefundStatus
from .safety import evaluate_refund

logger = logging.getLogger(__name__)

MAX_REASON_LEN = 500


class OrderNotFoundError(Exception):
    pass


def _fingerprint(order_id: str, amount_cents: int, reason: str) -> str:
    """Stable hash of the money-moving parameters, used to bind an idempotency key."""
    return hashlib.sha256(f"{order_id}|{amount_cents}|{reason}".encode()).hexdigest()


class SupportDesk:
    def __init__(self, db_path: str, settings: Settings | None = None) -> None:
        self.db_path = db_path
        self.settings = settings or get_settings()

    # ---- reads ---------------------------------------------------------------
    def find_orders(self, query: str) -> dict:
        """Find orders by order id, customer id, or customer email."""
        query = (query or "").strip()
        if not query:
            return {"query": query, "orders": [], "note": "Provide an order id, customer id, or email."}
        with db.connect(self.db_path) as conn:
            order = db.get_order(conn, query)
            if order:
                return {"query": query, "orders": [order.model_dump(mode="json")]}

            customer = db.get_customer(conn, query) or db.find_customer_by_email(conn, query)
            if not customer:
                return {"query": query, "orders": [], "note": "No matching order or customer."}

            orders = db.find_orders_by_customer(conn, customer.id)
            return {
                "query": query,
                "customer": customer.model_dump(mode="json"),
                "orders": [o.model_dump(mode="json") for o in orders],
            }

    def get_order(self, order_id: str) -> dict:
        """Return one order plus its refund history and refundable balance."""
        with db.connect(self.db_path) as conn:
            order = db.get_order(conn, order_id)
            if not order:
                raise OrderNotFoundError(order_id)
            refunded = db.total_refunded_for_order(conn, order_id)
            refunds = db.list_refunds_for_order(conn, order_id)
            return {
                "order": order.model_dump(mode="json"),
                "refunded_cents": refunded,
                "refundable_cents": max(order.total_cents - refunded, 0),
                "refunds": [r.model_dump(mode="json") for r in refunds],
            }

    def get_customer_file(self, customer_ref: str) -> dict:
        """Return a customer plus their orders (used by the customer:// resource)."""
        with db.connect(self.db_path) as conn:
            customer = db.get_customer(conn, customer_ref) or db.find_customer_by_email(
                conn, customer_ref
            )
            if not customer:
                return {"customer": None, "note": "No such customer.", "orders": []}
            orders = db.find_orders_by_customer(conn, customer.id)
            return {
                "customer": customer.model_dump(mode="json"),
                "orders": [o.model_dump(mode="json") for o in orders],
            }

    def search_help(self, query: str) -> dict:
        with db.connect(self.db_path) as conn:
            articles = db.search_help_articles(conn, (query or "").strip())
            return {"query": query, "articles": [a.model_dump(mode="json") for a in articles]}

    # ---- write (guarded) -----------------------------------------------------
    def create_refund(
        self,
        order_id: str,
        amount_cents: int,
        reason: str,
        confirm: bool = False,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Attempt a refund, subject to the safety engine.

        Returns a dict whose ``outcome`` is one of:
          * ``"ok"``                 — refund issued (``refund`` included)
          * ``"needs_confirmation"`` — too large; re-issue with confirm=true
          * ``"rejected"``           — a policy rule blocked it (``reason`` explains)

        The whole check-and-insert runs in one ``BEGIN IMMEDIATE`` transaction so
        two concurrent refunds on the same order can't both pass the balance check.
        """
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:  # never compare naive vs aware timestamps
            now = now.replace(tzinfo=timezone.utc)

        reason = (reason or "").strip()
        if not reason:
            return {"outcome": "rejected", "reason": "A non-empty refund reason is required."}
        if len(reason) > MAX_REASON_LEN:
            return {
                "outcome": "rejected",
                "reason": f"Refund reason is too long (max {MAX_REASON_LEN} characters).",
            }

        fingerprint = _fingerprint(order_id, amount_cents, reason)

        with db.transaction(self.db_path) as conn:
            # Idempotency: an identical retry returns the original refund; the SAME
            # key with DIFFERENT parameters is rejected, never silently replayed.
            if idempotency_key:
                existing = db.get_refund_by_idempotency_key(conn, idempotency_key)
                if existing:
                    return self._replay_or_conflict(conn, idempotency_key, fingerprint, existing)

            order = db.get_order(conn, order_id)
            if not order:
                logger.info("refund rejected: order %s not found", order_id)
                return {"outcome": "rejected", "reason": f"Order '{order_id}' not found."}

            already = db.total_refunded_for_order(conn, order_id)
            since = (now - timedelta(hours=self.settings.refund_rate_window_hours)).isoformat()
            recent = db.count_recent_refunds_for_customer(conn, order.customer_id, since)

            decision = evaluate_refund(
                order=order,
                amount_cents=amount_cents,
                already_refunded_cents=already,
                recent_refund_count=recent,
                confirm=confirm,
                settings=self.settings,
            )

            if decision.outcome != "ok":
                logger.info(
                    "refund %s on %s (%s cents): %s",
                    decision.outcome, order.id, amount_cents, decision.reason,
                )
                return {
                    "outcome": decision.outcome,
                    "reason": decision.reason,
                    "max_refundable_cents": decision.max_refundable_cents,
                    "preview": {
                        "order_id": order.id,
                        "item": order.item,
                        "amount_cents": amount_cents,
                        "currency": order.currency,
                    },
                }

            refund = Refund(
                id=f"ref_{uuid.uuid4().hex[:12]}",
                order_id=order.id,
                amount_cents=amount_cents,
                reason=reason,
                status=RefundStatus.SUCCEEDED,
                idempotency_key=idempotency_key,
                created_at=now,
            )
            try:
                db.insert_refund(conn, refund, fingerprint)
            except sqlite3.IntegrityError:
                # Defensive: a concurrent writer inserted this key first. Recover
                # the winner (subject to the same fingerprint check).
                if idempotency_key:
                    winner = db.get_refund_by_idempotency_key(conn, idempotency_key)
                    if winner:
                        return self._replay_or_conflict(conn, idempotency_key, fingerprint, winner)
                raise
            logger.info(
                "refund issued: %s for %s cents on %s", refund.id, amount_cents, order.id
            )
            return {
                "outcome": "ok",
                "reason": "Refund issued.",
                "refund": refund.model_dump(mode="json"),
            }

    # ---- helpers -------------------------------------------------------------
    def _replay_or_conflict(
        self, conn: sqlite3.Connection, key: str, fingerprint: str, existing: Refund
    ) -> dict:
        if db.get_idempotency_fingerprint(conn, key) != fingerprint:
            return {
                "outcome": "rejected",
                "reason": (
                    "Idempotency key was already used with different parameters; "
                    "use a new key for a different refund."
                ),
            }
        return {
            "outcome": "ok",
            "idempotent_replay": True,
            "reason": "Existing refund returned for this idempotency key.",
            "refund": existing.model_dump(mode="json"),
        }
