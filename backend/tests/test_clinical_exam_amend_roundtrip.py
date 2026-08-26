"""
IMS 2.0 - amending an eye test must never ERASE what it did not change
======================================================================
Owner report 2026-08-24: "in clinic edit tab opens another screen, make it open
the same screen in which we put values in the first place so that we can edit
all fields such as lensometer and slit lamp values as well".

The Edit pencil opened the Rx-only POS form, so a lensometer / slit-lamp /
auto-ref / subjective reading could be recorded and then never corrected. It
now reopens the seven-tab exam screen, which saves through
PUT /clinical/tests/{id}/exam -- and THAT is the dangerous half: an "edit" that
shows a tab blank and then saves would wipe a patient's clinical readings, a
worse outcome than the bug it replaces.

So the deciding test here is a ROUND TRIP through the REAL router and the REAL
repositories (in-memory StrictCollection, no MongoDB):

    complete an exam -> read it back -> send it back with ONE field changed
    -> read it again -> assert EVERY other key is byte-identical.

Also asserted:
  * the tabs stored on the document, as a SET (a count would pass with the
    wrong four);
  * the previous reading survives on the append-only `amendments` list;
  * a field the exam screen did not carry (a legacy exam has no stored IPD)
    never blanks the lab-facing prescription;
  * the amendment goes through the SAME clinical range gate as the first save,
    and a rejected amendment changes NOTHING;
  * a caller from another store gets 404 (existence hidden), like every other
    clinical read/write.

ASCII only (Windows cp1252).
"""

from __future__ import annotations

import copy
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import clinical  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from database.repositories.clinical_repository import EyeTestRepository  # noqa: E402
from database.repositories.prescription_repository import (  # noqa: E402
    PrescriptionRepository,
)
from tests.strict_fakes import StrictCollection  # noqa: E402


TEST_ID = "test-1"
STORE = "store-001"


# ---------------------------------------------------------------------------
# The exam, as the seven-tab form sends it. Every tab populated on purpose:
# a mapper that forgets one is caught by the round trip, not by inspection.
# ---------------------------------------------------------------------------
FULL_EXAM = {
    "examDate": "2026-08-01",
    "optometristName": "Dr Rao",
    "chiefComplaint": "Blurred distance vision",
    "vduUsage": "6-8 hours",
    "lensometer": {
        "rightEye": {
            "sphere": "+3.75", "cylinder": "-0.50", "axis": "85",
            "add": "+1.75", "pd": "32", "va": "6/9",
        },
        "leftEye": {
            "sphere": "+3.25", "cylinder": "-0.25", "axis": "95",
            "add": "+1.75", "pd": "32", "va": "6/9",
        },
        "remarks": "Previous pair, 2 years old",
    },
    "autoRef": {
        "rightEye": {
            "sphere": "+4.25", "cylinder": "-0.75", "axis": "90",
            "k1": "43.25", "k1Axis": "90", "k2": "44.00", "k2Axis": "180",
        },
        "leftEye": {
            "sphere": "+3.75", "cylinder": "-0.25", "axis": "95",
            "k1": "43.00", "k1Axis": "85", "k2": "43.75", "k2Axis": "175",
        },
        "remarks": "Auto-ref pre-dilation",
    },
    "subjectiveRx": {
        "rightEye": {
            "sphere": "+4.00", "cylinder": "-0.75", "axis": "90",
            "add": "+2.00", "pd": "32.5", "va": "6/6",
        },
        "leftEye": {"sphere": "+3.50", "add": "+2.00", "pd": "32", "va": "6/6"},
        "remarks": "Accepted comfortably",
    },
    "slitLamp": {
        "rightEye": {
            "lids": "Normal", "conjunctiva": "Clear", "cornea": "Clear",
            "ac": "Deep", "iris": "Normal", "pupil": "Round reactive",
            "lens": "Early NS", "fundus": "Normal", "iop": 16,
        },
        "leftEye": {
            "lids": "Normal", "conjunctiva": "Mild injection", "cornea": "Clear",
            "ac": "Deep", "iris": "Normal", "pupil": "Round reactive",
            "lens": "Clear", "fundus": "Normal", "iop": 15,
        },
        "remarks": "Early nuclear sclerosis RE",
    },
    "rightEye": {
        "sphere": "+4.00", "cylinder": "-0.75", "axis": "90",
        "add": "+2.00", "pd": "32.5", "va": "6/6",
    },
    "leftEye": {"sphere": "+3.50", "add": "+2.00", "pd": "32", "va": "6/6"},
    "ipd": "64",
    "lensRecommendation": "Progressive",
    "nextCheckup": "2027-08-01",
    "notes": "Blurred distance vision",
    "clinicalFindings": {
        "iopRight": 16, "iopLeft": 15, "diagnosis": "Presbyopia with early NS",
    },
    "soapNote": {"assessment": "Presbyopia", "plan": "Progressive lenses"},
}

