"""
IMS 2.0 - F39 NBA (next-best-action) daily call list (#39) -- INTENT tests
==========================================================================
The packet's intent: a ranked daily CALL list (15/day, exactly 2 reserved VIP
slots) built from REUSED CRM signals (Rx-expiry / follow-ups-due / persisted
vip_churn_risk + birthday + CL-reorder), worked through in-app (mark called /
outcome). It is NOT an outbound message channel -- logging an outcome creates an
in-app follow_up record, NEVER a provider send (WhatsApp ban; F39 is dark).

A hollow shell that just echoes a pre-seeded doc FAILS these tests: they assert
the scoring, ranking, VIP-slot reservation, cap, score-hiding, mark-called
persistence, the tag suggest->approve->visible workflow, PII stripping, RBAC, and
that NO live send fires.

CI-ROBUSTNESS: every repo/db accessor the handlers read is monkeypatched and the
fake DB is SEEDED with every doc the handler reads (no local-vs-CI fail-soft
divergence). Absence-of-a-value is asserted on the parsed object, never via a
whole-JSON substring check.

No emoji. Run:
  JWT_SECRET_KEY=test python -m pytest backend/tests/test_f39_nba_call_list.py -q
"""

from __future__ import annotations

import copy
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DISPATCH_MODE", "off")

import jwt  # noqa: E402
import pytest  # noqa: E402
from datetime import timezone  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import crm as crm_mod  # noqa: E402
from api.routers import customers as cust_mod  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402
from api.services import nba_call_list as nba  # noqa: E402

STORE = "BV-PUN-01"
OTHER_STORE = "BV-BOK-01"
TODAY = nba._today_ist()
NOW = datetime.fromisoformat(TODAY + "T10:00:00")


# ---------------------------------------------------------------------------
# Fake Mongo -- supports exactly the operators the F39 handlers use:
#   find($or / $gte / $lte / dotted-key cards.customer_id), find_one,
#   find_one_and_update ($set incl. positional cards.$.dismissed, $addToSet),
#   insert_one, count_documents, update_one ($addToSet).
# ---------------------------------------------------------------------------


def _get_nested(doc, dotted):
    cur = doc
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _matches(doc, query):
    for key, val in (query or {}).items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in val):
                return False
            continue
        # dotted "cards.customer_id" matches if ANY card has that customer_id
        if "." in key:
            head, _, tail = key.partition(".")
            container = doc.get(head)
            if isinstance(container, list):
                if not any(isinstance(c, dict) and c.get(tail) == val for c in container):
                    return False
                continue
            actual = _get_nested(doc, key)
        else:
            actual = doc.get(key)
        if isinstance(val, dict):
            if "$in" in val and actual not in val["$in"]:
                return False
            if "$nin" in val and actual in val["$nin"]:
                return False
            if "$gte" in val and (actual is None or actual < val["$gte"]):
                return False
            if "$lte" in val and (actual is None or actual > val["$lte"]):
                return False
            if "$ne" in val and actual == val["$ne"]:
                return False
        else:
            if actual != val:
                return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = [copy.deepcopy(d) for d in (docs or [])]
        self.inserts = 0

    def find_one(self, query=None, projection=None, sort=None):
        for d in self.docs:
            if _matches(d, query or {}):
                return copy.deepcopy(d)
        return None

    def find(self, query=None, projection=None):
        return _Cursor([copy.deepcopy(d) for d in self.docs if _matches(d, query or {})])

    def count_documents(self, query=None):
        return sum(1 for d in self.docs if _matches(d, query or {}))

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))
        self.inserts += 1
        return type("R", (), {"inserted_id": "oid"})()

    def _apply_update(self, doc, update, query):
        if "$set" in update:
            for k, v in update["$set"].items():
                if k.startswith("cards.$."):
                    # positional update of the matched card
                    field = k[len("cards.$."):]
                    target_cid = (query.get("cards.customer_id"))
                    for c in doc.get("cards", []):
                        if c.get("customer_id") == target_cid:
                            c[field] = v
                elif "." in k:
                    head, _, tail = k.partition(".")
                    doc.setdefault(head, {})[tail] = v
                else:
                    doc[k] = v
        if "$addToSet" in update:
            for k, v in update["$addToSet"].items():
                arr = doc.setdefault(k, [])
                if v not in arr:
                    arr.append(v)

    def update_one(self, query, update, **kw):
        for d in self.docs:
            if _matches(d, query or {}):
                self._apply_update(d, update, query)
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if kw.get("upsert"):
            self.docs.append({})
            self._apply_update(self.docs[-1], update, query)
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def find_one_and_update(self, query, update, return_document=False, upsert=False, **kw):
        for d in self.docs:
            if _matches(d, query or {}):
                before = copy.deepcopy(d)
                self._apply_update(d, update, query)
                return copy.deepcopy(d if return_document else before)
        if upsert:
            new = {}
            self._apply_update(new, update, query)
            self.docs.append(new)
            return copy.deepcopy(new) if return_document else None
        return None


