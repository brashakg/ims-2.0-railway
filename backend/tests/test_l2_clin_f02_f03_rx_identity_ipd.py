"""
L2-CLIN-F02 / L2-CLIN-F03 - a saved exam's Rx keeps WHO it is for, its IPD and
its issue date on every door.
==============================================================================

The clinical exam auto-creates the Rx with `patient_id` / `customer_id`, per-eye
monocular `pd`, a binocular `ipd` and a `test_date` -- and no name snapshot.
Reported (real UI + real Mongo):
  * clinical print door: "Patient: Patient", no IPD, and an asymmetric
    OD 31 / OS 32.5 / IPD 63.5 printed only "PD: 31 mm";
  * customer portal: "Issued -" and no PD for the same record.

These drive the REAL routes (clinical print, canonical print) and the portal's
projection with that exact stored shape, for a DEPENDENT on a family account
(the holder must never be substituted), the holder, and an unknown member.
"""

from __future__ import annotations

import os
import re
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.dependencies as deps  # noqa: E402
from api.routers import clinical, portal, prescriptions  # noqa: E402
from api.routers.clinical import rx_print  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

STORE = "store-001"

ACCOUNT = {
    "customer_id": "cust-1",
    "name": "Ravi Holder",
    "mobile": "9000000001",
    "patients": [
        {"patient_id": "p-ravi", "name": "Ravi Holder", "relation": "Self", "is_primary": True},
        {"patient_id": "p-asha", "name": "Asha Daughter", "relation": "Daughter", "mobile": None},
    ],
}


def _exam_rx(**over):
    """The exact shape eye_tests.complete_test writes."""
    rx = {
        "prescription_id": "rx-1",
        "prescription_number": "RX-261112-ABC123",
        "patient_id": "p-asha",
        "customer_id": "cust-1",
        "store_id": STORE,
        "source": "TESTED_AT_STORE",
        "optometrist_name": "Dr Opto",
        "right_eye": {"sph": "0", "cyl": None, "axis": None, "add": None, "pd": "31"},
        "left_eye": {"sph": "-1.00", "cyl": None, "axis": None, "add": None, "pd": "32.5"},
        "ipd": "63.5",
        "internal_note": "STAFF-ONLY-NOTE",
        "test_date": "2026-11-12T05:00:00Z",
        "expiry_date": "2027-11-12T05:00:00",
        "status": "ACTIVE",
    }
    rx.update(over)
    return rx


class _Repo:
    def __init__(self, *docs, key):
        self._docs = {d[key]: d for d in docs}

    def find_by_id(self, _id):
        return self._docs.get(_id)


