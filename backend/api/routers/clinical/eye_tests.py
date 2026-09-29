"""Eye-test endpoints: history list (date range), read, complete, amend exam.

Moved verbatim out of the 3,158-line api/routers/clinical.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends, Query
from typing import Optional
from datetime import datetime, date
from ...utils.ist import ist_today
import uuid
from ..auth import get_current_user, require_roles
from ..prescriptions import require_rx_read
from ...dependencies import (
    get_eye_test_queue_repository,
    get_eye_test_repository,
    get_prescription_repository,
    validate_store_access,
)
from ...services.data_horizon import horizon_start_iso_date, later_iso_bound
from ._shared import _CLINICAL_ROLES, _audit_clinical, _store_scope_or_404, router
from .models import EyeTestData
from .helpers import (
    _convert_to_camel,
    _exam_blocks_for_storage,
    _exam_header_for_storage,
    _eye_for_rx_storage,
    _get_empty_tests,
    _validate_eye_test_payload,
)


# ============================================================================
# TEST ENDPOINTS
# ============================================================================


def _resolve_test_date_range(
    range_kw: Optional[str],
    from_str: Optional[str],
    to_str: Optional[str],
) -> tuple:
    """Resolve the Test-History date filter into an inclusive (from, to) pair of
    ISO ``YYYY-MM-DD`` strings (or None for an open bound).

    Precedence: an explicit ``from``/``to`` wins; otherwise a ``range`` keyword
    (today | week | month | all) is expanded relative to today. ``all`` (and an
    unknown / empty keyword) yields (None, None) = the store's whole history.
    Kept pure so it can be unit-tested without a DB. The Week window is the last
    7 calendar days inclusive and Month the last ~30, matching the windows the
    Test-History page previously computed in the browser.
    """
    from datetime import timedelta

    # Explicit bounds take priority over a keyword.
    if from_str or to_str:
        return (from_str or None, to_str or None)

    kw = (range_kw or "").strip().lower()
    # BUG-104: the IST BUSINESS day, not the box's calendar day. Railway runs
    # UTC, so date.today() here is YESTERDAY between 00:00 and 05:30 IST --
    # "Today" on Test History listed yesterday's completed eye tests and hid
    # this morning's, for the first five and a half hours of every Indian
    # working day. The stored exam dates are IST business days, so the window
    # that selects them must be one too.
    today = ist_today()
    if kw == "today":
        iso = today.isoformat()
        return (iso, iso)
    if kw == "week":
        return ((today - timedelta(days=6)).isoformat(), today.isoformat())
    if kw == "month":
        return ((today - timedelta(days=29)).isoformat(), today.isoformat())
    # "all" / unknown / empty -> whole history.
    return (None, None)


@router.get("/tests")
async def get_tests(
    store_id: str = Query(..., alias="store_id"),
    date: Optional[str] = Query(None),
    range: Optional[str] = Query(
        None,
        description="Date window keyword: today | week | month | all. "
        "Ignored when from/to are supplied.",
    ),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    current_user: dict = Depends(get_current_user),
):
    """Get eye tests for a store, optionally over a date RANGE (server-side).

    Date selection (first match wins):
      * ``date=today`` -- legacy shortcut: only TODAY's COMPLETED tests
        (unchanged behaviour the dashboard / older callers rely on).
      * ``from`` and/or ``to`` (ISO YYYY-MM-DD) -- explicit inclusive window.
      * ``range=today|week|month|all`` -- expanded relative to today.
      * none of the above -- the store's whole history (newest first).

    Previously the Test-History page could only fetch ``date=today`` and then
    filtered Week / Month / All-Time in the browser, so those filters never
    actually queried older rows. The range/from/to params let the filter run on
    the server. Each COMPLETED test is annotated with ``prescriptionId`` (the Rx
    auto-created on completion, looked up by ``eye_test_id``) so the page's Print
    button can open the A5 Rx card without a second lookup. Fail-soft: the Rx
    lookup never blocks the list.
    """
    # BUG-062: 403 a store-scoped caller asking for another store's tests.
    store_id = validate_store_access(store_id, current_user)
    test_repo = get_eye_test_repository()

    if test_repo is not None:
        if date == "today":
            tests = test_repo.get_today_completed_tests(store_id)
        else:
            r_from, r_to = _resolve_test_date_range(range, from_date, to_date)
            # 30-day browse horizon (owner ruling 2026-09-01). This door is a
            # BROWSE -- it lists a whole store's exams -- so it is clamped for
            # every role but ADMIN / SUPERADMIN. `range=all` (and an explicit
            # `from=2020-01-01`) walked straight past the window otherwise,
            # which made the clamp on the prescriptions door decorative: the
            # same clinical history was one query param away. A named patient's
            # full history stays available on the customer-scoped doors.
            r_from = later_iso_bound(
                r_from, horizon_start_iso_date(current_user)
            )
            if r_from is None and r_to is None:
                tests = test_repo.get_store_tests(store_id)
            else:
                tests = test_repo.get_store_tests_in_range(
                    store_id, from_date=r_from, to_date=r_to
                )

        rx_repo = get_prescription_repository()
        result = []
        for test in tests:
            converted = _convert_to_camel(test)
            converted["id"] = test.get("test_id")
            # Surface the linked auto-created Rx id so the FE can print the A5
            # card directly. Best-effort: a missing Rx (legacy / not-yet-created)
            # just leaves prescriptionId unset and the print button degrades.
            if rx_repo is not None and test.get("test_id"):
                try:
                    linked = rx_repo.find_by_eye_test(test.get("test_id"))
                    if linked:
                        converted["prescriptionId"] = linked.get("prescription_id")
                except Exception:  # noqa: BLE001 - never break the list
                    pass
            result.append(converted)
        return {"tests": result}

    # Return empty tests when no DB available
    return {"tests": _get_empty_tests()}


@router.get("/tests/{test_id}")
async def get_test(test_id: str, current_user: dict = Depends(require_rx_read)):
    """Get a specific eye test.

    An eye test is clinical PII (full Rx + exam findings), so the read is
    role-gated to the SAME set as prescription reads (require_rx_read) and
    store-scoped per object: an out-of-scope caller gets 404 (existence
    hidden), exactly like GET /prescriptions/{id}. Previously this was
    readable by ANY authenticated role in ANY store (P1 IDOR).
    """
    test_repo = get_eye_test_repository()

    if test_repo is not None:
        test = test_repo.find_by_id(test_id)
        if test:
            _store_scope_or_404(test, current_user)
            result = _convert_to_camel(test)
            result["id"] = test.get("test_id")
            return result
        raise HTTPException(status_code=404, detail="Test not found")

    raise HTTPException(status_code=404, detail="Test not found")


@router.post("/tests/{test_id}/complete")
async def complete_test(
    test_id: str,
    data: EyeTestData,
    current_user: dict = Depends(require_roles(*_CLINICAL_ROLES)),
):
    """Complete an eye test with prescription data"""
    # Validate the captured Rx powers against the canonical clinical ranges
    # BEFORE anything is persisted. This path auto-creates a prescription on
    # success, so an out-of-range SPH/CYL/AXIS/ADD here would otherwise be
    # saved into an Rx the prescriptions endpoint would reject -- closing that
    # write-path gap. Raises 422 on a violation.
    _validate_eye_test_payload(data)

    test_repo = get_eye_test_repository()
    queue_repo = get_eye_test_queue_repository()

    if test_repo is not None:
        # Look the test up FIRST so completion is idempotent + ordered:
        #   * unknown test_id  -> 404 (don't silently mint an orphan Rx)
        #   * already COMPLETED -> return the EXISTING prescription, do NOT
        #     write a second one. The previous code blind-updated and re-created
        #     a prescription on every call, so a double-click / retry / page
        #     reload produced duplicate Rx rows for one exam.
        existing_test = test_repo.find_by_id(test_id)
        if existing_test is None:
            raise HTTPException(status_code=404, detail="Test not found")

        # Cross-store IDOR guard: a store-scoped clinician may only complete
        # (and thereby mint an Rx for) a test in their OWN store (404-hide).
        _store_scope_or_404(existing_test, current_user)

        rx_repo = get_prescription_repository()

        if existing_test.get("status") == "COMPLETED":
            existing_rx = (
                rx_repo.find_by_eye_test(test_id) if rx_repo is not None else None
            )
            return {
                "message": "Test already completed",
                "testId": test_id,
                "prescriptionId": (
                    existing_rx.get("prescription_id") if existing_rx else None
                ),
                "alreadyCompleted": True,
            }

        # Update test record. C6-B: persist the optional full-exam findings
        # (VA / IOP / history / diagnosis / ...) so a complete optometric exam
        # is recorded, not just the refraction. by_alias=False keeps the stored
        # keys snake_case (consistent with the rest of the test doc).
        # CLI-11: also persist the structured SOAP note when provided.
        now_iso = datetime.utcnow().isoformat()
        soap_note_dict = None
        if data.soap_note is not None:
            soap_note_dict = data.soap_note.model_dump(exclude_none=True)
            soap_note_dict.setdefault("recorded_by", current_user.get("user_id", ""))
            soap_note_dict.setdefault("recorded_at", now_iso)

        success = test_repo.complete_test(
            test_id=test_id,
            right_eye=data.right_eye,
            left_eye=data.left_eye,
            pd=data.pd,
            notes=data.notes,
            lens_recommendation=data.lens_recommendation,
            coating_recommendation=data.coating_recommendation,
            clinical_findings=(
                data.clinical_findings.model_dump(exclude_none=True)
                if data.clinical_findings
                else None
            ),
            soap_note=soap_note_dict,
            # The lensometer / slit-lamp / auto-ref / subjective-refraction
            # tabs. Empty dict for a refraction-only test -> nothing extra is
            # written and the stored document is unchanged.
            exam_blocks=_exam_blocks_for_storage(data),
            exam_header=_exam_header_for_storage(data),
            # Stored on the EXAM too, not only on the mirrored prescription --
            # otherwise the Edit screen reopens with an empty IPD box.
            ipd=data.ipd,
            next_checkup=data.next_checkup,
        )

        if success:
            # Get the test to find queue_id and patient info
            test = test_repo.find_by_id(test_id)
            if test and queue_repo:
                queue_id = test.get("queue_id")
                if queue_id:
                    queue_repo.update_status(queue_id, "COMPLETED")

            # ── Auto-create prescription so POS can find it ──
            prescription_id = None
            # Idempotency belt-and-braces: even if the test row's status didn't
            # flip COMPLETED for some reason (or a concurrent request raced us),
            # never create a duplicate Rx for an exam that already has one.
            already_rx = (
                rx_repo.find_by_eye_test(test_id)
                if (rx_repo is not None and test)
                else None
            )
            if already_rx:
                return {
                    "message": "Test completed",
                    "testId": test_id,
                    "prescriptionId": already_rx.get("prescription_id"),
                }
            if rx_repo is not None and test:
                from datetime import timedelta

                now = datetime.utcnow()
                rx_number = (
                    f"RX-{now.strftime('%y%m%d')}-{str(uuid.uuid4())[:6].upper()}"
                )
                customer_id = test.get("customer_id", "")
                store_id = test.get("store_id", "")

                rx_data = {
                    "prescription_id": str(uuid.uuid4()),
                    "prescription_number": rx_number,
                    # Attribute to the specific family member when the queue/test
                    # carried one; fall back to the account holder for legacy
                    # tests. Threading patient_id through queue->test is the
                    # remaining half of the Family-Rx grouping fix.
                    "patient_id": test.get("patient_id") or customer_id,
                    "customer_id": customer_id,
                    "store_id": store_id,
                    "source": "TESTED_AT_STORE",
                    "optometrist_id": current_user.get("user_id", ""),
                    "optometrist_name": current_user.get(
                        "full_name", current_user.get("username", "")
                    ),
                    "eye_test_id": test_id,
                    "right_eye": _eye_for_rx_storage(data.right_eye),
                    "left_eye": _eye_for_rx_storage(data.left_eye),
                    "lens_recommendation": data.lens_recommendation,
                    "coating_recommendation": data.coating_recommendation,
                    "ipd": data.ipd,
                    "next_checkup": data.next_checkup,
                    "remarks": data.notes,
                    "validity_months": 12,
                    "test_date": now.isoformat(),
                    "expiry_date": (now + timedelta(days=365)).isoformat(),
                    "status": "ACTIVE",
                    "created_at": now.isoformat(),
                    "created_by": current_user.get("user_id", ""),
                }
                try:
                    created = rx_repo.create(rx_data)
                    if created:
                        prescription_id = rx_data["prescription_id"]
                except Exception as e:
                    # Log but don't fail the test completion
                    import logging

                    logging.getLogger(__package__).warning(
                        f"Auto-prescription creation failed: {e}"
                    )

            _audit_clinical(
                "EYE_TEST_RECORDED",
                test_id,
                current_user,
                store_id=(test or {}).get("store_id"),
                detail={
                    "customer_id": (test or {}).get("customer_id"),
                    "patient_id": (test or {}).get("patient_id"),
                    "prescription_id": prescription_id,
                },
            )

            return {
                "message": "Test completed",
                "testId": test_id,
                "prescriptionId": prescription_id,
            }

    # Fallback for demo
    return {"message": "Test completed", "testId": test_id}


@router.put("/tests/{test_id}/exam")
async def amend_eye_test(
    test_id: str,
    data: EyeTestData,
    current_user: dict = Depends(require_roles(*_CLINICAL_ROLES)),
):
    """Amend an already-completed eye test -- the clinic's Edit screen -- or,
    for a test still IN_PROGRESS, save it and pause (see `is_pause` below).

    The Edit pencil used to open an Rx-ONLY form, so the lensometer, slit-lamp,
    auto-ref and subjective-refraction readings could not be corrected: roughly
    a hundred captured values with nineteen of them editable. It now reopens the
    SAME seven-tab exam screen the reading was typed into, and this is where
    that screen saves.

    Why not reuse POST /complete: that call also flips status to COMPLETED,
    stamps completed_at, mints the mirrored prescription document and fires the
    sales-floor handover. It refuses outright once a test is COMPLETED, and for
    good reason -- re-running it to save an edit would duplicate the Rx and the
    handover. This endpoint writes only what the exam screen owns.

    Same validation gate as completion, deliberately: an amendment is a write of
    a medical power and gets exactly the checks the first write got.

    The prior values are preserved on the document's append-only `amendments`
    list, so correcting a reading never erases what it replaced.
    """
    _validate_eye_test_payload(data)

    test_repo = get_eye_test_repository()
    if test_repo is None:
        raise HTTPException(status_code=404, detail="Test not found")

    existing = test_repo.find_by_id(test_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Test not found")
    # Cross-store IDOR guard, same as completion: 404-hide a test in another
    # store rather than confirming it exists.
    _store_scope_or_404(existing, current_user)

    # SAVE & PAUSE (the exam page, 2026-09-04). An exam still IN_PROGRESS is
    # saved through this same door: same seven steps in the body, same range
    # gate, same fields stored. What differs is the bookkeeping -- a pause is
    # not an amendment of a recorded exam, so nothing goes on the append-only
    # `amendments` list, nothing is stamped amended_by, there is no mirrored
    # prescription yet to re-sync, and the queue entry stays IN_PROGRESS so
    # "Continue" on the queue finds the exam where it was left.
    is_pause = existing.get("status") != "COMPLETED"

    now_iso = datetime.utcnow().isoformat()
    soap_note_dict = None
    if data.soap_note is not None:
        soap_note_dict = data.soap_note.model_dump(exclude_none=True)
        # An amendment does NOT re-date the note it corrects. The exam screen
        # sends the note back without its provenance, so re-stamping here would
        # move recorded_at to today and re-attribute the whole note to whoever
        # made the correction. The original recorder and time stay; who changed
        # it, and when, is on `amended_by` / `amended_at` and the `amendments`
        # list. Only a note that has no provenance at all gets stamped now.
        prior_note = existing.get("soap_note") or {}
        soap_note_dict.setdefault(
            "recorded_by",
            prior_note.get("recorded_by") or current_user.get("user_id", ""),
        )
        soap_note_dict.setdefault(
            "recorded_at", prior_note.get("recorded_at") or now_iso
        )

    ok = test_repo.amend_test(
        test_id=test_id,
        right_eye=data.right_eye,
        left_eye=data.left_eye,
        pd=data.pd,
        notes=data.notes,
        lens_recommendation=data.lens_recommendation,
        coating_recommendation=data.coating_recommendation,
        clinical_findings=(
            data.clinical_findings.model_dump(exclude_none=True)
            if data.clinical_findings
            else None
        ),
        soap_note=soap_note_dict,
        exam_blocks=_exam_blocks_for_storage(data),
        exam_header=_exam_header_for_storage(data),
        ipd=data.ipd,
        next_checkup=data.next_checkup,
        amended_by=current_user.get("user_id", ""),
        amended_at=now_iso,
        draft=is_pause,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to amend test")

    if is_pause:
        _audit_clinical(
            "EYE_TEST_PAUSED",
            test_id,
            current_user,
            store_id=existing.get("store_id"),
            detail={
                "customer_id": existing.get("customer_id"),
                "patient_id": existing.get("patient_id"),
                "exam_step": data.exam_step,
            },
        )
        return {
            "testId": test_id,
            "prescriptionId": None,
            "amended": False,
            "paused": True,
            "message": "Eye test saved; still in progress",
        }

    # Keep the mirrored prescription in step with the corrected Final Rx --
    # otherwise the amendment would fix the exam record while the document the
    # LAB and the PATIENT actually read still carried the old power.
    rx_repo = get_prescription_repository()
    prescription_id = None
    if rx_repo is not None:
        linked = rx_repo.find_by_eye_test(test_id)
        if linked:
            prescription_id = linked.get("prescription_id")
            rx_update = {
                "right_eye": _eye_for_rx_storage(data.right_eye),
                "left_eye": _eye_for_rx_storage(data.left_eye),
                "updated_at": now_iso,
            }
            # A field the exam screen did NOT carry must never blank the stored
            # one -- the same rule the exam tabs follow. It matters most for a
            # legacy exam saved before the IPD was persisted on the test
            # document: the box opens empty, and an empty box must not reach
            # the lab as "no pupillary distance".
            for key, value in (
                ("ipd", data.ipd),
                ("lens_recommendation", data.lens_recommendation),
                ("next_checkup", data.next_checkup),
            ):
                if value is not None and str(value).strip() != "":
                    rx_update[key] = value
            rx_repo.update(prescription_id, rx_update)

    # Completion is audited (EYE_TEST_RECORDED); a later correction of the same
    # clinical record is the same class of write and is audited too. The
    # document's own append-only `amendments` list holds WHAT changed; this is
    # the Activity-Log entry saying WHO changed it and when.
    _audit_clinical(
        "EYE_TEST_AMENDED",
        test_id,
        current_user,
        store_id=existing.get("store_id"),
        detail={
            "customer_id": existing.get("customer_id"),
            "patient_id": existing.get("patient_id"),
            "prescription_id": prescription_id,
        },
    )

    return {
        "testId": test_id,
        "prescriptionId": prescription_id,
        "amended": True,
        "message": "Eye test amended",
    }