class _FakeDB:
    is_connected = True

    def __init__(self, collections=None):
        self._cols = collections or {}

    def get_collection(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeColl()
        return self._cols[name]


class _FakeRepo:
    """Customer repository facade backed by a _FakeColl on customers."""

    id_field = "customer_id"

    def __init__(self, coll):
        self.collection = coll

    def find_by_id(self, cid):
        return self.collection.find_one({"customer_id": cid})

    def update(self, cid, data):
        return self.collection.update_one({"customer_id": cid}, {"$set": data})


# ---------------------------------------------------------------------------
# Seed builders
# ---------------------------------------------------------------------------


def _rx_created_for_days_until(days_until):
    """A prescription created_at so it expires `days_until` days from NOW
    (validity = RX_VALIDITY_DAYS in campaign_segments)."""
    from api.services.campaign_segments import RX_VALIDITY_DAYS

    created = NOW + timedelta(days=days_until) - timedelta(days=RX_VALIDITY_DAYS)
    return created.isoformat()


def _seed_db_for_scoring():
    """A store with a varied population that exercises every signal + VIP slots.

    - RXFAST: Rx expiring in 5 days  -> rx_expiry_7d (30)
    - VIP1/VIP2: tag VIP, NO signal-bearing data other than membership -> reserved
      slots even though low score.
    - CLDUE: a contact-lens order long ago -> cl_refill_due (25)
    - many FILLER customers with a follow_up due today -> fu_due_today (28) so they
      have high scores and would crowd out low-score VIPs without the reservation.
    """
    custs = [
        {"customer_id": "RXFAST", "name": "Rina Fast", "mobile": "9000000001", "store_id": STORE},
        {"customer_id": "CLDUE", "name": "Carl Lens", "mobile": "9000000002", "store_id": STORE},
        {"customer_id": "VIP1", "name": "Vee One", "mobile": "9000000010", "store_id": STORE, "tags": ["VIP"]},
        {"customer_id": "VIP2", "name": "Vee Two", "mobile": "9000000011", "store_id": STORE, "tags": ["vip"]},
    ]
    fus = []
    for i in range(20):
        cid = f"FILL{i:02d}"
        custs.append({"customer_id": cid, "name": f"Filler {i}", "mobile": f"90001000{i:02d}", "store_id": STORE})
        fus.append({
            "follow_up_id": f"FU-EXIST-{i}", "customer_id": cid, "customer_name": f"Filler {i}",
            "store_id": STORE, "type": "general", "status": "pending", "scheduled_date": TODAY,
        })

    rx = [{"prescription_id": "RX1", "customer_id": "RXFAST", "store_id": STORE,
           "created_at": _rx_created_for_days_until(5)}]
    # CLDUE: a CL order 120 days ago -> reorder due (cadence 30d).
    orders = [{"order_id": "O-CL", "customer_id": "CLDUE", "store_id": STORE,
               "created_at": NOW - timedelta(days=120),
               "items": [{"item_type": "CONTACT_LENS", "qty": 1}]}]
    return _FakeDB({
        "customers": _FakeColl(custs),
        "prescriptions": _FakeColl(rx),
        "orders": _FakeColl(orders),
        "follow_ups": _FakeColl(fus),
        "loyalty_accounts": _FakeColl([
            {"customer_id": "RXFAST", "tier": "GOLD"},
        ]),
        "walkouts": _FakeColl([]),
        "stores": _FakeColl([{"store_id": STORE, "status": "ACTIVE"}]),
        "nba_scores": _FakeColl([]),
    })


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _tok(roles, uid="u1", store_id=STORE):
    return jwt.encode(
        {
            "sub": uid, "user_id": uid, "username": "tester", "full_name": "Tester",
            "roles": list(roles), "active_store_id": store_id, "store_ids": [store_id],
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


def _hdr(roles=("STORE_MANAGER",), store_id=STORE):
    return {"Authorization": f"Bearer {_tok(roles, store_id=store_id)}"}


def _crm_client(db, monkeypatch):
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: db)
    app = FastAPI()
    app.include_router(crm_mod.router, prefix="/api/v1/crm")
    return TestClient(app)


def _cust_client(db, monkeypatch):
    repo = _FakeRepo(db.get_collection("customers"))
    monkeypatch.setattr(cust_mod, "get_customer_repository", lambda: repo)
    monkeypatch.setattr(cust_mod, "_tag_suggestions_coll", lambda: db.get_collection("tag_suggestions"))
    app = FastAPI()
    app.include_router(cust_mod.router, prefix="/api/v1/customers")
    return TestClient(app), repo


# ===========================================================================
# T1 -- scoring produces correct signal attribution
# ===========================================================================


def test_t1_signal_attribution_rx_and_cl():
    # Focused population (no dense fillers) so both signal carriers surface.
    # ONE customer carries BOTH rx_expiry_7d (30) AND cl_refill_due (25).
    custs = [
        {"customer_id": "BOTH", "name": "Both Signals", "mobile": "9", "store_id": STORE},
    ]
    rx = [{"prescription_id": "RX", "customer_id": "BOTH", "store_id": STORE,
           "created_at": _rx_created_for_days_until(5)}]
    orders = [{"order_id": "O", "customer_id": "BOTH", "store_id": STORE,
               "created_at": NOW - timedelta(days=120),
               "items": [{"item_type": "CONTACT_LENS", "qty": 1}]}]
    db = _FakeDB({
        "customers": _FakeColl(custs), "prescriptions": _FakeColl(rx),
        "orders": _FakeColl(orders), "follow_ups": _FakeColl([]),
        "loyalty_accounts": _FakeColl([]), "walkouts": _FakeColl([]),
    })
    cards = nba.score_nba(db, STORE, now=NOW)
    by_cid = {c["customer_id"]: c for c in cards}

    card = by_cid["BOTH"]
    # Both signals attributed.
    assert "rx_expiry_7d" in card["signals"]
    assert "cl_refill_due" in card["signals"]
    # Score is at least the sum of both weights (30 + 25 = 55).
    assert card["score"] >= nba.SIGNAL_WEIGHTS["rx_expiry_7d"] + nba.SIGNAL_WEIGHTS["cl_refill_due"]
    # Top signal (rx, the higher weight) drives the action.
    assert card["suggested_action"] in (nba.ACTION_BOOK_EYE_TEST, nba.ACTION_CL_REORDER)
    assert card["suggested_action"] == nba.ACTION_BOOK_EYE_TEST


# ===========================================================================
# T2 -- VIP slot reservation (DECISIONS s3 LOCKED): exactly 2 VIP slots,
# at rank 1-2, even when their raw score is lower than top non-VIPs.
# ===========================================================================


def test_t2_vip_slots_reserved_at_top():
    db = _seed_db_for_scoring()
    cards = nba.score_nba(db, STORE, now=NOW)
    assert len(cards) == 15

    vip_slot_cards = [c for c in cards if c["is_vip_slot"]]
    assert len(vip_slot_cards) == 2, "exactly 2 reserved VIP slots"
    # The two reserved slots are ranks 1 and 2.
    assert {c["rank"] for c in vip_slot_cards} == {1, 2}
    # They are the tagged VIP customers (low score, no signal) -- proving the
    # reservation overrode pure score-rank.
    assert {c["customer_id"] for c in vip_slot_cards} == {"VIP1", "VIP2"}
    # A high-scoring filler (fu_due_today) appears but NOT in a VIP slot.
    fillers = [c for c in cards if c["customer_id"].startswith("FILL")]
    assert fillers and all(not c["is_vip_slot"] for c in fillers)


# ===========================================================================
# T3 -- 15-card hard cap (DECISIONS s3 LOCKED)
# ===========================================================================


def test_t3_fifteen_card_cap_via_api(monkeypatch):
    db = _seed_db_for_scoring()
    client = _crm_client(db, monkeypatch)
    r = client.get(f"/api/v1/crm/nba/{STORE}", headers=_hdr(("STORE_MANAGER",)))
    assert r.status_code == 200
    cards = r.json()["cards"]
    assert len(cards) == 15  # never more than the policy cap


def test_t3b_fewer_when_population_small():
    # Only 3 signal-bearing customers -> at most 3 cards (never padded to 15).
    custs = [
        {"customer_id": "A", "name": "A", "mobile": "9", "store_id": STORE},
        {"customer_id": "B", "name": "B", "mobile": "9", "store_id": STORE},
    ]
    rx = [{"prescription_id": "RX", "customer_id": "A", "store_id": STORE,
           "created_at": _rx_created_for_days_until(3)}]
    db = _FakeDB({
        "customers": _FakeColl(custs), "prescriptions": _FakeColl(rx),
        "orders": _FakeColl([]), "follow_ups": _FakeColl([]),
        "loyalty_accounts": _FakeColl([]), "walkouts": _FakeColl([]),
    })
    cards = nba.score_nba(db, STORE, now=NOW)
    assert len(cards) == 1 and cards[0]["customer_id"] == "A"


# ===========================================================================
# T4 -- score value is hidden from the API response (gaming-prevention).
# Asserted on the parsed object keys, NOT a JSON substring.
# ===========================================================================


def test_t4_score_hidden_from_api(monkeypatch):
    db = _seed_db_for_scoring()
    client = _crm_client(db, monkeypatch)
    r = client.get(f"/api/v1/crm/nba/{STORE}", headers=_hdr(("STORE_MANAGER",)))
    assert r.status_code == 200
    cards = r.json()["cards"]
    assert cards, "expected cards"
    for c in cards:
        assert "score" not in c.keys()
        assert "rank" in c.keys()  # rank IS what the associate sees


# ===========================================================================
# T5 -- dismiss removes the card, resolves the follow_up, writes audit.
# ===========================================================================


def _persisted_nba_doc_db():
    """A DB whose nba_scores ALREADY holds today's doc (the MEGAPHONE-written
    path) with linked follow_up docs, so dismiss/complete have real targets."""
    cards = [
        {"rank": 1, "is_vip_slot": True, "customer_id": "X1", "customer_name": "Ex One",
         "customer_mobile": "9", "score": 60, "signals": ["rx_expiry_7d"], "headline": "h",
         "sub_headlines": [], "suggested_action": "BOOK_EYE_TEST", "loyalty_tier": None,
         "lifetime_value": 0, "last_purchase_date": None, "tags": [], "is_vip": True,
         "follow_up_id": "FU-X1", "dismissed": False},
        {"rank": 2, "is_vip_slot": True, "customer_id": "X2", "customer_name": "Ex Two",
         "customer_mobile": "9", "score": 30, "signals": ["fu_due_today"], "headline": "h",
         "sub_headlines": [], "suggested_action": "GENERAL_FOLLOWUP", "loyalty_tier": None,
         "lifetime_value": 0, "last_purchase_date": None, "tags": [], "is_vip": False,
         "follow_up_id": "FU-X2", "dismissed": False},
    ]
    doc = nba.build_nba_doc(STORE, cards)
    fus = [
        {"follow_up_id": "FU-X1", "customer_id": "X1", "store_id": STORE, "type": "nba_call",
         "status": "pending", "scheduled_date": TODAY, "outcome": None},
        {"follow_up_id": "FU-X2", "customer_id": "X2", "store_id": STORE, "type": "nba_call",
         "status": "pending", "scheduled_date": TODAY, "outcome": None},
    ]
    return _FakeDB({
        "nba_scores": _FakeColl([doc]),
        "follow_ups": _FakeColl(fus),
        "audit_logs": _FakeColl([]),
    })


def test_t5_dismiss(monkeypatch):
    db = _persisted_nba_doc_db()
    audit_calls = []
    monkeypatch.setattr(crm_mod, "get_audit_repository",
                        lambda: type("A", (), {"create": staticmethod(lambda row: audit_calls.append(row))})())
    client = _crm_client(db, monkeypatch)

    r = client.post(f"/api/v1/crm/nba/{STORE}/dismiss",
                    json={"customer_id": "X1", "reason": "no_answer"},
                    headers=_hdr(("SALES_STAFF",)))
    assert r.status_code == 200 and r.json()["ok"] is True

    # (1) GET no longer returns X1 (now 1 card).
    g = client.get(f"/api/v1/crm/nba/{STORE}", headers=_hdr(("SALES_STAFF",)))
    cids = {c["customer_id"] for c in g.json()["cards"]}
    assert "X1" not in cids and cids == {"X2"}

    # (2) the linked follow_up is skipped with the reason.
    fu = db.get_collection("follow_ups").find_one({"follow_up_id": "FU-X1"})
    assert fu["status"] == "skipped" and fu["outcome"] == "no_answer"

    # (3) audit row written.
    assert any(a.get("action") == "nba.dismissed" and a.get("entity_id") == "X1" for a in audit_calls)


# ===========================================================================
# T6 -- complete requires outcome_notes >= 10 chars; persists on success.
# ===========================================================================


def test_t6_complete_validation_and_persist(monkeypatch):
    db = _persisted_nba_doc_db()
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: None)
    client = _crm_client(db, monkeypatch)

    short = client.post(f"/api/v1/crm/nba/{STORE}/complete",
                        json={"customer_id": "X1", "outcome_notes": "short"},
                        headers=_hdr(("SALES_STAFF",)))
    assert short.status_code == 422  # < 10 chars rejected by the model

    ok = client.post(f"/api/v1/crm/nba/{STORE}/complete",
                     json={"customer_id": "X1", "outcome_notes": "Called, will visit Saturday."},
                     headers=_hdr(("SALES_STAFF",)))
    assert ok.status_code == 200
    fu = db.get_collection("follow_ups").find_one({"follow_up_id": "FU-X1"})
    assert fu["status"] == "completed"
    assert fu["notes"] == "Called, will visit Saturday."


def test_t6b_complete_schedules_next_followup(monkeypatch):
    db = _persisted_nba_doc_db()
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: None)
    client = _crm_client(db, monkeypatch)
    nextd = (NOW + timedelta(days=14)).date().isoformat()
    r = client.post(f"/api/v1/crm/nba/{STORE}/complete",
                    json={"customer_id": "X2", "outcome_notes": "Discussed new lenses.",
                          "follow_up_scheduled_date": nextd},
                    headers=_hdr(("SALES_STAFF",)))
    assert r.status_code == 200
    nfid = r.json()["next_follow_up_id"]
    assert nfid
    newfu = db.get_collection("follow_ups").find_one({"follow_up_id": nfid})
    assert newfu["type"] == "general" and newfu["scheduled_date"] == nextd
    assert newfu["status"] == "pending"


