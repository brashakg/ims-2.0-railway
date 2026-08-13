"""
IMS 2.0 - PATIENT SAFETY / AUDIT: per-eye axis provenance (axis_source)
======================================================================
POS used to fabricate an axis of 180 when a prescription carried none. It now
asks the counter for the axis instead, and stamps WHERE THAT AXIS CAME FROM on
the eye, as `EyeData.axis_source = "COUNTER_ENTERED"`.

Why the eye and not `remarks` (this is the whole point of the field):

  * `remarks` is projected to the OTP-gated CUSTOMER portal by
    portal._safe_prescription_view as "notes" and rendered by RxPortalPage, and
    it is printed on the patient-facing Rx card. Provenance there tells the
    PATIENT their axis was supplied at the counter.
  * No internal staff screen renders `remarks` at all, so the optician handling
    the remake dispute the marker exists for could never see it.
  * `remarks` is also a single free-text blob - it cannot say WHICH eye - and
    PrescriptionUpdate.remarks $sets it wholesale, so any later edit erases it.

WHAT AN EARLIER VERSION OF THIS FILE GOT WRONG, and why the portal cases below
exist. The header reasoned about portal._safe_prescription_view and concluded
that only `remarks` travels to the patient - then the class that claimed to
prove it asserted over remarks / lens_recommendation / coating_recommendation /
ipd and NEVER CALLED THE PORTAL AT ALL. The projection handed the EYE
sub-document over verbatim, so `axis_source` reached the patient's browser the
whole time. The test's name was the claim; its body checked a different field.
Every case here that talks about the patient now calls the real projection.

Also pinned here: a corrected axis must not keep the old axis's provenance, and
the four roles the create door accepts (the server half of the POS role-truth
banner, frontend components/pos/POSLayout RX_SAVE_ROLES).

ASCII only (Windows cp1252).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import portal  # noqa: E402
from api.routers import prescriptions  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


COUNTER = "COUNTER_ENTERED"


class _CapturingRepo:
    """Captures exactly what the create door persists."""

    def __init__(self):
        self.created = []

    def create(self, data):
        doc = dict(data)
        doc.setdefault("prescription_id", f"rx-{len(self.created) + 1}")
        doc.setdefault("prescription_number", "RX-TEST-000001")
        self.created.append(doc)
        return doc


class _EditRepo:
    """A prescription store the PUT (clinic edit) door can read and write."""

    def __init__(self, doc):
        self.doc = dict(doc)

    def find_by_id(self, _prescription_id):
        return dict(self.doc)

    def update(self, _prescription_id, update_doc):
        # The real repository writes with $set, which REPLACES a whole
        # sub-document. Mirror that exactly -- a merge here would hide the very
        # class of bug the router's deep-merge exists to prevent.
        self.doc.update(update_doc)
        return True


def _client(monkeypatch, repo, roles=("OPTOMETRIST",)):
    app = FastAPI()
    app.include_router(prescriptions.router, prefix="/prescriptions")

    async def _fake_user():
        return {
            "user_id": "u-counter",
            "username": "counter",
            "full_name": "Counter Staff",
            "active_store_id": "store-001",
            "roles": list(roles),
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: repo)
    monkeypatch.setattr(prescriptions, "get_customer_repository", lambda: None)
    return TestClient(app)


def _body(**eyes):
    base = {
        "patient_id": "pat-1",
        "customer_id": "cust-1",
        "optometrist_id": "opt-1",
    }
    base.update(eyes)
    return base


class TestAxisSourcePersists:
    def test_counter_entered_axis_is_stored_on_that_eye(self, monkeypatch):
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo)
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={
                    "sph": "-2.00",
                    "cyl": "-1.25",
                    "axis": 85,
                    "axis_source": COUNTER,
                },
                left_eye={"sph": "-1.00", "cyl": "0"},
            ),
        )
        assert resp.status_code == 201, resp.text
        doc = repo.created[0]
        # Persisted automatically by right_eye.model_dump() -- no router change.
        assert doc["right_eye"]["axis_source"] == COUNTER
        assert doc["right_eye"]["axis"] == 85

    def test_the_other_eye_is_not_marked(self, monkeypatch):
        """Per-eye is the point: only the eye that was typed in is flagged."""
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo)
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "-0.75", "axis": 90},
            ),
        )
        assert resp.status_code == 201, resp.text
        doc = repo.created[0]
        assert doc["right_eye"]["axis_source"] == COUNTER
        assert doc["left_eye"]["axis_source"] is None

    def test_a_clinician_recorded_rx_carries_no_marker(self, monkeypatch):
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo)
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85},
                left_eye={"sph": "-1.00", "cyl": "-0.75", "axis": 90},
            ),
        )
        assert resp.status_code == 201, resp.text
        doc = repo.created[0]
        assert doc["right_eye"]["axis_source"] is None
        assert doc["left_eye"]["axis_source"] is None


class TestProvenanceStaysOutOfPatientFacingFields:
    def test_marker_never_lands_in_remarks(self, monkeypatch):
        """`remarks` reaches the customer portal and the printed Rx card."""
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo)
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "0"},
                remarks="Dr. Rao",
            ),
        )
        assert resp.status_code == 201, resp.text
        doc = repo.created[0]
        assert doc["remarks"] == "Dr. Rao"
        # Nothing patient-reachable mentions the counter entry.
        for field in ("remarks", "lens_recommendation", "coating_recommendation", "ipd"):
            assert COUNTER not in str(doc.get(field) or "")

    def test_the_portal_projection_never_ships_the_marker(self, monkeypatch):
        """THE CASE THIS CLASS WAS MISSING: drive the real customer projection.

        `portal._safe_prescription_view` is what the OTP-gated CUSTOMER endpoint
        returns. It used to hand the eye sub-document over verbatim, so a
        patient reading their own prescription received a machine-readable flag
        saying their astigmatic axis was typed at the shop counter rather than
        measured -- the exact disclosure that loses a remake dispute. Taking it
        off `remarks` and off both printed cards did not close this door.
        """
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo)
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "-0.75", "axis": 90, "axis_source": COUNTER},
                remarks="Dr. Rao",
            ),
        )
        assert resp.status_code == 201, resp.text

        view = portal._safe_prescription_view(repo.created[0])
        # BOTH eyes: a fix applied to one eye only must fail here.
        for eye_key in ("right_eye", "left_eye"):
            assert "axis_source" not in view[eye_key], view[eye_key]
        # And nowhere else in the payload either.
        assert COUNTER not in str(view)

    def test_the_patient_still_gets_their_own_clinical_values(self):
        """The projection must not become a blanket redaction: an Rx the
        customer cannot read is worse than useless. SPH/CYL/AXIS/ADD are the
        customer's OWN data and the portal page renders exactly those."""
        view = portal._safe_prescription_view(
            {
                "prescription_id": "rx-1",
                "right_eye": {
                    "sph": "-2.00",
                    "cyl": "-1.25",
                    "axis": 85,
                    "add": "+2.00",
                    "pd": "32",
                    "axis_source": COUNTER,
                },
                "left_eye": {"sphere": "-1.00", "cylinder": "-0.75", "axis": 90},
            }
        )
        assert view["right_eye"] == {
            "sph": "-2.00",
            "cyl": "-1.25",
            "axis": 85,
            "add": "+2.00",
            "pd": "32",
        }
        # Legacy alias-keyed eyes (the finalize mirror's shape) still render.
        assert view["left_eye"] == {"sphere": "-1.00", "cylinder": "-0.75", "axis": 90}

    def test_an_unknown_internal_field_is_dropped_by_default(self):
        """The point of an ALLOWLIST. The eye is a whole-model dump on the write
        side, so the NEXT internal field added to EyeData persists with no
        router change -- and must not reach the patient just because nobody
        thought to add it to a denylist here."""
        view = portal._safe_prescription_view(
            {
                "right_eye": {
                    "sph": "-2.00",
                    "axis": 85,
                    "reviewed_by_user_id": "u-999",
                    "internal_flag_not_yet_invented": "SOMETHING_INTERNAL",
                },
                "left_eye": None,
            }
        )
        assert view["right_eye"] == {"sph": "-2.00", "axis": 85}
        assert view["left_eye"] is None


