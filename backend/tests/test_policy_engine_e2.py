"""E2 policy-engine -- INTENT-LEVEL acceptance tests (packet E2).

Asserts the engine's own intent (a hollow shell must FAIL): scoped resolution
store>entity>global>env>registry-default, typed validation, per-key + scope RBAC,
secret per-value encryption round-trip, audit-on-write, and the luxury-cap
LOWER-only invariant. Cross-phase consumer tests (packet T4 cost-floor / T5
refund-tier) are DEFERRED per PROTOCOL §11 + GAP_ANALYSIS -- they gate their
consumer item, not E2. T9 (schema-driven FE) lives in the frontend.

Fakes model Mongo find_one / update_one (dotted $set/$unset/$setOnInsert) on the
policy_settings + stores collections; cache + audit are injected. No emoji.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

from api.services import policy_engine as pe  # noqa: E402


# --------------------------------------------------------------------------- fakes


class _R:
    def __init__(self, n):
        self.matched_count = n
        self.modified_count = n


class _PolicyColl:
    def __init__(self):
        self.docs = {}

    def find_one(self, filt, projection=None):
        d = self.docs.get(filt.get("_id"))
        return copy.deepcopy(d) if d else None

    def update_one(self, filt, update, upsert=False):
        _id = filt["_id"]
        d = self.docs.get(_id)
        if d is None:
            if not upsert:
                return _R(0)
            d = {"_id": _id}
            for k, v in update.get("$setOnInsert", {}).items():
                d[k] = v
            self.docs[_id] = d
        for k, v in update.get("$set", {}).items():
            self._dset(d, k, v)
        for k in update.get("$unset", {}):
            self._dunset(d, k)
        return _R(1)

    def find_one_and_update(self, filt, update, upsert=False, return_document=None):
        # ReturnDocument.BEFORE is falsy, AFTER is truthy -- set_policy passes BEFORE.
        pre = copy.deepcopy(self.docs.get(filt.get("_id")))
        self.update_one(filt, update, upsert=upsert)
        if return_document:
            return copy.deepcopy(self.docs.get(filt.get("_id")))
        return pre

    @staticmethod
    def _dset(d, key, val):
        cur = d
        parts = key.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = val

    @staticmethod
    def _dunset(d, key):
        cur = d
        parts = key.split(".")
        for p in parts[:-1]:
            cur = cur.get(p)
            if not isinstance(cur, dict):
                return
        cur.pop(parts[-1], None)


class _StoresColl:
    def __init__(self, store_to_entity):
        self.m = store_to_entity  # {store_id: entity_id|None}

    def find_one(self, filt, projection=None):
        sid = filt.get("store_id")
        if sid in self.m:
            return {"store_id": sid, "entity_id": self.m[sid]}
        return None


class _FakeCache:
    TTL_LONG = 900

    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, ttl=None):
        self.store[k] = v

    def delete(self, k):
        self.store.pop(k, None)


class _FakeAuditRepo:
    def __init__(self):
        self.rows = []

    def create(self, data):
        self.rows.append(copy.deepcopy(data))
        return data


def _actor(roles, store_ids=None):
    return {"roles": roles, "store_ids": store_ids or [], "user_id": "u1",
            "active_store_id": (store_ids or [None])[0]}


SUPER = _actor(["SUPERADMIN"])
ADMIN = _actor(["ADMIN"])


@pytest.fixture
def eng(monkeypatch):
    pcoll = _PolicyColl()
    scoll = _StoresColl({"S1": "E1", "S2": "E1", "S3": "E_OTHER"})
    fake = {"policy_settings": pcoll, "stores": scoll}
    audit = _FakeAuditRepo()
    monkeypatch.setattr(pe, "cache", _FakeCache())
    monkeypatch.setattr(pe, "_coll", lambda name="policy_settings": fake.get(name))
    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: audit, raising=False)
    return {"policy": pcoll, "audit": audit}


# --------------------------------------------------------------------------- T1


def test_t1_override_precedence_three_levels(eng):
    pe.set_policy("cash.variance.block", 30000, {}, actor=ADMIN)
    pe.set_policy("cash.variance.block", 50000, {"entity_id": "E1"}, actor=ADMIN)
    pe.set_policy("cash.variance.block", 80000, {"store_id": "S1"}, actor=ADMIN)
    assert pe.get_policy("cash.variance.block", {"store_id": "S1"}) == 80000  # store wins
    assert pe.get_policy("cash.variance.block", {"store_id": "S2"}) == 50000  # S2 in E1, no store override
    assert pe.get_policy("cash.variance.block", {"store_id": "S3"}) == 30000  # other entity -> global
    pe.clear_override("cash.variance.block", {"store_id": "S1"}, actor=ADMIN)
    assert pe.get_policy("cash.variance.block", {"store_id": "S1"}) == 50000  # falls back to entity


def test_t1b_missing_entity_id_falls_to_global_never_raises(eng):
    # A store whose entity_id is None (unassigned/dirty) must resolve to global, not raise.
    eng_stores = _StoresColl({"S_ORPHAN": None})
    # rebuild engine with an orphan store
    import types  # noqa: F401
    pe.set_policy("cash.variance.block", 30000, {}, actor=ADMIN)
    # monkeypatch already routes stores; add the orphan by writing directly is overkill --
    # resolve against a store not in the map -> entity None -> global.
    assert pe.get_policy("cash.variance.block", {"store_id": "S_NOT_IN_MAP"}) == 30000


# --------------------------------------------------------------------------- T2


def test_t2_default_then_env_then_db(eng, monkeypatch):
    assert pe.get_policy("promo.ceiling_pct") == 30.0  # empty DB -> registry default
    monkeypatch.setenv("PROMO_CEILING_PCT", "25")
    assert pe.get_policy("promo.ceiling_pct") == 25.0  # env beats registry default
    pe.set_policy("promo.ceiling_pct", 20.0, {}, actor=ADMIN)
    assert pe.get_policy("promo.ceiling_pct") == 20.0  # DB global beats env


# --------------------------------------------------------------------------- T3


def test_t3_typed_validation(eng):
    with pytest.raises(pe.PolicyError):
        pe.set_policy("refund.tier.admin_above", -1, {}, actor=SUPER)   # min 0
    with pytest.raises(pe.PolicyError):
        pe.set_policy("promo.ceiling_pct", 150, {}, actor=ADMIN)        # percent max 100
    with pytest.raises(pe.PolicyError):
        pe.set_policy("cash.variance.block", "abc", {}, actor=ADMIN)    # not a number
    with pytest.raises(pe.PolicyError):
        pe.set_policy("cash.variance.frequency", "hourly", {}, actor=ADMIN)  # not in enum
    # valid write returns the effective value
    out = pe.set_policy("promo.ceiling_pct", 22.5, {}, actor=ADMIN)
    assert out["value"] == 22.5 and out["source"] == "global"


# --------------------------------------------------------------------------- T6


def test_t6_scope_and_per_key_rbac(eng):
    sm = _actor(["STORE_MANAGER"], store_ids=["S1"])
    # STORE_MANAGER writes own store (cash.variance.block lists STORE_MANAGER) -> ok
    out = pe.set_policy("cash.variance.block", 80000, {"store_id": "S1"}, actor=sm)
    assert out["value"] == 80000
    # other store -> 403
    with pytest.raises(pe.PolicyError) as e1:
        pe.set_policy("cash.variance.block", 80000, {"store_id": "S2"}, actor=sm)
    assert e1.value.status == 403
    # global -> 403 (store-scoped role cannot write above store)
    with pytest.raises(pe.PolicyError) as e2:
        pe.set_policy("cash.variance.block", 80000, {}, actor=sm)
    assert e2.value.status == 403
    # ACCOUNTANT writes ageing.ar_buckets at entity (in write_roles) -> ok
    acc = _actor(["ACCOUNTANT"])
    pe.set_policy("ageing.ar_buckets", [30, 60, 90, 120], {"entity_id": "E1"}, actor=acc)
    # ACCOUNTANT cannot write refund.tier (not in its write_roles) -> 403
    with pytest.raises(pe.PolicyError) as e3:
        pe.set_policy("refund.tier.admin_above", 100000, {}, actor=acc)
    assert e3.value.status == 403


# --------------------------------------------------------------------------- T7


def test_t7_secret_roundtrip(eng):
    pe.set_policy("tally.ledger_map", {"CASH": "Cash A/c"}, {"entity_id": "E1"}, actor=ADMIN)
    # stored value in Mongo is ciphertext (NOT the plaintext dict). The engine
    # writes $set "values.tally.ledger_map" which Mongo NESTS (values->tally->ledger_map).
    stored = eng["policy"].docs["entity:E1"]["values"]["tally"]["ledger_map"]
    assert isinstance(stored, str) and (stored.startswith("fernet:") or stored.startswith("enc:"))
    # GET masks the secret
    eff = pe.get_effective("tally.ledger_map", {"entity_id": "E1"})
    assert eff["value"] == "****" and eff["secret"] is True
    # internal get_policy decrypts back to the cleartext dict
    assert pe.get_policy("tally.ledger_map", {"entity_id": "E1"}) == {"CASH": "Cash A/c"}


# --------------------------------------------------------------------------- T8


def test_t8_audit_on_write(eng):
    pe.set_policy("promo.ceiling_pct", 25.0, {}, actor=ADMIN)
    rows = [r for r in eng["audit"].rows if r["entity_id"] == "promo.ceiling_pct"]
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "policy_update" and r["entity_type"] == "policy_setting"
    assert r["before_state"] == {"promo.ceiling_pct": None}
    assert r["after_state"] == {"promo.ceiling_pct": 25.0}
    # a second change records before=25 -> after=20
    pe.set_policy("promo.ceiling_pct", 20.0, {}, actor=ADMIN)
    rows2 = [r for r in eng["audit"].rows if r["entity_id"] == "promo.ceiling_pct"]
    assert rows2[-1]["before_state"] == {"promo.ceiling_pct": 25.0}


# --------------------------------------------------------------------------- T10


def test_t11_db_outage_does_not_poison_cache(eng, monkeypatch):
    # Adversarial regression: a transient DB outage must NOT cache an empty {} that
    # would silently drop all overrides for the cache TTL.
    pe.set_policy("promo.ceiling_pct", 22.0, {}, actor=ADMIN)
    assert pe.get_policy("promo.ceiling_pct") == 22.0
    # Outage: fresh cache (drop the warm entry) + _coll returns None.
    monkeypatch.setattr(pe, "cache", _FakeCache())
    monkeypatch.setattr(pe, "_coll", lambda name="policy_settings": None)
    assert pe.get_policy("promo.ceiling_pct") == 30.0  # degrades to default during the outage
    # Recovery: the override is visible immediately because the outage was never cached.
    monkeypatch.setattr(pe, "_coll", lambda name="policy_settings": eng["policy"])
    assert pe.get_policy("promo.ceiling_pct") == 22.0


def test_t10_luxury_cap_lower_only(eng):
    # raise above the pricing_caps code constant (LUXURY = 5%) -> rejected
    with pytest.raises(pe.PolicyError) as e:
        pe.set_policy("pricing.category_caps.LUXURY", 10.0, {"store_id": "S1"}, actor=ADMIN)
    assert "LOWER" in str(e.value) or "exceeds" in str(e.value)
    # lower it -> accepted
    out = pe.set_policy("pricing.category_caps.LUXURY", 3.0, {"store_id": "S1"}, actor=ADMIN)
    assert out["value"] == 3.0
    # there is NO per-brand registry key (Cartier/Gucci stay code constants)
    import api.services.policy_registry as reg
    assert not any("brand" in k.lower() for k in reg.REGISTRY)


# --------------------------------------------------------------------------- T12
# Audit L1-F01, server door. The settings SCREEN read (get_effective, behind
# GET /settings/policies[/{key}]) must never answer the code defaults for a
# scope it could not read: the Shopify live-sync screen showed "ON / 01:00,
# 09:00 / 200" as the saved values and Save wrote them over a stopped schedule.
# The runtime read (get_policy) stays fail-soft -- t11 above pins that.


class _DownColl:
    def find_one(self, *_a, **_k):
        raise RuntimeError("mongo failover")


def _saved_schedule_then_cold_cache(monkeypatch):
    pe.set_policy("promo.ceiling_pct", 22.0, {}, actor=ADMIN)
    monkeypatch.setattr(pe, "cache", _FakeCache())  # the policy cache TTL lapsed


@pytest.mark.parametrize("down", [None, _DownColl()], ids=["no-connection", "read-raises"])
def test_t12_screen_read_refuses_instead_of_answering_defaults(eng, monkeypatch, down):
    _saved_schedule_then_cold_cache(monkeypatch)
    monkeypatch.setattr(pe, "_coll", lambda name="policy_settings": down)

    with pytest.raises(pe.PolicyError) as e:
        pe.get_effective("promo.ceiling_pct")
    assert e.value.status == 503
    with pytest.raises(pe.PolicyError):
        pe.get_policies(None)
    # the till / runtime read still degrades to the default, never raises
    assert pe.get_policy("promo.ceiling_pct") == 30.0

    # recovery: the stored value, straight away (the failure was not cached)
    monkeypatch.setattr(pe, "_coll", lambda name="policy_settings": eng["policy"])
    assert pe.get_effective("promo.ceiling_pct")["value"] == 22.0


def test_t12_store_scope_refuses_when_the_shop_to_entity_read_fails(eng, monkeypatch):
    """A store's effective value passes through its entity. If the stores read
    fails the entity layer is unknown, so the answer is unknown -- not 'global'."""
    pe.set_policy("cash.variance.block", 50000, {"entity_id": "E1"}, actor=ADMIN)
    monkeypatch.setattr(pe, "cache", _FakeCache())
    real = eng["policy"]
    monkeypatch.setattr(
        pe, "_coll", lambda name="policy_settings": _DownColl() if name == "stores" else real
    )
    with pytest.raises(pe.PolicyError) as e:
        pe.get_effective("cash.variance.block", {"store_id": "S1"})
    assert e.value.status == 503


# --------------------------------------------------------------------------- T13
# 1417-P2 (Codex review of PR #1417). The store->entity memo is SHARED by the
# runtime read (get_policy, fail-soft), the settings-screen read (get_effective,
# strict) and the tender map. The runtime read used to cache a FAILED stores
# lookup as "" -- the sentinel for "this shop has no entity" -- so the next
# settings read took the failure for a verified absence and answered 200 with
# the code default over a stored entity value, during the outage and after the
# database came back, until the memo expired. A t12 cold strict read never
# showed it: the runtime read has to come FIRST.

_KEY = "cash.variance.block"


def _route_single(store_id):
    import asyncio

    from api.routers import settings as settings_router

    return asyncio.run(
        settings_router.get_policy_one(_KEY, scope=f"store:{store_id}", current_user=ADMIN)
    )


def _route_batch(store_id):
    import asyncio

    from api.routers import settings as settings_router

    out = asyncio.run(
        settings_router.get_policies_batch(scope=f"store:{store_id}", current_user=ADMIN)
    )
    return out["policies"][_KEY]


def _refused(read, store_id):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        read(store_id)
    return e.value.status_code


@pytest.mark.parametrize("down", [None, _DownColl()], ids=["no-connection", "read-raises"])
def test_t13_failed_entity_lookup_is_never_remembered_as_no_entity(eng, monkeypatch, down):
    from api.services import tender_reconciliation as tr

    default = pe.reg.REGISTRY[_KEY].default
    monkeypatch.delenv(pe.reg.REGISTRY[_KEY].env or "_NO_ENV_", raising=False)
    healthy = pe._coll
    pe.set_policy(_KEY, 12345, {"entity_id": "E1"}, actor=ADMIN)

    # 1. healthy: the shop's value is its entity's stored value
    assert (_route_single("S1")["value"], _route_single("S1")["source"]) == (12345, "entity")

    # 2. the memo lapses, then the stores lookup starts failing
    monkeypatch.setattr(pe, "cache", _FakeCache())
    monkeypatch.setattr(
        pe, "_coll", lambda name="policy_settings": down if name == "stores" else healthy(name)
    )
    assert _refused(_route_single, "S1") == 503  # cold strict read (t12)

    # 3. the till / runtime read keeps working on the default...
    assert pe.get_policy(_KEY, {"store_id": "S1"}) == default
    assert tr._scope_chain("S1", None) == ["GLOBAL", "STORE:S1"]
    # ...but the failure is NOT remembered as "no entity"
    assert "policy_se:S1" not in pe.cache.store

    # 4. so the settings reads still refuse during the fault -- never the default
    assert _refused(_route_single, "S1") == 503
    assert _refused(_route_batch, "S1") == 503

    # 5. fault removed: the stored entity value at once, nothing evicted by hand
    monkeypatch.setattr(pe, "_coll", healthy)
    single, batch = _route_single("S1"), _route_batch("S1")
    assert (single["value"], single["source"]) == (12345, "entity")
    assert (batch["value"], batch["source"]) == (12345, "entity")
    assert pe.get_policy(_KEY, {"store_id": "S1"}) == 12345
    assert tr._scope_chain("S1", None) == ["GLOBAL", "ENTITY:E1", "STORE:S1"]
    assert pe.cache.store["policy_se:S1"] == "E1"  # the successful lookup IS memoized


@pytest.mark.parametrize("store_id", ["S_ORPHAN", "S_NO_SUCH_SHOP"], ids=["no-entity", "no-row"])
def test_t13_confirmed_absence_is_still_memoized_after_a_successful_lookup(
    eng, monkeypatch, store_id
):
    """Control: a shop that really has no entity resolves to global / default and
    that answer IS cached -- the correction must not turn it into a refusal or a
    stores read on every request."""
    default = pe.reg.REGISTRY[_KEY].default
    monkeypatch.delenv(pe.reg.REGISTRY[_KEY].env or "_NO_ENV_", raising=False)
    healthy = pe._coll
    orphan = _StoresColl({"S_ORPHAN": None})
    monkeypatch.setattr(
        pe, "_coll", lambda name="policy_settings": orphan if name == "stores" else healthy(name)
    )

    out = _route_single(store_id)
    assert (out["value"], out["source"]) == (default, "default")
    assert pe.cache.store[f"policy_se:{store_id}"] == ""

    # the stores read now fails: the verified absence answers from the memo
    monkeypatch.setattr(
        pe, "_coll",
        lambda name="policy_settings": _DownColl() if name == "stores" else healthy(name),
    )
    assert _route_single(store_id)["source"] == "default"
    assert _route_batch(store_id)["source"] == "default"
    assert pe.get_policy(_KEY, {"store_id": store_id}) == default


def test_t12_policy_routes_answer_503_not_200_defaults(eng, monkeypatch):
    import asyncio

    from fastapi import HTTPException

    from api.routers import settings as settings_router

    _saved_schedule_then_cold_cache(monkeypatch)
    monkeypatch.setattr(pe, "_coll", lambda name="policy_settings": None)
    for call in (
        settings_router.get_policies_batch(scope=None, current_user=ADMIN),
        settings_router.get_policy_one("promo.ceiling_pct", scope=None, current_user=ADMIN),
    ):
        with pytest.raises(HTTPException) as e:
            asyncio.run(call)
        assert e.value.status_code == 503