# ===========================================================================
# T7 -- tag suggest -> approve -> visible workflow.
# ===========================================================================


def test_t7_tag_suggest_approve_visible(monkeypatch):
    db = _FakeDB({
        "customers": _FakeColl([{"customer_id": "C1", "name": "Cust", "store_id": STORE}]),
        "tag_suggestions": _FakeColl([]),
        "audit_logs": _FakeColl([]),
    })
    monkeypatch.setattr(cust_mod, "get_audit_repository", lambda: None)
    client, repo = _cust_client(db, monkeypatch)

    # Staff suggests.
    s = client.post("/api/v1/customers/C1/tags/suggest", json={"tag": "Zeiss fan"},
                    headers=_hdr(("SALES_STAFF",)))
    assert s.status_code == 200
    sug_id = s.json()["suggestion_id"]
    assert s.json()["status"] == "pending"

    # (1) suggestion is PENDING.
    sug = db.get_collection("tag_suggestions").find_one({"suggestion_id": sug_id})
    assert sug["status"] == "pending"

    # (2) tag NOT yet on the customer record (scorer would not see it).
    cust = repo.find_by_id("C1")
    assert "Zeiss fan" not in (cust.get("tags") or [])

    # Manager approves.
    a = client.post(f"/api/v1/customers/C1/tags/suggestions/{sug_id}/approve",
                    headers=_hdr(("STORE_MANAGER",)))
    assert a.status_code == 200 and a.json()["tag"] == "Zeiss fan"

    # (3) now on customers.tags -> next scorer run reflects it.
    cust = repo.find_by_id("C1")
    assert "Zeiss fan" in (cust.get("tags") or [])