# The four exam tabs, by the key they are STORED under on the test document.
EXAM_TAB_KEYS = {"lensometer", "auto_ref", "subjective_rx", "slit_lamp"}

# Keys that are EXPECTED to move on an amendment -- everything else must not.
VOLATILE = {"updated_at", "amended_at", "amended_by", "amendments"}


class _Harness:
    """The real router + the real repositories over in-memory collections."""

    def __init__(self, monkeypatch, *, store_id: str = STORE, user_store: str = STORE):
        self.tests = StrictCollection(
            "eye_tests",
            [
                {
                    "_id": TEST_ID,
                    "test_id": TEST_ID,
                    "queue_id": "q-1",
                    "patient_name": "Asha Kumari",
                    "customer_id": "cust-1",
                    "patient_id": "pat-1",
                    "store_id": store_id,
                    "status": "IN_PROGRESS",
                }
            ],
        )
        self.rx = StrictCollection("prescriptions", [])
        self.audits = []
        test_repo = EyeTestRepository(self.tests)
        rx_repo = PrescriptionRepository(self.rx)

        monkeypatch.setattr(clinical, "get_eye_test_repository", lambda: test_repo)
        monkeypatch.setattr(clinical, "get_prescription_repository", lambda: rx_repo)
        monkeypatch.setattr(clinical, "get_eye_test_queue_repository", lambda: None)

        audits = self.audits

        class _AuditRepo:
            def create(self, doc):
                audits.append(doc)
                return doc

        monkeypatch.setattr(clinical, "get_audit_repository", lambda: _AuditRepo())

        app = FastAPI()
        app.include_router(clinical.router, prefix="/clinical")

        async def _fake_user():
            return {
                "user_id": "u1",
                "username": "opto",
                "full_name": "Dr Rao",
                "active_store_id": user_store,
                "store_id": user_store,
                "roles": ["OPTOMETRIST"],
            }

        app.dependency_overrides[get_current_user] = _fake_user
        self.client = TestClient(app)

    # -- the three calls under test ---------------------------------------
    def complete(self, body=None):
        return self.client.post(
            "/clinical/tests/%s/complete" % TEST_ID, json=body or FULL_EXAM
        )

    def read(self):
        return self.client.get("/clinical/tests/%s" % TEST_ID)

    def amend(self, body):
        return self.client.put("/clinical/tests/%s/exam" % TEST_ID, json=body)

    def stored_test(self):
        return copy.deepcopy(self.tests.docs[0])

    def stored_rx(self):
        return copy.deepcopy(self.rx.docs[0]) if self.rx.docs else None


@pytest.fixture()
def exam(monkeypatch):
    h = _Harness(monkeypatch)
    resp = h.complete()
    assert resp.status_code == 200, resp.text
    return h


# ---------------------------------------------------------------------------
# The exam is stored in full in the first place -- without this, "edit" has
# nothing to reopen and every assertion below would be vacuous.
# ---------------------------------------------------------------------------


def test_completion_stores_every_exam_tab(exam):
    doc = exam.stored_test()
    assert EXAM_TAB_KEYS & set(doc) == EXAM_TAB_KEYS, sorted(set(doc))
    assert doc["lensometer"]["right_eye"]["sphere"] == "+3.75"
    assert doc["slit_lamp"]["left_eye"]["conjunctiva"] == "Mild injection"
    assert doc["auto_ref"]["right_eye"]["k1"] == "43.25"


def test_the_read_returns_every_tab_camel_cased(exam):
    body = exam.read().json()
    assert {"lensometer", "autoRef", "subjectiveRx", "slitLamp"} <= set(body)
    assert body["lensometer"]["rightEye"]["sphere"] == "+3.75"
    assert body["autoRef"]["rightEye"]["k1Axis"] == "90"
    assert body["prescription"]["ipd"] == "64", (
        "the exam must carry the BINOCULAR ipd itself -- the Edit screen has "
        "nowhere else to read it from"
    )


# ---------------------------------------------------------------------------
# THE DECIDING TEST
# ---------------------------------------------------------------------------


def test_amending_one_field_leaves_every_other_key_byte_identical(exam):
    before = exam.stored_test()

    changed = copy.deepcopy(FULL_EXAM)
    changed["lensometer"]["rightEye"]["sphere"] = "+3.50"
    resp = exam.amend(changed)
    assert resp.status_code == 200, resp.text

    after = exam.stored_test()

    # The one field the optometrist corrected.
    assert after["lensometer"]["right_eye"]["sphere"] == "+3.50"
    assert before["lensometer"]["right_eye"]["sphere"] == "+3.75"

    # ...and NOTHING else. Compared key by key so a failure names the key that
    # moved, not just "dicts differ".
    assert set(after) - set(before) == {"amended_at", "amended_by", "amendments"}
    assert set(before) - set(after) == set()
    for key in sorted(set(before) - VOLATILE - {"lensometer"}):
        assert after[key] == before[key], "amendment moved %s" % key

    # The tab itself only moved where it was told to.
    assert after["lensometer"]["left_eye"] == before["lensometer"]["left_eye"]
    assert after["lensometer"]["remarks"] == before["lensometer"]["remarks"]


