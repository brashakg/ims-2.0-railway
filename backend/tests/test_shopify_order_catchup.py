"""
Sync audit gap #5 -- the scheduled Shopify pull is the missed-order catch-up.

The Shopify -> IMS order path is webhook-only; a delivery Shopify gave up on
(receiver down, 5xx during a deploy) was never caught up. Now NEXUS's hourly
pull fetches every order CREATED in the window and feeds each one not yet in
IMS through the SAME mapper the webhook path uses (map_shopify_order ->
ingest_shopify_order), gated on shopify_dispatch_mode()=='live'.

Rig: the mapper test's FakeDB + `wired` fixture (the REAL mapper + ingest run
against it, so the order-id dedupe under test is the production one), a faked
Shopify fetch, faked credentials. Nothing here is hollow: every rule has a
test that goes red when that rule alone is reverted (table in the PR body).
"""

from __future__ import annotations

import asyncio
import copy
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_online_order_mapper import _frame_order, wired  # noqa: E402,F401 (fixture)

from agents import nexus_providers as np  # noqa: E402
from api.routers import online_store_orders as oso  # noqa: E402
from api.services import online_order_mapper  # noqa: E402

UPDATED = "2026-09-06T01:00:00Z"


def _raise(exc):
    raise exc


def _pulled(order_id, **over):
    """A Shopify REST order as orders.json returns it (== the webhook body)."""
    o = _frame_order(order_id)
    o["created_at"] = "2026-09-06T00:30:00Z"
    o["updated_at"] = UPDATED
    o.update(over)
    return o


@pytest.fixture
def pull(monkeypatch, wired):
    """The pull, wired: creds resolve, the Shopify fetch is a fake that records
    the window it was asked for, dispatch mode defaults to live."""
    import api.services.shopify_auth as auth

    monkeypatch.setattr(
        auth,
        "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {"shop_url": "bv.myshopify.com", "access_token": "t"},
    )
    state = {"orders": [], "complete": True, "calls": [], "error": None}

    async def fake_fetch(shop_url, token, *, created_at_min):
        state["calls"].append(created_at_min)
        if state["error"] is not None:
            _raise(state["error"])
        return [copy.deepcopy(o) for o in state["orders"]], state["complete"]

    monkeypatch.setattr(np, "_shopify_fetch_orders", fake_fetch)
    monkeypatch.setattr(np, "shopify_dispatch_mode", lambda: "live")

    real_map = online_order_mapper.map_shopify_order
    seen = []

    def spy(payload, db, **kw):
        seen.append(str(payload.get("id")))
        return real_map(payload, db, **kw)

    monkeypatch.setattr(online_order_mapper, "map_shopify_order", spy)

    return {
        "db": wired["db"],
        "orders": wired["orders"],
        "inbox": wired["db"]["webhook_inbox"],
        "state": state,
        "seen": seen,
        "mp": monkeypatch,
        "real_map": real_map,
        "run": lambda: asyncio.run(np.shopify_pull_orders(wired["db"])),
    }


# ---------------------------------------------------------------------------
# Rule: unseen orders are mapped, booked ones are skipped WITHOUT re-mapping
# ---------------------------------------------------------------------------


def test_pull_maps_unseen_and_skips_booked_without_touching_the_mapper(pull):
    booked = pull["real_map"](_pulled(20001), pull["db"], topic="orders/create")
    assert booked["status"] == "created"
    pull["seen"].clear()

    pull["state"]["orders"] = [_pulled(20001), _pulled(20002)]
    res = pull["run"]()

    assert res.ok is True
    p = res.payload
    assert p["fetched"] == 2
    assert p["already_in_ims"] == 1
    assert p["mapped"] == ["20002"]
    assert p["failed"] == [] and p["skipped_dark"] == []
    assert res.items_synced == 1
    # The already-in-IMS skip is real: the mapper never saw the booked order.
    assert "20001" not in pull["seen"]
    assert sorted(d["shopify_order_id"] for d in pull["orders"].docs) == ["20001", "20002"]
    # The pulled booking is a normal ONLINE order with a GST invoice.
    new = pull["orders"].find_one({"shopify_order_id": "20002"})
    assert new["channel"] == "ONLINE" and new["invoice_number"]


# ---------------------------------------------------------------------------
# Rule: idempotency against a REAL webhook that arrives after the pull mapped it
# ---------------------------------------------------------------------------


