"""
IMS 2.0 - F2 Internal lab routing (disposable job cards) tests
==============================================================

Asserts the INTENT (PROTOCOL Sec 5), not the HTTP shape: a hollow shell that
returns 200 without mutating workshop_jobs.current_station / station_timestamps
/ station_dwell_ms / scan_history MUST fail these tests.

These run with a small in-memory fake DB (no live mongod): a fake
`workshop_jobs` collection that honours the exact Mongo operators the routing
service uses ($set with dot-notation, $push, find_one_and_update CAS with the
return_document arg, the $or/$exists null-guard) and a fake `lab_stations`
collection. The CAS semantics are the load-bearing part of acceptance #4
(concurrency), so the fake reproduces find_one_and_update atomicity faithfully.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_f2_lab_routing.py -q
"""

from __future__ import annotations

import copy
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")


# ============================================================================
# In-memory fakes that honour the Mongo operators the service actually uses
# ============================================================================


class _ReturnDocument:
    BEFORE = 0
    AFTER = 1


def _set_dotted(doc: Dict[str, Any], key: str, value: Any) -> None:
    """Apply a possibly-dotted key (e.g. 'station_timestamps.EDGING') into doc."""
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
    """Minimal filter matcher supporting equality, $or, and {$exists/$in}."""
    for k, cond in flt.items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in cond):
                return False
            continue
        actual = doc.get(k)
        if isinstance(cond, dict):
            if "$exists" in cond:
                exists = k in doc
                if exists != cond["$exists"]:
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
    """In-memory collection supporting the operators used by lab_routing +
    WorkshopJobRepository.update (find_one_and_update CAS, $set dotted, $push,
    find, insert_one, update_one, count_documents, find_one)."""

    def __init__(self, key_field: str = "job_id"):
        self._docs: List[Dict[str, Any]] = []
        self.key_field = key_field

    def insert_one(self, doc):
        self._docs.append(copy.deepcopy(doc))
        return type("R", (), {"inserted_id": doc.get(self.key_field)})()

    def find(self, flt=None):
        flt = flt or {}
        return [copy.deepcopy(d) for d in self._docs if _matches(d, flt)]

    def find_one(self, flt):
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

    def update_one(self, flt, update):
        for d in self._docs:
            if _matches(d, flt):
                self._apply_update(d, update)
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()

    def find_one_and_update(self, flt, update, return_document=_ReturnDocument.AFTER):
        # Atomic CAS: find the FIRST doc matching the guard, mutate IN PLACE.
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
            self._cols[name] = FakeCollection(
                key_field="job_id" if name == "workshop_jobs" else "station_id"
            )
        return self._cols[name]


class FakeWorkshopRepo:
    """Reads/writes the SAME FakeDB workshop_jobs collection so the router's
    repo view and the service's direct find_one_and_update see one store of
    truth (critical for the concurrency + dwell assertions)."""

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

    def find_by_store(self, store_id, status=None):
        flt = {"store_id": store_id}
        if status:
            flt["status"] = status
        return self.collection.find(flt)


def _mk_job(db: FakeDB, job_id: str, **extra):
    doc = {
        "job_id": job_id,
        "job_number": f"WS-{job_id}",
        "status": "PENDING",
        "store_id": "BV-TEST-01",
        "customer_name": "Asha Verma",
        "customer_phone": "9876500000",
    }
    doc.update(extra)
    db.get_collection("workshop_jobs").insert_one(doc)
    return doc


