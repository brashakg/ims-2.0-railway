"""Lens-order lifecycle (NOT_ORDERED -> ORDERED -> RECEIVED -> MOUNTED) and the
ready-for-pickup notify.

Moved verbatim out of the 3,528-line api/routers/workshop.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends
from typing import Optional
from datetime import datetime
import uuid
from ..auth import require_roles
from ...dependencies import (
    get_db,
    get_workshop_repository,
    get_order_repository,
    get_audit_repository,
)
from ._shared import (
    LENS_STATUS_ORDER,
    LENS_STATUS_TIMESTAMP_FIELD,
    WORKSHOP_ROLES,
    _next_lens_status_ok,
    logger,
    router,
)
from .models import LensStatusBody
from .helpers import _assert_job_store_access


# ============================================================================
# LENS-ORDER LIFECYCLE + READY-NOTIFY
# ============================================================================
# A workshop job's physical lens moves NOT_ORDERED -> ORDERED -> RECEIVED ->
# MOUNTED. This is independent of the job's overall workflow status (PENDING /
# IN_PROGRESS / READY / ...) and tracks where the actual lens is. When the job
# is finished we ping the customer that it's ready for pickup.


@router.post("/jobs/{job_id}/lens-status")
async def update_lens_status(
    job_id: str,
    payload: LensStatusBody,
    current_user: dict = Depends(require_roles(*WORKSHOP_ROLES)),
):
    """Advance a job's lens lifecycle by ONE forward step.

    Forward-only along NOT_ORDERED -> ORDERED -> RECEIVED -> MOUNTED. Skips,
    backwards moves, and no-ops are rejected with 400. The matching timestamp
    field (lens_ordered_at / lens_received_at / lens_mounted_at) is stamped.
    Fail-soft: repo absent -> 503, never an unhandled 500.
    """
    target = (payload.status or "").strip().upper()
    if target not in LENS_STATUS_ORDER:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown lens status {target!r}. Allowed: {', '.join(LENS_STATUS_ORDER)}.",
        )

    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    _assert_job_store_access(job, current_user)

    current = job.get("lens_status") or "NOT_ORDERED"
    if not _next_lens_status_ok(current, target):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot move lens status from {current} to {target}. "
                f"Lens lifecycle is forward-only: {' -> '.join(LENS_STATUS_ORDER)}."
            ),
        )

    now = datetime.now()
    update = {
        "lens_status": target,
        "lens_status_updated_by": current_user.get("user_id"),
    }
    ts_field = LENS_STATUS_TIMESTAMP_FIELD.get(target)
    if ts_field:
        update[ts_field] = now.isoformat()

    if not repo.update(job_id, update):
        raise HTTPException(status_code=500, detail="Failed to update lens status")

    # Audit (fail-soft)
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.lens_status",
                    "entity_type": "workshop_job",
                    "entity_id": job_id,
                    "store_id": job.get("store_id"),
                    "user_id": current_user.get("user_id"),
                    "detail": {"from": current, "to": target},
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] lens_status audit failed: %s", e)

    # Branch B' sub-PR 4 -- on lens MOUNTED, hard-commit the reserved
    # lens-catalog cell (the unit physically left the tray and is in
    # the customer's frame). Fail-soft: a missing reservation, mongo
    # blip, or 409 here is logged but never blocks the lens-status
    # transition (the workshop has already cut the lens).
    if target == "MOUNTED":
        try:
            order_id = job.get("order_id")
            if order_id and get_order_repository is not None:
                order_repo = get_order_repository()
                if order_repo is not None:
                    order = order_repo.find_by_id(order_id)
                    if order:
                        from ...services.lens_stock_hook import (
                            commit_for_workshop_dispatch,
                        )

                        items_for_commit = order.get("items") or []
                        for idx, oi in enumerate(items_for_commit):
                            try:
                                await commit_for_workshop_dispatch(
                                    order_item=oi,
                                    order_id=order_id,
                                    line_index=idx,
                                    store_id=(
                                        order.get("store_id")
                                        or job.get("store_id")
                                        or ""
                                    ),
                                    user=current_user,
                                )
                            except Exception as cm_exc:  # noqa: BLE001
                                logger.warning(
                                    "[LENS_HOOK] commit on MOUNTED failed "
                                    "(order %s line %s): %s",
                                    order_id,
                                    idx,
                                    cm_exc,
                                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[LENS_HOOK] MOUNTED commit outer error job=%s: %s",
                job_id,
                exc,
            )

    return {
        "job_id": job_id,
        "lens_status": target,
        **({ts_field: update[ts_field]} if ts_field else {}),
        "message": f"Lens status updated to {target}",
    }


def _ready_whatsapp_text(job: dict) -> str:
    """Plain-text 'ready for pickup' WhatsApp body. Pure (no IO)."""
    name = job.get("customer_name") or "Customer"
    job_no = job.get("job_number") or job.get("job_id") or ""
    tail = f" (Job {job_no})" if job_no else ""
    return (
        f"Hi {name}, your eyewear order{tail} is ready for pickup at our store. "
        f"Please visit us at your convenience. - Better Vision"
    )


async def _perform_ready_notify(job: dict, actor_id: Optional[str]) -> dict:
    """Send the 'ready for pickup' WhatsApp + stamp ready_notified_at + write an
    in-app notification row. Reused by BOTH the manual notify-ready endpoint and
    the F2 auto-notify-on-DISPATCH path.

    Fail-soft everywhere: a provider/DB hiccup never raises; the WhatsApp result
    (SENT / SIMULATED / FAILED / no_phone) is reported back in the dict.
    """
    job_id = job.get("job_id")
    phone = job.get("customer_phone") or job.get("customerPhone")
    now = datetime.now()

    # 1. WhatsApp (provider is DISPATCH_MODE-gated + fail-soft internally).
    wa_status = "no_phone"
    if phone:
        try:
            from agents.providers import send_whatsapp  # lazy import

            res = await send_whatsapp(
                phone,
                _ready_whatsapp_text(job),
                template_id="WORKSHOP_READY",
                store_id=job.get("store_id"),
            )
            wa_status = getattr(res, "status", "SENT")
            # Log the REAL send to notification_logs (channel expansions):
            # WORKSHOP_READY is a direct send, so without this row a FAILED
            # delivery report has nothing to match -> no enrichment and no
            # SMS fallback. SENT-with-provider-id only: a dark deploy never
            # reaches SENT, so nothing new is written while unarmed.
            if wa_status == "SENT" and getattr(res, "provider_id", None):
                try:
                    from ...services.notification_service import (
                        queue_notification_row,
                    )

                    queue_notification_row(
                        store_id=job.get("store_id"),
                        customer_id=job.get("customer_id"),
                        customer_phone=phone,
                        customer_name=job.get("customer_name") or "",
                        template_id="WORKSHOP_READY",
                        channel="WHATSAPP",
                        message=_ready_whatsapp_text(job),
                        category="SERVICE",
                        triggered_by="workshop_ready",
                        related_entity_type="workshop_job",
                        related_entity_id=job_id,
                        extra={
                            # Already dispatched - never drain this row again.
                            "status": "SENT",
                            "delivery_status": "SENT",
                            "sent_at": now.isoformat(),
                            "provider_msg_id": res.provider_id,
                            "provider_id": res.provider_id,
                        },
                    )
                except Exception as log_exc:  # noqa: BLE001
                    logger.warning(
                        "[WORKSHOP] notify-ready log row failed: %s", log_exc
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("[WORKSHOP] notify-ready whatsapp failed: %s", e)
            wa_status = "FAILED"

    # 2. Stamp the job (fail-soft).
    try:
        repo = get_workshop_repository()
        if repo is not None:
            repo.update(
                job_id,
                {
                    "ready_notified_at": now.isoformat(),
                    "ready_notified_by": actor_id,
                },
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] notify-ready stamp failed: %s", e)

    # 3. In-app notification row (fail-soft; only if the collection exists).
    notif_written = False
    try:
        db = get_db()
        if db is not None and getattr(db, "is_connected", True):
            coll = db.get_collection("notifications")
            if coll is not None:
                coll.insert_one(
                    {
                        "notification_id": f"NTF-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}",
                        "notification_type": "workshop_ready",
                        "user_id": actor_id,
                        "title": "Pickup notification sent",
                        "message": (
                            f"Customer notified that job "
                            f"{job.get('job_number') or job_id} is ready for pickup."
                        ),
                        "entity_type": "workshop_job",
                        "entity_id": job_id,
                        "action_url": "/workshop",
                        "channels": ["WHATSAPP", "IN_APP"],
                        "priority": "NORMAL",
                        "status": "SENT",
                        "created_at": now,
                    }
                )
                notif_written = True
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] notify-ready notification insert failed: %s", e)

    return {
        "ready_notified_at": now.isoformat(),
        "whatsapp_status": wa_status,
        "notification_logged": notif_written,
    }


@router.post("/jobs/{job_id}/notify-ready")
async def notify_ready(
    job_id: str,
    current_user: dict = Depends(require_roles(*WORKSHOP_ROLES)),
):
    """Notify the customer that their job is ready for pickup.

    Sends a WhatsApp via the existing MSG91 provider (DISPATCH_MODE-gated +
    fail-soft), stamps `ready_notified_at` on the job, and inserts a row into
    the `notifications` collection when available. Never raises on a provider
    or DB hiccup: the WhatsApp result is reported back in the response so the
    UI can surface SENT / SIMULATED / FAILED.
    """
    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    _assert_job_store_access(job, current_user)

    result = await _perform_ready_notify(job, current_user.get("user_id"))
    return {
        "job_id": job_id,
        "ready_notified_at": result["ready_notified_at"],
        "whatsapp_status": result["whatsapp_status"],
        "notification_logged": result["notification_logged"],
        "message": "Pickup notification processed",
    }