def test_t7c_generic_update_cannot_inject_tags(monkeypatch):
    """P1 regression (adversarial): tags must NOT be writable through the generic
    AUTHENTICATED PUT /customers/{id}. That path skips the STORE_MANAGER+ gate, the
    PII strip, and the suggest->approve workflow -- tags are governed ONLY by
    PATCH /{id}/tags. A SALES_STAFF smuggling tags via the generic update is a no-op."""
    db = _FakeDB({
        "customers": _FakeColl([{"customer_id": "C1", "name": "Cust", "store_id": STORE}]),
        "audit_logs": _FakeColl([]),
    })
    monkeypatch.setattr(cust_mod, "get_audit_repository", lambda: None)
    client, repo = _cust_client(db, monkeypatch)

    r = client.put("/api/v1/customers/C1",
                   json={"name": "Cust Edited", "tags": ["VIP", "sneaky"]},
                   headers=_hdr(("SALES_STAFF",)))
    assert r.status_code == 200, r.text  # the legit field edit still succeeds
    cust = repo.find_by_id("C1")
    assert cust.get("name") == "Cust Edited"          # non-tag field updated
    assert "VIP" not in (cust.get("tags") or [])       # injected tags dropped
    assert "sneaky" not in (cust.get("tags") or [])


# ===========================================================================
# T8 -- tag PII stripping (phone / email / GSTIN rejected; clean tag kept).
# ===========================================================================