@pytest.fixture
def fake_env(monkeypatch):
    """Install a FakeDB + FakeWorkshopRepo into every symbol the scan path
    touches: the router's get_db / get_workshop_repository AND the lazy
    dependencies.get_workshop_repository used inside lab_routing for the status
    transition. Also make pymongo.ReturnDocument importable as a stand-in."""
    from api.routers import workshop as wmod
    from api import dependencies as deps

    db = FakeDB()
    repo = FakeWorkshopRepo(db)

    monkeypatch.setattr(wmod, "get_db", lambda: db)
    monkeypatch.setattr(wmod, "get_workshop_repository", lambda: repo)
    monkeypatch.setattr(deps, "get_workshop_repository", lambda: repo)
    # Audit is fail-soft already; force it off so tests don't touch real Mongo.
    monkeypatch.setattr(wmod, "get_audit_repository", lambda: None)

    return db, repo


def _scan(client, headers, code, station, store_id="BV-TEST-01"):
    return client.post(
        "/api/v1/workshop/scan",
        headers=headers,
        json={"scanned_code": code, "station_code": station, "store_id": store_id},
    ).json()


# ============================================================================
# Acceptance tests (intent-level)
# ============================================================================


def test_auth_required(client):
    resp = client.post("/api/v1/workshop/scan", json={"scanned_code": "x", "station_code": "INTAKE"})
    assert resp.status_code == 401


def test_1_forward_only_gate_rejects_out_of_order(client, auth_headers, fake_env):
    """Scan COATING when current_station is null -> WRONG_STATION, no mutation."""
    db, repo = fake_env
    _mk_job(db, "j1")  # current_station unset
    body = _scan(client, auth_headers, "WS-j1", "COATING")
    assert body["ok"] is False
    assert body["reason"] == "WRONG_STATION"
    job = repo.find_by_id("j1")
    assert job.get("current_station") is None
    assert not job.get("scan_history")  # unchanged


def test_2_happy_path_advance_and_dwell(client, auth_headers, fake_env):
    """INTAKE then EDGING: current_station + timestamps + dwell + history all set."""
    db, repo = fake_env
    _mk_job(db, "j2")

    b1 = _scan(client, auth_headers, "WS-j2", "INTAKE")
    assert b1["ok"] is True
    job = repo.find_by_id("j2")
    assert job["current_station"] == "INTAKE"
    assert job["status"] == "IN_PROGRESS"  # INTAKE advances_job_status
    assert "INTAKE" in job["station_timestamps"]
    assert len(job["scan_history"]) == 1
    assert job["scan_history"][0]["station"] == "INTAKE"

    b2 = _scan(client, auth_headers, "WS-j2", "EDGING")
    assert b2["ok"] is True
    job = repo.find_by_id("j2")
    assert job["current_station"] == "EDGING"
    assert job["status"] == "IN_PROGRESS"  # inner station does not change status
    # Dwell for the station JUST LEFT (INTAKE) is a non-negative int.
    assert isinstance(job["station_dwell_ms"]["INTAKE"], int)
    assert job["station_dwell_ms"]["INTAKE"] >= 0
    assert len(job["scan_history"]) == 2


def test_3_dispatch_sets_ready_and_notifies(client, auth_headers, fake_env, monkeypatch):
    """Walk to QC_LAB then DISPATCH -> status READY + ready_notified_at stamped."""
    db, repo = fake_env

    # Stub the WhatsApp provider so notify is SIMULATED, never a real network call.
    async def _fake_wa(phone, text):
        return type("R", (), {"status": "SIMULATED"})()

    import agents.providers as prov
    monkeypatch.setattr(prov, "send_whatsapp", _fake_wa)

    _mk_job(db, "j3")
    for st in ("INTAKE", "EDGING", "COATING", "QC_LAB"):
        assert _scan(client, auth_headers, "WS-j3", st)["ok"] is True

    body = _scan(client, auth_headers, "WS-j3", "DISPATCH")
    assert body["ok"] is True
    job = repo.find_by_id("j3")
    assert job["status"] == "READY"
    assert job["current_station"] == "DISPATCH"
    assert job.get("ready_notified_at")  # auto-notify fired


