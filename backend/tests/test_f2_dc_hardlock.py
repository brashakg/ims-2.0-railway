"""
IMS 2.0 - F2 x F9: DC hardlock on the lab-routing scan path
===========================================================

F9 (merged) gates the workshop status PATCH: an external-lab lens job
(top-level lens_status == "ORDERED") may not advance to IN_PROGRESS until an
accepted DELIVERY_CHALLAN GRN covers its lens SKU at that store (422
DC_HARDLOCK; ADMIN+ override audited). The F2 physical-scan path
(POST /workshop/scan -> services/lab_routing.advance_lab_station) ALSO flips a
job to IN_PROGRESS at INTAKE -- these tests pin the closure of that bypass.

GATE-HOLD CONTRACT under test (mirrors SALES_CONFIRM_REQUIRED / QC_REQUIRED):
  * the scan is NEVER 422'd -- HTTP 200, ok=true (the card really was scanned);
  * the physical scan IS recorded (current_station / scan_history advance);
  * the STATUS flip is HELD (job stays PENDING, advanced_status=None);
  * auto_notify is suppressed; response carries status_gate_blocked="DC_REQUIRED";
  * flag off / cutover-exempt / in-house lens behave EXACTLY like the workshop
    PATCH gate (same _check_dc_hardlock, imported -- not duplicated);
  * NO override path on a scan (overrides stay on the manager status PATCH).

CI-ROBUSTNESS: every repo/db accessor the scan path touches is monkeypatched
(workshop.get_db / get_workshop_repository / get_audit_repository AND the lazy
dependencies.get_workshop_repository used inside lab_routing), and every doc
the shared check reads is SEEDED explicitly (purchase_settings, grns) -- the
blocking tests never rely on the fail-soft default, so there is no local-vs-CI
divergence. A WhatsApp tripwire proves no customer notify fires on a held scan.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_f2_dc_hardlock.py -q
"""

from __future__ import annotations

import copy
import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")


# ============================================================================
# In-memory fakes honouring every Mongo operator this path actually uses:
# lab_routing's CAS + station registry AND the F9 hardlock's projection'd
# find_one with the dotted "items.product_id" membership match.
# ============================================================================


def _set_dotted(doc: Dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    cur = doc
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _matches(doc: Dict[str, Any], flt: Dict[str, Any]) -> bool:
    """Equality, $or, {$exists/$in/$lt}, and dotted keys -- 'items.product_id'
    matches ANY element of the items array (Mongo array-membership semantics),
    a dotted key over a sub-dict matches that nested field."""
    for k, cond in flt.items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in cond):
                return False
            continue
        if "." in k and not isinstance(cond, dict):
            head, rest = k.split(".", 1)
            sub = doc.get(head)
            if isinstance(sub, list):
                if not any(isinstance(el, dict) and el.get(rest) == cond for el in sub):
                    return False
                continue
            if isinstance(sub, dict):
                if sub.get(rest) != cond:
                    return False
                continue
            return False
        actual = doc.get(k)
        if isinstance(cond, dict):
            if "$exists" in cond:
                if (k in doc) != cond["$exists"]:
                    return False
            if "$in" in cond:
                if actual not in cond["$in"]:
                    return False
            if "$lt" in cond and not (actual is not None and actual < cond["$lt"]):
                return False
        else:
            if actual != cond:
                return False
    return True


class FakeCollection:
    def __init__(self):
        self._docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        self._docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find(self, flt=None, projection=None):
        flt = flt or {}
        return [copy.deepcopy(d) for d in self._docs if _matches(d, flt)]

    def find_one(self, flt, projection=None):
        # Projection-aware signature (the F9 hardlock passes one); the
        # projection itself is irrelevant to the assertions, so it is accepted
        # and ignored -- the load-bearing part is that the call DOES NOT crash.
        for d in self._docs:
            if _matches(d, flt):
                return copy.deepcopy(d)
        return None

    def count_documents(self, flt):
        return sum(1 for d in self._docs if _matches(d, flt or {}))

    def _apply_update(self, doc, update):
        if "$set" in update:
            for k, v in update["$set"].items():
                _set_dotted(doc, k, v)
        if "$push" in update:
            for k, v in update["$push"].items():
                arr = doc.get(k)
                if not isinstance(arr, list):
                    arr = []
                    doc[k] = arr
                arr.append(v)

    def update_one(self, flt, update, upsert=False):
        for d in self._docs:
            if _matches(d, flt):
                self._apply_update(d, update)
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def find_one_and_update(self, flt, update, return_document=None):
        for d in self._docs:
            if _matches(d, flt):
                self._apply_update(d, update)
                return copy.deepcopy(d)
        return None


class FakeDB:
    def __init__(self):
        self._cols: Dict[str, FakeCollection] = {}
        self.is_connected = True

    def get_collection(self, name):
        if name not in self._cols:
            self._cols[name] = FakeCollection()
        return self._cols[name]


