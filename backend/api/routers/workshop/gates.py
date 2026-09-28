"""The shared patient-safety and money gates: the scan-advance gate both
barcode paths call, the patient-facing QC rule (and its public alias
qc_cleared), the linked-job QC handover gate with its pristine-ghost
carve-out, and the job handover money gate. Orders, Labels, Shipping and
services.lab_routing import these by the package path.

Moved verbatim out of the 3,528-line api/routers/workshop.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException
from typing import Optional, List
from ...dependencies import get_workshop_repository, get_order_repository
from ._shared import (
    VALID_JOB_TRANSITIONS,
    _QC_INPUT_STATUSES,
    _check_dc_hardlock,
    logger,
)


# ---------------------------------------------------------------------------
# SHARED SCAN-ADVANCE SAFETY GATE
# ---------------------------------------------------------------------------
# BOTH barcode-scan status paths -- labels.scan_advance (label scan) and
# services.lab_routing.advance_lab_station (lab-station scan) -- must enforce the
# SAME patient-safety gates the manager-facing status PATCH (update_job_status,
# below) enforces before a scan is allowed to flip a job's status. Historically
# each scan path carried its own copy of the rules, and the label-scan path
# carried NONE at all -- which is exactly how it drifted into letting a COMPLETED
# lens reach READY (patient pickup) with zero QC record.
#
# This helper is the single source of truth for the scan-side gates. It reuses
# the SAME rule sources as the PATCH handler -- the confirmed_by_sales fitting
# flag, the qc_passed/qc_waived QC record, and the canonical _check_dc_hardlock
# F9 helper -- so a scan gate can no longer silently diverge from the PATCH gate.
#
# A scan is a PHYSICAL act at the bench: a blocked gate is a HOLD (the caller
# keeps the scan record but does NOT flip the status), never an override surface.
# The DC hardlock is therefore always evaluated with no override_reason and no
# privileged roles, so its 403 override-forbidden branch is unreachable here and
# it can only ALLOW (return) or BLOCK (raise DC_HARDLOCK). The manager PATCH
# keeps its own audited ADMIN+ override path untouched.
#
# Transition LEGALITY (the VALID_JOB_TRANSITIONS map) and the QC_FAILED ->
# IN_PROGRESS rework cap are intentionally NOT re-checked here: the scan callers
# already constrain the target to a legal forward move (labels.next_stage / the
# lab-station sequence) and neither scan surface exposes the rework path. This
# adds only the missing SAFETY gates without changing any currently-legal scan
# transition.

# Machine-readable block codes + plain-English counter-staff messages. Both scan
# callers surface these, so keeping the strings here means they cannot drift.
SCAN_GATE_MESSAGES = {
    "SALES_CONFIRM_REQUIRED": (
        "Sales has not confirmed the fitting for this job yet - ask sales to "
        "confirm the fitting details before starting work."
    ),
    "DC_REQUIRED": (
        "No Delivery Challan logged for this lens - ask the Store Manager to "
        "record the DC before starting work."
    ),
    "QC_REQUIRED": (
        "QC not passed for this job - complete QC (or record an audited waiver) "
        "before marking it Ready or handing it to the patient."
    ),
    "PAYMENT_DUE": (
        "Money is still due on this order - collect the balance first, or a "
        "manager (or a manager-approved credit-delivery PIN token) must "
        "authorise handing it over on credit."
    ),
}

# PATIENT-FACING statuses. READY parks the job on the pickup shelf; DELIVERED is
# the physical handover to the patient. BOTH require a QC pass or an explicit,
# audited QC waiver.
#
# Gating READY alone was NOT enough (the bug this set closes): the PICKUP lab
# station advances a job straight to DELIVERED (see the default station sequence
# in services/lab_routing.py), and a blocked gate on the scan path is a HOLD, not
# a rejection -- the station still advances, only the status is withheld. So a
# job whose DISPATCH -> READY leg was held HERE for missing QC would keep moving
# down the bench and the very next scan (PICKUP -> DELIVERED) handed it to the
# patient with zero QC record. The handover itself must be gated too.
_QC_REQUIRED_TARGETS = frozenset({"READY", "DELIVERED"})

# ...but the gate does NOT test membership of the set above, because that would
# fail OPEN on anything unforeseen. A station's advances_job_status is written
# straight into workshop_jobs.status by a scan, and was (until the companion fix
# in lab_routing.upsert_station) an unvalidated STORE_MANAGER-editable string --
# so pointing PICKUP at "COLLECTED" produced a target the named-pair check did
# not recognise, and the handover sailed through un-QC'd.
#
# So the gate INVERTS the test: it enumerates the statuses that are provably NOT
# patient-facing (bench-internal work states, the QC-fail branch, and the
# negative terminal) and treats EVERYTHING ELSE as putting the job in front of
# the patient. A new or malformed status is therefore gated by default.
# QC_FAILED must stay exempt -- it is the QC failure route itself, and requiring
# QC to record a QC failure would be circular (pinned by
# test_workshop_qc_gate.py::test_qc_failed_path_open_without_qc).
_NON_PATIENT_FACING_STATUSES = frozenset(
    {"PENDING", "IN_PROGRESS", "COMPLETED", "QC_FAILED", "CANCELLED"}
)


def _is_patient_facing(target_status: str) -> bool:
    """True when advancing a job to `target_status` puts it in front of the
    patient, and therefore requires QC. Fails CLOSED: an unknown / malformed /
    maliciously-configured status is patient-facing.

    The canonical members are _QC_REQUIRED_TARGETS (READY, DELIVERED); this
    helper is what the gates actually call so nothing can slip past by simply
    not being one of those two strings.
    """
    return (target_status or "").strip().upper() not in _NON_PATIENT_FACING_STATUSES


def _qc_cleared(job: dict) -> bool:
    """True when a job carries a QC pass or an explicit, audited QC waiver.

    SINGLE source of truth for the patient-facing QC rule -- used by the shared
    scan gate below AND by the manager-facing status PATCH, so the bench and the
    manager screen can never disagree about what "QC done" means. `qc_waived` is
    only ever set by the QC endpoints, which demand a waive_reason and write an
    audit row, so a waiver reaching this check is always an audited one.
    """
    return job.get("qc_passed") is True or job.get("qc_waived") is True


# PUBLIC ALIAS. orders.deliver_order / mark_ready import THIS rather than
# re-deriving the rule, so the two handover doors (Workshop screen and Orders
# screen) can never disagree about what "QC done" means. Do not inline a copy.
qc_cleared = _qc_cleared

# The counter-facing sentence for a blocked handover. Shared by the workshop
# status PATCH and the order-side deliver/ready gates so the person standing in
# front of the patient reads the SAME instruction whichever screen they used.
QC_HANDOVER_BLOCKED_MESSAGE = (
    "Lens QC has not been recorded for workshop job {job}. Ask workshop staff or "
    "the store manager to run QC on it (or record an audited waiver) before "
    "handing it to the customer."
)

# Job statuses the handover gate NEVER blocks on, whatever else is true.
#
# PENDING IS DELIBERATELY NOT HERE, and this comment exists so nobody puts it
# back. It was added for one round and that single change re-opened this PR's own
# hole for 100% of the live in-flight spectacle population: both un-QC'd jobs in
# production are PENDING, so the counter door returned 200 on jobs the bench door
# was simultaneously holding for missing QC. Two claims were used to justify the
# skip and BOTH were false:
#   * "no lab work has begun, so nothing exists that could be un-QC'd" -- wrong.
#     PENDING is not in lab_routing._TERMINAL_JOB_STATUSES, and only INTAKE
#     advances the status, so a job whose INTAKE leg was HELD walks every station
#     to PICKUP -- real bench work, full station_timestamps and scan_history --
#     while its status stays PENDING.
#   * "there is no performable remedy" -- wrong. The live rows carry
#     confirmed_by_sales=True (the sales gate passes) and lens_status=None (the
#     F9 DC hardlock fires only on ORDERED), so PENDING -> IN_PROGRESS ->
#     COMPLETED -> QC is performable today.
# The real defect was that the 400 NAMED THE WRONG REMEDY ("run QC" on a status
# QC refuses). The fix for a message that names the wrong remedy is to fix the
# MESSAGE -- see _handover_block_detail -- never to remove the block.
_HANDOVER_GATE_SKIP_STATUSES = frozenset({"CANCELLED", "DELIVERED"})

# Statuses the gate blocks on whose remedy is NOT "run QC" (QC refuses them), so
# they need their own sentence instead. Keeping them here rather than in the skip
# set is the whole point: they are still blocked, just told the truth.
_HANDOVER_NON_QC_REMEDY_STATUSES = frozenset({"PENDING"})

# ENFORCED INVARIANTS, not comments. Each encodes a rule this PR was sent back
# for violating; an assert at import fails the deploy loudly instead of letting
# the halves drift apart again in six months.
#   1. Every status the gate blocks on must have a remedy that actually exists:
#      either QC accepts it, or it has its own named non-QC remedy. What must
#      never happen is a block whose message points at an API that refuses.
#   2. The canonical patient-facing pair must never leak into the
#      not-patient-facing set that _is_patient_facing tests against.
assert (
    set(VALID_JOB_TRANSITIONS)
    - _HANDOVER_GATE_SKIP_STATUSES
    - _HANDOVER_NON_QC_REMEDY_STATUSES
) <= set(_QC_INPUT_STATUSES), "handover gate would block a status with no remedy"
assert not (_QC_REQUIRED_TARGETS & _NON_PATIENT_FACING_STATUSES), (
    "a patient-facing status is marked bench-internal"
)


# Every key a freshly-created workshop job carries. The union of the two create
# doors' shapes -- orders._ensure_workshop_job_for_order (the POS safety net) and
# workshop.create_job (the client door) -- plus the keys BaseRepository.create
# stamps (job_id, created_at, updated_at) and Mongo's _id.
#
# Pinned against the REAL creation path by
# test_pristine_key_set_matches_what_creation_actually_writes: add a field to
# either create door and that test goes red, telling you to update this set.
_CREATION_KEYS = frozenset(
    {
        "_id",
        "job_id",
        "job_number",
        "order_id",
        "store_id",
        "frame_details",
        "lens_details",
        "prescription_id",
        "fitting_instructions",
        "fitting_details",
        "special_notes",
        "expected_date",
        "status",
        "created_by",
        "created_at",
        "updated_at",
        "auto_created",
    }
)

# Keys written by actions that record NO physical progress on the lens: editing
# notes or the expected date (PUT /jobs/{id} stamps updated_by), and assigning
# the job to a technician (a work-queue decision -- nothing has been cut).
#
# Adding a key here is a SAFETY DECISION, not bookkeeping. See the asymmetry note
# in _is_pristine_ghost: a key missing from this set costs a false BLOCK, which
# is recoverable at the counter; a key wrongly ADDED here re-opens the bypass
# this whole predicate exists to close. Only add one when writing it provably
# means no lens was touched.
_ADMINISTRATIVE_KEYS = frozenset(
    {
        "updated_by",     # PUT /jobs/{id} -- notes / expected_date edit
        "technician_id",  # assign_technician
        "assigned_at",
        "assigned_to",    # legacy alias for the same assignment
    }
)

_PRISTINE_GHOST_KEYS = _CREATION_KEYS | _ADMINISTRATIVE_KEYS


def _is_pristine_ghost(job: dict) -> bool:
    """PROVE this row is untouched since creation -- do not go hunting for
    evidence that it was worked.

    THIS PREDICATE IS DELIBERATELY INVERTED, and the history is why. The ghost
    carve-out below exists for a duplicate row that produced NOTHING. Three
    successive attempts asked "can I find evidence this job WAS worked?" and each
    time reality had one more channel:
      1. status alone            -- missed a job walked through six bench scans;
      2. + scan_history /        -- missed the LENS LIFECYCLE
         station_timestamps /       (update_lens_status writes only lens_status
         current_station            and its timestamps, has no job-status guard,
                                    and hard-commits the reserved lens cell on
                                    MOUNTED because the lens is already cut and
                                    in the customer's frame), and missed the
                                    VENDOR channel (vendor_portal.post_status /
                                    post_admin_vendor_status write only
                                    vendor_status*).
    Enumerating writers cannot be finished; a fourth channel would simply be
    found. So the question is now the positive one: is this doc EXACTLY what a
    create door produced, with nothing added and nothing changed?

    THE TEST: any key outside _PRISTINE_GHOST_KEYS carrying a TRUTHY value
    disqualifies the row. Recording progress means writing it down somewhere, and
    whatever field a future writer invents lands here without anyone updating a
    list. (Falsy extras -- scan_history=[], station_timestamps={},
    current_station=None -- record nothing and are ignored, so an empty-but-
    present container still reads as pristine.)

    AN EARLIER VERSION ALSO COMPARED updated_at AGAINST created_at, and that was
    wrong -- it made the carve-out unreachable for 100% of the live population.
    BaseRepository.update stamps updated_at on EVERY write, so the check could
    not tell "someone recorded work" from "someone confirmed the fitting" or
    "someone assigned a technician". Both live PENDING rows carried NO extra
    keys at all and were still disqualified purely by a moved timestamp, and a
    single administrative PATCH flipped a real ghost from deliverable to blocked
    forever. A signal that fires on every write is not a signal.

    Why the key test alone is sufficient:
      * Progress is NEW information, so it needs somewhere new to live. The
        allowlist is the set of fields that exist for reasons OTHER than
        progress, so any progress record is outside it by construction.
      * The one allowlisted field that could encode progress is `status`, and the
        carve-out already requires PENDING. Every status write additionally goes
        through WorkshopJobRepository.update_status, which writes
        status_updated_at / status_updated_by / status_history -- none of them
        allowlisted -- so even a status that returned to PENDING leaves a trace
        this test sees.

    NOTE ON THE ASYMMETRY, because this is enumeration too and the difference is
    the whole point: forgetting a key in _ADMINISTRATIVE_KEYS causes a false
    BLOCK -- the handover refuses and staff run QC or cancel the duplicate. That
    is the SAFE direction. Forgetting an entry in the old evidence list caused a
    BYPASS: un-QC'd lenses handed to a patient. Same technique, opposite failure
    mode. So when adding to the allowlist, the only question that matters is:
    does writing this key mean NO physical progress happened? If unsure, leave it
    out and accept the false block.
    """
    for key, value in job.items():
        if key not in _PRISTINE_GHOST_KEYS and value:
            return False
    return True


def _handover_block_detail(job: dict) -> str:
    """The counter-facing sentence for a blocked handover, branched on WHY the
    job is blocked so it always names a step that is actually PERFORMABLE.

    The PENDING branch is split because "Start the job" is refused for a job
    whose fitting sales-confirmation is missing: /start, PATCH -> IN_PROGRESS,
    /complete, PATCH -> READY and /qc ALL 400 in that state, so the counter was
    handed an instruction every door rejects. The remedy that does work is
    confirming the fitting details first (PATCH /jobs/{id}/fitting-details, then
    /start succeeds), so that is what the message says.
    """
    label = job.get("job_number") or job.get("job_id") or "linked"
    if (job.get("status") or "").strip().upper() in _HANDOVER_NON_QC_REMEDY_STATUSES:
        if not (job.get("fitting_details") or {}).get("confirmed_by_sales"):
            return (
                f"Workshop job {label} is still waiting for sales to confirm the "
                f"fitting details, so it cannot be started or QC'd yet. Confirm "
                f"the fitting details on the job, then start it, complete it and "
                f"record QC before handing it to the customer."
            )
        return (
            f"Workshop job {label} has not been started yet, so it has no QC "
            f"record. Start the job, complete it and record QC before handing it "
            f"to the customer."
        )
    return QC_HANDOVER_BLOCKED_MESSAGE.format(job=label)


def assert_linked_job_qc_cleared(order: dict) -> None:
    """PATIENT SAFETY: raise 400 when an order's linked workshop job has not
    passed (or been granted an audited waiver for) lens QC.

    The Orders screen carries its own green "Mark Delivered", and that is the
    screen the counter actually uses -- payment and invoice live there. Gating
    only the Workshop screen left the likelier handover door wide open, so this
    is called from orders.deliver_order and orders.mark_ready.

    Resolution is a UNION, never a short-circuit. An earlier build checked the
    workshop_job_id reverse pointer and, only if that missed, swept
    find_by_order -- so an order with TWO lab jobs had exactly ONE examined and
    every sibling was invisible. That is not hypothetical: a duplicate pair
    exists in prod today (created before create_job gained its dedup, which
    cannot fix rows retroactively). QC would pass on the pointed-at job while the
    un-QC'd SIBLING was the one the bench actually ground, and the patient
    received un-QC'd spectacles at HTTP 200. So: ALWAYS sweep find_by_order, and
    add the pointer job only when it really belongs to THIS order.

    The pointer is ownership-checked because find_by_id is keyed on job_id
    alone: a stale or cross-order workshop_job_id would otherwise make the gate
    judge a DIFFERENT order's QC record -- failing open, or falsely blocking a
    correctly-QC'd order forever. create_job's dedup already applies this exact
    check, so both halves now agree.

    SKIP RULES. A gate must never block with a remedy that does not exist, but
    the answer to that is an honest MESSAGE, not a skip -- so the skip list is as
    short as it can be:
      * CANCELLED -- not being handed over.
      * DELIVERED -- the handover ALREADY happened; blocking buys zero safety and
        is unrecoverable (QC refuses DELIVERED and VALID_JOB_TRANSITIONS leaves
        it terminal), so a legacy delivered job would strand its order forever
        for every role including SUPERADMIN. Safe because no NEW job can reach
        DELIVERED without QC now.
      * an UNTOUCHED PENDING job on an order that already has a QC-cleared job --
        the duplicate "ghost" shape. The safety net created a second job nobody
        worked; the real job is QC'd and its glasses are finished and on the
        shelf. Blocking there strands a customer for a row that produced nothing.
        "Untouched" is PROVEN by _is_pristine_ghost, not inferred from the status
        string and not searched for field by field -- see that helper for why the
        test is inverted.
        A LONE PENDING job is still BLOCKED (it is the live prod shape) -- it
        just gets its own sentence from _handover_block_detail naming the real
        remedy: start the job.
    The invariant "every blocked state has a remedy that exists" is an
    import-time assert plus a test.

    An order with NO workshop job (a frame-only or accessory sale) has nothing to
    check and passes.

    Fail-SOFT on infrastructure only -- no workshop repo, or a lookup error,
    passes through rather than blocking a paid customer on an outage. A job that
    IS found and is not QC-cleared is a hard 400.
    """
    if not isinstance(order, dict):
        return
    try:
        repo = get_workshop_repository()
    except Exception:  # noqa: BLE001 -- infrastructure fail-soft
        repo = None
    if repo is None:
        return

    order_id = order.get("order_id")
    try:
        jobs: List[dict] = list(repo.find_by_order(order_id) or [])
    except Exception:  # noqa: BLE001 -- infrastructure fail-soft
        return

    # Add the pointed-at job ONLY when it belongs to this order and the sweep
    # missed it (a sweep that already returned it needs no duplicate entry).
    job_id = order.get("workshop_job_id")
    if job_id and not any(j.get("job_id") == job_id for j in jobs if isinstance(j, dict)):
        try:
            linked = repo.find_by_id(job_id)
        except Exception:  # noqa: BLE001
            linked = None
        if (
            isinstance(linked, dict)
            and str(linked.get("order_id") or "") == str(order_id or "")
        ):
            jobs.append(linked)

    # The ghost carve-out needs to know whether ANY job on this order is cleared,
    # so compute it before the loop rather than per-job.
    has_cleared_job = any(
        _qc_cleared(j) for j in jobs if isinstance(j, dict)
    )

    for job in jobs:
        if not isinstance(job, dict):
            continue
        status = (job.get("status") or "").strip().upper()
        if status in _HANDOVER_GATE_SKIP_STATUSES:
            continue
        # Ghost duplicate ONLY: a PENDING row PROVABLY untouched since creation,
        # alongside a job that is genuinely QC-cleared. A lone PENDING job, or one
        # that anything has written to, falls through and blocks -- otherwise a
        # two-job order could hand over a lens that was ground but never
        # inspected, while the bench scanner refuses that same document.
        # _is_pristine_ghost is deliberately a POSITIVE proof of untouchedness,
        # not a search for evidence of work; see its docstring.
        if status == "PENDING" and has_cleared_job and _is_pristine_ghost(job):
            continue
        if not _qc_cleared(job):
            raise HTTPException(
                status_code=400, detail=_handover_block_detail(job)
            )


def _resolve_job_order(job: dict) -> Optional[dict]:
    """The ORDER a workshop job bills under, or None. Infrastructure fail-soft
    (no order repo / lookup error -> None); a job with no order_id also
    resolves None (legacy / anomalous rows - logged by the caller)."""
    order_id = (job or {}).get("order_id")
    if not order_id:
        return None
    try:
        repo = get_order_repository()
    except Exception:  # noqa: BLE001 -- infrastructure fail-soft
        return None
    if repo is None:
        return None
    try:
        found = repo.find_by_id(order_id)
    except Exception:  # noqa: BLE001 -- infrastructure fail-soft
        return None
    return found if isinstance(found, dict) else None


def gate_job_handover_payment(
    job: dict,
    current_user: dict,
    approval_token: Optional[str],
    db=None,
) -> None:
    """MONEY GATE for handing a workshop JOB to the customer (-> DELIVERED).

    A job going DELIVERED is a physical handover of the order's goods, so it
    must clear the SAME money rule as the Orders-screen deliver door: at least
    partial payment on record, and a balance still due needs a manager or a
    manager-approved CREDIT_DELIVERY token (see services.delivery_gate - the
    single implementation; never re-derive it here).

    Skip rules (each one deliberate):
      * no order repo / lookup error -> infrastructure fail-soft, pass
        (mirrors assert_linked_job_qc_cleared: an outage must not strand a
        paid customer; prod fails loud on DB-down anyway).
      * order not found / job carries no order_id -> pass with a LOUD log
        (data anomaly - jobs are created against a validated order, so this
        is a legacy shape, and blocking it would have no remedy).
      * order already DELIVERED -> pass: the handover (and its credit
        decision, if any) already happened at the counter door and is
        audited there; re-blocking the job record buys zero safety and would
        strand it forever for non-manager roles.

    Raises HTTPException (400 unpaid / 403 credit) when the rule bites."""
    order = _resolve_job_order(job)
    if order is None:
        if (job or {}).get("order_id"):
            logger.warning(
                "[WORKSHOP] money gate: order %s for job %s not resolvable - "
                "payment check skipped",
                (job or {}).get("order_id"), (job or {}).get("job_id"),
            )
        return
    if (order.get("status") or "").strip().upper() == "DELIVERED":
        return
    from ...services.delivery_gate import assert_handover_payment

    assert_handover_payment(
        order,
        approval_token=approval_token,
        current_user=current_user,
        db=db,
    )


def evaluate_scan_transition_gate(
    db,
    job: dict,
    target_status: str,
    current_user: Optional[dict] = None,
    approval_token: Optional[str] = None,
) -> Optional[str]:
    """Return None if a SCAN may advance `job` to `target_status`, else a block
    code (a key of SCAN_GATE_MESSAGES).

    current_user / approval_token feed the DELIVERED money gate only: a caller
    that omits them (the lab-station PICKUP scan cannot supply either) is
    treated as never-privileged, so its scan BLOCKS whenever money is due -
    fail-closed; the remedy is the Orders deliver door or the labels
    scan-advance, which do carry the caller's roles/token.

    Near-pure decision: never mutates job state and never raises for a gate
    failure. The ONE side effect is the money leg's single-use CREDIT_DELIVERY
    token consume (same ordering hazard as the counter door: the token is
    spent before the status write). This is
    the single source of truth for the scan-side patient-safety gates and mirrors
    the update_job_status PATCH gates by reusing the SAME rule sources (see the
    section comment above).

    Gate precedence matches the PATCH handler:
      -> IN_PROGRESS : sales must have confirmed the fitting, THEN (for an
                       external-lab ORDERED lens) an accepted Delivery Challan
                       must cover the lens SKU (F9 hardlock).
      -> READY       : lens QC must have passed or been explicitly waived.
      -> DELIVERED   : same QC rule -- the PICKUP station advances straight to
                       DELIVERED, so the handover to the patient is gated too.
    """
    target = (target_status or "").strip().upper()

    if target == "IN_PROGRESS":
        # BUG-116c: the workshop may only START a job once sales confirm the
        # fitting (power + product correct).
        if not (job.get("fitting_details") or {}).get("confirmed_by_sales"):
            return "SALES_CONFIRM_REQUIRED"
        # F9 DC HARDLOCK: same canonical helper the PATCH handler calls. On a
        # scan there is no override (no reason, no privileged roles), so the
        # helper can only ALLOW (return) or BLOCK (raise DC_HARDLOCK 422).
        try:
            _check_dc_hardlock(
                db,
                job.get("lens_status"),
                (job.get("lens_details") or {}).get("product_id"),
                job.get("store_id"),
                job.get("created_at"),
                {"roles": []},  # scan path: never privileged
                None,  # no override_reason on a scan
            )
        except HTTPException:
            return "DC_REQUIRED"

    # BUG-116a (patient-safety): a lens job must NOT reach the patient -- neither
    # the READY pickup shelf nor the DELIVERED handover -- without a QC pass or an
    # explicit, audited waiver. See _QC_REQUIRED_TARGETS for why DELIVERED (the
    # PICKUP station's target) has to be in this set.
    if _is_patient_facing(target) and not _qc_cleared(job):
        return "QC_REQUIRED"

    # MONEY GATE (owner ruling): the PICKUP scan lands straight on DELIVERED -
    # a physical handover - so it must clear the SAME money rule as the
    # Orders-screen deliver door. Same helper, never re-derived. A missing
    # current_user is never-privileged (see docstring).
    if target == "DELIVERED":
        try:
            gate_job_handover_payment(
                job, current_user or {"roles": []}, approval_token, db=None
            )
        except HTTPException:
            return "PAYMENT_DUE"

    return None
