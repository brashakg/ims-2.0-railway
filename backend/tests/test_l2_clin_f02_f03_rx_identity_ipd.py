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
    assert view["prescription_date"] == "2026-11-12T05:00:00Z"
    assert view["pd"] == "63.5"


@pytest.mark.parametrize(
    "stored, expected",
    [
        ({"prescription_date": "2026-01-02", "test_date": "2026-01-02"}, "2026-01-02"),
        ({"test_date": "2026-03-04T00:00:00"}, "2026-03-04T00:00:00"),
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
