"""Job helpers: job-number minting, the per-object store-scope guard, the Rx
verification for a new job, technician-name stamping and the camelCase
converter.

Moved verbatim out of the 3,528-line api/routers/workshop.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException
from datetime import datetime
import uuid
from typing import Optional
from ...dependencies import (
    get_db,
    get_order_repository,
    can_access_store_scoped,
    user_store_scope,
)
from ...services.cost_mask import mask_cost


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def generate_job_number(repo=None) -> str:
    """Generate unique workshop job number with collision retry."""
    for _ in range(5):
        candidate = (
            f"WS-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        )
        if repo is not None:
            try:
                existing = repo.collection.find_one({"job_number": candidate})
                if existing:
                    continue  # collision — retry
            except Exception:
                pass
        return candidate
    # Fallback: use full UUID to guarantee uniqueness
    return f"WS-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:12].upper()}"


def _assert_job_store_access(job: dict, current_user: dict) -> None:
    """Object-level IDOR guard: existence-hide a workshop job whose store the
    caller can't reach. A workshop job carries customer + medical Rx data and
    drives real lens-lifecycle / QC state, so a store-scoped caller must never
    read or mutate another store's job. SUPERADMIN/ADMIN bypass; an unattributed
    legacy doc (no store_id) is admin-only -- both handled by
    dependencies.can_access_store_scoped. 404 (not 403) mirrors GET /{id} so the
    job's existence isn't confirmed to a cross-store caller."""
    if not _job_in_reach(job, current_user):
        raise HTTPException(status_code=404, detail="Workshop job not found")


def _job_in_reach(job, current_user: dict) -> bool:
    """One job through `_jobs_in_reach`, THE store rule of a workshop job."""
    return bool(job) and bool(_jobs_in_reach([job], current_user))


def _jobs_in_reach(jobs, current_user: dict) -> list:
    """THE store rule of a workshop job (PR #1376 F3): the job's own shop AND
    its ORDER's shop (the canonical owner) must both be the caller's. A legacy
    job stamped with one shop but attached to another shop's order -- the
    L2-WS-F01 create bug wrote those -- and a job whose order cannot be
    resolved are therefore admin-only until an authorized repair; a correct
    same-shop job is unchanged. SUPERADMIN/ADMIN reach every job. One order
    read for the whole list, so a rollup can run it over a shop's jobs; an
    unreadable order store hides the jobs (fail closed)."""
    jobs = [j for j in (jobs or []) if j]
    is_cross, stores = user_store_scope(current_user)
    if is_cross:
        return jobs
    mine = [j for j in jobs if can_access_store_scoped(j.get("store_id"), current_user)]
    order_repo = get_order_repository()
    if not mine or order_repo is None:
        return []
    owned = order_repo.ids_in_stores(
        {j.get("order_id") for j in mine if j.get("order_id")}, stores
    )
    return [j for j in mine if j.get("order_id") in owned]


def _hidden_job_ids(repo, filter_dict: dict, current_user: dict) -> list:
    """The job_ids matching `filter_dict` that the caller may NOT reach, for a
    query to exclude: this is how the rule runs BEFORE paging or counting
    (PR #1376 round 2). Admins hide nothing and pay no read."""
    # ponytail: an id-only read of the filter's jobs per request, to find what
    # is normally nothing; drop it once the legacy rows are repaired.
    if user_store_scope(current_user)[0]:
        return []
    keys = repo.find_reach_keys(filter_dict)
    reachable = {k.get("job_id") for k in _jobs_in_reach(keys, current_user)}
    return [k.get("job_id") for k in keys if k.get("job_id") not in reachable]


def _reachable_page(repo, filter_dict: dict, current_user: dict, *, sort, skip, limit):
    """(page, total) of the jobs the caller reaches: Mongo pages and counts
    with the hidden jobs already excluded, so no hidden row uses up a page."""
    hidden = _hidden_job_ids(repo, filter_dict, current_user)
    if hidden:
        filter_dict = {**filter_dict, "job_id": {"$nin": hidden}}
    jobs = repo.find_many(filter_dict, skip=skip, limit=limit, sort=sort) or []
    return jobs, repo.count(filter_dict)


def _board_jobs(find, store_id, current_user: dict) -> list:
    """A board list (`repo.find_pending` / `find_overdue` / `find_ready`) with
    its 100-row cap taken AFTER the rule."""
    if user_store_scope(current_user)[0]:
        return find(store_id)
    return _jobs_in_reach(find(store_id, limit=0), current_user)[:100]


def _order_has_rx_required_line(order) -> bool:
    """True when ANY line on `order` is a spectacle-lens line that the POS Rx
    gate would have enforced expiry on.

    Deliberately reuses rx_validation.is_rx_required_line -- the SAME classifier
    the order path uses (orders._validate_order_line_rx), applied to the SAME
    item docs -- so the workshop gate cannot drift into a different opinion about
    which sales are Rx-required. Frames, sunglasses, accessories and CONTACT
    LENSES classify False (owner decision 2026-06-18).

    Fail-SAFE: an order we cannot read (None / no items) returns True, so a job
    we cannot classify is still expiry-checked rather than silently exempted.
    """
    if not isinstance(order, dict):
        return True
    items = order.get("items")
    if not isinstance(items, list) or not items:
        return True
    try:
        from ...services.rx_validation import is_rx_required_line
    except Exception:  # noqa: BLE001 -- classifier unavailable -> fail safe
        return True
    for item in items:
        if not isinstance(item, dict):
            continue
        if is_rx_required_line(item.get("item_type"), item.get("category")):
            return True
    return False


def _verify_job_prescription(prescription_id, order, current_user) -> None:
    """Verify the prescription a workshop job is about to be created against.

    A workshop job's prescription_id is what the bench GRINDS. Storing it
    unverified meant a typo'd / stale / WRONG-PATIENT Rx id could be attached to
    a job and drive a real lens; nothing downstream re-checked it.

    This deliberately REUSES the canonical POS gate's rules
    (orders._validate_order_line_rx, BUG-006) rather than inventing a second
    policy -- same checks, same 422 shape, same expiry-override role list:

      * blank prescription_id -> nothing to verify (behaviour unchanged)
      * unknown prescription_id            -> 422 (HARD)
      * Rx belongs to a DIFFERENT customer -> 422 (HARD -- wrong patient)
      * EXPIRED Rx -> 422 unless the caller is Store-Manager+ (the SAME
        _RX_EXPIRY_OVERRIDE_ROLES the order path uses); a deliberate, senior
        clinical decision, never a silent pass.

    SCOPE (this is where the mirror has to be exact, not approximate). The order
    path does not run the Rx block at all for a line that is not Rx-required --
    is_rx_required_line exempts FRAMES and CONTACT LENSES per the owner's
    2026-06-18 "block Rx lenses, allow contacts" decision. Running the EXPIRY
    branch unconditionally here therefore 422'd a frame-only or contact-lens job
    that the order path had deliberately let through -- and it fired AFTER the
    money was taken, at job-create. So the expiry branch is now gated on the SAME
    classifier applied to the SAME data: the order's own item lines. If no line
    on the order is an Rx-required (spectacle-lens) line, the expiry check is
    skipped exactly as it was at billing.
    Existence and wrong-customer stay UNCONDITIONAL: a supplied Rx that does not
    exist, or belongs to somebody else, is wrong for a contact-lens job too, and
    enforcing it cannot false-block a correct one. Fail-SAFE on an unresolvable
    order (no order doc -> treat as Rx-required so expiry is still enforced).

    Fail-SOFT only where the order path already fails soft: no prescription repo
    or a repo/lookup error passes through (a clinical-store outage must not 500
    or stall the bench). A wrong-patient Rx is NEVER fail-soft -- once the Rx doc
    is in hand, a mismatch is a hard error.

    `order` is the resolved order doc (or None when the order repo is
    unavailable); it is the only source of the job's customer, since the job doc
    itself carries no customer_id at create time. With no order doc the customer
    match is skipped -- there is nothing to compare against -- but existence and
    expiry are still enforced.
    """
    rx_id = (prescription_id or "").strip()
    if not rx_id:
        return

    try:
        from ...dependencies import get_prescription_repository

        rx_repo = get_prescription_repository()
    except Exception:  # noqa: BLE001 -- mirrors orders.py fail-soft
        rx_repo = None
    if rx_repo is None:
        return

    try:
        rx = rx_repo.find_by_id(rx_id)
    except Exception:  # noqa: BLE001 -- clinical store unreachable -> fail-soft
        return

    if rx is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Prescription '{rx_id}' was not found. Select the customer's "
                f"prescription before opening the workshop job."
            ),
        )

    # WRONG-PATIENT guard (hard). The Rx must belong to the order's customer.
    customer_id = ""
    if isinstance(order, dict):
        customer_id = order.get("customer_id") or order.get("customerId") or ""
    rx_customer = rx.get("customer_id") or rx.get("customerId")
    if customer_id and rx_customer and str(rx_customer) != str(customer_id):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Prescription '{rx_id}' belongs to a different customer than "
                f"this order. Select a prescription for the order's customer."
            ),
        )

    # Expiry: SAME policy, SAME role list AND SAME scope as the POS order path.
    if not _order_has_rx_required_line(order):
        return

    try:
        from ..prescriptions import _rx_validity

        _expiry, is_valid = _rx_validity(rx)
    except Exception:  # noqa: BLE001 -- can't compute -> don't block (fail-soft)
        is_valid = None
    if is_valid is False:
        from ..orders import _RX_EXPIRY_OVERRIDE_ROLES

        roles = current_user.get("roles") or []
        if not any(r in roles for r in _RX_EXPIRY_OVERRIDE_ROLES):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Prescription '{rx_id}' has EXPIRED. A Store Manager or "
                    f"higher must approve opening a workshop job on an expired "
                    f"prescription."
                ),
            )