class FakeWorkshopRepo:
    """Shares the FakeDB workshop_jobs collection so the router's repo view and
    lab_routing's direct find_one_and_update see one store of truth."""

    def __init__(self, db: FakeDB):
        self.collection = db.get_collection("workshop_jobs")

    def find_by_id(self, job_id):
        return self.collection.find_one({"job_id": job_id})

    def find_by_number(self, job_number):
        return self.collection.find_one({"job_number": job_number})

    def update(self, job_id, data):
        return self.collection.update_one({"job_id": job_id}, {"$set": data}).modified_count > 0

    def update_status(self, job_id, status, by_user=None, notes=None):
        upd = {"status": status, "status_updated_by": by_user}
        return self.collection.update_one({"job_id": job_id}, {"$set": upd}).modified_count > 0


STORE = "BV-TEST-01"


def _mk_job(db: FakeDB, job_id: str, **extra):
    """An external-lab lens job, sales-confirmed (so the SALES_CONFIRM gate
    cannot mask the DC gate under test), created AFTER any realistic cutover."""
    doc = {
        "job_id": job_id,
        "job_number": f"WS-{job_id}",
        "status": "PENDING",
        "store_id": STORE,
        "customer_name": "Asha Verma",
        "customer_phone": "9876500000",
        "created_at": "2026-06-10T10:00:00",
        "lens_status": "ORDERED",                      # TOP-LEVEL F9 field
        "lens_details": {"product_id": "L1"},          # Rx spec carries the SKU
        "fitting_details": {"confirmed_by_sales": True},
    }
    doc.update(extra)
    db.get_collection("workshop_jobs").insert_one(doc)
    return doc


def _seed_settings(db: FakeDB, require=True, cutover=None):
    """SEED the purchase_settings doc the shared check reads -- blocking tests
    must never depend on the fail-soft 'no doc -> lock ON' default."""
    db.get_collection("purchase_settings").insert_one(
        {"_id": "default", "require_dc_for_workshop": require,
         "dc_hardlock_from_date": cutover}
    )


def _seed_dc(db: FakeDB, product_id="L1", store_id=STORE, status="ACCEPTED",
             grn_id="DC1"):
    db.get_collection("grns").insert_one(
        {
            "grn_id": grn_id,
            "grn_subtype": "DELIVERY_CHALLAN",
            "status": status,
            "store_id": store_id,
            "items": [{"product_id": product_id, "received_qty": 2,
                       "accepted_qty": 2, "rejected_qty": 0}],
        }
    )


@pytest.fixture
def fake_env(monkeypatch):
    """Monkeypatch EVERY accessor the scan path touches + a WhatsApp tripwire."""
    from api.routers import workshop as wmod
    from api import dependencies as deps
    import agents.providers as prov

    db = FakeDB()
    repo = FakeWorkshopRepo(db)

    monkeypatch.setattr(wmod, "get_db", lambda: db)
    monkeypatch.setattr(wmod, "get_workshop_repository", lambda: repo)
    monkeypatch.setattr(deps, "get_workshop_repository", lambda: repo)
    monkeypatch.setattr(wmod, "get_audit_repository", lambda: None)

    notify_calls: List[tuple] = []

    async def _tripwire(phone, text):
        notify_calls.append((phone, text))
        return type("R", (), {"status": "SIMULATED"})()

    monkeypatch.setattr(prov, "send_whatsapp", _tripwire)
    return db, repo, notify_calls


def _scan(client, headers, code, station, store_id=STORE):
    return client.post(
        "/api/v1/workshop/scan",
        headers=headers,
        json={"scanned_code": code, "station_code": station, "store_id": store_id},
    )


# ============================================================================
# Acceptance tests (intent-level; a hollow shell must FAIL)
# ============================================================================


def test_ordered_no_dc_scan_recorded_status_held_no_notify(client, auth_headers, fake_env):
    """ORDERED lens + no DC: the INTAKE scan is HTTP 200 / ok=true (never a
    422), the physical scan IS recorded, the IN_PROGRESS flip is HELD with
    gate_block=DC_REQUIRED, and no customer notify fires."""
    db, repo, notify_calls = fake_env
    _seed_settings(db, require=True)  # explicit: not relying on fail-soft default
    _mk_job(db, "j1")

    resp = _scan(client, auth_headers, "WS-j1", "INTAKE")
    assert resp.status_code == 200  # gate-hold contract: a scan is never 422'd
    body = resp.json()
    assert body["ok"] is True
    assert body["status_gate_blocked"] == "DC_REQUIRED"
    assert body["advanced_status"] is None
    assert body["auto_notify"] is False

    job = repo.find_by_id("j1")
    assert job["current_station"] == "INTAKE"          # scan recorded
    assert len(job["scan_history"]) == 1
    assert job["status"] == "PENDING"                  # status HELD
    assert not job.get("ready_notified_at")
    assert notify_calls == []                          # no customer notify


def test_ordered_with_accepted_dc_advances(client, auth_headers, fake_env):
    """An accepted DELIVERY_CHALLAN covering the lens SKU at this store
    satisfies the lock: the scan advances the status to IN_PROGRESS."""
    db, repo, _ = fake_env
    _seed_settings(db, require=True)
    _seed_dc(db, product_id="L1", store_id=STORE)
    _mk_job(db, "j2")

    body = _scan(client, auth_headers, "WS-j2", "INTAKE").json()
    assert body["ok"] is True
    assert body["status_gate_blocked"] is None
    assert body["advanced_status"] == "IN_PROGRESS"
    assert repo.find_by_id("j2")["status"] == "IN_PROGRESS"