def _client(monkeypatch, rx, customer=ACCOUNT):
    rx_repo = _Repo(rx, key="prescription_id")
    cust_repo = _Repo(customer, key="customer_id") if customer else _Repo(key="customer_id")
    monkeypatch.setattr(rx_print, "get_prescription_repository", lambda: rx_repo)
    monkeypatch.setattr(rx_print, "get_store_repository", lambda: None)
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: rx_repo)
    monkeypatch.setattr(deps, "get_customer_repository", lambda: cust_repo)
    app = FastAPI()
    app.include_router(clinical.router, prefix="/clinical")
    app.include_router(prescriptions.router, prefix="/prescriptions")

    async def _user():
        return {
            "user_id": "u1",
            "username": "opto",
            "full_name": "Dr Opto",
            "active_store_id": STORE,
            "store_ids": [STORE],
            "roles": ["OPTOMETRIST"],
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def _clinical_html(client):
    r = client.get("/clinical/prescriptions/rx-1/print")
    assert r.status_code == 200, r.text
    return r.text


def _canonical_html(client):
    r = client.get("/prescriptions/rx-1/print")
    assert r.status_code == 200, r.text
    return r.json()["html"]


def _clinical_patient(html):
    return re.search(r"<b>Patient:</b> (.*?)</span>", html).group(1)


def _clinical_eye_pds(html):
    out = []
    for label in ("Right (OD)", "Left (OS)"):
        start = html.index(f"<td class='eye'>{label}</td>")
        out.append(re.findall(r"<td>(.*?)</td>", html[start : html.index("</tr>", start)])[-1])
    return tuple(out)


def _canonical_patient(html):
    return re.search(r"<strong>Patient:</strong> (.*?)(?: &nbsp;|</p>)", html).group(1)


# ---------------------------------------------------------------- F02 clinical door
def test_clinical_print_names_the_examined_dependent_with_ipd_and_both_eye_pds(monkeypatch):
    html = _clinical_html(_client(monkeypatch, _exam_rx()))
    assert _clinical_patient(html) == "Asha Daughter"
    assert "Ravi Holder" not in html  # the holder is never substituted
    assert "9000000001" in html  # family contact number
    assert "<b>IPD (binocular):</b> 63.5 mm" in html
    assert _clinical_eye_pds(html) == ("31", "32.5")
    assert "PD:</b> 31 mm" not in html  # the old single collapsed PD
    assert "STAFF-ONLY-NOTE" not in html
    assert "Plano" in html and "-1.00" in html  # powers unchanged
    assert "12 Nov 2026" in html


def test_clinical_print_holder_exam_names_the_holder(monkeypatch):
    html = _clinical_html(_client(monkeypatch, _exam_rx(patient_id="cust-1")))
    assert _clinical_patient(html) == "Ravi Holder"


def test_clinical_print_unknown_member_is_not_recorded_never_the_holder(monkeypatch):
    html = _clinical_html(_client(monkeypatch, _exam_rx(patient_id="p-gone")))
    assert _clinical_patient(html) == "Not recorded"
    assert "Ravi Holder" not in html


def test_clinical_print_blank_ipd_and_eye_pd_stay_blank(monkeypatch):
    rx = _exam_rx(ipd=None)
    rx["right_eye"]["pd"] = None
    html = _clinical_html(_client(monkeypatch, rx))
    assert "IPD (binocular)" not in html
    assert _clinical_eye_pds(html) == ("-", "32.5")


def test_embedded_snapshot_still_wins(monkeypatch):
    html = _clinical_html(_client(monkeypatch, _exam_rx(patient_name="Snapshot Name"), customer=None))
    assert _clinical_patient(html) == "Snapshot Name"


# ---------------------------------------------------------------- F02 canonical door
def test_canonical_print_names_the_dependent_and_keeps_ipd_and_date(monkeypatch):
    html = _canonical_html(_client(monkeypatch, _exam_rx()))
    assert _canonical_patient(html) == "Asha Daughter"
    assert "Ravi Holder" not in html
    assert "<strong>IPD (binocular):</strong> 63.5 mm" in html
    assert "Date: 2026-11-12" in html  # test_date; was blank (prescription_date only)
    cells = re.findall(r"<td>(.*?)</td>", html)
    assert "31" in cells and "32.5" in cells and "63.5" not in cells


def test_canonical_print_escapes_the_patient_name(monkeypatch):
    html = _canonical_html(_client(monkeypatch, _exam_rx(patient_name="<b>x</b>")))
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


# ---------------------------------------------------------------- F03 portal
def test_portal_projection_carries_the_exam_issue_date_and_ipd():
    view = portal._safe_prescription_view(_exam_rx())
    assert view["prescription_date"] == "2026-11-12"  # its IST calendar day
    assert view["pd"] == "63.5"


@pytest.mark.parametrize(
    "stored, expected",
    [
        ({"prescription_date": "2026-01-02", "test_date": "2026-01-02"}, "2026-01-02"),
        ({"test_date": "2026-03-04T00:00:00"}, "2026-03-04"),
        ({"created_at": "2026-05-06T00:00:00"}, None),  # not a recorded issue date
    ],
)
def test_portal_issue_date_aliases(stored, expected):
    assert portal._safe_prescription_view(stored)["prescription_date"] == expected


@pytest.mark.parametrize(
    "stored, expected",
    [
        ({"ipd": "62.0"}, "62.0"),
        ({"pd": 61}, 61),
        ({"pupillary_distance": "60"}, "60"),
        ({"ipd": "None", "pd": 64}, 64),
        ({"right_eye": {"pd": "31"}, "left_eye": {"pd": "31"}}, None),  # never derived
    ],
)
def test_portal_binocular_pd_aliases(stored, expected):
    assert portal._safe_prescription_view(stored)["pd"] == expected


def test_canonical_print_date_is_the_ist_day_of_the_exam(monkeypatch):
    """An exam at 02:00 IST is stamped '...T20:30:00+00:00' the day before;
    a [:10] slice printed the UTC (previous) day (BUG-104 rule)."""
    html = _canonical_html(_client(monkeypatch, _exam_rx(test_date="2026-11-11T20:30:00+00:00")))
    assert "Date: 2026-11-12" in html


# ======================================================= Codex PR-1430 round 1
# (1) portal issued date must not move with the viewer's timezone: the server
#     sends the IST calendar day, never the raw stored instant.
def test_portal_issue_date_is_the_ist_day_not_the_raw_instant():
    view = portal._safe_prescription_view(_exam_rx(test_date="2026-11-13T20:30:00+00:00"))
    assert view["prescription_date"] == "2026-11-14"


# (2) an undated legacy Rx: both print doors say "Not recorded"; created_at (when
#     the row was written) is never printed as the issue date.
UNDATED = {"test_date": None, "created_at": "2026-11-01T08:00:00+00:00"}


def test_undated_rx_clinical_print_never_prints_created_at(monkeypatch):
    html = _clinical_html(_client(monkeypatch, _exam_rx(**UNDATED)))
    assert "<b>Date:</b> Not recorded" in html
    assert "01 Nov 2026" not in html


def test_undated_rx_canonical_print_agrees(monkeypatch):
    html = _canonical_html(_client(monkeypatch, _exam_rx(**UNDATED)))
    assert "Date: Not recorded" in html


# (3) an offset-aware stored date 500'd the WHOLE family history (naive IST now
#     compared with an aware expiry).
AWARE_ROWS = [
    _exam_rx(prescription_id="rx-mid", test_date="2026-11-13T20:30:00+00:00"),
    _exam_rx(prescription_id="rx-undated", patient_id="p-ravi", test_date=None,
             created_at="2026-11-01T08:00:00+00:00"),
    _exam_rx(prescription_id="rx-frac", test_date="2026-11-14T05:30:35.811187+00:00"),
]


@pytest.mark.parametrize("rx", AWARE_ROWS, ids=lambda r: r["prescription_id"])
def test_rx_validity_handles_an_offset_aware_date_on_the_ist_clock(rx):
    from datetime import datetime

    expiry, valid = prescriptions._rx_validity(rx)
    assert expiry.tzinfo is None
    want = {
        "rx-mid": datetime(2027, 11, 14, 2, 0),  # 20:30 UTC = 02:00 IST next day
        "rx-undated": datetime(2027, 11, 1, 13, 30),
        "rx-frac": datetime(2027, 11, 14, 11, 0, 35, 811187),
    }[rx["prescription_id"]]
    assert expiry == want
    assert isinstance(valid, bool)


class _FamRxRepo:
    def __init__(self, rows):
        self._rows = rows

    def find_by_customer(self, cid):
        return [dict(r) for r in self._rows if r.get("customer_id") == cid]


def _family_client(monkeypatch, rx_repo, cust_repo):
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: rx_repo)
    monkeypatch.setattr(prescriptions, "get_customer_repository", lambda: cust_repo)
    return _client(monkeypatch, _exam_rx())


