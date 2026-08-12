"""
IMS 2.0 - PATIENT SAFETY: the Rx VERSION patch must merge, not replace
======================================================================
Sibling of test_rx_toric_axis_and_merge.py (PR #969, finding F12). That PR
deep-merged the TOP-LEVEL eyes on PUT /prescriptions/{id}; the 4-version door
did not get the same treatment and is fixed here.

THE DEFECT
  `prescription_versions.merge_version` writes `versions[name] = payload`, i.e.
  it REPLACES the whole version sub-document. So

      PATCH /prescriptions/{id}/version/final  {"right_eye": {"sphere": -1.5}}

  dropped that slot's left_eye / pd / source AND blanked the rest of right_eye
  (cylinder / axis / addition). `versions.final` is MIRRORED into the top-level
  right_eye/left_eye that POS and the workshop read (POST /finalize), so a
  blanked version power could propagate into the DISPENSING record - the powers
  ground into a patient's lenses.

THE FIX (same semantics PR #969 established, applied one level deeper)
  1. a PARTIAL patch PRESERVES every power it does not mention;
  2. an EXPLICIT null still CLEARS a field (absent != null - that distinction
     is the whole design, and it is what `exclude_unset` buys us);
  3. validation runs on the MERGED slot, NEVER on the raw patch - what is about
     to be STORED is what has to be clinically valid. `{"axis": null}` on an eye
     whose STORED cylinder is -1.25 is clean as a patch and un-grindable as a
     prescription;
  4. the toric rule (non-zero CYL requires an AXIS) holds for versions too;
  5. the sph/sphere alias twins stay in step, because the finalize mirror
     (`_canonical_eye`) reads `sph` BEFORE `sphere` - a merge that updated only
     one of them would mirror the PRE-EDIT power into the dispensing record.

Every test drives the REAL endpoint through TestClient (this repo has a history
of tests that pass while the defect is live), and every rejection asserts
`repo.updates == []` - nothing was written at all, not written-then-rolled-back.

Bare-app + dependency-override / monkeypatch harness (mirrors
test_rx_toric_axis_and_merge.py) - no DB required. ASCII only (Windows cp1252).
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

from api.routers import prescriptions  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ============================================================================
# In-memory fake + client
# ============================================================================


class _FakeSingleRxRepo:
    """Stand-in for PrescriptionRepository holding ONE editable doc.

    `updates` records every write, so a test can assert a rejected patch wrote
    NOTHING (rather than writing and then being corrected).
    """

    def __init__(self, doc):
        self._doc = doc
        self.updates = []

    def find_by_id(self, _id):
        if self._doc and self._doc.get("prescription_id") == _id:
            # Deep-ish copy so a handler mutating what it read cannot fake a
            # pass by writing through to the stored document.
            import copy

            return copy.deepcopy(self._doc)
        return None

    def update(self, _id, data):
        if not self._doc or self._doc.get("prescription_id") != _id:
            return False
        self.updates.append(dict(data))
        self._doc.update(data)
        return True


def _rx_client(monkeypatch, repo, roles=("OPTOMETRIST",), user_id="u-opto"):
    app = FastAPI()
    app.include_router(prescriptions.router, prefix="/prescriptions")

    async def _fake_user():
        return {
            "user_id": user_id,
            "username": "opto",
            "full_name": "Dr Test",
            "active_store_id": "store-001",
            "roles": list(roles),
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: repo)
    monkeypatch.setattr(prescriptions, "get_customer_repository", lambda: None)
    return TestClient(app)


def _seed_versioned_doc():
    """An in_progress Rx that ALREADY carries a populated version slot.

    No top-level eyes -> `backfill_versions_from_top_level` is a no-op, so what
    the handler merges against is exactly what is seeded here.
    """
    return {
        "prescription_id": "rx-1",
        "prescription_number": "RX-260808-ABC123",
        "store_id": "store-001",
        "customer_id": "cust-1",
        "status": "in_progress",
        "versions": {
            "before_testing": None,
            "after_testing": {
                "right_eye": {
                    "sphere": -1.0,
                    "cylinder": -1.25,
                    "axis": 90,
                    "addition": 2.0,
                    "va": "6/6",
                },
                "left_eye": {
                    "sphere": -1.25,
                    "cylinder": -0.5,
                    "axis": 85,
                    "addition": 2.0,
                    "va": "6/9",
                },
                "pd": 62.0,
                "source": "subjective_refraction",
                "captured_at": "2026-08-01T10:00:00+00:00",
                "captured_by": "u-earlier",
            },
            "manual": None,
            "final": None,
        },
    }


def _seed_legacy_doc():
    """A legacy single-Rx doc: TOP-LEVEL eyes keyed sph/cyl/add, no versions.

    Read-time backfill materialises `versions.final` FROM those top-level eyes,
    so the stored slot carries the canonical sph/cyl/add twins while the
    version door patches sphere/cylinder/addition. This is where the alias sync
    earns its keep.
    """
    return {
        "prescription_id": "rx-1",
        "store_id": "store-001",
        "customer_id": "cust-1",
        "status": "in_progress",
        "created_at": "2026-05-01T10:00:00",
        "optometrist_id": "opt-1",
        "right_eye": {
            "sph": "-1.00", "cyl": "-0.50", "axis": 90, "add": "2.00", "pd": "32",
        },
        "left_eye": {
            "sph": "-1.25", "cyl": "-0.25", "axis": 85, "add": "2.00", "pd": "32",
        },
    }


def _stored_slot(repo, name="after_testing"):
    return repo._doc["versions"][name]


# ============================================================================
# 1 - a PARTIAL patch preserves every power it did not mention
# ============================================================================


class TestPartialVersionPatchPreserves:
    def test_single_power_patch_keeps_the_rest_of_that_eye(self, monkeypatch):
        """THE core regression: one field in, four fields survive."""
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"sphere": -1.5}},
        )
        assert resp.status_code == 200, resp.text
        right = _stored_slot(repo)["right_eye"]
        assert right["sphere"] == -1.5           # the edit landed
        assert right["cylinder"] == -1.25        # ...and nothing else moved
        assert right["axis"] == 90
        assert right["addition"] == 2.0
        assert right["va"] == "6/6"

    def test_single_power_patch_keeps_the_OTHER_eye(self, monkeypatch):
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"sphere": -1.5}},
        )
        assert resp.status_code == 200, resp.text
        left = _stored_slot(repo)["left_eye"]
        assert left == {
            "sphere": -1.25, "cylinder": -0.5, "axis": 85,
            "addition": 2.0, "va": "6/9",
        }

    def test_single_power_patch_keeps_slot_level_fields(self, monkeypatch):
        """pd / source / override_reason live on the SLOT, not the eye, and were
        dropped by the wholesale replace just as surely as the powers were."""
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"sphere": -1.5}},
        )
        assert resp.status_code == 200, resp.text
        slot = _stored_slot(repo)
        assert slot["pd"] == 62.0
        assert slot["source"] == "subjective_refraction"

    def test_response_body_shows_the_merged_slot(self, monkeypatch):
        """The editor re-renders from the response, so it must not show the
        caller a blanked eye that was never actually stored."""
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"sphere": -1.5}},
        )
        assert resp.status_code == 200, resp.text
        right = resp.json()["versions"]["after_testing"]["right_eye"]
        assert right["cylinder"] == -1.25
        assert right["axis"] == 90

    def test_patching_an_empty_slot_still_writes_just_the_patch(self, monkeypatch):
        """A slot that has never been filled has nothing to preserve - the merge
        must not invent values from a sibling slot."""
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/manual",
            json={"right_eye": {"sphere": -3.0}, "source": "manual_override"},
        )
        assert resp.status_code == 200, resp.text
        slot = _stored_slot(repo, "manual")
        assert slot["right_eye"] == {"sphere": -3.0}
        assert slot.get("left_eye") is None
        assert slot["source"] == "manual_override"

    def test_provenance_names_the_clinician_who_made_THIS_edit(self, monkeypatch):
        """Merging must not carry the stored captured_by forward - a partial
        edit would then stay attributed to whoever first filled the slot."""
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo, user_id="u-second-opto")
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"sphere": -1.5}},
        )
        assert resp.status_code == 200, resp.text
        slot = _stored_slot(repo)
        assert slot["captured_by"] == "u-second-opto"
        assert slot["captured_at"] != "2026-08-01T10:00:00+00:00"

    def test_version_patch_never_touches_the_top_level_eyes(self, monkeypatch):
        """Only POST /finalize may write the top-level (dispensing) eyes."""
        repo = _FakeSingleRxRepo(_seed_legacy_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/manual",
            json={"right_eye": {"sphere": -9.0}},
        )
        assert resp.status_code == 200, resp.text
        assert len(repo.updates) == 1
        assert set(repo.updates[0]) == {"versions", "status"}
        assert repo._doc["right_eye"]["sph"] == "-1.00"


# ============================================================================
# 2 - an EXPLICIT null still CLEARS (absent != null)
# ============================================================================


class TestExplicitNullStillClears:
    def test_explicit_null_clears_one_power(self, monkeypatch):
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"addition": None}},
        )
        assert resp.status_code == 200, resp.text
        right = _stored_slot(repo)["right_eye"]
        assert right["addition"] is None         # cleared on purpose
        assert right["sphere"] == -1.0           # neighbours untouched
        assert right["cylinder"] == -1.25
        assert right["axis"] == 90

    def test_explicit_null_clears_a_slot_level_field(self, monkeypatch):
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"pd": None},
        )
        assert resp.status_code == 200, resp.text
        slot = _stored_slot(repo)
        assert slot["pd"] is None
        assert slot["right_eye"]["cylinder"] == -1.25

    def test_explicit_null_clears_a_whole_eye(self, monkeypatch):
        """Clearing an eye outright stays possible - and `can_finalize` then
        refuses to finalize, which is the safe end state."""
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": None},
        )
        assert resp.status_code == 200, resp.text
        assert _stored_slot(repo)["right_eye"] is None
        assert _stored_slot(repo)["left_eye"]["sphere"] == -1.25

    def test_clearing_the_cylinder_also_clears_its_alias_twin(self, monkeypatch):
        """A backfilled slot carries BOTH `cyl` and (after a patch) `cylinder`.
        Clearing one while the other survives would leave the finalize mirror
        reading a cylinder the clinician just removed."""
        repo = _FakeSingleRxRepo(_seed_legacy_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/final",
            json={"right_eye": {"cylinder": None}},
        )
        assert resp.status_code == 200, resp.text
        right = _stored_slot(repo, "final")["right_eye"]
        assert right["cylinder"] is None
        assert right["cyl"] is None
        assert right["sph"] == "-1.00"


# ============================================================================
# 3 - validation runs on the MERGED slot, never on the raw patch
# ============================================================================


class TestValidationRunsOnTheMergedSlot:
    def test_clearing_the_axis_of_a_stored_toric_eye_is_REJECTED(self, monkeypatch):
        """THE ordering test. `{"axis": null}` is a clean-looking patch; the
        MERGED eye it produces has cylinder -1.25 and no axis, which is
        un-grindable. Validating the patch alone lets it straight through."""
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"axis": None}},
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert isinstance(detail, str)           # readable toast, not a Pydantic list
        assert "Right eye" in detail
        assert "-1.25" in detail
        assert "no axis" in detail
        # Nothing was written AT ALL - not written and then corrected.
        assert repo.updates == []
        assert _stored_slot(repo)["right_eye"]["axis"] == 90

    def test_adding_a_cylinder_to_an_axis_less_eye_is_REJECTED(self, monkeypatch):
        doc = _seed_versioned_doc()
        doc["versions"]["after_testing"]["right_eye"] = {"sphere": -1.0, "axis": None}
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"cylinder": -1.25}},
        )
        assert resp.status_code == 422, resp.text
        assert "no axis" in resp.json()["detail"]
        assert repo.updates == []

    def test_adding_a_cylinder_is_ACCEPTED_when_the_stored_eye_has_an_axis(
        self, monkeypatch
    ):
        """The merge is not just a rejection machine: the stored axis is what
        makes this patch legal, and the raw-patch check used to refuse it."""
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/manual",
            json={"right_eye": {"sphere": -1.0, "cylinder": -2.0, "axis": 10}},
        )
        assert resp.status_code == 200, resp.text
        resp2 = client.patch(
            "/prescriptions/rx-1/version/manual",
            json={"right_eye": {"cylinder": -2.5}},
        )
        assert resp2.status_code == 200, resp2.text
        right = _stored_slot(repo, "manual")["right_eye"]
        assert right["cylinder"] == -2.5
        assert right["axis"] == 10

    def test_a_toric_patch_with_no_axis_anywhere_is_REJECTED(self, monkeypatch):
        """Empty slot, so the merge adds nothing - the toric gate still bites."""
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/manual",
            json={"right_eye": {"sphere": -1.0, "cylinder": -1.25}},
        )
        assert resp.status_code == 422, resp.text
        assert "no axis" in resp.json()["detail"]
        assert repo.updates == []

    def test_an_out_of_range_stored_power_is_caught_on_the_merged_eye(
        self, monkeypatch
    ):
        """A slot written before the version validators existed can hold a
        power no lens is ground to. Editing that eye re-validates what is about
        to be STORED, so the bad value has to be corrected, not carried."""
        doc = _seed_versioned_doc()
        doc["versions"]["after_testing"]["right_eye"] = {
            "sphere": -40.0, "cylinder": -1.25, "axis": 90,
        }
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"axis": 100}},
        )
        assert resp.status_code == 422, resp.text
        assert "Right eye" in resp.json()["detail"]
        assert "range" in resp.json()["detail"]
        assert repo.updates == []

    def test_an_untouched_eye_is_not_re_validated(self, monkeypatch):
        """Only the eyes the PATCH touches are gated - an eye that is being
        rewritten byte-identical must not block an unrelated edit."""
        doc = _seed_versioned_doc()
        doc["versions"]["after_testing"]["left_eye"] = {
            "sphere": -40.0, "cylinder": -1.25, "axis": 90,
        }
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"sphere": -1.5}},
        )
        assert resp.status_code == 200, resp.text
        assert _stored_slot(repo)["left_eye"]["sphere"] == -40.0


# ============================================================================
# 4 - the sph/sphere alias twins stay in step (finalize reads `sph` FIRST)
# ============================================================================


class TestAliasTwinsStayInStep:
    def test_patching_sphere_updates_the_canonical_sph_twin(self, monkeypatch):
        """A backfilled slot is keyed sph/cyl/add; the version door patches
        sphere/cylinder/addition. `_canonical_eye` reads `sph` BEFORE `sphere`,
        so leaving the twins disagreeing hides the edit from the mirror."""
        repo = _FakeSingleRxRepo(_seed_legacy_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/final",
            json={"right_eye": {"sphere": -2.0}},
        )
        assert resp.status_code == 200, resp.text
        right = _stored_slot(repo, "final")["right_eye"]
        assert right["sphere"] == -2.0
        assert right["sph"] == -2.0              # twin re-pointed, not left stale
        assert right["cyl"] == "-0.50"           # untouched powers survive
        assert right["axis"] == 90

    def test_the_edited_power_is_what_finalize_mirrors_to_the_top_level(
        self, monkeypatch
    ):
        """End to end: edit a version power, finalize, and read what POS reads."""
        repo = _FakeSingleRxRepo(_seed_legacy_doc())
        client = _rx_client(monkeypatch, repo)
        patched = client.patch(
            "/prescriptions/rx-1/version/final",
            json={"right_eye": {"sphere": -2.0}},
        )
        assert patched.status_code == 200, patched.text
        finalized = client.post("/prescriptions/rx-1/finalize")
        assert finalized.status_code == 200, finalized.text
        top = repo._doc["right_eye"]
        assert float(top["sph"]) == -2.0         # the NEW power, not "-1.00"
        assert top["cyl"] == "-0.50"             # and the un-mentioned ones survived
        assert top["axis"] == 90


# ============================================================================
# 5 - the finalize mirror cannot propagate a blanked / invalid eye
# ============================================================================


class TestFinalizeMirrorIsSafe:
    def test_finalize_refuses_an_incomplete_final(self, monkeypatch):
        """`can_finalize` requires BOTH eyes on versions.final, so a slot whose
        other eye was cleared is a 400 - it never reaches the mirror."""
        doc = _seed_versioned_doc()
        doc["versions"]["final"] = {"right_eye": {"sphere": -1.0}, "left_eye": None}
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        resp = client.post("/prescriptions/rx-1/finalize")
        assert resp.status_code == 400, resp.text
        assert "final" in resp.json()["detail"]
        assert repo.updates == []
        assert repo._doc["status"] == "in_progress"

    def test_finalize_refuses_a_toric_final_with_no_axis(self, monkeypatch):
        """Belt and braces behind the version gate: a `final` captured before
        the gate existed is re-checked at the mirror, on the MIRRORED eye."""
        doc = _seed_versioned_doc()
        doc["versions"]["final"] = {
            "right_eye": {"sphere": -1.0, "cylinder": -1.25, "axis": None},
            "left_eye": {"sphere": -1.0, "cylinder": 0, "axis": None},
        }
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        resp = client.post("/prescriptions/rx-1/finalize")
        assert resp.status_code == 422, resp.text
        assert "no axis" in resp.json()["detail"]
        assert repo.updates == []
        assert repo._doc["status"] == "in_progress"

    def test_a_partial_final_edit_dispenses_the_full_power_set(self, monkeypatch):
        """The whole point, stated as one flow: partially edit versions.final,
        finalize, and every power the clinician did not resend is still in the
        record the workshop grinds from."""
        doc = _seed_versioned_doc()
        doc["versions"]["final"] = {
            "right_eye": {
                "sphere": -1.0, "cylinder": -1.25, "axis": 90, "addition": 2.0,
            },
            "left_eye": {
                "sphere": -1.25, "cylinder": -0.5, "axis": 85, "addition": 2.0,
            },
            "pd": 62.0,
        }
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        patched = client.patch(
            "/prescriptions/rx-1/version/final",
            json={"right_eye": {"sphere": -1.75}},
        )
        assert patched.status_code == 200, patched.text
        finalized = client.post("/prescriptions/rx-1/finalize")
        assert finalized.status_code == 200, finalized.text
        top_right = repo._doc["right_eye"]
        assert float(top_right["sph"]) == -1.75
        assert float(top_right["cyl"]) == -1.25   # would be BLANK before the fix
        assert top_right["axis"] == 90
        assert float(top_right["add"]) == 2.0
        top_left = repo._doc["left_eye"]
        assert float(top_left["sph"]) == -1.25
        assert top_left["axis"] == 85
        assert repo._doc["pd"] == 62.0
        assert repo._doc["status"] == "finalized"


# ============================================================================
# 6 - the pre-existing version-door guarantees still hold
# ============================================================================


class TestVersionDoorRegressions:
    def test_finalized_rx_cannot_be_patched(self, monkeypatch):
        doc = _seed_versioned_doc()
        doc["status"] = "finalized"
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"sphere": -1.5}},
        )
        assert resp.status_code == 409, resp.text
        assert repo.updates == []

    def test_unknown_version_name_rejected(self, monkeypatch):
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/post_op",
            json={"right_eye": {"sphere": -1.5}},
        )
        assert resp.status_code == 400, resp.text
        assert repo.updates == []

    @pytest.mark.parametrize("role", ["CASHIER", "SALES_STAFF", "WORKSHOP_STAFF"])
    def test_non_clinical_roles_still_blocked(self, monkeypatch, role):
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo, roles=(role,))
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"sphere": -1.5}},
        )
        assert resp.status_code == 403, resp.text
        assert repo.updates == []

    def test_out_of_range_patch_value_still_rejected_by_the_model(self, monkeypatch):
        """VersionEyeData's own field validators are unchanged - the merge did
        not open a numeric bypass."""
        repo = _FakeSingleRxRepo(_seed_versioned_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"sphere": 99.0}},
        )
        assert resp.status_code == 422, resp.text
        assert repo.updates == []