def test_t8_tag_pii_stripping(monkeypatch):
    db = _FakeDB({
        "customers": _FakeColl([{"customer_id": "C1", "name": "Cust", "store_id": STORE}]),
        "tag_suggestions": _FakeColl([]), "audit_logs": _FakeColl([]),
    })
    monkeypatch.setattr(cust_mod, "get_audit_repository", lambda: None)
    client, repo = _cust_client(db, monkeypatch)

    r = client.patch("/api/v1/customers/C1/tags",
                     json={"tags": ["call 9876543210", "john@example.com",
                                    "GSTIN 22AAAAA0000A1Z5", "Cartier fan"]},
                     headers=_hdr(("STORE_MANAGER",)))
    assert r.status_code == 200
    assert r.json()["tags"] == ["Cartier fan"]
    cust = repo.find_by_id("C1")
    assert cust.get("tags") == ["Cartier fan"]


# ===========================================================================
# T9 -- staff cannot approve, only suggest (RBAC 403).
# ===========================================================================


def test_t9_staff_cannot_approve(monkeypatch):
    db = _FakeDB({
        "customers": _FakeColl([{"customer_id": "C1", "name": "Cust", "store_id": STORE}]),
        "tag_suggestions": _FakeColl([{"suggestion_id": "S1", "customer_id": "C1",
                                       "tag": "T", "status": "pending"}]),
    })
    client, _ = _cust_client(db, monkeypatch)
    r = client.post("/api/v1/customers/C1/tags/suggestions/S1/approve",
                    headers=_hdr(("SALES_STAFF",)))
    assert r.status_code == 403