def _stamp_job_actor_names(jobs: list) -> None:
    """Name the technician, not their user id, everywhere a job is shown.

    The workshop board's "Assigned:" line, the job drawer's "Assigned To" and
    the PRINTED JOB CARD that goes to the bench all render assignedTo, which
    job_to_frontend maps straight from the stored technician_id / assigned_to
    -- a raw user id. Resolve the display name on the way OUT, in place on the
    FETCHED docs (these read paths never write the job back; the stored job
    keeps the id and nothing else). Batched -- one users read per response.
    An id that no longer resolves gets NO ``_name`` sibling and the screen /
    job card prints the id verbatim -- never an invented name. Fail-soft.
    """
    try:
        from ...services.name_resolver import stamp_user_names

        stamp_user_names(
            get_db(), [j for j in jobs if isinstance(j, dict)],
            ("technician_id", "assigned_to"),
        )
    except Exception:  # noqa: BLE001
        pass
    # L2-WS-F02: neither create door stores the order's identity on the job,
    # so the board, the drawer and the printed card showed a blank customer
    # and order. Read it from the job's own order on the way out (one batched
    # read; the job already passed THE job rule, which checks this order's
    # shop). A field the job itself carries wins; nothing is written back.
    try:
        need = [j for j in jobs if isinstance(j, dict) and j.get("order_id")]
        db = get_db() if need else None
        if db is None:
            return
        fields = ("order_number", "customer_name", "customer_phone", "customer_id")
        orders = {
            o.get("order_id"): o
            for o in db.get_collection("orders").find(
                {"order_id": {"$in": list({j["order_id"] for j in need})}},
                {"_id": 0, "order_id": 1, **{f: 1 for f in fields}},
            )
        }
        for j in need:
            o = orders.get(j["order_id"]) or {}
            for f in fields:
                if not j.get(f) and o.get(f):
                    j[f] = o[f]
    except Exception:  # noqa: BLE001
        pass