def test_4_concurrency_one_winner(client, auth_headers, fake_env):
    """Two scans to EDGING (current=INTAKE): exactly one wins, one CONCURRENT.
    Exactly one EDGING scan_history entry; INTAKE dwell recorded exactly once."""
    db, repo = fake_env
    _mk_job(db, "j4")
    assert _scan(client, auth_headers, "WS-j4", "INTAKE")["ok"] is True

    b1 = _scan(client, auth_headers, "WS-j4", "EDGING")
    b2 = _scan(client, auth_headers, "WS-j4", "EDGING")
    oks = sorted([b1["ok"], b2["ok"]])
    assert oks == [False, True]
    loser = b1 if not b1["ok"] else b2
    # The loser is either the CAS-loss (CONCURRENT_CONFLICT) or, since this is a
    # sequential test, the ALREADY_HERE duplicate guard -- both prove no double
    # advance happened.
    assert loser["reason"] in ("CONCURRENT_CONFLICT", "ALREADY_HERE")

    job = repo.find_by_id("j4")
    edging_entries = [h for h in job["scan_history"] if h.get("station") == "EDGING"]
    assert len(edging_entries) == 1
    # INTAKE dwell recorded exactly once (one transition out of INTAKE).
    assert "INTAKE" in job["station_dwell_ms"]


def test_4b_true_cas_race_one_loses_concurrent(fake_env):
    """The hard concurrency case: TWO writers both read current_station=INTAKE
    (a genuine race) and try to advance to EDGING. The find_one_and_update CAS
    guard means exactly one wins; the second sees current_station already moved
    -> CONCURRENT_CONFLICT (NOT ALREADY_HERE). Proves the CAS, not the dup guard.
    Calls the service directly so both writers share the same pre-state snapshot."""
    from api.services import lab_routing

    db, repo = fake_env
    _mk_job(db, "race1")
    # Advance to INTAKE first.
    lab_routing.advance_lab_station(db, repo.find_by_id("race1"), "INTAKE", "u1")

    # Both writers read the SAME job snapshot (current_station=INTAKE) BEFORE
    # either writes -- this is what two concurrent requests would observe.
    snap = repo.find_by_id("race1")
    r1 = lab_routing.advance_lab_station(db, dict(snap), "EDGING", "u1")
    r2 = lab_routing.advance_lab_station(db, dict(snap), "EDGING", "u2")

    oks = sorted([r1["ok"], r2["ok"]])
    assert oks == [False, True]
    loser = r1 if not r1["ok"] else r2
    assert loser["reason"] == "CONCURRENT_CONFLICT"

    job = repo.find_by_id("race1")
    assert len([h for h in job["scan_history"] if h.get("station") == "EDGING"]) == 1
    assert job["current_station"] == "EDGING"


def test_5_store_configurable_sequence_skips_coating(client, auth_headers, fake_env):
    """Deactivate COATING -> sequence is INTAKE->EDGING->QC_LAB. Scanning COATING
    is UNKNOWN_STATION; QC_LAB right after EDGING succeeds."""
    db, repo = fake_env
    _mk_job(db, "j5")
    # Deactivate COATING via the config endpoint.
    resp = client.post(
        "/api/v1/workshop/stations",
        headers=auth_headers,
        json={"code": "COATING", "is_active": False, "store_id": "BV-TEST-01"},
    )
    assert resp.status_code == 200

    assert _scan(client, auth_headers, "WS-j5", "INTAKE")["ok"] is True
    assert _scan(client, auth_headers, "WS-j5", "EDGING")["ok"] is True
    # COATING is now inactive -> not in the active sequence at all.
    coat = _scan(client, auth_headers, "WS-j5", "COATING")
    assert coat["ok"] is False
    assert coat["reason"] == "UNKNOWN_STATION"
    # QC_LAB directly after EDGING is now the legal next step.
    qc = _scan(client, auth_headers, "WS-j5", "QC_LAB")
    assert qc["ok"] is True
    assert repo.find_by_id("j5")["current_station"] == "QC_LAB"