class TestAxisSourceIsAClosedVocabulary:
    @pytest.mark.parametrize("bad", ["counter_entered", "GUESSED", "", "COUNTER ENTERED"])
    def test_an_unknown_source_is_rejected_not_stored(self, monkeypatch, bad):
        """A Literal, not a bare str: a typo can never masquerade as provenance
        (and a silent rename would break loudly here rather than orphan rows)."""
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo)
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": bad},
                left_eye={"sph": "-1.00", "cyl": "0"},
            ),
        )
        assert resp.status_code == 422, resp.text
        assert repo.created == []

    def test_the_toric_axis_gate_is_unchanged_by_the_new_field(self, monkeypatch):
        """A cylinder with no axis is still rejected -- provenance is metadata,
        never a way to smuggle an axis-less toric past the clinical gate."""
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo)
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "0"},
            ),
        )
        assert resp.status_code == 422, resp.text
        assert "axis" in resp.text
        assert repo.created == []


# ============================================================================
# A CORRECTED axis must not keep the previous axis's provenance
# ============================================================================
# The marker describes the AXIS, not the eye. Once an optometrist re-measures
# and corrects a counter-entered axis, a surviving COUNTER_ENTERED attributes
# the clinician's measurement to a counter guess -- permanently, and in the one
# place the marker is ever read (a remake dispute).
#
# EyeDataEdit deliberately has no `axis_source` field, so the clinic edit door
# could not clear it even on purpose. The merge clears it instead.


