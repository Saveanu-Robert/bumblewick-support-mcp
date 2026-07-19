"""SQLite data-access layer.

Small, boring, and deliberately separate from the MCP server so it can be tested
without a client. Money is stored in integer cents; timestamps are ISO-8601 UTC
strings. Writes that must be atomic use :func:`transaction` (``BEGIN IMMEDIATE``)
so concurrent refunds can't race past the balance check.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from .models import Customer, HelpArticle, Order, Refund

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    is_vip      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id           TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(id),
    item         TEXT NOT NULL,
    status       TEXT NOT NULL,
    total_cents  INTEGER NOT NULL,
    currency     TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refunds (
    id                   TEXT PRIMARY KEY,
    order_id             TEXT NOT NULL REFERENCES orders(id),
    amount_cents         INTEGER NOT NULL,
    reason               TEXT NOT NULL,
    status               TEXT NOT NULL,
    idempotency_key      TEXT UNIQUE,
    request_fingerprint  TEXT,
    created_at           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS help_articles (
    id     TEXT PRIMARY KEY,
    title  TEXT NOT NULL,
    body   TEXT NOT NULL,
    tags   TEXT NOT NULL DEFAULT ''
);
"""

_BUSY_TIMEOUT_MS = 5000


def _prepare(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS};")


@contextmanager
def connect(path: str) -> Iterator[sqlite3.Connection]:
    """Open a connection for reads (commit on success, always close)."""
    conn = sqlite3.connect(path)
    _prepare(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def transaction(path: str) -> Iterator[sqlite3.Connection]:
    """Open a serialized write transaction (``BEGIN IMMEDIATE``).

    Taking the reserved lock up front means a concurrent refund on the same
    database blocks (up to the busy timeout) instead of interleaving with our
    balance/rate-limit reads — closing the check-then-insert race.
    """
    conn = sqlite3.connect(path, isolation_level=None)  # manual transaction control
    try:
        _prepare(conn)
        conn.execute("BEGIN IMMEDIATE;")
        try:
            yield conn
            conn.execute("COMMIT;")
        except BaseException:
            conn.execute("ROLLBACK;")
            raise
    finally:
        # runs even if _prepare / BEGIN IMMEDIATE (e.g. lock timeout) raised
        conn.close()


def init_db(path: str) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


# ---- row -> model helpers ----------------------------------------------------
def _customer(row: sqlite3.Row) -> Customer:
    return Customer(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        is_vip=bool(row["is_vip"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _order(row: sqlite3.Row) -> Order:
    return Order(
        id=row["id"],
        customer_id=row["customer_id"],
        item=row["item"],
        status=row["status"],
        total_cents=row["total_cents"],
        currency=row["currency"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _refund(row: sqlite3.Row) -> Refund:
    return Refund(
        id=row["id"],
        order_id=row["order_id"],
        amount_cents=row["amount_cents"],
        reason=row["reason"],
        status=row["status"],
        idempotency_key=row["idempotency_key"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _help_article(row: sqlite3.Row) -> HelpArticle:
    tags = [t for t in (row["tags"] or "").split(",") if t]
    return HelpArticle(id=row["id"], title=row["title"], body=row["body"], tags=tags)


# ---- reads -------------------------------------------------------------------
def get_customer(conn: sqlite3.Connection, customer_id: str) -> Customer | None:
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return _customer(row) if row else None


def find_customer_by_email(conn: sqlite3.Connection, email: str) -> Customer | None:
    row = conn.execute(
        "SELECT * FROM customers WHERE email = ? COLLATE NOCASE", (email,)
    ).fetchone()
    return _customer(row) if row else None


def get_order(conn: sqlite3.Connection, order_id: str) -> Order | None:
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return _order(row) if row else None


def find_orders_by_customer(conn: sqlite3.Connection, customer_id: str) -> list[Order]:
    rows = conn.execute(
        "SELECT * FROM orders WHERE customer_id = ? ORDER BY created_at DESC",
        (customer_id,),
    ).fetchall()
    return [_order(r) for r in rows]


def list_refunds_for_order(conn: sqlite3.Connection, order_id: str) -> list[Refund]:
    rows = conn.execute(
        "SELECT * FROM refunds WHERE order_id = ? ORDER BY created_at", (order_id,)
    ).fetchall()
    return [_refund(r) for r in rows]


def total_refunded_for_order(conn: sqlite3.Connection, order_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM refunds WHERE order_id = ?",
        (order_id,),
    ).fetchone()
    return int(row["total"])


def count_recent_refunds_for_customer(
    conn: sqlite3.Connection, customer_id: str, since_iso: str
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM refunds r
        JOIN orders o ON o.id = r.order_id
        WHERE o.customer_id = ? AND r.created_at >= ?
        """,
        (customer_id, since_iso),
    ).fetchone()
    return int(row["n"])


def get_refund_by_idempotency_key(conn: sqlite3.Connection, key: str) -> Refund | None:
    row = conn.execute(
        "SELECT * FROM refunds WHERE idempotency_key = ?", (key,)
    ).fetchone()
    return _refund(row) if row else None


def get_idempotency_fingerprint(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT request_fingerprint FROM refunds WHERE idempotency_key = ?", (key,)
    ).fetchone()
    return row["request_fingerprint"] if row else None


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards so user input can't turn into a match-everything query."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_help_articles(
    conn: sqlite3.Connection, query: str, limit: int = 5
) -> list[HelpArticle]:
    like = f"%{_escape_like(query)}%"
    rows = conn.execute(
        """
        SELECT * FROM help_articles
        WHERE title LIKE ? ESCAPE '\\'
           OR body  LIKE ? ESCAPE '\\'
           OR tags  LIKE ? ESCAPE '\\'
        LIMIT ?
        """,
        (like, like, like, limit),
    ).fetchall()
    return [_help_article(r) for r in rows]


# ---- writes ------------------------------------------------------------------
def insert_refund(
    conn: sqlite3.Connection, refund: Refund, request_fingerprint: str | None = None
) -> None:
    conn.execute(
        """
        INSERT INTO refunds
            (id, order_id, amount_cents, reason, status, idempotency_key,
             request_fingerprint, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            refund.id,
            refund.order_id,
            refund.amount_cents,
            refund.reason,
            refund.status.value,
            refund.idempotency_key,
            request_fingerprint,
            refund.created_at.isoformat(),
        ),
    )