def test_6_job_card_barcode_resolves_by_number(client, auth_headers, fake_env):
    """A keyboard-wedge scan of the card's barcode_value (== job_number) with no
    station hint... here we still pass the station, but the RESOLUTION is by
    job_number. Assert the scan resolves to the right job."""
    db, repo = fake_env
    _mk_job(db, "j6")
    # Print the job card -> stamps + returns the barcode value.
    pj = client.post("/api/v1/workshop/jobs/j6/print-job-card", headers=auth_headers).json()
    assert pj["ok"] is True
    assert pj["barcode_value"] == "WS-j6"
    # Scan using that exact barcode value resolves the job.
    body = _scan(client, auth_headers, pj["barcode_value"], "INTAKE")
    assert body["ok"] is True
    assert body["job_id"] == "j6"


def test_7_station_queue_excludes_departed(client, auth_headers, fake_env):
    """3 jobs at EDGING + 1 moved on (to COATING) -> EDGING queue returns
    exactly 3; the departed job does NOT appear."""
    db, repo = fake_env
    for jid in ("q1", "q2", "q3", "q4"):
        _mk_job(db, jid)
        assert _scan(client, auth_headers, f"WS-{jid}", "INTAKE")["ok"] is True
        assert _scan(client, auth_headers, f"WS-{jid}", "EDGING")["ok"] is True
    # Move q4 on to the NEXT default station (COATING) so it leaves the EDGING queue.
    assert _scan(client, auth_headers, "WS-q4", "COATING")["ok"] is True

    resp = client.get(
        "/api/v1/workshop/stations/EDGING/queue?store_id=BV-TEST-01", headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    ids = {j["job_id"] for j in data["jobs"]}
    assert ids == {"q1", "q2", "q3"}
    assert "q4" not in ids


def test_8_auto_notify_failsoft_does_not_rollback(client, auth_headers, fake_env, monkeypatch):
    """If send_whatsapp raises, the DISPATCH scan still returns ok + READY."""
    db, repo = fake_env

    async def _boom(phone, text):
        raise RuntimeError("provider down")

    import agents.providers as prov
    monkeypatch.setattr(prov, "send_whatsapp", _boom)

    _mk_job(db, "j8")
    for st in ("INTAKE", "EDGING", "COATING", "QC_LAB"):
        assert _scan(client, auth_headers, "WS-j8", st)["ok"] is True

    body = _scan(client, auth_headers, "WS-j8", "DISPATCH")
    assert body["ok"] is True
    job = repo.find_by_id("j8")
    assert job["status"] == "READY"
    assert job["current_station"] == "DISPATCH"


def test_9_dashboard_kpis_extended(client, auth_headers, fake_env):
    """dashboard-kpis gains per_station_counts (EDGING==2 after 2 jobs) without
    dropping any existing key."""
    db, repo = fake_env
    for jid in ("k1", "k2"):
        _mk_job(db, jid)
        assert _scan(client, auth_headers, f"WS-{jid}", "INTAKE")["ok"] is True
        assert _scan(client, auth_headers, f"WS-{jid}", "EDGING")["ok"] is True

    resp = client.get("/api/v1/workshop/dashboard-kpis?store_id=BV-TEST-01", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    # Existing keys preserved.
    for key in ("pending", "in_progress", "qc_failed", "ready_for_pickup", "overdue"):
        assert key in data
    # New keys present + correct.
    assert "per_station_counts" in data
    assert data["per_station_counts"].get("EDGING") == 2
    assert "avg_dwell_by_station" in data


def test_10_dwell_is_server_computed_not_client(client, auth_headers, fake_env):
    """A client-supplied station_dwell_ms in the body is ignored; the stored
    dwell is the server-computed elapsed time (a real int, not the bogus value)."""
    db, repo = fake_env
    _mk_job(db, "j10")
    assert _scan(client, auth_headers, "WS-j10", "INTAKE")["ok"] is True
    # Include a malicious dwell value in the body.
    resp = client.post(
        "/api/v1/workshop/scan",
        headers=auth_headers,
        json={
            "scanned_code": "WS-j10",
            "station_code": "EDGING",
            "store_id": "BV-TEST-01",
            "station_dwell_ms": {"INTAKE": 999999999},  # ignored extra field
        },
    )
    assert resp.json()["ok"] is True
    job = repo.find_by_id("j10")
    # Server-computed dwell is a small non-negative int, never the injected value.
    assert job["station_dwell_ms"]["INTAKE"] != 999999999
    assert job["station_dwell_ms"]["INTAKE"] >= 0


def test_11_delivered_job_rejects_scan(client, auth_headers, fake_env):
    """A DELIVERED job -> TERMINAL_STAGE, no mutation."""
    db, repo = fake_env
    _mk_job(db, "j11", status="DELIVERED", current_station="PICKUP")
    body = _scan(client, auth_headers, "WS-j11", "INTAKE")
    assert body["ok"] is False
    assert body["reason"] == "TERMINAL_STAGE"
    job = repo.find_by_id("j11")
    assert job["current_station"] == "PICKUP"  # unchanged


def test_12_label_barcode_value_is_job_number(client, auth_headers, fake_env, monkeypatch):
    """The traveler label's barcode_value is non-empty and equals job_number --
    the value the frontend Code128 renders + the wedge scanner re-emits."""
    db, repo = fake_env
    _mk_job(db, "j12")
    # The labels router uses its own get_workshop_repository symbol.
    from api.routers import labels as labels_mod
    monkeypatch.setattr(labels_mod, "get_workshop_repository", lambda: repo)
    resp = client.get("/api/v1/workshop/jobs/j12/label?type=traveler", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["barcode_value"]
    assert body["barcode_value"] == body["job_number"] == "WS-j12"


def test_station_config_rejects_unknown_code(client, auth_headers, fake_env):
    """Upserting a station with a code outside the canonical vocabulary -> 400."""
    resp = client.post(
        "/api/v1/workshop/stations",
        headers=auth_headers,
        json={"code": "TELEPORTER", "store_id": "BV-TEST-01"},
    )
    assert resp.status_code == 400


def test_already_here_duplicate_scan(client, auth_headers, fake_env):
    """Scanning the SAME station twice in a row -> ALREADY_HERE, no double entry."""
    db, repo = fake_env
    _mk_job(db, "jdup")
    assert _scan(client, auth_headers, "WS-jdup", "INTAKE")["ok"] is True
    again = _scan(client, auth_headers, "WS-jdup", "INTAKE")
    assert again["ok"] is False
    assert again["reason"] == "ALREADY_HERE"
    job = repo.find_by_id("jdup")
    assert len([h for h in job["scan_history"] if h["station"] == "INTAKE"]) == 1


def _workshop_headers(store_id="BV-TEST-01"):
    """A store-scoped WORKSHOP_STAFF token (a real scan role, single store)."""
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "test-ws-001",
            "username": "wsstaff",
            "roles": ["WORKSHOP_STAFF"],
            "store_ids": [store_id],
            "active_store_id": store_id,
        }
    )
    return {"Authorization": f"Bearer {token}"}


def test_cross_store_scan_existence_hidden(client, fake_env):
    """A store-scoped scan-capable caller scanning a job at ANOTHER store gets
    NOT_FOUND (existence-hide), and the job is never advanced."""
    db, repo = fake_env
    _mk_job(db, "jx", store_id="BV-OTHER-99")
    headers = _workshop_headers("BV-TEST-01")  # caller bound to BV-TEST-01
    body = _scan(client, headers, "WS-jx", "INTAKE", store_id="BV-OTHER-99")
    assert body["ok"] is False
    assert body["reason"] == "NOT_FOUND"
    job = repo.find_by_id("jx")
    assert job.get("current_station") is None