def _stored_rx(**eyes):
    doc = {
        "prescription_id": "rx-edit-1",
        "customer_id": "cust-1",
        "patient_id": "pat-1",
        "store_id": "store-001",
        "rx_kind": "SPECTACLE",
    }
    doc.update(eyes)
    return doc


def _edit_client(monkeypatch, repo, roles=("OPTOMETRIST",)):
    client = _client(monkeypatch, repo, roles=roles)
    monkeypatch.setattr(prescriptions, "can_access_store_scoped", lambda *_a, **_k: True)
    return client


class TestCorrectingAnAxisClearsItsProvenance:
    def test_a_corrected_right_eye_axis_drops_the_counter_marker(self, monkeypatch):
        repo = _EditRepo(
            _stored_rx(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "0"},
            )
        )
        client = _edit_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-edit-1", json={"right_eye": {"axis": 92}})
        assert resp.status_code == 200, resp.text
        eye = repo.doc["right_eye"]
        assert eye["axis"] == 92
        assert "axis_source" not in eye, eye
        # The partial edit still did not blank the rest of the eye.
        assert eye["sph"] == "-2.00" and eye["cyl"] == "-1.25"

    def test_the_same_holds_for_the_left_eye(self, monkeypatch):
        """The sibling. A rule applied to one eye and not its twin is this
        repo's dominant defect shape."""
        repo = _EditRepo(
            _stored_rx(
                right_eye={"sph": "-2.00", "cyl": "0"},
                left_eye={"sph": "-1.00", "cyl": "-0.75", "axis": 90, "axis_source": COUNTER},
            )
        )
        client = _edit_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-edit-1", json={"left_eye": {"axis": 12}})
        assert resp.status_code == 200, resp.text
        eye = repo.doc["left_eye"]
        assert eye["axis"] == 12
        assert "axis_source" not in eye, eye

    def test_correcting_one_eye_leaves_the_other_eyes_marker_alone(self, monkeypatch):
        """Provenance is PER EYE. Correcting the right eye says nothing about
        how the left eye's axis was recorded."""
        repo = _EditRepo(
            _stored_rx(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "-0.75", "axis": 90, "axis_source": COUNTER},
            )
        )
        client = _edit_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-edit-1", json={"right_eye": {"axis": 92}})
        assert resp.status_code == 200, resp.text
        assert "axis_source" not in repo.doc["right_eye"]
        assert repo.doc["left_eye"]["axis_source"] == COUNTER

    def test_re_sending_the_same_axis_is_not_a_correction(self, monkeypatch):
        """Nothing moved, so nothing is misattributed: the stored axis IS still
        the counter-entered one. Clearing here would quietly launder a counter
        entry into a clinician measurement on any full-eye save."""
        repo = _EditRepo(
            _stored_rx(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "0"},
            )
        )
        client = _edit_client(monkeypatch, repo)
        resp = client.put(
            "/prescriptions/rx-edit-1",
            json={"right_eye": {"sph": "-2.25", "cyl": "-1.25", "axis": 85}},
        )
        assert resp.status_code == 200, resp.text
        assert repo.doc["right_eye"]["axis_source"] == COUNTER
        assert repo.doc["right_eye"]["sph"] == "-2.25"

    def test_a_legacy_string_axis_re_sent_as_a_number_is_not_a_correction(self, monkeypatch):
        """A stored "85" and a patched 85 are the same meridian. Comparing them
        as raw values would clear a marker that is still true."""
        repo = _EditRepo(
            _stored_rx(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": "85", "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "0"},
            )
        )
        client = _edit_client(monkeypatch, repo)
        resp = client.put(
            "/prescriptions/rx-edit-1", json={"right_eye": {"axis": 85, "sph": "-2.25"}}
        )
        assert resp.status_code == 200, resp.text
        assert repo.doc["right_eye"]["axis_source"] == COUNTER

    def test_editing_a_power_without_touching_the_axis_keeps_the_marker(self, monkeypatch):
        """The marker is about the axis. Correcting the sphere says nothing
        about where the axis came from."""
        repo = _EditRepo(
            _stored_rx(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "0"},
            )
        )
        client = _edit_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-edit-1", json={"right_eye": {"sph": "-2.25"}})
        assert resp.status_code == 200, resp.text
        assert repo.doc["right_eye"]["axis_source"] == COUNTER
        assert repo.doc["right_eye"]["axis"] == 85

    def test_the_clinic_edit_door_cannot_stamp_a_counter_marker(self, monkeypatch):
        """EyeDataEdit has no `axis_source` field, on purpose: the only defined
        marker means "typed at the POS counter", which a clinic edit can never
        truthfully claim. A caller that sends one is ignored, not obeyed."""
        repo = _EditRepo(
            _stored_rx(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85},
                left_eye={"sph": "-1.00", "cyl": "0"},
            )
        )
        client = _edit_client(monkeypatch, repo)
        resp = client.put(
            "/prescriptions/rx-edit-1",
            json={"right_eye": {"axis": 92, "axis_source": COUNTER}},
        )
        assert resp.status_code == 200, resp.text
        assert "axis_source" not in repo.doc["right_eye"], repo.doc["right_eye"]

    def test_a_corrected_axis_still_has_to_be_clinically_valid(self, monkeypatch):
        """Clearing provenance must not become a way past the toric gate."""
        repo = _EditRepo(
            _stored_rx(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "0"},
            )
        )
        client = _edit_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-edit-1", json={"right_eye": {"axis": None}})
        assert resp.status_code == 400, resp.text
        assert "axis" in resp.text
        # Nothing was written: the stored eye is untouched, marker included.
        assert repo.doc["right_eye"]["axis"] == 85
        assert repo.doc["right_eye"]["axis_source"] == COUNTER