def test_the_replaced_reading_survives_on_the_amendments_list(exam):
    changed = copy.deepcopy(FULL_EXAM)
    changed["lensometer"]["rightEye"]["sphere"] = "+3.50"
    exam.amend(changed)

    history = exam.stored_test()["amendments"]
    assert len(history) == 1
    assert history[0]["previous"]["lensometer"]["right_eye"]["sphere"] == "+3.75"
    assert history[0]["amended_by"] == "u1"

    # A second amendment APPENDS; it never rewrites the first.
    changed["lensometer"]["rightEye"]["sphere"] = "+3.25"
    exam.amend(changed)
    history = exam.stored_test()["amendments"]
    assert len(history) == 2
    assert history[0]["previous"]["lensometer"]["right_eye"]["sphere"] == "+3.75"
    assert history[1]["previous"]["lensometer"]["right_eye"]["sphere"] == "+3.50"


def test_an_amendment_does_not_re_date_the_soap_note(exam):
    # The exam screen sends the SOAP note back without its provenance. Stamping
    # it again would move recorded_at to the day of the CORRECTION and
    # re-attribute the note to whoever made it -- the clinical record would then
    # claim a history was taken on a day nobody saw the patient.
    before = exam.stored_test()["soap_note"]
    changed = copy.deepcopy(FULL_EXAM)
    changed["soapNote"]["assessment"] = "Presbyopia, progressing"
    exam.amend(changed)

    after = exam.stored_test()["soap_note"]
    assert after["assessment"] == "Presbyopia, progressing"
    assert after["recorded_at"] == before["recorded_at"]
    assert after["recorded_by"] == before["recorded_by"]


def test_amending_a_tab_does_not_disturb_the_dispensable_prescription(exam):
    rx_before = exam.stored_rx()
    assert rx_before is not None
    changed = copy.deepcopy(FULL_EXAM)
    changed["lensometer"]["rightEye"]["sphere"] = "+3.50"
    exam.amend(changed)

    rx_after = exam.stored_rx()
    for key in sorted(set(rx_before) - {"updated_at"}):
        assert rx_after[key] == rx_before[key], "a lensometer edit moved Rx %s" % key


def test_a_corrected_final_rx_reaches_the_dispensable_prescription(exam):
    # The mirror in the other direction: correcting the FINAL Rx must reach the
    # document the lab and the patient actually read, or the amendment would
    # fix the exam while the dispensed power stayed wrong.
    changed = copy.deepcopy(FULL_EXAM)
    changed["rightEye"]["sphere"] = "+4.50"
    exam.amend(changed)

    rx = exam.stored_rx()
    assert rx["right_eye"]["sph"] == "+4.50"
    assert rx["left_eye"]["sph"] == "+3.50"
    assert rx["ipd"] == "64"


def test_an_omitted_ipd_never_blanks_the_one_on_the_prescription(exam):
    # A legacy exam completed before the IPD was stored on the test document
    # opens with an EMPTY ipd box, so the amendment carries none. An empty box
    # must not reach the lab as "no pupillary distance".
    lean = copy.deepcopy(FULL_EXAM)
    lean.pop("ipd")
    lean.pop("lensRecommendation")
    resp = exam.amend(lean)
    assert resp.status_code == 200, resp.text

    rx = exam.stored_rx()
    assert rx["ipd"] == "64"
    assert rx["lens_recommendation"] == "Progressive"


def test_an_amendment_goes_through_the_same_range_gate_and_changes_nothing(exam):
    before = exam.stored_test()
    bad = copy.deepcopy(FULL_EXAM)
    bad["lensometer"]["rightEye"]["sphere"] = "-9999"
    resp = exam.amend(bad)
    assert resp.status_code == 422, resp.text
    assert exam.stored_test() == before, "a REJECTED amendment still wrote"


def test_an_amendment_from_another_store_is_404_not_403(monkeypatch):
    h = _Harness(monkeypatch, store_id="store-001", user_store="store-999")
    resp = h.amend(FULL_EXAM)
    assert resp.status_code == 404, resp.text
    assert "not found" in resp.json()["detail"].lower()


def test_an_unknown_test_is_404(exam):
    resp = exam.client.put("/clinical/tests/nope/exam", json=FULL_EXAM)
    assert resp.status_code == 404


def test_the_amendment_is_recorded_in_the_activity_log(exam):
    exam.audits.clear()
    exam.amend(copy.deepcopy(FULL_EXAM))
    actions = [a.get("action") for a in exam.audits]
    assert actions == ["EYE_TEST_AMENDED"], actions
    assert exam.audits[0]["entity_id"] == TEST_ID