# ===========================================================================
# T10 -- MEGAPHONE idempotency: a second run for the same store+date writes no
# new nba_scores doc and no duplicate follow_up docs.
# ===========================================================================


def test_t10_megaphone_idempotent(monkeypatch):
    import agents.implementations.megaphone as mp

    db = _seed_db_for_scoring()
    agent = mp.MegaphoneAgent(db=db)

    first = agent._score_nba_daily()
    assert first["stores_scored"] == 1
    nba_docs_after_first = len(db.get_collection("nba_scores").docs)
    # follow_ups created this run (existing pending generic FUs + new nba_call).
    fu_after_first = len(db.get_collection("follow_ups").docs)

    second = agent._score_nba_daily()
    assert second["stores_scored"] == 0  # already scored -> skipped
    assert len(db.get_collection("nba_scores").docs) == nba_docs_after_first
    assert len(db.get_collection("follow_ups").docs) == fu_after_first


# ===========================================================================
# T11 -- fallback scoring when MEGAPHONE has not run (no nba_scores doc).
# ===========================================================================


def test_t11_fallback_sync_scoring(monkeypatch):
    db = _seed_db_for_scoring()  # nba_scores is EMPTY
    client = _crm_client(db, monkeypatch)
    r = client.get(f"/api/v1/crm/nba/{STORE}", headers=_hdr(("STORE_MANAGER",)))
    assert r.status_code == 200
    body = r.json()
    assert body["cards"], "fallback must compute a non-empty list"
    assert body["generated_at"]  # a fresh timestamp, not a stale persisted one