# ============================================================================
# Who may create a prescription -- the SERVER half of the POS role banner
# ============================================================================
# POS shows a warning in the counter-axis prompt when the signed-in role cannot
# actually save ("you can enter the axis, but saving needs a manager or
# optometrist"), driven by RX_SAVE_ROLES in frontend components/pos/POSLayout.
# That list is a MIRROR of what this door enforces. If the two drift, the banner
# either nags a role that can save or lets a cashier type a clinical value into
# a 403 with the sale stranded -- the exact stall the prompt exists to avoid.
# The frontend half is pinned by POSAxisPrompt.test.tsx ("mirrors the backend
# clinical-role list exactly").


class TestWhoMayCreateAPrescription:
    @pytest.mark.parametrize(
        "role", ["SUPERADMIN", "ADMIN", "STORE_MANAGER", "OPTOMETRIST"]
    )
    def test_a_clinical_role_may_save(self, monkeypatch, role):
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo, roles=(role,))
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "0"},
            ),
        )
        assert resp.status_code == 201, resp.text

    @pytest.mark.parametrize("role", ["CASHIER", "SALESPERSON", "TECHNICIAN"])
    def test_a_non_clinical_role_is_refused(self, monkeypatch, role):
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo, roles=(role,))
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "0"},
            ),
        )
        assert resp.status_code == 403, resp.text
        assert repo.created == []
