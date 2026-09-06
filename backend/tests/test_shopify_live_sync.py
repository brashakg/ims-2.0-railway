"""Shopify LIVE-PRODUCT SYNC (owner ruling 2026-09-06) -- behavioural tests.

"anytime a product that has already been pushed to shopify, edited or changed
in our ims, it should automatically reflect on shopify. if needed a sync
everyday twice should be done. one before store opening around 9 am and next
at 1 am." + a manual button + a SUPERADMIN settings section.

***** SAFETY-CRITICAL: no Shopify network. ***** Every test runs DARK (writes
gate off) with a `_graphql` spy that EXPLODES if called, or replaces the
engine's push_product outright. The strict in-memory fakes (tests/strict_fakes)
drive the REAL service, router, policy engine and scheduler tick.

Discriminating power (measured by reverting, see the PR): the selection
filter, the 55-minute grace window, the slot lock, the enabled switch, the
slots validator, the per-run ceiling, the run ledger and the RBAC row each
have a test that goes red when that one rule is removed.

No emoji (Windows cp1252). No UTC day faces in fixtures: every clock value is
an explicit tz-aware IST instant.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import policy_engine as pe  # noqa: E402
from api.services import policy_registry as reg  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402
from api.services import shopify_live_sync as ls  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.utils.ist import IST  # noqa: E402
from tests.strict_fakes import StrictDB  # noqa: E402

SUPER = {"user_id": "u-super", "roles": ["SUPERADMIN"]}
ADMIN = {"user_id": "u-admin", "roles": ["ADMIN"]}


def _run(coro):
    return asyncio.run(coro)


def ist(y, mo, d, h, mi):
    """An explicit tz-aware IST instant (never a box-clock face)."""
    return datetime(y, mo, d, h, mi, tzinfo=IST)


class _Audit:
    """Records audit rows; find_many mirrors the repo API the router reads."""

    def __init__(self):
        self.rows = []

    def create(self, data):
        self.rows.append(dict(data))
        return data

    def find_many(self, flt, **_kw):
        return [r for r in self.rows if all(r.get(k) == v for k, v in flt.items())]


class _Cache:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v, ttl=None):
        self.d[k] = v

    def delete(self, k):
        self.d.pop(k, None)


class _Conn:
    is_connected = True

    def __init__(self, db):
        self.db = db


PHOTO = ["https://cdn.example.com/p.jpg"]


def _seed_products(db):
    """One of each: live+dirty (the ONLY row a sync may touch), never-pushed
    dirty (awaiting first publish), live+clean, live+dirty but taken down."""
    db.seed(
        "catalog_products",
        [
            {"id": "P-live", "sku": "S1", "title": "Live edited", "brand": "RB", "images": PHOTO,
             "ecom": {"shopify_product_id": "gid://shopify/Product/1", "locally_modified": True}},
            {"id": "P-new", "sku": "S2", "title": "Never pushed", "brand": "RB", "images": PHOTO,
             "ecom": {"locally_modified": True, "status": "PUBLISHED"}},
            {"id": "P-clean", "sku": "S3", "title": "Live clean", "images": PHOTO,
             "ecom": {"shopify_product_id": "gid://shopify/Product/3"}},
            {"id": "P-down", "sku": "S4", "title": "Taken down", "images": PHOTO,
             "ecom": {"shopify_product_id": "gid://shopify/Product/4", "locally_modified": True,
                      "taken_down_at": "2026-09-01T00:00:00+00:00"}},
        ],
    )


@pytest.fixture
def world(monkeypatch):
    """StrictDB wired everywhere the service looks: dependencies.get_db /
    get_audit_repository (the sweep + audit), policy_engine._coll + cache (the
    settings), and the engine forced DARK with an exploding network spy."""
    from api import dependencies as deps

    db = StrictDB()
    audit = _Audit()
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(db))
    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit)
    monkeypatch.setattr(pe, "cache", _Cache())
    monkeypatch.setattr(pe, "_coll", lambda name="policy_settings": db[name])

    creds = {"shop_url": "x", "access_token": "y", "source": "vault"}
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "resolve_shopify_credentials",
                        lambda db, storefront_id="BV": creds)

    async def _boom(db, query, variables):  # pragma: no cover - must never run
        raise AssertionError("DARK sync must never hit the Shopify network")

    monkeypatch.setattr(shopify_push, "_graphql", _boom)
    return db, audit


# ---------------------------------------------------------------------------
# Selection: already-live + dirty ONLY; never a first publish
# ---------------------------------------------------------------------------


def test_only_live_dirty_products_are_synced_and_gidless_twins_are_counted(world):
    db, audit = world
    _seed_products(db)

    run = _run(ls.sync_live_products(db, trigger="manual", actor="u-super"))

    assert run["selected"] == 1 and run["attempted"] == 1
    assert run["awaiting_first_publish"] == 1  # P-new counted, NOT pushed
    assert run["taken_down_skipped"] == 1  # P-down never resurrected
    assert run["mode"] == "SIMULATED" and run["pushed_ok"] == 1 and run["failed"] == 0
    pushed_ids = [r["entity_id"] for r in audit.find_many({"action": "ONLINE_STORE_PUSH"})]
    assert pushed_ids == ["P-live"]
    # The actor is on every audit row (a scheduled run stamps the scheduler id).
    assert {r["user_id"] for r in audit.rows} == {"u-super"}
    # Dark => a SIMULATED plan; the boom spy proves zero network.
    assert audit.rows[0]["details"]["mode"] == "SIMULATED"


def test_scheduled_run_stamps_the_scheduler_actor(world):
    db, audit = world
    _seed_products(db)
    run = _run(ls.scheduled_tick(ist(2026, 9, 7, 1, 0)))
    assert run["trigger"] == "scheduled" and run["actor"] == ls.SCHEDULER_ACTOR
    assert {r["user_id"] for r in audit.rows} == {ls.SCHEDULER_ACTOR}


# ---------------------------------------------------------------------------
# Schedule: the IST slots, the 55-minute grace window, never twice
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "when, fires",
    [
        (ist(2026, 9, 7, 0, 59), False),  # a minute early: not due
        (ist(2026, 9, 7, 1, 0), True),  # on the dot
        (ist(2026, 9, 7, 1, 54), True),  # missed the :00 tick, still inside grace
        (ist(2026, 9, 7, 1, 55), False),  # grace over: skipped, not run late
        (ist(2026, 9, 7, 9, 0), True),  # the second default slot
        (ist(2026, 9, 7, 13, 0), False),  # any other hour
        (ist(2026, 9, 7, 21, 0), False),  # 21:00 IST is 15:30 UTC, not a slot
    ],
)
def test_default_slots_fire_only_inside_their_ist_window(world, when, fires):
    db, _ = world
    _seed_products(db)
    run = _run(ls.scheduled_tick(when))
    assert (run is not None) is fires
    assert len(db["online_sync_runs"].docs) == (1 if fires else 0)


def test_a_slot_is_never_run_twice_but_the_next_slot_runs(world):
    db, audit = world
    _seed_products(db)
    first = _run(ls.scheduled_tick(ist(2026, 9, 7, 1, 0)))
    assert first is not None and first["slot"] == "2026-09-07 01:00"
    # Same slot, later tick (or another worker): the lock says no.
    assert _run(ls.scheduled_tick(ist(2026, 9, 7, 1, 10))) is None
    assert _run(ls.scheduled_tick(ist(2026, 9, 7, 1, 40))) is None
    assert len(db["online_sync_runs"].docs) == 1
    assert len(audit.rows) == 1  # one push, not three
    # The next slot is a different key -> runs.
    second = _run(ls.scheduled_tick(ist(2026, 9, 7, 9, 0)))
    assert second is not None and second["slot"] == "2026-09-07 09:00"
    assert len(db["online_sync_runs"].docs) == 2


def test_the_lock_is_the_unique_lock_key(world):
    """Two claims for one slot share ONE lock_key, which the sparse unique index
    on online_sync_runs.lock_key makes atomic on real Mongo."""
    db, _ = world
    first = ls._open_run(db, trigger="scheduled", actor="s", slot="2026-09-07 01:00")
    assert first["lock_key"] == "shopify-live-sync:2026-09-07 01:00"
    assert ls._open_run(db, trigger="scheduled", actor="s", slot="2026-09-07 01:00") is None
    # Manual presses never lock: each is its own run.
    assert ls._open_run(db, trigger="manual", actor="u", slot=ls.MANUAL_SLOT) is not None
    assert ls._open_run(db, trigger="manual", actor="u", slot=ls.MANUAL_SLOT) is not None


def test_disabled_setting_stops_the_schedule_but_not_the_button(world):
    db, _ = world
    _seed_products(db)
    pe.set_policy(reg.LIVE_SYNC_ENABLED_KEY, False, {}, actor=SUPER)
    assert _run(ls.scheduled_tick(ist(2026, 9, 7, 1, 0))) is None
    assert _run(ls.scheduled_tick(ist(2026, 9, 7, 9, 0))) is None
    assert db["online_sync_runs"].docs == []
    # "a dedicated button to sync manually whenever needed by humans"
    run = _run(ls.sync_live_products(db, trigger="manual", actor="u-super"))
    assert run["selected"] == 1
    assert ls.status_block(db)["next_slot"] is None  # nothing scheduled while off


def test_custom_slots_take_effect_without_a_restart(world):
    db, _ = world
    _seed_products(db)
    pe.set_policy(reg.LIVE_SYNC_SLOTS_KEY, ["13:30"], {}, actor=SUPER)
    assert _run(ls.scheduled_tick(ist(2026, 9, 7, 1, 0))) is None  # old slot gone
    run = _run(ls.scheduled_tick(ist(2026, 9, 7, 13, 30)))
    assert run is not None and run["slot"] == "2026-09-07 13:30"


def test_next_slot_is_reported_in_ist_and_rolls_over_midnight():
    slots = ["01:00", "09:00"]
    nxt = ls.next_slot(slots, ist(2026, 9, 7, 9, 0))  # exactly at 09:00 -> tomorrow 01:00
    assert nxt["slot"] == "2026-09-08 01:00"
    assert nxt["at"] == "2026-09-08T01:00:00+05:30"
    assert nxt["label"].endswith("01:00 IST")
    assert ls.next_slot(slots, ist(2026, 9, 7, 3, 0))["slot"] == "2026-09-07 09:00"
    # A late-evening slot is still due just after midnight (yesterday's face).
    assert ls.due_slot(["23:30"], ist(2026, 9, 8, 0, 10)) == "2026-09-07 23:30"
    # A NAIVE instant is the UTC box clock (BUG-104): 19:30 UTC == 01:00 IST.
    assert ls.due_slot(slots, datetime(2026, 9, 6, 19, 30)) == "2026-09-07 01:00"


# ---------------------------------------------------------------------------
# Settings: SUPERADMIN-only, validated at the write door
# ---------------------------------------------------------------------------


def test_slots_validator_rejects_bad_faces_and_too_many(world):
    with pytest.raises(pe.PolicyError, match="25:00"):
        pe.set_policy(reg.LIVE_SYNC_SLOTS_KEY, ["25:00"], {}, actor=SUPER)
    with pytest.raises(pe.PolicyError, match="1 to 6"):
        pe.set_policy(reg.LIVE_SYNC_SLOTS_KEY, [f"0{i}:00" for i in range(7)], {}, actor=SUPER)
    with pytest.raises(pe.PolicyError):
        pe.set_policy(reg.LIVE_SYNC_SLOTS_KEY, [], {}, actor=SUPER)
    with pytest.raises(pe.PolicyError):
        pe.set_policy(reg.LIVE_SYNC_SLOTS_KEY, ["09:60"], {}, actor=SUPER)
    with pytest.raises(pe.PolicyError):
        pe.set_policy(reg.LIVE_SYNC_SLOTS_KEY, ["nine"], {}, actor=SUPER)
    # Accepted faces are normalised (zero-padded, sorted, deduped).
    out = pe.set_policy(reg.LIVE_SYNC_SLOTS_KEY, ["9:5", "01:00", "01:00"], {}, actor=SUPER)
    assert out["value"] == ["01:00", "09:05"]
    assert ls.live_sync_config()["slots"] == ["01:00", "09:05"]


def test_live_sync_settings_are_superadmin_only_global(world):
    for key, value in ((reg.LIVE_SYNC_ENABLED_KEY, False),
                       (reg.LIVE_SYNC_SLOTS_KEY, ["02:00"]),
                       (reg.LIVE_SYNC_MAX_KEY, 50)):
        with pytest.raises(pe.PolicyError) as exc:
            pe.set_policy(key, value, {}, actor=ADMIN)
        assert exc.value.status == 403
        with pytest.raises(pe.PolicyError):
            pe.set_policy(key, value, {"store_id": "S1"}, actor=SUPER)  # global only
    with pytest.raises(pe.PolicyError):
        pe.set_policy(reg.LIVE_SYNC_MAX_KEY, 0, {}, actor=SUPER)  # minimum 1


def test_max_products_per_run_is_honoured(world):
    db, _ = world
    db.seed(
        "catalog_products",
        [
            {"id": f"P{i}", "sku": f"S{i}", "title": f"L{i}", "images": PHOTO,
             "ecom": {"shopify_product_id": f"gid://shopify/Product/{i}", "locally_modified": True}}
            for i in range(3)
        ],
    )
    pe.set_policy(reg.LIVE_SYNC_MAX_KEY, 2, {}, actor=SUPER)
    run = _run(ls.scheduled_tick(ist(2026, 9, 7, 1, 0)))
    assert run["selected"] == 3 and run["attempted"] == 2
    assert run["limit"] == 2 and run["limit_reached"] is True


# ---------------------------------------------------------------------------
# The run ledger
# ---------------------------------------------------------------------------


def test_run_summary_is_persisted_and_failures_carry_code_and_message(world, monkeypatch):
    db, _ = world
    _seed_products(db)

    async def _fail(db, product, variants, blocked=None):
        return shopify_push.PushResult(
            mode="LIVE", entity="product", action="update", target_id=product["id"],
            ok=False, error="Shopify said no", code="PUBLISH_SCOPE_MISSING",
        )

    monkeypatch.setattr(shopify_push, "push_product", _fail)
    run = _run(ls.scheduled_tick(ist(2026, 9, 7, 1, 0)))

    stored = db["online_sync_runs"].docs
    assert len(stored) == 1
    doc = stored[0]
    for key in ("run_id", "trigger", "actor", "slot", "started_at", "finished_at", "mode",
                "selected", "pushed_ok", "failed", "refused_no_photo",
                "awaiting_first_publish", "failures"):
        assert key in doc, key
    assert doc["status"] == "done" and doc["finished_at"] is not None
    assert doc["failed"] == 1 and doc["pushed_ok"] == 0
    assert doc["failures"] == [{
        "product_id": "P-live", "sku": "S1", "name": "Live edited",
        "code": "PUBLISH_SCOPE_MISSING", "reason": None, "error": "Shopify said no",
    }]
    # What the status endpoint reports: the same run, JSON-ready, plus the next slot.
    block = ls.status_block(db)
    assert block["last_run"]["run_id"] == run["run_id"] == doc["run_id"]
    assert block["last_run"]["started_at"].endswith("+00:00")
    # next_slot is relative to the REAL clock here (the frozen-clock cases
    # live in test_next_slot_...); only its shape is asserted.
    assert block["next_slot"]["slot"].endswith(("01:00", "09:00"))
    assert block["next_slot"]["at"].endswith("+05:30")
    assert block["enabled"] is True and block["slots"] == ["01:00", "09:00"]


def test_idempotent_after_a_clean_run(world, monkeypatch):
    """A LIVE push clears locally_modified in the engine's write-back; a second
    run therefore selects nothing. Modelled by a fake engine that clears it."""
    db, _ = world
    _seed_products(db)

    async def _ok(db, product, variants, blocked=None):
        db["catalog_products"].update_one({"id": product["id"]}, {"$set": {"ecom.locally_modified": False}})
        return shopify_push.PushResult(mode="LIVE", entity="product", action="update",
                                       target_id=product["id"], ok=True)

    monkeypatch.setattr(shopify_push, "push_product", _ok)
    assert _run(ls.sync_live_products(db, trigger="manual", actor="u"))["selected"] == 1
    assert _run(ls.sync_live_products(db, trigger="manual", actor="u"))["selected"] == 0


# ---------------------------------------------------------------------------
# One implementation: the sweep door and the sync share the core
# ---------------------------------------------------------------------------


def test_router_reuses_the_service_helpers_not_copies():
    from api.routers import online_store_push as router

    assert router._write_audit is ls.write_push_audit
    assert router._all_docs is ls.all_docs
    assert router._get_variants_for_product is ls.variants_for_product
    assert router._get_db is ls.connected_db


# ---------------------------------------------------------------------------
# The manual door over HTTP + RBAC
# ---------------------------------------------------------------------------


def _headers(roles):
    from api.routers.auth import create_access_token

    token = create_access_token({
        "user_id": f"test-{roles[0].lower()}", "username": roles[0].lower(),
        "roles": roles, "store_ids": ["BV-TEST-01"], "active_store_id": "BV-TEST-01",
    })
    return {"Authorization": f"Bearer {token}"}


def test_rbac_row_is_admin_superadmin_and_the_write_union_is_unchanged():
    row = rbac.policy_for("POST", "/api/v1/online-store/push/sync-live")
    assert row is not None and set(row["allowed"]) == {"ADMIN", "SUPERADMIN"}
    from api.services.capabilities import capability_for, capability_roles

    assert capability_for("POST", "/api/v1/online-store/push/sync-live") == "online-store:write"
    assert set(capability_roles("online-store:write")) == {
        "ACCOUNTANT", "ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN",
    }


@pytest.mark.parametrize("roles, status", [
    (["SUPERADMIN"], 200),
    (["ADMIN"], 200),
    (["CATALOG_MANAGER"], 403),
    (["DESIGN_MANAGER"], 403),
    (["STORE_MANAGER"], 403),
    (["SALES_STAFF"], 403),
])
def test_manual_route_role_gate(client, world, roles, status):
    db, _ = world
    _seed_products(db)
    r = client.post("/api/v1/online-store/push/sync-live", headers=_headers(roles))
    assert r.status_code == status, r.text
    if status == 200:
        body = r.json()
        assert body["run"]["trigger"] == "manual"
        assert body["run"]["actor"] == f"test-{roles[0].lower()}"
        assert body["run"]["selected"] == 1 and body["run"]["awaiting_first_publish"] == 1
        assert body["live_sync"]["last_run"]["run_id"] == body["run"]["run_id"]
        assert body["live_sync"]["next_slot"]["slot"].endswith(("01:00", "09:00"))
    else:
        assert db["online_sync_runs"].docs == []


def test_status_reports_the_live_sync_block(client, world):
    db, _ = world
    r = client.get("/api/v1/online-store/push/status", headers=_headers(["ADMIN"]))
    assert r.status_code == 200, r.text
    block = r.json()["live_sync"]
    assert block["enabled"] is True and block["slots"] == ["01:00", "09:00"]
    assert block["max_products_per_run"] == 200
    assert block["last_run"] is None and block["next_slot"]["at"].endswith("+05:30")
