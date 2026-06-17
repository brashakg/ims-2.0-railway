"""
SUPERADMIN Activity Log -- organization (legal entity) + store filters
======================================================================
GET /settings/audit-logs lets the operator filter the audit trail by store and
by organization. An org (entity) resolves to the set of its stores' audit
`store_id` values; the query is then constrained to `store_id IN {those}`.

These tests drive the endpoint coroutine directly with fakes (no mongod): a fake
store repo seeds the stores collection and a fake audit repo echoes the filter
it was handed so we can assert exactly how org_id / store_id shape the query.
"""

import asyncio

from api.routers import settings as settings_mod


# A 3-store world across 2 orgs. Audit rows record the human store_code, but we
# resolve BOTH code and id so the filter matches whichever a row wrote.
STORES = [
    {"store_id": "uuid-bok", "store_code": "BV-BOK-01", "entity_id": "ent_bv"},
    {"store_id": "uuid-ran", "store_code": "BV-RAN-02", "entity_id": "ent_bv"},
    {"store_id": "uuid-pun", "store_code": "WZ-PUN-01", "entity_id": "ent_wz"},
]


class _FakeStoreRepo:
    def find_many(self, filt, **kwargs):
        eid = filt.get("entity_id")
        return [s for s in STORES if s.get("entity_id") == eid]


class _EchoAuditRepo:
    """Records the filter it is queried with and returns one matching-ish row."""

    def __init__(self):
        self.last_filter = None

    def find_many(self, filt, sort=None, skip=0, limit=50):
        self.last_filter = filt
        return [{"_id": "x", "log_id": "L1", "user_id": "u1", "action": "VIEW"}]

    def count(self, filt):
        self.last_filter = filt
        return 1


def _run(repo, **params):
    return asyncio.run(
        settings_mod.get_audit_logs(
            current_user={"roles": ["SUPERADMIN"]}, **params
        )
    )


def _patch(monkeypatch, audit_repo, store_repo=None):
    monkeypatch.setattr(settings_mod, "get_audit_repository", lambda: audit_repo)
    monkeypatch.setattr(
        settings_mod, "get_store_repository", lambda: store_repo or _FakeStoreRepo()
    )


# --- (1) org_id resolves to its stores; only those rows -------------------------

def test_org_filter_constrains_to_its_stores(monkeypatch):
    audit = _EchoAuditRepo()
    _patch(monkeypatch, audit)
    _run(audit, org_id="ent_bv")
    clause = audit.last_filter["store_id"]
    assert "$in" in clause
    # Both the code and the uuid for each Better Vision store, nothing else.
    assert set(clause["$in"]) == {
        "uuid-bok",
        "BV-BOK-01",
        "uuid-ran",
        "BV-RAN-02",
    }
    assert "WZ-PUN-01" not in clause["$in"]  # WizOpt store excluded


# --- (2) org with no stores -> empty result set (impossible match, no 500) ------

def test_org_with_no_stores_returns_empty(monkeypatch):
    audit = _EchoAuditRepo()
    _patch(monkeypatch, audit)
    res = _run(audit, org_id="ent_unknown")
    assert audit.last_filter["store_id"] == {"$in": []}
    # An empty $in matches nothing -> the fake still returns its canned row, so
    # assert the FILTER is the empty-set match (the contract); real Mongo yields
    # zero rows for {"$in": []}.
    assert isinstance(res["logs"], list)


def test_org_lookup_failure_is_failsoft_not_500(monkeypatch):
    class _BoomStoreRepo:
        def find_many(self, filt, **kwargs):
            raise RuntimeError("db down")

    audit = _EchoAuditRepo()
    _patch(monkeypatch, audit, store_repo=_BoomStoreRepo())
    # Must not raise; resolves to the empty set.
    _run(audit, org_id="ent_bv")
    assert audit.last_filter["store_id"] == {"$in": []}


# --- (3) explicit store_id still works (and wins over org) ----------------------

def test_explicit_store_id_filters_exactly(monkeypatch):
    audit = _EchoAuditRepo()
    _patch(monkeypatch, audit)
    _run(audit, store_id="BV-BOK-01")
    assert audit.last_filter["store_id"] == "BV-BOK-01"


def test_explicit_store_id_wins_over_org(monkeypatch):
    audit = _EchoAuditRepo()
    _patch(monkeypatch, audit)
    _run(audit, store_id="BV-BOK-01", org_id="ent_wz")
    # Explicit store applied as-is; the org clause is skipped (no $in).
    assert audit.last_filter["store_id"] == "BV-BOK-01"


# --- (4) no store/org filter -> no store_id clause (regression) -----------------

def test_no_store_or_org_filter_has_no_store_clause(monkeypatch):
    audit = _EchoAuditRepo()
    _patch(monkeypatch, audit)
    _run(audit, user_id="u1")
    assert "store_id" not in audit.last_filter
    assert audit.last_filter.get("user_id") == "u1"


# --- helper: _org_store_values fail-soft when no repo ----------------------------

def test_org_store_values_no_repo_returns_empty(monkeypatch):
    monkeypatch.setattr(settings_mod, "get_store_repository", lambda: None)
    assert settings_mod._org_store_values("ent_bv") == []