def test_family_history_survives_offset_aware_dates(monkeypatch):
    for r in AWARE_ROWS:
        r["store_id"] = STORE
    c = _family_client(monkeypatch, _FamRxRepo(AWARE_ROWS), _Repo(ACCOUNT, key="customer_id"))
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: _FamRxRepo(AWARE_ROWS))
    r = c.get("/prescriptions/family/cust-1")
    assert r.status_code == 200, r.text
    ids = {p["prescription_id"] for m in r.json()["members"] for p in m["prescriptions"]}
    assert ids == {"rx-mid", "rx-undated", "rx-frac"}


def test_family_history_missing_store_is_503_not_an_empty_history(monkeypatch):
    c = _family_client(monkeypatch, None, _Repo(ACCOUNT, key="customer_id"))
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: None)
    assert c.get("/prescriptions/family/cust-1").status_code == 503
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: _FamRxRepo([]))
    monkeypatch.setattr(prescriptions, "get_customer_repository", lambda: None)
    assert c.get("/prescriptions/family/cust-1").status_code == 503


# (4) D71: every portal card names the family member examined.
def _portal_get(monkeypatch, rows, cust_repo):
    monkeypatch.setattr(deps, "get_prescription_repository", lambda: _FamRxRepo(rows) if rows is not None else None)
    monkeypatch.setattr(deps, "get_customer_repository", lambda: cust_repo)
    app = FastAPI()
    app.include_router(portal.router, prefix="/portal")
    tok = portal._issue_view_token("cust-1")
    return TestClient(app).get("/portal/rx", headers={"Authorization": "Bearer " + tok})


def test_portal_labels_every_member(monkeypatch):
    rows = [
        _exam_rx(prescription_id="rx-a"),  # dependent
        _exam_rx(prescription_id="rx-h", patient_id="p-ravi"),  # holder
        _exam_rx(prescription_id="rx-x", patient_id="p-nobody"),  # unknown member
    ]
    r = _portal_get(monkeypatch, rows, _Repo(ACCOUNT, key="customer_id"))
    assert r.status_code == 200, r.text
    names = {p["prescription_id"]: p["patient_name"] for p in r.json()["prescriptions"]}
    assert names == {"rx-a": "Asha Daughter", "rx-h": "Ravi Holder", "rx-x": None}


class _Boom:
    def find_by_id(self, _id):
        raise RuntimeError("mongo down")


def test_portal_read_failures_are_503_never_a_false_empty_or_unnamed_list(monkeypatch):
    assert _portal_get(monkeypatch, None, _Repo(ACCOUNT, key="customer_id")).status_code == 503
    assert _portal_get(monkeypatch, [_exam_rx()], _Boom()).status_code == 503
    assert _portal_get(monkeypatch, [_exam_rx()], None).status_code == 503