def test_wrong_store_wrong_sku_or_unaccepted_dc_does_not_satisfy(client, auth_headers, fake_env):
    """The DC must be ACCEPTED, at THIS store, covering THIS SKU -- a DC at
    another store, for another SKU, or still pending does not unlock."""
    db, repo, _ = fake_env
    _seed_settings(db, require=True)
    _seed_dc(db, product_id="L1", store_id="BV-OTHER-99", grn_id="DC-otherstore")
    _seed_dc(db, product_id="L9", store_id=STORE, grn_id="DC-othersku")
    _seed_dc(db, product_id="L1", store_id=STORE, status="PENDING", grn_id="DC-pending")
    _mk_job(db, "j3")

    body = _scan(client, auth_headers, "WS-j3", "INTAKE").json()
    assert body["ok"] is True
    assert body["status_gate_blocked"] == "DC_REQUIRED"
    assert repo.find_by_id("j3")["status"] == "PENDING"


def test_inhouse_lens_exempt(client, auth_headers, fake_env):
    """lens_status RECEIVED (in-house stock) is exempt -- exactly like the
    workshop PATCH gate."""
    db, repo, _ = fake_env
    _seed_settings(db, require=True)
    _mk_job(db, "j4", lens_status="RECEIVED")

    body = _scan(client, auth_headers, "WS-j4", "INTAKE").json()
    assert body["ok"] is True
    assert body["status_gate_blocked"] is None
    assert body["advanced_status"] == "IN_PROGRESS"
    assert repo.find_by_id("j4")["status"] == "IN_PROGRESS"


def test_flag_off_exempt(client, auth_headers, fake_env):
    """require_dc_for_workshop=false (grace period) disables the lock on the
    scan path too -- no redeploy divergence between the two gates."""
    db, repo, _ = fake_env
    _seed_settings(db, require=False)
    _mk_job(db, "j5")

    body = _scan(client, auth_headers, "WS-j5", "INTAKE").json()
    assert body["ok"] is True
    assert body["status_gate_blocked"] is None
    assert repo.find_by_id("j5")["status"] == "IN_PROGRESS"


def test_cutover_date_respected(client, auth_headers, fake_env):
    """A job created BEFORE dc_hardlock_from_date is never blocked (no
    retroactive lock); created AFTER it -> held."""
    db, repo, _ = fake_env
    _seed_settings(db, require=True, cutover="2099-01-01")
    _mk_job(db, "j6")  # created 2026 < 2099 cutover -> exempt
    body = _scan(client, auth_headers, "WS-j6", "INTAKE").json()
    assert body["status_gate_blocked"] is None
    assert repo.find_by_id("j6")["status"] == "IN_PROGRESS"

    # Flip the cutover into the past -> a post-cutover job IS held.
    db.get_collection("purchase_settings").update_one(
        {"_id": "default"}, {"$set": {"dc_hardlock_from_date": "2000-01-01"}}
    )
    _mk_job(db, "j7")
    body2 = _scan(client, auth_headers, "WS-j7", "INTAKE").json()
    assert body2["ok"] is True
    assert body2["status_gate_blocked"] == "DC_REQUIRED"
    assert repo.find_by_id("j7")["status"] == "PENDING"


def test_sales_confirm_gate_takes_precedence(client, auth_headers, fake_env):
    """Gate precedence mirrors the PATCH handler: an unconfirmed fitting
    reports SALES_CONFIRM_REQUIRED (one gate at a time), not DC_REQUIRED."""
    db, repo, _ = fake_env
    _seed_settings(db, require=True)
    _mk_job(db, "j8", fitting_details={})  # NOT confirmed; also no DC

    body = _scan(client, auth_headers, "WS-j8", "INTAKE").json()
    assert body["ok"] is True
    assert body["status_gate_blocked"] == "SALES_CONFIRM_REQUIRED"
    assert repo.find_by_id("j8")["status"] == "PENDING"


def test_direct_service_contract_no_http_layer(fake_env):
    """Belt-and-braces: the gate lives in advance_lab_station itself (not in
    router plumbing) -- calling the service directly shows the same hold, so a
    future second scan entry-point cannot silently re-open the bypass."""
    from api.services import lab_routing

    db, repo, _ = fake_env
    _seed_settings(db, require=True)
    _mk_job(db, "jd")

    res = lab_routing.advance_lab_station(db, repo.find_by_id("jd"), "INTAKE", "u1")
    assert res["ok"] is True
    assert res["status_gate_blocked"] == "DC_REQUIRED"
    assert res["advanced_status"] is None
    assert res["auto_notify"] is False
    assert "Delivery Challan" in res["message"]
    job = repo.find_by_id("jd")
    assert job["current_station"] == "INTAKE"
    assert job["status"] == "PENDING"
