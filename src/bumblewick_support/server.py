"""The MCP server — a thin layer over SupportDesk.

Every tool/resource/prompt here just validates inputs and delegates to the
service layer. Keeping the MCP surface thin is what makes the business logic
testable without a client, and keeps the "give the robot hands" wiring readable.
"""

from __future__ import annotations

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

from .config import get_settings
from .seed import REFUND_POLICY, seed
from .services import OrderNotFoundError, SupportDesk

logger = logging.getLogger(__name__)

settings = get_settings()

# Seed the demo database on first run so the server always has data to serve.
if not os.path.exists(settings.db_path):
    seed(settings.db_path)

desk = SupportDesk(settings.db_path, settings)

mcp = FastMCP("Bumblewick Support Desk")


# ---- tools -------------------------------------------------------------------
@mcp.tool()
def find_orders(query: str) -> dict:
    """Look up a customer's orders.

    Accepts an order id (e.g. "ord_1001"), a customer id (e.g. "cus_ivy"), or a
    customer email. Returns matching orders; read-only.
    """
    try:
        return desk.find_orders(query)
    except Exception:  # never leak internals/paths to the client
        logger.exception("find_orders failed")
        return {"error": "Could not look up orders right now."}


@mcp.tool()
def get_order(order_id: str) -> dict:
    """Get one order with its refund history and remaining refundable balance.

    Read-only. Use this before proposing a refund so you know what's allowed.
    """
    try:
        return desk.get_order(order_id)
    except OrderNotFoundError:
        return {"error": f"Order '{order_id}' not found."}
    except Exception:
        logger.exception("get_order failed")
        return {"error": "Could not fetch the order right now."}


@mcp.tool()
def search_help(query: str) -> dict:
    """Search the Bumblewick help centre for care, shipping, and policy articles."""
    try:
        return desk.search_help(query)
    except Exception:
        logger.exception("search_help failed")
        return {"error": "Could not search help articles right now."}


@mcp.tool()
def create_refund(
    order_id: str,
    amount_cents: int,
    reason: str,
    confirm: bool = False,
    idempotency_key: str | None = None,
) -> dict:
    """Issue a refund on an order. This changes money — handle with care.

    Rules enforced server-side:
      * only shipped/delivered orders, never more than the refundable balance;
      * amounts over the auto-approve limit return outcome="needs_confirmation"
        and do NOT execute until you re-call with confirm=true;
      * a per-customer rate limit applies;
      * pass a stable idempotency_key so retries never double-refund.

    Returns outcome = "ok" | "needs_confirmation" | "rejected".
    """
    try:
        return desk.create_refund(
            order_id=order_id,
            amount_cents=amount_cents,
            reason=reason,
            confirm=confirm,
            idempotency_key=idempotency_key,
        )
    except Exception:
        # Fail closed: an internal error means the transaction rolled back, so no
        # money moved. Report rejected rather than leaking the exception/path.
        logger.exception("create_refund failed")
        return {"outcome": "rejected", "reason": "Internal error; the refund was not applied."}


# ---- resources ---------------------------------------------------------------
@mcp.resource("bumblewick://policy/refunds")
def refund_policy() -> str:
    """The human-readable refund policy (read-only context for the model)."""
    return REFUND_POLICY


@mcp.resource("bumblewick://customer/{customer_ref}")
def customer_file(customer_ref: str) -> str:
    """A customer's file (profile + orders) as readable text, by id or email."""
    data = desk.get_customer_file(customer_ref)
    if not data.get("customer"):
        return f"No customer found for '{customer_ref}'."
    c = data["customer"]
    lines = [
        f"Customer: {c['name']} <{c['email']}>  (id: {c['id']}{', VIP' if c['is_vip'] else ''})",
        "Orders:",
    ]
    for o in data["orders"]:
        # integer-cents formatting (no float) keeps money exact end to end
        dollars, cents = divmod(o["total_cents"], 100)
        lines.append(
            f"  - {o['id']}: {o['item']} — {o['status']} — "
            f"{dollars}.{cents:02d} {o['currency']}"
        )
    if not data["orders"]:
        lines.append("  (none)")
    return "\n".join(lines)


# ---- prompts -----------------------------------------------------------------
@mcp.prompt()
def draft_apology(order_id: str, tone: str = "warm") -> str:
    """A reusable prompt that asks for a short apology + next step for an order."""
    return (
        f"Look up order {order_id} with the get_order tool. Then write a short, "
        f"{tone} reply to the customer that acknowledges the problem, states the "
        "concrete next step (refund, replacement, or ETA), and avoids promising "
        "anything the refund policy doesn't allow. Two short paragraphs, no filler."
    )


def main() -> None:
    """Console-script entry point: run over stdio (for Claude Desktop / Inspector)."""
    # Log to STDERR only — stdout carries the JSON-RPC stream and must stay clean.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    mcp.run()


if __name__ == "__main__":
    main()