def job_to_frontend(job: dict, user: Optional[dict] = None) -> dict:
    """Convert workshop job from snake_case to camelCase for frontend.

    `user` is the reader: a remake's spoilage cost (remake_reasons[].cost_paise,
    priced from the lens's cost) reaches only a role services/cost_mask
    "product" admits (owner ruling D7). No user = nobody's cost."""
    if job is None:
        return job

    key_map = {
        "job_id": "id",
        "job_number": "jobNumber",
        "order_id": "orderId",
        "order_number": "orderNumber",
        "store_id": "storeId",
        "customer_id": "customerId",
        "customer_name": "customerName",
        "customer_phone": "customerPhone",
        "frame_details": "frameDetails",
        "frame_name": "frameName",
        "frame_barcode": "frameBarcode",
        "lens_details": "lensDetails",
        "lens_type": "lensType",
        "prescription_id": "prescriptionId",
        "fitting_instructions": "fittingInstructions",
        "special_notes": "notes",
        "technician_id": "assignedTo",
        "assigned_to": "assignedTo",
        # Stamped by _stamp_job_actor_names on the way out; absent when the
        # id resolves to nobody (the screen then prints the id verbatim).
        "technician_id_name": "assignedToName",
        "assigned_to_name": "assignedToName",
        "expected_date": "expectedDate",
        "promised_date": "promisedDate",
        "created_at": "createdAt",
        "completed_at": "completedAt",
        "updated_at": "updatedAt",
        "updated_by": "updatedBy",
        "created_by": "createdBy",
    }

    result = {}
    for key, value in job.items():
        # Drop MongoDB's BSON ObjectId — same reasoning as
        # orders.order_to_frontend: Pydantic/FastAPI's default JSON
        # encoder can't serialise ObjectId, and workshop_jobs carry
        # their own job_id/job_number so `_id` isn't needed in responses.
        if key == "_id":
            continue
        if key in key_map:
            result[key_map[key]] = value
        else:
            # Keep other fields as-is
            result[key] = value

    # L2-WS-F02: the flat display fields the board, drawer and job card read,
    # derived from what both create doors DO store. A stored flat field wins.
    frame = result.get("frameDetails") if isinstance(result.get("frameDetails"), dict) else {}
    lens = result.get("lensDetails") if isinstance(result.get("lensDetails"), dict) else {}
    if not result.get("frameName"):
        name = str(frame.get("name") or "").strip()
        brand = str(frame.get("brand") or "").strip()
        result["frameName"] = (
            f"{brand} {name}" if brand and name and not name.lower().startswith(brand.lower())
            else (name or brand or None)
        )
    if not result.get("lensType"):
        # POS lens_details: {type, material, index, coatings}; the safety
        # net's fallback for a catalogue lens: {product_id, name}.
        kind = lens.get("lens_type") or lens.get("type")
        result["lensType"] = (
            " ".join(str(p) for p in (kind, lens.get("material")) if p)
            if kind
            else (lens.get("name") or None)
        )
    if not result.get("promisedDate"):
        result["promisedDate"] = result.get("expectedDate") or None
    # No create door stores a priority; every job is worked as NORMAL.
    if not result.get("priority"):
        result["priority"] = "NORMAL"

    return mask_cost(result, user, "product")
