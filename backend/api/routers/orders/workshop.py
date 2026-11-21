"""Workshop hand-off: which orders need fitting, and the job creation.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

import logging
from datetime import timedelta
from typing import Optional, Tuple
from database.repositories.workshop_repository import (
    end_job_if_order_cancelled,
    end_jobs_of_cancelled_order,
    job_id_for_order,
)
from ...dependencies import get_order_repository
from ...utils.event_time import utc_now
from ...utils.ist import ist_today

logger = logging.getLogger(__name__)

# Item-type / category buckets used to decide whether a confirmed order needs a
# workshop/lab job. Mirrors the POS client's Phase-6.8 gate so the safety-net and
# the client agree on what "a fitting order" is.
_WORKSHOP_LENS_TYPES = {
    "LENS",
    "OPTICAL_LENS",
    "OPTICAL_LENSES",
    "SPECTACLE_LENS",
    "SPECTACLE_LENSES",
    "RX_LENSES",
    "LENSES",
}
_WORKSHOP_FRAME_TYPES = {"FRAME", "FRAMES", "SPECTACLE_FRAME", "SUNGLASS", "SUNGLASSES"}


def _order_item_kind(it: dict) -> str:
    return str(it.get("item_type") or it.get("category") or "").strip().upper()


def _order_needs_fitting(order: dict) -> bool:
    """A spectacle job needs the lab/workshop when it has a lens to grind, or a
    frame paired with a prescription. Accessory / watch / contact-lens-box /
    service- or eye-test-only orders do NOT."""
    items = order.get("items") or []
    has_lens = any(
        _order_item_kind(it) in _WORKSHOP_LENS_TYPES or it.get("lens_details")
        for it in items
    )
    if has_lens:
        return True
    has_frame = any(_order_item_kind(it) in _WORKSHOP_FRAME_TYPES for it in items)
    has_rx = bool(order.get("prescription_id")) or any(
        it.get("prescription_id") for it in items
    )
    return bool(has_frame and has_rx)


def _ensure_workshop_job_for_order(
    order: dict, by_user: Optional[str], current_user: Optional[dict] = None
) -> Optional[str]:
    """ensure_workshop_job for a door that must not fail on it: a workshop-job
    hiccup never blocks confirming a paid order. A failure here is finished by
    repair_workshop_handoffs. Returns the job_id, else None."""
    try:
        return ensure_workshop_job(order, by_user, current_user=current_user)
    except Exception as e:  # never block a confirm
        logger.warning(
            "[ORDERS] workshop auto-link skipped for %s: %s", order.get("order_id"), e
        )
        return None


def _jobs_linkable(jobs, order: dict, current_user: Optional[dict]) -> bool:
    """PR #1376 round 4: THE job rule before the handoff shows or links a job
    already on the order. A door's caller must reach EVERY one of them
    (workshop.helpers._jobs_in_reach, as POST /workshop/jobs checks); a system
    actor (repair, Razorpay reconcile) only a job of its order's own shop. A
    legacy mismatch is left as it stands: not shown, not linked, not restamped."""
    if current_user is not None:
        from ..workshop.helpers import _jobs_in_reach

        return len(_jobs_in_reach(jobs, current_user)) == len(jobs)
    shop = order.get("store_id")
    return bool(shop) and all(j.get("store_id") == shop for j in jobs)


def ensure_workshop_job(
    order: dict, by_user: Optional[str], wrepo=None, order_repo=None, current_user=None
) -> Optional[str]:
    """Idempotently ensure a CONFIRMED fitting order has a workshop/lab job, and
    that the order carries the reverse `workshop_job_id` pointer.

    The POS happy-path already creates the job from the client (Phase 6.8); this
    is the SAFETY NET for (a) that client call failing and (b) orders confirmed
    via a non-POS path (e.g. the Orders page). It NEVER creates a duplicate:
    every job of an order shares one _id (job_id_for_order), so a contender's
    insert is refused by Mongo and it takes the winner's row. Returns the job_id
    (created or pre-existing), or None when the order needs no job or was
    cancelled meanwhile -- `order` is the caller's read, so the stored bill is
    read again once the job stands, and its job ends with it. RAISES when it
    cannot say the job and its pointer are stored, so a caller with a retry
    keeps retrying. `wrepo` / `order_repo` default to the global repositories.
    `current_user` is the door's caller (None for a system actor): a job already
    on the order that fails _jobs_linkable answers None and is never linked.
    """
    if not _order_needs_fitting(order):
        return None
    order_id = order.get("order_id")
    if not order_id:
        return None
    if wrepo is None:
        from ...dependencies import get_workshop_repository

        wrepo = get_workshop_repository()
    if wrepo is None:
        raise RuntimeError(f"no workshop repository for order {order_id}")
    if order_repo is None:
        order_repo = get_order_repository()

    def link(job: dict) -> Optional[str]:
        job_id = job.get("job_id")
        # update() answers False for an outage AND for a write that changed
        # nothing -- a contender stamped this same pointer within the
        # millisecond -- so only the stored bill can say which it was.
        if (
            order_repo is not None
            and order_repo.update(
                order_id,
                {
                    "workshop_job_id": job_id,
                    "workshop_job_number": job.get("job_number"),
                },
            ) is False
            and (order_repo.find_by_id(order_id) or {}).get("workshop_job_id") != job_id
        ):
            raise RuntimeError(f"workshop pointer not stored on order {order_id}")
        return job_id

    def settle(job: dict) -> Optional[str]:
        if end_job_if_order_cancelled(order_repo, wrepo, order_id):
            return None
        return link(job)

    existing = wrepo.find_by_order(order_id)
    if existing:
        if not _jobs_linkable(existing, order, current_user):
            return None
        # Backfill the reverse pointer if the client created the job but never
        # stamped it back onto the order.
        if order.get("workshop_job_id"):
            return existing[0].get("job_id")
        return settle(existing[0])

    items = order.get("items") or []
    frame = next(
        (it for it in items if _order_item_kind(it) in _WORKSHOP_FRAME_TYPES), None
    )
    lens = next(
        (
            it
            for it in items
            if _order_item_kind(it) in _WORKSHOP_LENS_TYPES or it.get("lens_details")
        ),
        None,
    )
    rx_id = (
        (lens or {}).get("prescription_id")
        or (frame or {}).get("prescription_id")
        or order.get("prescription_id")
        or ""
    )
    # expected_date: the order's scheduled delivery (YYYY-MM-DD) or +5 days.
    exp = str(order.get("expected_delivery") or "")[:10]
    if not exp:
        exp = (ist_today() + timedelta(days=5)).isoformat()

    from ..workshop import generate_job_number

    job_data = {
        "job_id": job_id_for_order(order_id),
        "job_number": generate_job_number(wrepo),
        "order_id": order_id,
        "store_id": order.get("store_id"),
        "frame_details": (
            {
                "product_id": frame.get("product_id"),
                "name": frame.get("product_name") or frame.get("name"),
                "sku": frame.get("sku"),
                "brand": frame.get("brand"),
            }
            if frame
            else {}
        ),
        "lens_details": (
            lens.get("lens_details")
            if (lens and lens.get("lens_details"))
            else (
                {
                    "product_id": lens.get("product_id"),
                    "name": lens.get("product_name") or lens.get("name"),
                }
                if lens
                else {}
            )
        ),
        "prescription_id": rx_id,
        # L2-WS-F03: this job usually beats the POS client's createJob, whose
        # dedup then returns it unchanged -- so it carries the sale's line
        # notes itself, in the client's "name: note; ..." form.
        "fitting_instructions": "; ".join(
            f"{it.get('product_name') or it.get('name')}: {str(it['item_note']).strip()}"
            for it in items
            if str(it.get("item_note") or "").strip()
        ) or None,
        "special_notes": order.get("notes"),
        "expected_date": exp,
        "status": "PENDING",
        "created_by": by_user,
        # Provenance: spawned by the confirm safety-net, not the POS client.
        "auto_created": True,
    }
    created = wrepo.create(job_data)
    if not created:
        # create() answers None for a contender's row and for a lost reply
        # alike: only the store can say whether a job now stands.
        stored = wrepo.find_by_order(order_id)
        if not stored:
            raise RuntimeError(f"workshop job not stored for order {order_id}")
        if not _jobs_linkable(stored, order, current_user):
            return None
        created = stored[0]
    return settle(created)


def _confirmed_at_a_door(order: dict) -> bool:
    """CONFIRMED by a door that owes a lab job. An online or migrated order is
    born CONFIRMED and system: status moves are Shopify's; neither ever ran the
    safety net, and an old fitting order must not sprout a job on a late rupee."""
    by = order.get("status_updated_by")
    return (
        order.get("status") == "CONFIRMED"
        and bool(by)
        and not str(by).startswith("system:")
    )


def confirm_and_hand_off(
    repo, order_id: str, by_user: Optional[str], hand_off=None, current_user=None
) -> Tuple[dict, Optional[str]]:
    """THE lifecycle step of every confirm door: a DRAFT bill becomes CONFIRMED
    and a fitting bill reaches the lab, whichever worker wins the status.

    A refused claim proves nothing -- _claim_order_status also answers False for
    a write that landed and lost its reply, and for its own outage -- so the
    outcome is read back and the handoff follows the STORED bill, not the claim.
    Raises while the bill is still DRAFT or unreadable (repo.find_by_id answers
    None for a failed read, and None must not pass as 'no longer DRAFT'); a
    DRAFT holding a tender comes back through repair_workshop_handoffs.
    Returns (stored bill, job_id). A bill another door moved on, or cancelled,
    is left standing and gets no job. A staff door passes its `current_user`
    so the default handoff links only a job that caller reaches.
    """
    from .release import _claim_order_status

    order = repo.find_by_id(order_id)
    if order and order.get("status") == "DRAFT":
        _claim_order_status(repo, order_id, "CONFIRMED", ("DRAFT",), by_user)
        order = repo.find_by_id(order_id)
    if not order:
        raise RuntimeError(f"order {order_id} cannot be read to confirm it")
    if order.get("status") == "DRAFT":
        raise RuntimeError(f"order {order_id} took its payment but is still DRAFT")
    if not _confirmed_at_a_door(order):
        return order, None
    if hand_off is None:
        return order, _ensure_workshop_job_for_order(order, by_user, current_user)
    return order, hand_off(order, by_user)


# How far back the repair looks. A door-confirmed fitting order without a job
# older than this is left to a person (the hours of a real outage fit easily).
# ponytail: one flat window, widen it if an outage ever outlasts it.
_HANDOFF_REPAIR_WINDOW = timedelta(days=7)
_REPAIR_ACTOR = "workshop-handoff-repair"


def _end_jobs_of_recent_cancels(orders, jobs, since) -> None:
    """A cancel whose sweep failed, or a door that died between its job insert
    and its re-check, leaves an open job on a cancelled bill: end it here. A
    cancel stamps cancelled_at (text); an online one status_updated_at."""
    for order in orders.find({"status": "CANCELLED", "$or": [
        {"cancelled_at": {"$gte": since.isoformat()}},
        {"status_updated_at": {"$gte": since}},
    ]}):
        try:
            ended = end_jobs_of_cancelled_order(jobs, order["order_id"], order.get("cancellation_reason"))
            if ended:
                logger.warning("[ORDERS] open job of cancelled %s ended by the repair", order["order_id"])
        except Exception as exc:  # noqa: BLE001 -- the next tick comes back
            logger.warning("[ORDERS] cancelled %s job not ended: %s", order.get("order_id"), exc)


def _confirm_paid_drafts(orders, repo, wrepo, since) -> int:
    """A tender stands on a DRAFT only when its door's confirm failed: every door
    confirms on the first tender, and that bill has no other way on (no staff
    screen confirms, a re-press replays, a settled bill takes no new tender, and
    the invoice, ready and deliver doors refuse a DRAFT). The same step as those
    doors, so a cancel still wins the claim. `updated_at` is the tender writer's
    stamp; a bill a refund-advance claim holds is on its way to CANCELLED."""
    linked = 0
    for order in orders.find({
        "status": "DRAFT",
        "payments.0": {"$exists": True},
        "advance_refund_id": None,
        "updated_at": {"$gte": since},
    }):
        order_id = order.get("order_id")
        try:
            stored, job_id = confirm_and_hand_off(
                repo, order_id, _REPAIR_ACTOR,
                hand_off=lambda bill, by: ensure_workshop_job(bill, by, wrepo, repo),
            )
            linked += 1 if job_id else 0
            logger.warning("[ORDERS] paid draft %s moved on by the repair: %s", order_id, stored.get("status"))
        except Exception as exc:  # noqa: BLE001 -- the next tick comes back
            logger.warning("[ORDERS] paid draft %s not confirmed: %s", order_id, exc)
    return linked


def repair_workshop_handoffs(db) -> int:
    """The hourly repair: confirm any recent DRAFT that holds a tender, finish
    the handoff of any recently door-confirmed bill that has no job pointer --
    the reach of a till or /confirm door with no inbox row to retry, and of an
    event that went dead -- and end any job still open on a recently cancelled
    bill. Idempotent (the job's fixed _id, the guarded claim), fail-soft per
    bill. Returns how many bills it linked."""
    from database.repositories.order_repository import OrderRepository
    from database.repositories.workshop_repository import WorkshopJobRepository

    repaired = 0
    try:
        orders = db.get_collection("orders")
        repo = OrderRepository(orders)
        wrepo = WorkshopJobRepository(db.get_collection("workshop_jobs"))
        since = utc_now().replace(tzinfo=None) - _HANDOFF_REPAIR_WINDOW
        repaired += _confirm_paid_drafts(orders, repo, wrepo, since)
        for order in orders.find({
            "status": "CONFIRMED",
            "workshop_job_id": {"$in": [None, ""]},
            "status_updated_at": {"$gte": since},
        }):
            if not _confirmed_at_a_door(order):
                continue
            try:
                if ensure_workshop_job(order, _REPAIR_ACTOR, wrepo, repo):
                    repaired += 1
                    logger.warning("[ORDERS] workshop handoff repaired for %s", order.get("order_id"))
            except Exception as exc:  # noqa: BLE001 -- the next tick comes back
                logger.warning("[ORDERS] workshop handoff repair failed for %s: %s",
                               order.get("order_id"), exc)
        _end_jobs_of_recent_cancels(orders, wrepo.collection, since)
    except Exception as exc:  # noqa: BLE001 -- a repair never breaks the tick
        logger.warning("[ORDERS] workshop handoff repair sweep failed: %s", exc)
    return repaired
