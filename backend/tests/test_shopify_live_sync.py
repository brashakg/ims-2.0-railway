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

from api.services import catalog_dictionary  # noqa: E402
from api.services import policy_engine as pe  # noqa: E402
from api.services import policy_registry as reg  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402
from api.services import shopify_live_sync as ls  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.utils.ist import IST  # noqa: E402
from tests.strict_fakes import StrictDB  # noqa: E402

# The REAL brand gate, captured before the module-wide fixture stubs it, for
# the first-publish test that seeds real Brand Master rows.
_REAL_BRAND_REFUSAL = catalog_dictionary.brand_website_refusal

# The push's other mechanics, for a brand that IS for the website (owner D6;
# the brand rule has its own test in test_add_product_owner_rulings.py).
pytestmark = pytest.mark.usefixtures("brand_is_for_the_website")

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
            {"id": "P-live", "sku": "S1", "title": "Live edited", "brand": "RB", "mrp": 1000, "images": PHOTO,
             "ecom": {"shopify_product_id": "gid://shopify/Product/1", "locally_modified": True}},
            {"id": "P-new", "sku": "S2", "title": "Never pushed", "brand": "RB", "mrp": 1000, "images": PHOTO,
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
# Selection: dirty minus taken-down; live re-pushes, then first publishes
# ---------------------------------------------------------------------------


def test_dirty_products_are_synced_and_dark_first_publish_stays_queued(world):
    db, audit = world
    _seed_products(db)

    run = _run(ls.sync_live_products(db, trigger="manual", actor="u-super"))

    assert run["selected"] == 1 and run["attempted"] == 2
    # P-new goes through the first-publish pass (owner 2026-10-20) -- DARK, so
    # a SIMULATED plan: nothing went live, it is still awaiting its publish.
    assert run["first_publish_queued"] == 1 and run["first_published"] == 1
    assert run["awaiting_first_publish"] == 1
    assert run["taken_down_skipped"] == 1  # P-down never resurrected
    assert run["mode"] == "SIMULATED" and run["pushed_ok"] == 1 and run["failed"] == 0
    pushed_ids = [r["entity_id"] for r in audit.find_many({"action": "ONLINE_STORE_PUSH"})]
    assert pushed_ids == ["P-live", "P-new"]
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
    assert len(audit.rows) == 2  # one run (P-live + P-new), not three
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
    assert doc["failed"] == 2 and doc["pushed_ok"] == 0 and doc["first_published"] == 0
    assert doc["failures"] == [{
        "product_id": "P-live", "sku": "S1", "name": "Live edited",
        "code": "PUBLISH_SCOPE_MISSING", "reason": None, "error": "Shopify said no",
    }, {
        "product_id": "P-new", "sku": "S2", "name": "Never pushed",
        "code": "PUBLISH_SCOPE_MISSING", "reason": None, "error": "Shopify said no",
    }]
    assert doc["awaiting_first_publish"] == 1  # a failed first publish stays queued
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
# FIRST PUBLISH by the scheduled sync (owner ruling 2026-10-20: "SHOULD BE
# PUSHED BY AUTOMATIC SYNC TOO")
# ---------------------------------------------------------------------------


def _new_row(i, **extra):
    return {"id": f"N{i}", "sku": f"NS{i}", "title": f"New {i}", "brand": "RB", "mrp": 1000,
            "images": PHOTO, "ecom": {"locally_modified": True}, **extra}


@pytest.fixture
def live_engine(monkeypatch):
    """A LIVE engine stand-in: records each product it is handed and writes
    back what a real LIVE push does (gid on a create, dirty flag cleared)."""
    calls = []

    async def _push(db, product, variants, blocked=None):
        ecom = product.get("ecom") or {}
        calls.append((product["id"], "update" if ecom.get("shopify_product_id") else "create"))
        db["catalog_products"].update_one({"id": product["id"]}, {"$set": {
            "ecom.shopify_product_id": ecom.get("shopify_product_id") or f"gid://shopify/Product/{product['id']}",
            "ecom.locally_modified": False,
        }})
        return shopify_push.PushResult(mode="LIVE", entity="product", action=calls[-1][1],
                                       target_id=product["id"], ok=True)

    monkeypatch.setattr(shopify_push, "push_product", _push)
    return calls


def test_scheduled_sync_first_publishes_a_queued_product_and_still_syncs_live(world, live_engine):
    db, audit = world
    _seed_products(db)

    run = _run(ls.scheduled_tick(ist(2026, 10, 21, 1, 0)))

    # The already-live product still syncs as before (an update) ...
    assert ("P-live", "update") in live_engine and run["pushed_ok"] == 1 and run["selected"] == 1
    # ... and the never-published one is CREATED by the unattended run.
    assert ("P-new", "create") in live_engine
    assert run["first_published"] == 1 and run["awaiting_first_publish"] == 0
    new = db["catalog_products"].find_one({"id": "P-new"})
    assert new["ecom"]["shopify_product_id"] and new["ecom"]["locally_modified"] is False
    # Taken down by hand: never re-listed, not even by the first-publish pass.
    assert "P-down" not in {c[0] for c in live_engine}
    assert {r["user_id"] for r in audit.rows} == {ls.SCHEDULER_ACTOR}


def test_always_refused_never_sell_take_downs_never_starve_the_other_passes(world, live_engine, monkeypatch):
    """Review of #1252 (the reviewer's probe): limit 3, three Never-sell
    listings Shopify always refuses (deleted there by hand: "Product does not
    exist") plus a dirty live listing and a queued first publish. The sweep
    has its own cap, outside the run limit, so the re-push and the first
    publish go out on the FIRST run; the three are retried and named, once
    each, on every run."""
    db, _ = world
    db.seed("brand_masters", [
        {"brand_id": "b-no", "name": "NoWeb", "is_active": True, "never_sell_online": True},
    ])
    db.seed("catalog_products", [
        {"id": f"G{i}", "sku": f"GS{i}", "title": "Gone", "brand": "NoWeb", "mrp": 1000, "images": PHOTO,
         "ecom": {"shopify_product_id": f"gid://shopify/Product/G{i}"}}
        for i in range(3)
    ] + [
        {"id": "P-live", "sku": "S1", "title": "Live edited", "brand": "RB", "mrp": 1000, "images": PHOTO,
         "ecom": {"shopify_product_id": "gid://shopify/Product/1", "locally_modified": True}},
        _new_row(1),
    ])
    attempts = []

    async def _refused(_db, product):
        attempts.append(product["id"])
        return shopify_push.PushResult(mode="LIVE", entity="product", action="update",
                                       target_id=product["id"], ok=False,
                                       error="Product does not exist")

    monkeypatch.setattr(shopify_push, "push_product_delist", _refused)
    for n in (1, 2, 3):
        run = _run(ls.sync_live_products(db, trigger="manual", actor="u", limit=3))
        assert len(attempts) == 3 * n, "every listing retried each run, nothing more"
        named = [f for f in run["failures"] if f["code"] == ls.NEVER_SELL_STILL_LIVE]
        assert sorted(f["sku"] for f in named) == ["GS0", "GS1", "GS2"]
        assert all("take-down failed" in f["error"] for f in named)
        assert run["never_sell_taken_down"] == 0
    # Both other passes made progress on the first run, despite the refusals.
    assert sorted(live_engine) == [("N1", "create"), ("P-live", "update")]


def test_a_refused_product_is_not_first_published_and_is_named(world, live_engine, monkeypatch):
    """The REAL product gate: a Never-sell-online brand and an unfinished
    (archived) row are refused, stay queued, and ride the run's failures."""
    db, _ = world
    monkeypatch.setattr(catalog_dictionary, "brand_website_refusal", _REAL_BRAND_REFUSAL)
    db.seed("brand_masters", [
        {"brand_id": "b-ok", "name": "RB", "is_active": True, "sync_to_shopify_default": True},
        {"brand_id": "b-no", "name": "NoWeb", "is_active": True, "sync_to_shopify_default": True,
         "never_sell_online": True},
    ])
    db.seed("catalog_products", [
        _new_row(1),
        _new_row(2, brand="NoWeb"),
        _new_row(3, deleted_at="2026-10-01T00:00:00+00:00"),
    ])

    run = _run(ls.scheduled_tick(ist(2026, 10, 21, 9, 0)))

    assert [c[0] for c in live_engine] == ["N1"]
    assert run["first_published"] == 1 and run["first_publish_refused"] == 2
    assert run["awaiting_first_publish"] == 2 and run["first_publish_not_checked"] == 0
    refused = {f["product_id"]: f for f in run["failures"] if f["code"] == ls.FIRST_PUBLISH_REFUSED}
    assert set(refused) == {"N2", "N3"}
    assert "has not allowed online selling" in refused["N2"]["reason"]
    assert "archived" in refused["N3"]["reason"]
    for pid in ("N2", "N3"):
        row = db["catalog_products"].find_one({"id": pid})
        assert row["ecom"]["locally_modified"] is True and not row["ecom"].get("shopify_product_id")


def test_the_scheduled_first_publish_cap_is_100_and_the_rest_stay_queued(world, live_engine):
    """Owner 2026-10-21: the background 01:00 / 09:00 run first-publishes up
    to 100 (the ~433 backlog clears in ~4 syncs); the rest wait for the next."""
    db, _ = world
    cap = ls.FIRST_PUBLISH_CAP_PER_SYNC
    assert cap == 100
    db.seed("catalog_products", [_new_row(i) for i in range(cap + 3)])

    run = _run(ls.scheduled_tick(ist(2026, 10, 21, 1, 0)))

    assert len(live_engine) == cap and run["first_published"] == cap
    assert run["first_publish_cap"] == cap and run["first_publish_cap_reached"] is True
    assert run["awaiting_first_publish"] == 3
    assert run["first_publish_not_checked"] == 3 and run["first_publish_refused"] == 0
    left = [d for d in db["catalog_products"].docs if not d["ecom"].get("shopify_product_id")]
    assert len(left) == 3 and all(d["ecom"]["locally_modified"] for d in left)
    # The next slot continues where this one stopped.
    nxt = _run(ls.scheduled_tick(ist(2026, 10, 21, 9, 0)))
    assert nxt["first_published"] == 3 and nxt["awaiting_first_publish"] == 0


def test_the_manual_sync_button_runs_in_the_request_so_keeps_the_press_cap(
    client, world, live_engine
):
    """POST /sync-live awaits the sync INSIDE the HTTP request, so it keeps
    the Push-all cap (25), never the scheduler's 100."""
    db, _ = world
    db.seed("catalog_products", [_new_row(i) for i in range(ls.PRODUCT_BATCH_CAP + 3)])
    r = client.post("/api/v1/online-store/push/sync-live", headers=_headers(["ADMIN"]))
    assert r.status_code == 200, r.text
    run = r.json()["run"]
    assert len(live_engine) == ls.PRODUCT_BATCH_CAP == 25
    assert run["first_published"] == 25 and run["first_publish_cap"] == 25
    assert run["first_publish_cap_reached"] is True and run["awaiting_first_publish"] == 3


@pytest.fixture
def photo_checking_engine(monkeypatch):
    """The engine's own photo refusal (returns before any Shopify call, writes
    nothing back) in front of a LIVE create -- the reviewer's #1251 probe."""
    calls = []

    async def _push(db, product, variants, blocked=None):
        if not product.get("images"):
            return shopify_push.PushResult(mode="BLOCKED", entity="product", action="skip",
                                           target_id=product["id"], ok=False, reason="no_photo")
        calls.append(product["id"])
        db["catalog_products"].update_one({"id": product["id"]}, {"$set": {
            "ecom.shopify_product_id": f"gid://shopify/Product/{product['id']}",
            "ecom.locally_modified": False}})
        return shopify_push.PushResult(mode="LIVE", entity="product", action="create",
                                       target_id=product["id"], ok=True)

    monkeypatch.setattr(shopify_push, "push_product", _push)
    return calls


def test_photo_less_rows_never_starve_the_first_publish(world, photo_checking_engine):
    """210 photo-less never-pushed rows sit at the front of the queue (they
    share queue_order's key and are never written back). They must not use
    up the 200 run ceiling: the 5 photographed products behind them go out."""
    db, _ = world
    photoless = [_new_row(i) for i in range(210)]
    for d in photoless:
        d.pop("images")
    good = [_new_row(1000 + i) for i in range(5)]
    db.seed("catalog_products", photoless + good)

    first = _run(ls.scheduled_tick(ist(2026, 10, 21, 1, 0)))
    second = _run(ls.scheduled_tick(ist(2026, 10, 21, 9, 0)))

    assert sorted(photo_checking_engine) == sorted(d["id"] for d in good)
    assert first["first_published"] == 5 and first["refused_no_photo"] == 210
    assert first["limit_reached"] is False and first["first_publish_not_checked"] == 0
    assert first["awaiting_first_publish"] == 210
    # The next slot: nothing new to publish, the photo-less still waiting.
    assert second["first_published"] == 0 and second["awaiting_first_publish"] == 210


def test_room_smaller_than_cap(world):
    """Reviewer's #1251 probe: a manual run whose limit leaves room for 2
    first publishes (3 live re-pushes take the rest of 5) publishes exactly
    2, says the LIMIT stopped it, and does not claim the per-run cap did."""
    db, _ = world
    live = [_new_row(i) for i in range(3)]
    for d in live:
        d["ecom"]["shopify_product_id"] = f"gid://shopify/Product/{d['id']}"
    db.seed("catalog_products", live + [_new_row(100 + i) for i in range(10)])
    run = _run(ls.sync_live_products(db, trigger="manual", actor="u", limit=5))
    assert run["first_published"] == 2 and run["limit_reached"] is True
    assert run["first_publish_cap_reached"] is False


def test_the_push_all_door_and_the_sync_read_one_cap_and_one_order():
    from api.routers import online_store_push as router

    assert router.PRODUCT_BATCH_CAP is ls.PRODUCT_BATCH_CAP
    assert router._queue_order is ls.queue_order


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
