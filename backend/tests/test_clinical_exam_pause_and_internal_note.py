"""
IMS 2.0 - the eye examination PAGE: save & pause, and the staff-only note
=========================================================================
Owner (2026-09-04): "why is this screen still a pop up". The exam became its
own page (/clinical/test/:entryId), and two things the page needs land on the
EXISTING write door, PUT /clinical/tests/{id}/exam:

  * SAVE & PAUSE. An exam still IN_PROGRESS is saved through that door. The
    test must STAY in progress (so the queue's "Continue" finds it), the
    readings must be stored, and NOTHING may be booked as an amendment -- a
    pause is not a correction of a recorded exam.

  * INTERNAL NOTE. Staff-only. Stored on the test document and nowhere else:
    the mirrored prescription (what the print card, the customer portal and a
    WhatsApp send actually read) must never carry it.

Driven through the REAL router and the REAL repositories over in-memory
collections, via the harness the amend round-trip test already built.

ASCII only (Windows cp1252).
"""

from __future__ import annotations

import copy

import pytest

from tests.test_clinical_exam_amend_roundtrip import (
    EXAM_TAB_KEYS,
    FULL_EXAM,
    _Harness,
)

NOTE = "Progressive trial went badly in 2024 - push a wider corridor this time."


@pytest.fixture
def exam(monkeypatch):
    return _Harness(monkeypatch)


def _paused_body(**extra):
    body = copy.deepcopy(FULL_EXAM)
    body["examStep"] = "subjective"
    body["internalNote"] = NOTE
    body.update(extra)
    return body


# ---------------------------------------------------------------------------
# SAVE & PAUSE
# ---------------------------------------------------------------------------
def test_pause_stores_the_readings_and_keeps_the_test_in_progress(exam):
    r = exam.amend(_paused_body())
    assert r.status_code == 200, r.text
    assert r.json()["paused"] is True
    assert r.json()["prescriptionId"] is None

    stored = exam.stored_test()
    # THE REQUIREMENT: still in the queue's "in progress" state.
    assert stored["status"] == "IN_PROGRESS"
    # The readings typed before walking away are on the document...
    assert EXAM_TAB_KEYS <= set(stored)
    assert stored["lensometer"]["right_eye"]["sphere"] == "+3.75"
    # ...and so is where the optometrist stopped.
    assert stored["exam_step"] == "subjective"
    # A pause is NOT an amendment: no history entry, no amended_by stamp.
    assert "amendments" not in stored
    assert "amended_by" not in stored
    assert "draft_saved_at" in stored


def test_pause_mints_no_prescription_and_audits_as_a_pause(exam):
    exam.amend(_paused_body())
    assert list(exam.rx.find({})) == []
    actions = [a["action"] for a in exam.audits]
    assert actions == ["EYE_TEST_PAUSED"], actions


def test_pause_goes_through_the_same_range_gate_as_completion(exam):
    bad = _paused_body()
    bad["lensometer"]["rightEye"]["sphere"] = "-9999"
    r = exam.amend(bad)
    assert r.status_code == 422, r.text
    # And a refused pause stores nothing.
    assert "lensometer" not in exam.stored_test()


def test_pause_then_complete_is_one_exam_with_one_prescription(exam):
    exam.amend(_paused_body())
    r = exam.complete()
    assert r.status_code == 200, r.text
    stored = exam.stored_test()
    assert stored["status"] == "COMPLETED"
    assert "amendments" not in stored
    assert len(list(exam.rx.find({}))) == 1


def test_a_completed_exam_is_still_amended_not_paused(exam):
    # POSITIVE CONTROL for the pause branch: once COMPLETED, the same door is
    # the Edit screen again and books an amendment exactly as before.
    exam.complete()
    r = exam.amend(_paused_body())
    assert r.status_code == 200, r.text
    assert r.json()["amended"] is True
    stored = exam.stored_test()
    assert stored["status"] == "COMPLETED"
    assert len(stored["amendments"]) == 1
    assert [a["action"] for a in exam.audits][-1] == "EYE_TEST_AMENDED"


# ---------------------------------------------------------------------------
# THE STAFF-ONLY NOTE
# ---------------------------------------------------------------------------
def _assert_note_nowhere_on(doc: dict):
    flat = repr(doc)
    assert NOTE not in flat, "the internal note leaked onto: %r" % doc
    assert "internal_note" not in doc
    assert "internalNote" not in doc


def test_internal_note_is_stored_on_the_exam_and_read_back(exam):
    body = copy.deepcopy(FULL_EXAM)
    body["internalNote"] = NOTE
    assert exam.complete(body).status_code == 200
    assert exam.stored_test()["internal_note"] == NOTE
    # The staff read (GET /clinical/tests/{id}) returns it, camelCased.
    assert exam.read().json()["internalNote"] == NOTE


def test_internal_note_never_reaches_the_prescription(exam):
    body = copy.deepcopy(FULL_EXAM)
    body["internalNote"] = NOTE
    exam.complete(body)
    (rx,) = list(exam.rx.find({}))
    _assert_note_nowhere_on(rx)

    # ...and an amendment that changes the note still leaves the Rx clean.
    body["internalNote"] = NOTE + " Do not sell him the cheapest PAL again."
    assert exam.amend(body).status_code == 200
    (rx,) = list(exam.rx.find({}))
    _assert_note_nowhere_on(rx)
    assert exam.stored_test()["internal_note"].endswith("PAL again.")


def test_a_blank_note_clears_and_an_absent_note_leaves_alone(exam):
    body = copy.deepcopy(FULL_EXAM)
    body["internalNote"] = NOTE
    exam.complete(body)

    # Absent -> untouched (a caller that is not the exam page).
    exam.amend(copy.deepcopy(FULL_EXAM))
    assert exam.stored_test()["internal_note"] == NOTE

    # Empty string -> cleared (the optometrist deleted it).
    body["internalNote"] = ""
    exam.amend(body)
    assert exam.stored_test()["internal_note"] == ""