def test_real_webhook_after_pull_books_nothing_further(pull):
    pull["state"]["orders"] = [_pulled(20003)]
    assert pull["run"]().payload["mapped"] == ["20003"]
    invoices = sorted(d["invoice_number"] for d in pull["orders"].docs)

    # Shopify finally delivers the orders/create webhook for the same order.
    res = pull["real_map"](
        _pulled(20003), pull["db"], webhook_id="real-delivery-1", topic="orders/create"
    )

    assert res["status"] == "duplicate" and res["order_id"]
    assert len(pull["orders"].docs) == 1, "count-once: still exactly one IMS order"
    assert sorted(d["invoice_number"] for d in pull["orders"].docs) == invoices


# ---------------------------------------------------------------------------
# Rule: dark mode records what it WOULD have mapped and maps nothing
# ---------------------------------------------------------------------------


def test_dark_mode_records_would_map_and_books_nothing(pull):
    pull["mp"].setattr(np, "shopify_dispatch_mode", lambda: "off")
    pull["state"]["orders"] = [_pulled(20004)]

    res = pull["run"]()

    assert res.ok is True
    p = res.payload
    assert p["skipped_dark"] == ["20004"] and p["mapped"] == [] and p["failed"] == []
    assert p["watermark_advanced"] is False
    assert pull["orders"].docs == []
    assert pull["seen"] == []
    row = pull["inbox"].find_one({"_id": f"pull:20004:{UPDATED}"})
    assert row["source"] == "shopify_pull" and row["processed"] is True
    assert "not live" in row["skipped_reason"]


# ---------------------------------------------------------------------------
# Rule: per-order failures are isolated, recorded, and surfaced where the
# operator looks (the FAILED queue), with the remap door able to find them
# ---------------------------------------------------------------------------


def test_one_bad_order_never_aborts_the_run_and_lands_in_the_failed_queue(pull):
    real = pull["real_map"]

    def exploding(payload, db, **kw):
        if str(payload.get("id")) == "20005":
            raise RuntimeError("mapper exploded")
        return real(payload, db, **kw)

    pull["mp"].setattr(online_order_mapper, "map_shopify_order", exploding)
    pull["state"]["orders"] = [
        _pulled(20005),
        _pulled(20006),
        _pulled(20007, line_items=[]),  # nothing bookable -> the mapper skips it
    ]

    res = pull["run"]()

    assert res.ok is True, "per-order failures never fail the run"
    p = res.payload
    assert p["failed"] == ["20005", "20007"]
    assert p["mapped"] == ["20006"]
    assert "RuntimeError" in p["failed_reasons"]["20005"]
    assert p["failed_reasons"]["20007"].startswith("skipped:no_line_items")

    # The online-orders page's FAILED queue shows both, with honest reasons,
    # and NOT the one that booked.
    queue = {r["shopify_order_id"]: r for r in oso._unbooked_webhook_rows(pull["db"], search=None)}
    assert set(queue) == {"20005", "20007"}
    assert queue["20005"]["map_status"] == "FAILED"
    assert "RuntimeError" in queue["20005"]["map_error"]

    # The remap door finds the pulled payload for the failed order.
    payload, webhook_id, topic = oso._load_last_shopify_payload(pull["db"], "20005")
    assert payload["id"] == 20005
    assert webhook_id == f"pull:20005:{UPDATED}" and topic == "orders/create"


def test_pulled_row_stores_shopify_body_and_remap_dedupes(pull):
    pull["state"]["orders"] = [_pulled(20010)]
    assert pull["run"]().payload["mapped"] == ["20010"]

    payload, webhook_id, topic = oso._load_last_shopify_payload(pull["db"], "20010")
    # The inbox keeps Shopify's own body, not the mapper's stamped copy.
    assert not [k for k in payload if k.startswith("_ims_")]
    assert payload["line_items"][0]["sku"] == "RB-1234"

    res = pull["real_map"](payload, pull["db"], webhook_id=webhook_id, topic=topic)
    assert res["status"] == "duplicate" and res["order_id"]
    assert len(pull["orders"].docs) == 1


def test_re_pull_refreshes_the_inbox_row_instead_of_duplicating_it(pull):
    pull["state"]["orders"] = [_pulled(20011, line_items=[])]
    pull["run"]()
    pull["run"]()
    rows = [d for d in pull["inbox"].docs if d.get("webhook_id") == f"pull:20011:{UPDATED}"]
    assert len(rows) == 1
    assert rows[0]["handler_error"].startswith("skipped:no_line_items")


# ---------------------------------------------------------------------------
# Rule: the pulled order's FINAL Shopify state lands (the orders/updated step
# a real webhook sequence would have delivered after the create)
# ---------------------------------------------------------------------------