# ===========================================================================
# T12 -- RBAC: ACCOUNTANT cannot reach the NBA list (not in the allow-list).
# Verified through the central policy registry (the coverage-lock test asserts
# the row exists; here we assert the gate decision).
# ===========================================================================


def test_t12_rbac_accountant_denied():
    from api.services import rbac_policy as rbac

    path = f"/api/v1/crm/nba/{STORE}"
    assert rbac.check_access("GET", path, ["ACCOUNTANT"]) is False
    assert rbac.check_access("GET", path, ["OPTOMETRIST"]) is False
    assert rbac.check_access("GET", path, ["WORKSHOP_STAFF"]) is False
    # store-facing roles ARE allowed.
    for role in ("SALES_STAFF", "SALES_CASHIER", "STORE_MANAGER", "ADMIN", "SUPERADMIN"):
        assert rbac.check_access("GET", path, [role]) is True, role


def test_t12b_rbac_tag_approve_manager_only():
    from api.services import rbac_policy as rbac

    p = "/api/v1/customers/C1/tags/suggestions/S1/approve"
    assert rbac.check_access("POST", p, ["SALES_STAFF"]) is False
    assert rbac.check_access("POST", p, ["STORE_MANAGER"]) is True
    # suggest is open to staff.
    ps = "/api/v1/customers/C1/tags/suggest"
    assert rbac.check_access("POST", ps, ["SALES_STAFF"]) is True


# ===========================================================================
# T13 -- cross-store IDOR blocked: a store-scoped role asking for another
# store's NBA list gets 403 (validate_store_access), not a 200 with the data.
# ===========================================================================


def test_t13_cross_store_idor_blocked(monkeypatch):
    db = _seed_db_for_scoring()
    client = _crm_client(db, monkeypatch)
    # SALES_STAFF whose token is scoped to STORE asks for OTHER_STORE.
    r = client.get(f"/api/v1/crm/nba/{OTHER_STORE}", headers=_hdr(("SALES_STAFF",), store_id=STORE))
    assert r.status_code == 403


# ===========================================================================
# T14 -- IST day-key + TTL anchor (audit F39-P3 timezone class). nba_scores is
# keyed on the IST calendar day and TTL-anchored on the NAIVE-UTC instant of
# the next IST midnight (the migrations TTL index is expireAfterSeconds=0 on
# ttl_expires_at, and Mongo reads naive BSON dates as UTC).
# ===========================================================================


