"""F50 clinical -> retail handover: send-to-floor + the in-app bell.

Moved verbatim out of the 3,158-line api/routers/clinical.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends
from typing import List, Optional
from datetime import datetime, timezone
import uuid
from ..auth import require_roles
from ...dependencies import (
    get_db,
    get_eye_test_repository,
    get_prescription_repository,
    get_handoff_repository,
    get_user_repository,
    validate_store_access,
)
from ._shared import _CLINICAL_ROLES, _audit_clinical, router
from .models import SendToFloorInput


# ============================================================================
# F50 -- CLINICAL -> RETAIL HANDOVER (send-to-floor)
# ============================================================================
# An optometrist marks an eye test COMPLETED, then sends the Rx to the sales
# floor: a CLINICAL_RX handoff doc + an in-app bell to every sales associate +
# store manager at that store. NO outbound message (WhatsApp/SMS) is sent -- the
# comms channel is dark; the bell (notifications collection) is the only delivery.
# 8h TTL (owner-locked). Free-text recommendations. Manual mark-served (in the
# handoffs router). Conversion credit split 50/50 (DECISIONS sec 3), recorded by
# the mark-served audit row. Behind a per-store feature flag (E2 get_policy).


def _clinical_handover_enabled(store_id: Optional[str]) -> bool:
    """Resolve the E2 per-store feature flag (global > entity > store override
    chain). Fail-soft default False so a fresh DB keeps the feature dark."""
    try:
        from api.services.policy_engine import get_policy

        return bool(
            get_policy(
                "clinical.handover_enabled",
                {"store_id": store_id} if store_id else None,
                default=False,
            )
        )
    except Exception:  # noqa: BLE001
        return False


@router.post("/tests/{test_id}/send-to-floor", status_code=201)
async def send_test_to_floor(
    test_id: str,
    body: SendToFloorInput,
    current_user: dict = Depends(require_roles(*_CLINICAL_ROLES)),
):
    """Send a COMPLETED eye test's Rx to the sales floor as an in-app handover.

    Mints a CLINICAL_RX handoff (8h TTL) and writes one in-app notification per
    eligible recipient (sales staff/cashier + store manager at the test's store).
    NO outbound message is sent. Idempotent: a second send for the same test
    returns the existing handoff (already_sent) without re-notifying."""
    from datetime import timedelta

    test_repo = get_eye_test_repository()
    if test_repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    test = test_repo.find_by_id(test_id)
    if test is None:
        raise HTTPException(status_code=404, detail="Test not found")

    store_id = test.get("store_id")

    # IDOR guard FIRST -- never leak another store's patient data, even on a
    # flag-off store (403 store-access before any other branch).
    validate_store_access(store_id, current_user)

    # Feature flag (per-store pilot). 403 when off for this store.
    if not _clinical_handover_enabled(store_id):
        raise HTTPException(
            status_code=403,
            detail="Clinical handover is not enabled for this store.",
        )

    if test.get("status") != "COMPLETED":
        raise HTTPException(
            status_code=422,
            detail="Test must be COMPLETED before sending to floor.",
        )

    rx_repo = get_prescription_repository()
    rx = rx_repo.find_by_eye_test(test_id) if rx_repo is not None else None
    if not rx:
        raise HTTPException(
            status_code=422,
            detail="No prescription on this test -- complete the refraction first.",
        )

    handoff_repo = get_handoff_repository()
    if handoff_repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    now = datetime.now(timezone.utc)

    # Idempotency: an active CLINICAL_RX for this test already exists -> return it
    # (no duplicate doc, no second notification batch).
    existing = handoff_repo.find_active_clinical_for_test(test_id, now=now)
    if existing is not None:
        return {
            "handoff_id": existing.get("handoff_id"),
            "recipient_count": len(existing.get("recipients") or []),
            "already_sent": True,
        }

    # Resolve recipients: sales floor + store manager AT THIS STORE only.
    recipients: List[dict] = []
    user_repo = get_user_repository()
    if user_repo is not None:
        try:
            roster = (
                user_repo.find_many(
                    {
                        "roles": {
                            "$in": [
                                "SALES_STAFF",
                                "SALES_CASHIER",
                                "STORE_MANAGER",
                            ]
                        },
                        "store_ids": store_id,
                    },
                    limit=200,
                )
                or []
            )
        except Exception:  # noqa: BLE001
            roster = []
        seen = set()
        for u in roster:
            uid = u.get("user_id")
            if not uid or uid in seen or not u.get("is_active", True):
                continue
            seen.add(uid)
            roles = u.get("roles") or []
            recipients.append(
                {
                    "user_id": uid,
                    "user_name": u.get("name")
                    or u.get("full_name")
                    or u.get("username")
                    or uid,
                    "role": roles[0] if roles else "",
                    "status": "pending",
                    "response": None,
                    "comment": None,
                    "responded_at": None,
                    "dismissed": False,
                    "kept": False,
                    "snooze_until": None,
                }
            )

    patient_name = test.get("patient_name") or rx.get("patient_name") or "Patient"
    opto_name = current_user.get("full_name") or current_user.get("username") or ""

    handoff_id = str(uuid.uuid4())
    doc = {
        "handoff_id": handoff_id,
        "handoff_type": "CLINICAL_RX",
        "uploader_id": current_user.get("user_id"),
        "uploader_name": opto_name,
        "optometrist_id": current_user.get("user_id"),
        "optometrist_name": opto_name,
        "title": f"Rx ready -- {patient_name}",
        "patient_name": patient_name,
        "description": (body.clinical_summary or "").strip() or None,
        "clinical_summary": (body.clinical_summary or "").strip() or None,
        "prescription_id": rx.get("prescription_id"),
        "eye_test_id": test_id,
        "store_id": store_id,
        "customer_id": test.get("customer_id"),
        "patient_id": test.get("patient_id"),
        "product_recommendations": [
            r.model_dump(exclude_none=True) for r in body.product_recommendations
        ],
        "recipients": recipients,
        "created_at": now,
        # 8h TTL -- owner-locked (NOT an E2 key). The handoffs TTL index on
        # expires_at deletes it server-side; clinical-inbox also hides on expiry.
        "expires_at": now + timedelta(hours=8),
        "validity_days": None,
        "mark_served": False,
        "served_by": None,
        "served_at": None,
        "acknowledged_by": None,
        "acknowledged_at": None,
        "parent_handoff_id": None,
    }

    saved = handoff_repo.create(doc)
    if saved is None:
        raise HTTPException(status_code=500, detail="Failed to persist handover")

    # In-app bell, one per recipient. FAIL-SOFT per recipient -- a write hiccup
    # never undoes the handover. NO outbound message anywhere.
    _notify_handover_recipients(recipients, handoff_id, patient_name, opto_name)

    _audit_clinical(
        "CLINICAL_HANDOVER_SENT",
        test_id,
        current_user,
        store_id=store_id,
        detail={
            "handoff_id": handoff_id,
            "prescription_id": rx.get("prescription_id"),
            "recipient_count": len(recipients),
        },
    )

    return {
        "handoff_id": handoff_id,
        "recipient_count": len(recipients),
        "already_sent": False,
    }


def _notify_handover_recipients(
    recipients: List[dict], handoff_id: str, patient_name: str, opto_name: str
) -> None:
    """Write one in-app notification (staff bell) per recipient into the
    `notifications` collection. Same schema as task_notify.build_escalation_*.
    IN_APP channel only -- there is NO send path (WhatsApp/SMS). FAIL-SOFT: each
    write is independent; a failure is logged, never raised."""
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return
    try:
        coll = db.get_collection("notifications")
    except Exception:  # noqa: BLE001
        return
    if coll is None:
        return
    now = datetime.now()
    for r in recipients:
        uid = r.get("user_id")
        if not uid:
            continue
        try:
            coll.insert_one(
                {
                    "notification_id": f"NTF-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}",
                    "notification_type": "clinical_handover",
                    "user_id": uid,
                    "title": f"New Rx ready -- {patient_name}",
                    "message": (
                        f"Dr. {opto_name} sent an Rx for {patient_name}. "
                        "Open Clinical Handovers to view."
                    ),
                    "entity_type": "handoff",
                    "entity_id": handoff_id,
                    "action_url": "/handoffs/clinical",
                    "channels": ["IN_APP"],
                    "priority": "NORMAL",
                    "status": "SENT",
                    "created_at": now,
                }
            )
        except Exception as e:  # noqa: BLE001
            import logging

            logging.getLogger(__package__).warning(
                "[CLINICAL_HANDOVER] in-app notify failed for %s: %s", uid, e
            )