def test_pulled_order_already_cancelled_on_shopify_lands_cancelled(pull):
    pull["state"]["orders"] = [
        _pulled(20009, financial_status="paid", cancelled_at="2026-09-06T00:45:00Z")
    ]
    assert pull["run"]().payload["mapped"] == ["20009"]
    doc = pull["orders"].find_one({"shopify_order_id": "20009"})
    assert doc["status"] == "CANCELLED"
    assert len(pull["orders"].docs) == 1


# ---------------------------------------------------------------------------
# Rule: window = last advanced watermark, never less than the 48 h floor;
# the watermark advances only on a complete, live run
# ---------------------------------------------------------------------------


def _ledger_row(now, hours_ago, ok, advanced):
    return {
        "integration": "shopify",
        "kind": "pull",
        "ok": ok,
        "ran_at": (now - timedelta(hours=hours_ago)).isoformat(),
        "payload": {"watermark_advanced": advanced},
    }


def test_window_reaches_back_to_the_last_advanced_watermark_floored_at_48h(pull):
    now = datetime.now(timezone.utc)
    ledger = pull["db"]["sync_runs"]
    # Newest rows are a failed run and a dark run: neither may count.
    ledger.insert_one(_ledger_row(now, 1, ok=False, advanced=True))
    ledger.insert_one(_ledger_row(now, 2, ok=True, advanced=False))
    ledger.insert_one(_ledger_row(now, 100, ok=True, advanced=True))

    pull["run"]()
    since = datetime.fromisoformat(pull["state"]["calls"][-1])
    assert abs((now - since) - timedelta(hours=100)) < timedelta(minutes=1)

    # A recent advanced watermark never shrinks the window below the floor.
    ledger.insert_one(_ledger_row(now, 0.5, ok=True, advanced=True))
    pull["run"]()
    since = datetime.fromisoformat(pull["state"]["calls"][-1])
    assert abs((now - since) - timedelta(hours=np.SHOPIFY_PULL_FLOOR_HOURS)) < timedelta(minutes=1)


def test_watermark_advances_only_on_a_complete_live_run(pull):
    pull["state"]["orders"] = [_pulled(20008)]
    assert pull["run"]().payload["watermark_advanced"] is True

    pull["state"]["complete"] = False  # Shopify had more pages than the cap
    res = pull["run"]()
    assert res.ok is True and res.payload["watermark_advanced"] is False
    assert "TRUNCATED" in res.notes
    pull["state"]["complete"] = True

    pull["mp"].setattr(np, "shopify_dispatch_mode", lambda: "test")
    assert pull["run"]().payload["watermark_advanced"] is False
    pull["mp"].setattr(np, "shopify_dispatch_mode", lambda: "live")

    pull["state"]["error"] = httpx.TimeoutException("timeout")
    res = pull["run"]()
    assert res.ok is False and res.error == "timeout" and res.payload is None


# ---------------------------------------------------------------------------
# The REST page walker: follows Link rel=next, stops cleanly, reports truncation
# ---------------------------------------------------------------------------


def _page(orders, next_url=None, status=200):
    headers = {"link": f'<{next_url}>; rel="next"'} if next_url else {}
    return httpx.Response(status, json={"orders": orders}, headers=headers,
                          request=httpx.Request("GET", "https://bv.myshopify.com/x"))


def test_fetch_follows_link_next_and_flags_truncation(monkeypatch):
    calls = []

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            calls.append((url, params))
            n = len(calls)
            if n == 1:
                assert params["created_at_min"] == "2026-09-04T00:00:00+00:00"
                assert params["status"] == "any" and params["limit"] == np.SHOPIFY_PULL_PAGE_LIMIT
                return _page([{"id": 1}], next_url="https://bv.myshopify.com/p?page_info=abc")
            assert params is None and "page_info=abc" in url
            return _page([{"id": 2}])

    monkeypatch.setattr(np.httpx, "AsyncClient", _Client)
    orders, complete = asyncio.run(
        np._shopify_fetch_orders("bv.myshopify.com", "t", created_at_min="2026-09-04T00:00:00+00:00")
    )
    assert [o["id"] for o in orders] == [1, 2] and complete is True

    monkeypatch.setattr(np, "SHOPIFY_PULL_MAX_PAGES", 1)
    calls.clear()
    orders, complete = asyncio.run(
        np._shopify_fetch_orders("bv.myshopify.com", "t", created_at_min="2026-09-04T00:00:00+00:00")
    )
    assert [o["id"] for o in orders] == [1] and complete is False


def test_fetch_non_200_is_a_run_failure_not_a_crash(pull):
    pull["state"]["error"] = httpx.HTTPStatusError(
        "status 401: bad token",
        request=httpx.Request("GET", "https://x"),
        response=httpx.Response(401),
    )
    res = pull["run"]()
    assert res.ok is False and "401" in res.error