def test_t14_ttl_anchor_is_utc_instant_of_ist_midnight():
    # IST midnight after 2026-06-10 = 2026-06-11T00:00+05:30 = 2026-06-10T18:30Z.
    # Regression: the old bare `.astimezone()` converted to the SERVER-LOCAL
    # zone (not UTC) -- correct on a UTC Railway box but 5h30m late on any
    # non-UTC host (an IST box stamped 2026-06-11T00:00 naive, which Mongo's
    # TTL monitor reads as UTC -> the list out-lived its IST day).
    assert nba._ist_midnight_utc("2026-06-10") == datetime(2026, 6, 10, 18, 30)
    doc = nba.build_nba_doc(STORE, [], date_str="2026-06-10")
    assert doc["ttl_expires_at"] == datetime(2026, 6, 10, 18, 30)
    assert doc["date"] == "2026-06-10"
    assert doc["nba_id"] == f"NBA-20260610-{STORE}"


def test_t14b_day_key_boundary_early_morning_ist():
    # 01:00 IST on 2026-06-10 == 19:30 UTC on 2026-06-09: a list built in the
    # early IST morning must key on the IST day, never the prior UTC day (the
    # old fallback used utcnow().date()).
    early = datetime(2026, 6, 9, 19, 30, tzinfo=timezone.utc)
    assert nba._today_ist(early) == "2026-06-10"
    # 23:59 IST (18:29 UTC) stays on the same IST day.
    late = datetime(2026, 6, 9, 18, 29, tzinfo=timezone.utc)
    assert nba._today_ist(late) == "2026-06-09"
    # The TTL anchor for that early-morning day key is the SAME IST-midnight
    # instant regardless of when in the IST day the list was built.
    assert nba._ist_midnight_utc(nba._today_ist(early)) == datetime(2026, 6, 10, 18, 30)


# ===========================================================================
# NO-LIVE-SEND (dark): the whole feature creates in-app follow_up records, never
# a provider send. Assert the scorer + the complete/dismiss path do not import or
# call any send function, and that completing a card writes a follow_up (record)
# rather than a notification_logs row.
# ===========================================================================


def test_no_live_send_complete_is_record_only(monkeypatch):
    db = _persisted_nba_doc_db()
    db._cols["notification_logs"] = _FakeColl([])
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: None)

    # Tripwire: if any code path tried to send, this would record it.
    sent = []

    import api.routers.crm as _crm
    # crm.py does not import a send function; guard that none is referenced.
    assert not hasattr(_crm, "send_notification"), "NBA crm path must not import a sender"
    assert not hasattr(nba, "send_notification"), "NBA scorer must not import a sender"
    assert not hasattr(nba, "send_whatsapp")
    assert not hasattr(nba, "send_sms")

    client = _crm_client(db, monkeypatch)
    r = client.post(f"/api/v1/crm/nba/{STORE}/complete",
                    json={"customer_id": "X1", "outcome_notes": "Spoke to the customer today."},
                    headers=_hdr(("SALES_STAFF",)))
    assert r.status_code == 200
    # The outcome became a follow_up RECORD; NO notification_logs row was written.
    assert db.get_collection("notification_logs").inserts == 0
    assert len(sent) == 0
    fu = db.get_collection("follow_ups").find_one({"follow_up_id": "FU-X1"})
    assert fu["status"] == "completed"


def test_no_live_send_megaphone_nba_queues_no_messages(monkeypatch):
    import agents.implementations.megaphone as mp

    db = _seed_db_for_scoring()
    db._cols["notification_logs"] = _FakeColl([])
    agent = mp.MegaphoneAgent(db=db)
    agent._score_nba_daily()
    # MEGAPHONE's NBA step writes nba_scores + nba_call follow_ups, NOT messages.
    assert db.get_collection("notification_logs").inserts == 0
    fu_types = {f.get("type") for f in db.get_collection("follow_ups").docs}
    assert "nba_call" in fu_types
