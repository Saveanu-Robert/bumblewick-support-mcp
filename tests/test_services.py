from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from bumblewick_support.services import OrderNotFoundError, SupportDesk


def test_find_orders_by_email(desk):
    res = desk.find_orders("ivy@example.com")
    ids = {o["id"] for o in res["orders"]}
    assert {"ord_1001", "ord_1002"} <= ids
    assert res["customer"]["id"] == "cus_ivy"


def test_find_orders_by_order_id(desk):
    res = desk.find_orders("ord_1003")
    assert len(res["orders"]) == 1
    assert res["orders"][0]["id"] == "ord_1003"


def test_find_orders_unknown(desk):
    res = desk.find_orders("nobody@nowhere.test")
    assert res["orders"] == []


def test_get_order_refundable(desk):
    res = desk.get_order("ord_1001")
    assert res["order"]["status"] == "delivered"
    assert res["refundable_cents"] == 2400
    assert res["refunds"] == []


def test_get_order_missing_raises(desk):
    with pytest.raises(OrderNotFoundError):
        desk.get_order("ord_nope")


def test_small_refund_ok_and_balance_drops(desk):
    res = desk.create_refund("ord_1001", 1500, "wax sank")
    assert res["outcome"] == "ok"
    assert res["refund"]["amount_cents"] == 1500
    assert desk.get_order("ord_1001")["refundable_cents"] == 900


def test_refund_over_balance_rejected(desk):
    res = desk.create_refund("ord_1001", 999999, "greedy")
    assert res["outcome"] == "rejected"
    assert "exceeds" in res["reason"]


def test_refund_cancelled_order_rejected(desk):
    res = desk.create_refund("ord_1002", 500, "too late")
    assert res["outcome"] == "rejected"
    assert "not eligible" in res["reason"]


def test_unknown_order_rejected(desk):
    res = desk.create_refund("ord_nope", 500, "ghost")
    assert res["outcome"] == "rejected"
    assert "not found" in res["reason"]


def test_large_refund_needs_confirmation_then_confirms(desk):
    first = desk.create_refund("ord_1004", 9900, "whole cabin returned")
    assert first["outcome"] == "needs_confirmation"
    # Nothing was charged yet.
    assert desk.get_order("ord_1004")["refundable_cents"] == 9900

    confirmed = desk.create_refund("ord_1004", 9900, "whole cabin returned", confirm=True)
    assert confirmed["outcome"] == "ok"
    assert desk.get_order("ord_1004")["refundable_cents"] == 0


def test_idempotent_refund_does_not_double_charge(desk):
    a = desk.create_refund("ord_1001", 500, "retry test", idempotency_key="key-123")
    b = desk.create_refund("ord_1001", 500, "retry test", idempotency_key="key-123")
    assert a["outcome"] == "ok"
    assert b["outcome"] == "ok"
    assert b.get("idempotent_replay") is True
    assert a["refund"]["id"] == b["refund"]["id"]
    # Only one refund actually applied.
    assert desk.get_order("ord_1001")["refunded_cents"] == 500


def test_rate_limit_blocks_fourth_refund(desk):
    for i in range(3):
        res = desk.create_refund("ord_1001", 300, f"partial {i}", idempotency_key=f"rl-{i}")
        assert res["outcome"] == "ok"
    fourth = desk.create_refund("ord_1001", 300, "one too many", idempotency_key="rl-3")
    assert fourth["outcome"] == "rejected"
    assert "rate limit" in fourth["reason"].lower()


def test_idempotency_race_returns_existing(desk, monkeypatch):
    # First refund reserves the idempotency key (identical params → a true replay).
    first = desk.create_refund("ord_1001", 300, "wax sank", idempotency_key="race")
    assert first["outcome"] == "ok"

    # Simulate the race window: make the pre-check miss once so the INSERT hits
    # the UNIQUE constraint and the except-branch has to recover the winner.
    import bumblewick_support.services as svc

    real = svc.db.get_refund_by_idempotency_key
    calls = {"n": 0}

    def flaky(conn, key):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(conn, key)

    monkeypatch.setattr(svc.db, "get_refund_by_idempotency_key", flaky)

    second = desk.create_refund("ord_1001", 300, "wax sank", idempotency_key="race")
    assert second["outcome"] == "ok"
    assert second.get("idempotent_replay") is True
    assert second["refund"]["id"] == first["refund"]["id"]
    assert desk.get_order("ord_1001")["refunded_cents"] == 300  # still only one


def test_idempotency_key_reuse_with_different_params_rejected(desk):
    a = desk.create_refund("ord_1001", 300, "first", idempotency_key="dup")
    assert a["outcome"] == "ok"
    b = desk.create_refund("ord_1001", 500, "different amount", idempotency_key="dup")
    assert b["outcome"] == "rejected"
    assert "different parameters" in b["reason"]
    assert desk.get_order("ord_1001")["refunded_cents"] == 300  # original stands


def test_empty_reason_rejected(desk):
    res = desk.create_refund("ord_1001", 300, "   ")
    assert res["outcome"] == "rejected"
    assert "reason" in res["reason"].lower()


def test_rate_limit_window_expiry(desk):
    t0 = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    for i in range(3):
        r = desk.create_refund("ord_1001", 200, f"old {i}", idempotency_key=f"w{i}", now=t0)
        assert r["outcome"] == "ok"
    # 48h later the three earlier refunds fall outside the 24h window → allowed again.
    later = datetime(2026, 7, 3, 9, 0, tzinfo=timezone.utc)
    r4 = desk.create_refund("ord_1001", 200, "fresh", idempotency_key="w3", now=later)
    assert r4["outcome"] == "ok"


def test_concurrent_refunds_do_not_over_refund(db_path, settings):
    # Two refunds of 2000c race on a 2400c order. Serialized writes must let
    # exactly one through; the other sees the reduced balance and is rejected.
    def attempt(i):
        return SupportDesk(db_path, settings).create_refund(
            "ord_1001", 2000, f"concurrent {i}", idempotency_key=f"c{i}"
        )

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(attempt, range(2)))

    outcomes = sorted(r["outcome"] for r in results)
    assert outcomes == ["ok", "rejected"]
    assert SupportDesk(db_path, settings).get_order("ord_1001")["refunded_cents"] == 2000


def test_find_orders_by_id_has_no_customer_key(desk):
    res = desk.find_orders("ord_1003")
    assert "customer" not in res
    assert res["orders"][0]["id"] == "ord_1003"


def test_search_help_empty_query_is_safe(desk):
    res = desk.search_help("")
    assert isinstance(res["articles"], list)


def test_search_help_wildcard_is_literal(desk):
    # "%" must be treated literally (escaped), not as match-everything.
    assert desk.search_help("%")["articles"] == []


def test_empty_query_is_handled(desk):
    res = desk.find_orders("   ")
    assert res["orders"] == []
    assert "note" in res


def test_naive_datetime_is_normalized(desk):
    from datetime import datetime

    # A naive timestamp must not blow up the rate-limit comparison.
    res = desk.create_refund("ord_1001", 300, "naive now", now=datetime(2026, 7, 1, 12, 0))
    assert res["outcome"] == "ok"


def test_search_help(desk):
    res = desk.search_help("refund")
    titles = {a["title"] for a in res["articles"]}
    assert "Refunds and returns" in titles


def test_customer_file(desk):
    res = desk.get_customer_file("cus_bram")
    assert res["customer"]["name"] == "Bram Kettle"
    assert len(res["orders"]) == 2
