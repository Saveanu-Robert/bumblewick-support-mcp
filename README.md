# Bumblewick Support-Desk MCP Server 🕯️

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg?logo=python&logoColor=white)
[![MCP](https://img.shields.io/badge/Model_Context_Protocol-server-6E56CF.svg)](https://modelcontextprotocol.io)

The example project for the **"Give the Robot Hands"** series — a small, safe,
well-tested [Model Context Protocol](https://modelcontextprotocol.io) server for
a fictional online candle shop, **Bumblewick & Co.**

It gives an AI assistant carefully-scoped "hands": it can look up orders, read a
customer's file, search the help centre, and issue refunds — with every
dangerous action fenced in by server-side rules. No real people, no real money.

> Everything runs locally against a seeded SQLite database. Nothing leaves your machine.

## What it exposes

**Tools**
- `find_orders(query)` — look up orders by order id, customer id, or email *(read-only)*
- `get_order(order_id)` — one order, its refund history, and the refundable balance *(read-only)*
- `search_help(query)` — search the help centre *(read-only)*
- `create_refund(order_id, amount_cents, reason, confirm=false, idempotency_key=None)` — issue a refund *(guarded write)*

**Resources**
- `bumblewick://policy/refunds` — the refund policy, as context
- `bumblewick://customer/{customer_ref}` — a customer file (profile + orders)

**Prompts**
- `draft_apology(order_id, tone="warm")` — a reusable support-reply template

## The safety rules (a.k.a. "don't give the robot a chainsaw")

`create_refund` refuses to misbehave, server-side:

1. Only **shipped/delivered** orders are refundable.
2. Never more than the **remaining refundable balance** (no over-refunds).
3. Amounts over the **auto-approve limit** return `needs_confirmation` and do
   **not** execute until you re-call with `confirm=true` (human-in-the-loop).
4. A per-customer **rate limit** caps refunds within a rolling window.
5. An **idempotency key** makes retries safe — the same key returns the original
   refund, and reusing a key with *different* parameters is rejected (never a
   silent wrong-refund).
6. The check-and-write runs in one **atomic transaction** (`BEGIN IMMEDIATE`), so
   two concurrent refunds can't both slip past the balance check.
7. A non-empty **reason** is required, and every decision is written to an
   **audit log on stderr** (stdout is reserved for the JSON-RPC stream).

## Quick start

```bash
# 0. Clone
git clone https://github.com/Saveanu-Robert/bumblewick-support-mcp.git
cd bumblewick-support-mcp

# 1. Install (any of: pip, uv, poetry)
pip install -e ".[dev]"

# 2. Run the tests
pytest

# 3. Explore it in the MCP Inspector (opens a GUI to poke every tool)
mcp dev src/bumblewick_support/server.py

# 4. Or run it over stdio (for a client like Claude Desktop)
python -m bumblewick_support.server
```

First run seeds `bumblewick.db` with sample customers, candle orders, and help
articles. Delete the file to reset.

### Connect it to Claude Desktop

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "bumblewick": {
      "command": "python",
      "args": ["-m", "bumblewick_support.server"]
    }
  }
}
```

Then ask: *"Find Ivy's orders and refund the Vanilla Doom candle."* Watch it use
`find_orders`, `get_order`, and `create_refund` — and watch it get politely
stopped when it tries to refund something it shouldn't.

## Configuration

All settings are environment variables (prefix `BUMBLEWICK_`); see `.env.example`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BUMBLEWICK_DB_PATH` | `bumblewick.db` | SQLite file path |
| `BUMBLEWICK_AUTO_REFUND_LIMIT_CENTS` | `2000` | auto-approve ceiling; above needs `confirm=true` |
| `BUMBLEWICK_REFUND_RATE_LIMIT` | `3` | max refunds per customer per window |
| `BUMBLEWICK_REFUND_RATE_WINDOW_HOURS` | `24` | rate-limit window |

## Project layout

```
src/bumblewick_support/
  server.py     # thin MCP layer: tools, resources, prompts
  services.py   # SupportDesk — all business logic (testable, no MCP)
  safety.py     # pure refund-policy engine
  db.py         # SQLite data access
  models.py     # pydantic domain models
  seed.py       # deterministic sample data
  config.py     # env-driven settings
tests/          # pytest: safety, services, and server smoke tests
```

The golden rule the series teaches: **keep the business logic out of the MCP
layer.** `server.py` only wires things up; everything worth testing lives in
`services.py` and `safety.py`, so it's testable without a client.

## License

Released under the [MIT License](LICENSE).
