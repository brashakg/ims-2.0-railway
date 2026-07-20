"""
IMS 2.0 - Marketing Automation Router
=======================================
Tier 1 marketing features:
1. WhatsApp notification sending and logging
2. Google Review automation
3. Prescription expiry recall alerts
4. Referral program management
5. NPS survey system
6. Walk-in capture
7. Walkout recovery
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal
from datetime import datetime, date, timedelta
import re
import uuid
import time
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

from .auth import get_current_user, require_roles

# Simple per-user notification rate limiter: max 20 sends per 10 minutes
_notification_rate: dict = defaultdict(list)
_NOTIFICATION_LIMIT = 20
_NOTIFICATION_WINDOW = 600  # 10 minutes


def _check_notification_rate(user_id: str) -> Optional[str]:
    """Returns error message if rate-limited, None if OK."""
    now = time.time()
    cutoff = now - _NOTIFICATION_WINDOW
    _notification_rate[user_id] = [t for t in _notification_rate[user_id] if t > cutoff]
    if len(_notification_rate[user_id]) >= _NOTIFICATION_LIMIT:
        return f"Rate limit exceeded: max {_NOTIFICATION_LIMIT} notifications per {_NOTIFICATION_WINDOW // 60} minutes."
    _notification_rate[user_id].append(now)
    return None


from ..dependencies import get_db as _dep_get_db
from ..dependencies import validate_store_access
from ..services.notification_service import send_notification, populate_template

router = APIRouter()

# Roles permitted to fan out bulk notifications to customers (mass WhatsApp /
# SMS — spammy + metered). Mirrors the campaign-management roles. SUPERADMIN
# auto-passes via require_roles.
_BULK_SEND_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")

# Roles permitted to MINT customer monetary value (referral store-credit reward).
# Mirrors customers.py _CREDIT_ROLES — crediting money is a controlled action,
# not something any logged-in cashier/sales-staff may do.
_REWARD_ROLES = ("ACCOUNTANT", "STORE_MANAGER", "AREA_MANAGER", "ADMIN")


def _get_db():
    try:
        from database.connection import get_db

        return get_db().db
    except Exception:
        return _dep_get_db()


# ============================================================================
# SCHEMAS
# ============================================================================


# Valid channels and known template IDs — gate the API surface.
# Callers must use one of these; free-form strings are rejected (validation
# errors > silent mis-configuration or injection).
VALID_CHANNELS = {"WHATSAPP", "SMS", "EMAIL"}
# Templates that are purely transactional and therefore exempt from the
# marketing_consent gate AND the promotional quiet-hours window. TRAI/DLT allow
# transactional messages (order confirmation, OTP, service reminders) regardless
# of marketing opt-out or time of day.
_TRANSACTIONAL_TEMPLATES = {
    "ORDER_DELIVERED",
    "GOOGLE_REVIEW_REQUEST",
    "NPS_SURVEY",
}


def _enforce_promo_window(template_id: str) -> None:
    """Block PROMOTIONAL manual sends outside the 9 AM - 9 PM IST window.

    TRAI/DLT forbids promotional messages during night quiet hours (21:00-09:00
    IST). The MEGAPHONE agent already queues-and-defers automated promos; this
    guards the MANUAL send API the same way, using the SAME shared IST guard
    (agents.quiet_hours) so both agree on the window and timezone.

    Transactional/service templates in _TRANSACTIONAL_TEMPLATES are exempt
    (order confirmations, review/NPS, OTP-class). Everything else is treated as
    promotional and is BLOCKED out-of-window -- we default to block rather than
    silently auto-queueing, because turning a manual "send now" into a deferred
    send is an owner policy decision, not a default the API should make.
    Fail-soft: if the IST guard can't be imported we DO NOT block (availability
    over a hard stop on an infra hiccup)."""
    if template_id in _TRANSACTIONAL_TEMPLATES:
        return
    try:
        from agents.quiet_hours import promo_send_allowed, now_ist
    except Exception:  # pragma: no cover - guard import failure -> don't block
        return
    if not promo_send_allowed():
        ist = now_ist().strftime("%H:%M IST")
        raise HTTPException(
            status_code=409,
            detail=(
                f"Promotional messages are blocked during quiet hours "
                f"(21:00-09:00 IST). Current time is {ist}. Send transactional "
                f"templates only, or retry after 09:00 IST."
            ),
        )


# Indian mobile: 10 digits starting with 6-9.
_INDIA_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")


def _validate_phone(v: str) -> str:
    """Validate + normalize an Indian mobile to the canonical bare 10-digit form.
    Delegates to the shared services.phone util (single source of truth) -- the
    old local `re.sub(r"^\\+?91", ...)` also stripped a leading "91" from a valid
    mobile like 9123456789, corrupting it; the shared helper only strips 91 when
    it leaves exactly 10 digits."""
    from ..services.phone import normalize_indian_mobile

    norm = normalize_indian_mobile(v)
    if not norm:
        raise ValueError(
            "Phone must be a 10-digit Indian mobile number starting with 6-9"
        )
    return norm


class SendNotificationRequest(BaseModel):
    customer_id: str
    customer_phone: str
    customer_name: str
    template_id: str
    channel: str = "WHATSAPP"
    variables: dict = {}
    category: str = "SERVICE"

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, v: str) -> str:
        val = v.upper().strip()
        if val not in VALID_CHANNELS:
            raise ValueError(f"channel must be one of {sorted(VALID_CHANNELS)}")
        return val

    @field_validator("customer_phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _validate_phone(v)


class WalkinRequest(BaseModel):
    # Normalized via the shared phone util: accepts +91 / 0 / spaced input that
    # staff type at the counter and stores the canonical bare 10-digit form
    # (the old raw ^[6-9]\d{9}$ pattern 422'd those, so the same walk-in could be
    # logged under two surface forms). Still required + still ^[6-9]\d{9}$ stored.
    phone: str
    name: Optional[str] = None
    interest: str = "frames"
    notes: Optional[str] = None

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_walkin_phone(cls, v):
        return _validate_phone(v)


class WalkoutRequest(BaseModel):
    frames_tried: List[str] = []
    reason: Optional[str] = None
    notes: Optional[str] = None


class NpsResponseRequest(BaseModel):
    nps_id: str
    score: int = Field(..., ge=0, le=10)
    feedback: Optional[str] = None


class RxSnoozeRequest(BaseModel):
    days: int = 7


# ============================================================================
# FEATURE 1: NOTIFICATION SENDING & LOGS
# ============================================================================


@router.post("/notifications/send")
async def send_marketing_notification(
    req: SendNotificationRequest,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_BULK_SEND_ROLES)),
):
    """Send a notification to a customer via WhatsApp/SMS"""
    # Rate limit to prevent notification spam
    rate_err = _check_notification_rate(current_user.get("user_id", "unknown"))
    if rate_err:
        raise HTTPException(status_code=429, detail=rate_err)

    # Promo quiet-hours window: block promotional templates outside 9AM-9PM IST
    # (transactional templates are exempt). Same shared guard as MEGAPHONE.
    _enforce_promo_window(req.template_id)

    # Marketing-consent gate: non-transactional templates must not be sent to
    # customers who have opted out.  Only an explicit False opts out; missing /
    # None defaults to consented (matches the customer-create default).
    if req.template_id not in _TRANSACTIONAL_TEMPLATES:
        db = _get_db()
        if db is not None:
            if is_opted_out(req.customer_id, db):
                raise HTTPException(
                    status_code=422,
                    detail="Customer has opted out of marketing messages",
                )

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id", "")
    result = await send_notification(
        store_id=active_store,
        customer_id=req.customer_id,
        customer_phone=req.customer_phone,
        customer_name=req.customer_name,
        template_id=req.template_id,
        channel=req.channel,
        variables=req.variables,
        category=req.category,
        triggered_by=current_user.get("user_id", "unknown"),
    )
    return {"message": "Notification queued", "notification": result}


class BulkRecipient(BaseModel):
    customer_id: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    variables: dict = {}


class SendBulkRequest(BaseModel):
    template_id: str
    recipients: List[BulkRecipient]
    channel: str = "WHATSAPP"

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, v: str) -> str:
        val = v.upper().strip()
        if val not in VALID_CHANNELS:
            raise ValueError(f"channel must be one of {sorted(VALID_CHANNELS)}")
        return val


@router.post("/notifications/send-bulk")
async def send_bulk_notifications(
    req: SendBulkRequest,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_BULK_SEND_ROLES)),
):
    """Fan-out a template to many recipients. Frontend
    settingsApi.sendBulkNotifications was 404'ing. Honors the same
    rate-limit + DISPATCH_MODE safety gate as the single send."""
    rate_err = _check_notification_rate(current_user.get("user_id", "unknown"))
    if rate_err:
        raise HTTPException(status_code=429, detail=rate_err)
    # Promo quiet-hours window: a bulk fan-out is promotional unless it uses a
    # transactional template. Block out-of-window (9AM-9PM IST) before any send.
    _enforce_promo_window(req.template_id)
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id", "")
    # Respect marketing consent (consent / DLT compliance): a customer who has
    # turned OFF "Receive marketing messages" (marketing_consent == False) must
    # not get promotional fan-outs. Only an explicit False opts out — missing /
    # None defaults to consented (matches the create-path default). Ad-hoc
    # recipients with no customer_id can't be consent-checked and are sent.
    db = _get_db()
    results = []
    skipped = 0
    for r in req.recipients:
        if r.customer_id and db is not None:
            if is_opted_out(r.customer_id, db):
                skipped += 1
                results.append(
                    {"phone": r.phone, "status": "skipped", "reason": "opted_out"}
                )
                continue
        try:
            res = await send_notification(
                store_id=active_store,
                customer_id=r.customer_id or "",
                customer_phone=r.phone or "",
                customer_name=(r.variables or {}).get("name", ""),
                template_id=req.template_id,
                channel=req.channel,
                variables=r.variables,
                category="MARKETING",
                triggered_by=current_user.get("user_id", "unknown"),
            )
            results.append({"phone": r.phone, "status": "queued", "result": res})
        except Exception as e:
            results.append({"phone": r.phone, "status": "failed", "error": str(e)})
    queued = sum(1 for r in results if r["status"] == "queued")
    msg = f"{queued}/{len(results)} queued"
    if skipped:
        msg += f" ({skipped} skipped — opted out)"
    return {"message": msg, "results": results, "skipped": skipped}


@router.get("/notifications/logs")
async def get_notification_logs(
    store_id: Optional[str] = Query(None),
    template_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    current_user: dict = Depends(get_current_user),
):
    """Get notification logs with filters"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id", "")
    db = _get_db()
    if db is None:
        return {"logs": [], "total": 0}

    query = {"store_id": active_store}
    if template_id:
        query["template_id"] = template_id
    if status:
        query["status"] = status

    coll = db.get_collection("notification_logs")
    logs = list(coll.find(query).sort("created_at", -1).limit(limit))
    for log in logs:
        log.pop("_id", None)

    return {"logs": logs, "total": len(logs)}


# ============================================================================
# FEATURE 2: GOOGLE REVIEW AUTOMATION
# ============================================================================


@router.post("/review-request/{order_id}")
async def send_review_request(
    order_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Send a Google Review request after order delivery"""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Look up order and customer
    order = db.get_collection("orders").find_one({"order_id": order_id})
    if not order:
        order = db.get_collection("orders").find_one({"_id": order_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    customer_id = order.get("customer_id", "")
    customer = (
        db.get_collection("customers").find_one({"customer_id": customer_id}) or {}
    )

    result = await send_notification(
        store_id=order.get("store_id", current_user.get("active_store_id", "")),
        customer_id=customer_id,
        customer_phone=customer.get("mobile", order.get("customer_phone", "")),
        customer_name=customer.get("name", order.get("customer_name", "Customer")),
        template_id="GOOGLE_REVIEW_REQUEST",
        channel="WHATSAPP",
        variables={
            "store_name": order.get("store_name", "Better Vision"),
            "review_link": "https://g.page/r/bettervision/review",
        },
        category="SERVICE",
        triggered_by=current_user.get("user_id", "unknown"),
        related_entity_type="order",
        related_entity_id=order_id,
    )
    return {"message": "Review request sent", "notification": result}


# ============================================================================
# FEATURE 3: PRESCRIPTION EXPIRY RECALL
# ============================================================================


@router.get("/rx-expiry-alerts")
async def get_rx_expiry_alerts(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get prescriptions expiring in 30/60/90 days"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id", "")
    db = _get_db()
    if db is None:
        return {"urgent": [], "soon": [], "upcoming": [], "total_count": 0}

    now = datetime.now()
    rx_coll = db.get_collection("prescriptions")
    cust_coll = db.get_collection("customers")

    # Find prescriptions with validity dates
    all_rx = list(rx_coll.find({"store_id": active_store}).limit(500))

    urgent, soon, upcoming = [], [], []

    for rx in all_rx:
        # Calculate expiry: prescription valid for 2 years from creation
        created = rx.get("created_at") or rx.get("test_date")
        if not created:
            continue
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except Exception:
                continue

        expiry = created + timedelta(days=730)  # 2 years
        days_until = (expiry - now).days

        if days_until < 0 or days_until > 90:
            continue

        # Look up customer
        customer = cust_coll.find_one({"customer_id": rx.get("customer_id")}) or {}

        alert = {
            "prescription_id": rx.get("prescription_id") or str(rx.get("_id", "")),
            "customer_id": rx.get("customer_id", ""),
            "customer_name": customer.get("name", rx.get("patient_name", "Unknown")),
            "customer_phone": customer.get("mobile", ""),
            "expiry_date": expiry.strftime("%Y-%m-%d"),
            "days_until_expiry": days_until,
            "prescription_date": created.strftime("%Y-%m-%d"),
        }

        if days_until <= 30:
            urgent.append(alert)
        elif days_until <= 60:
            soon.append(alert)
        else:
            upcoming.append(alert)

    return {
        "urgent": sorted(urgent, key=lambda x: x["days_until_expiry"]),
        "soon": sorted(soon, key=lambda x: x["days_until_expiry"]),
        "upcoming": sorted(upcoming, key=lambda x: x["days_until_expiry"]),
        "total_count": len(urgent) + len(soon) + len(upcoming),
    }


@router.post("/rx-reminder/{customer_id}")
async def send_rx_reminder(
    customer_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Send prescription expiry reminder to customer"""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    customer = (
        db.get_collection("customers").find_one({"customer_id": customer_id}) or {}
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Marketing-consent gate: PRESCRIPTION_EXPIRY is a reminder template and
    # counts as marketing under TRAI/TCCCPR (it is not a transactional event
    # like an OTP or order confirmation).  Never send to opted-out customers.
    if is_opted_out(customer_id, db):
        raise HTTPException(
            status_code=422,
            detail="Customer has opted out of marketing messages",
        )

    # Phone validation: refuse to call the provider with an empty or clearly
    # invalid number rather than silently logging a PENDING notification that
    # will never be delivered.
    phone = customer.get("mobile", "")
    try:
        phone = _validate_phone(phone)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid customer phone: {exc}"
        ) from exc

    store_id = current_user.get("active_store_id", "")
    store = db.get_collection("stores").find_one({"store_id": store_id}) or {}

    # Duplicate / spam guard: do not send the same Rx expiry reminder more than
    # once in a 24-hour window.  Repeat clicks (or UI retries) must not spam
    # the customer.  Only PENDING/SENT/SIMULATED logs count; FAILED entries
    # do NOT block a retry (the previous attempt didn't reach the customer).
    cutoff_24h = (datetime.now() - timedelta(hours=24)).isoformat()
    recent = db.get_collection("notification_logs").find_one(
        {
            "customer_id": customer_id,
            "template_id": "PRESCRIPTION_EXPIRY",
            "status": {"$in": ["PENDING", "SENT", "SIMULATED"]},
            "created_at": {"$gte": cutoff_24h},
        }
    )
    if recent:
        raise HTTPException(
            status_code=429,
            detail="Rx reminder already sent to this customer within the last 24 hours",
        )

    # Derive the actual expiry date from the most recent prescription so the
    # customer sees "expires 12 Jun 2026" rather than the placeholder "soon".
    rx_coll = db.get_collection("prescriptions")
    latest_rx = rx_coll.find_one(
        {"customer_id": customer_id},
        sort=[("created_at", -1)],
    )
    expiry_date_str = "soon"
    if latest_rx:
        created = latest_rx.get("created_at") or latest_rx.get("test_date")
        if created:
            if isinstance(created, str):
                try:
                    created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except Exception:
                    created = None
            if created:
                expiry_date_str = (created + timedelta(days=730)).strftime("%d %b %Y")

    result = await send_notification(
        store_id=store_id,
        customer_id=customer_id,
        customer_phone=phone,
        customer_name=customer.get("name", "Customer"),
        template_id="PRESCRIPTION_EXPIRY",
        channel="WHATSAPP",
        variables={
            "store_name": store.get("name", "Better Vision"),
            "store_phone": store.get("phone", ""),
            "expiry_date": expiry_date_str,
        },
        category="REMINDER",
        triggered_by=current_user.get("user_id", "unknown"),
        related_entity_type="prescription",
        related_entity_id=customer_id,
    )
    return {"message": "Rx reminder sent", "notification": result}


@router.post("/rx-snooze/{customer_id}")
async def snooze_rx_alert(
    customer_id: str,
    req: RxSnoozeRequest,
    current_user: dict = Depends(get_current_user),
):
    """Snooze an Rx expiry alert for N days"""
    db = _get_db()
    if db is None:
        return {"message": "Snoozed"}

    snooze_until = (datetime.now() + timedelta(days=req.days)).isoformat()
    db.get_collection("notification_logs").update_many(
        {"customer_id": customer_id, "template_id": "PRESCRIPTION_EXPIRY"},
        {"$set": {"snooze_until": snooze_until}},
    )
    return {
        "message": f"Alert snoozed for {req.days} days",
        "snooze_until": snooze_until,
    }


# ============================================================================
# FEATURE 4: REFERRAL PROGRAM
# ============================================================================


@router.post("/referral-invite/{customer_id}")
async def send_referral_invite(
    customer_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Generate referral code and send invite"""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    customer = (
        db.get_collection("customers").find_one({"customer_id": customer_id}) or {}
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Marketing-consent gate: REFERRAL_INVITE is a PROMOTIONAL message.
    if is_opted_out(customer_id, db):
        raise HTTPException(
            status_code=422,
            detail="Customer has opted out of marketing messages",
        )

    store_id = current_user.get("active_store_id", "")

    # Generate or get existing referral code
    ref_coll = db.get_collection("referrals")
    existing = ref_coll.find_one(
        {"referrer_customer_id": customer_id, "store_id": store_id, "status": "INVITED"}
    )

    if existing:
        referral_code = existing["referral_code"]
    else:
        # Generate code from customer name
        name_words = customer.get("name", "").split()
        name_part = (name_words[0].upper()[:6] if name_words else "REF")
        referral_code = f"{name_part}{uuid.uuid4().hex[:4].upper()}"

        referral = {
            "referral_id": f"REF-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}",
            "store_id": store_id,
            "referrer_customer_id": customer_id,
            "referrer_name": customer.get("name", ""),
            "referrer_phone": customer.get("mobile", ""),
            "referral_code": referral_code,
            "referee_customer_id": None,
            "referee_name": None,
            "status": "INVITED",
            "reward_amount": 500,
            "invite_sent_at": datetime.now().isoformat(),
            "created_at": datetime.now().isoformat(),
        }
        ref_coll.insert_one(referral)

    # Send invite notification
    result = await send_notification(
        store_id=store_id,
        customer_id=customer_id,
        customer_phone=customer.get("mobile", ""),
        customer_name=customer.get("name", "Customer"),
        template_id="REFERRAL_INVITE",
        channel="WHATSAPP",
        variables={
            "referral_code": referral_code,
            "referee_reward": "Rs.500",
            "referrer_reward": "Rs.500",
        },
        category="PROMOTIONAL",
        triggered_by=current_user.get("user_id", "unknown"),
        related_entity_type="referral",
        related_entity_id=referral_code,
    )
    return {
        "message": "Referral invite sent",
        "referral_code": referral_code,
        "notification": result,
    }


@router.get("/referrals")
async def get_referrals(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    current_user: dict = Depends(get_current_user),
):
    """List referrals for a store"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id", "")
    db = _get_db()
    if db is None:
        return {"referrals": [], "total": 0}

    query = {"store_id": active_store}
    if status:
        query["status"] = status

    coll = db.get_collection("referrals")
    refs = list(coll.find(query).sort("created_at", -1).limit(limit))
    for r in refs:
        r.pop("_id", None)

    return {"referrals": refs, "total": len(refs)}


@router.post("/referrals/{referral_id}/redeem")
async def redeem_referral(
    referral_id: str,
    current_user: dict = Depends(require_roles(*_REWARD_ROLES)),
):
    """Credit the referrer's reward for a completed referral.

    Two holes closed here:
      1. ROLE GATE — crediting real store credit is now restricted to the same
         money-minting roles as customers.py store-credit (was any logged-in
         user, down to a cashier).
      2. IDEMPOTENCY — the credit is claimed ATOMICALLY: a single
         find_one_and_update flips status INVITED/REDEEMED -> REWARD_CREDITED
         only if it is NOT already credited. Previously the handler $inc'd the
         reward and THEN set the status without ever checking it, so calling the
         endpoint twice (double-click / retry / abuse) minted the reward again
         and again. The customer wallet is only credited once the claim wins.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    coll = db.get_collection("referrals")
    referral = coll.find_one({"referral_id": referral_id})
    if not referral:
        raise HTTPException(status_code=404, detail="Referral not found")

    reward = referral.get("reward_amount", 500)

    # Atomic claim: only the caller that flips it to REWARD_CREDITED proceeds to
    # credit the wallet. A concurrent / repeat call matches nothing and is a
    # no-op "already credited" — never a second credit.
    try:
        from pymongo import ReturnDocument

        claimed = coll.find_one_and_update(
            {"referral_id": referral_id, "status": {"$ne": "REWARD_CREDITED"}},
            {
                "$set": {
                    "status": "REWARD_CREDITED",
                    "reward_credited_at": datetime.now().isoformat(),
                    "reward_credited_by": current_user.get("user_id"),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
    except Exception:
        # Minimal/mock collection without find_one_and_update: fall back to a
        # guarded update_one (still single-credit under the status filter).
        res = coll.update_one(
            {"referral_id": referral_id, "status": {"$ne": "REWARD_CREDITED"}},
            {
                "$set": {
                    "status": "REWARD_CREDITED",
                    "reward_credited_at": datetime.now().isoformat(),
                    "reward_credited_by": current_user.get("user_id"),
                }
            },
        )
        claimed = referral if getattr(res, "modified_count", 0) else None

    if not claimed:
        # Someone (or an earlier call) already credited this referral.
        return {
            "message": "Referral already credited",
            "referral_id": referral_id,
            "already_credited": True,
        }

    # We hold the claim — credit the referrer exactly once.
    referrer_id = referral["referrer_customer_id"]
    # 1. Store credit: lives on the customers doc (correct canonical field).
    db.get_collection("customers").update_one(
        {"customer_id": referrer_id},
        {"$inc": {"store_credit": reward}},
    )
    # 2. Loyalty points: must go through the loyalty_accounts ledger, not the
    #    customers doc (which has a phantom loyalty_points field the real engine
    #    never reads — fixed CRM-1). Route via adjust_balance so the balance +
    #    lifetime_earned counters + tier stay in sync. Fail-soft so a loyalty
    #    failure never rolls back the store-credit that was already written.
    loyalty_points_awarded = 0
    try:
        from ..dependencies import (
            get_loyalty_account_repository,
            get_loyalty_transaction_repository,
            get_loyalty_settings_repository,
        )
        from ..services.loyalty_engine import compute_tier, expiry_for_earn

        points = int(reward / 10)
        if points > 0:
            accounts = get_loyalty_account_repository()
            txns = get_loyalty_transaction_repository()
            settings_repo = get_loyalty_settings_repository()
            settings = (settings_repo.get() if settings_repo else None) or {}
            if accounts and txns:
                account = accounts.find_or_create(referrer_id)
                txn_id = str(uuid.uuid4())
                txns.create(
                    {
                        "txn_id": txn_id,
                        "customer_id": referrer_id,
                        "type": "EARN",
                        "points": points,
                        "rupee_value": float(reward),
                        "order_id": None,
                        "reason": f"Referral reward {referral_id}",
                        "expires_at": expiry_for_earn(settings),
                        "tier_at_earn": account.get("tier", "BRONZE"),
                        "tier_multiplier": 1.0,
                        "store_id": referral.get("store_id"),
                        "created_by": current_user.get("user_id"),
                        "created_at": datetime.now(),
                    }
                )
                new_lifetime = int(account.get("lifetime_earned", 0)) + points
                new_tier = compute_tier(new_lifetime, settings)
                accounts.adjust_balance(
                    referrer_id,
                    delta_points=points,
                    delta_lifetime_earned=points,
                    new_tier=new_tier if new_tier != account.get("tier") else None,
                )
                loyalty_points_awarded = points
    except Exception as _lp_exc:
        logger.warning(
            "[REFERRAL] loyalty points award failed for %s: %s", referrer_id, _lp_exc
        )

    return {
        "message": f"Reward of Rs.{reward} credited",
        "referral_id": referral_id,
        "loyalty_points_awarded": loyalty_points_awarded,
    }


# ============================================================================
# FEATURE 5: NPS SURVEY
# ============================================================================


@router.post("/nps-survey/{order_id}")
async def send_nps_survey(
    order_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Send NPS survey for a delivered order"""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    order = db.get_collection("orders").find_one(
        {"order_id": order_id}
    ) or db.get_collection("orders").find_one({"_id": order_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    customer = (
        db.get_collection("customers").find_one(
            {"customer_id": order.get("customer_id")}
        )
        or {}
    )
    store_id = order.get("store_id", current_user.get("active_store_id", ""))

    nps_id = f"NPS-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

    nps_entry = {
        "nps_id": nps_id,
        "store_id": store_id,
        "customer_id": order.get("customer_id", ""),
        "customer_name": customer.get("name", order.get("customer_name", "")),
        "order_id": order_id,
        "score": None,
        "feedback": None,
        "status": "SENT",
        "survey_sent_at": datetime.now().isoformat(),
        "responded_at": None,
        "created_at": datetime.now().isoformat(),
    }

    db.get_collection("nps_responses").insert_one(nps_entry)

    store = db.get_collection("stores").find_one({"store_id": store_id}) or {}

    await send_notification(
        store_id=store_id,
        customer_id=order.get("customer_id", ""),
        customer_phone=customer.get("mobile", ""),
        customer_name=customer.get("name", "Customer"),
        template_id="NPS_SURVEY",
        channel="WHATSAPP",
        variables={
            "store_name": store.get("name", "Better Vision"),
            "survey_link": f"https://bettervision.in/nps/{nps_id}",
        },
        category="SERVICE",
        triggered_by=current_user.get("user_id", "unknown"),
        related_entity_type="order",
        related_entity_id=order_id,
    )

    return {"message": "NPS survey sent", "nps_id": nps_id}


@router.post("/nps-response")
async def submit_nps_response(
    req: NpsResponseRequest,
    current_user: dict = Depends(get_current_user),
):
    """Record NPS survey response"""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    coll = db.get_collection("nps_responses")
    result = coll.update_one(
        {"nps_id": req.nps_id},
        {
            "$set": {
                "score": req.score,
                "feedback": req.feedback,
                "status": "RESPONDED",
                "responded_at": datetime.now().isoformat(),
            }
        },
    )

    # If detractor (score <= 6), create follow-up task for manager.
    # Field names must match the follow_ups collection schema used by
    # follow_ups.py (scheduled_date, notes, customer_phone).
    if req.score <= 6:
        nps = coll.find_one({"nps_id": req.nps_id}) or {}
        customer_id = nps.get("customer_id", "")
        # Attempt to look up the customer's phone for the follow-up list
        customer_phone = ""
        if customer_id:
            try:
                customer_doc = (
                    db.get_collection("customers").find_one(
                        {"customer_id": customer_id}
                    )
                    or {}
                )
                customer_phone = str(
                    customer_doc.get("mobile") or customer_doc.get("phone") or ""
                )
            except Exception:
                pass
        db.get_collection("follow_ups").insert_one(
            {
                "follow_up_id": f"FU-{uuid.uuid4().hex[:8].upper()}",
                "store_id": nps.get("store_id", ""),
                "customer_id": customer_id,
                "customer_name": nps.get("customer_name", ""),
                "customer_phone": customer_phone,
                "type": "general",
                "notes": f"NPS detractor (score: {req.score}): {req.feedback or 'No feedback'}",
                "scheduled_date": (datetime.now() + timedelta(days=1))
                .date()
                .isoformat(),
                "status": "pending",
                "outcome": None,
                "created_at": datetime.now().isoformat(),
                "completed_at": None,
                "completed_by": None,
            }
        )

    return {"message": "NPS response recorded", "score": req.score}


@router.get("/nps-dashboard")
async def get_nps_dashboard(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get NPS dashboard data"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id", "")
    db = _get_db()
    if db is None:
        return {
            "avg_score": 0,
            "promoters": 0,
            "passives": 0,
            "detractors": 0,
            "response_rate": 0,
            "responses": [],
        }

    coll = db.get_collection("nps_responses")
    all_surveys = list(
        coll.find({"store_id": active_store}).sort("created_at", -1).limit(200)
    )

    responded = [s for s in all_surveys if s.get("score") is not None]
    scores = [s["score"] for s in responded]

    promoters = len([s for s in scores if s >= 9])
    passives = len([s for s in scores if 7 <= s <= 8])
    detractors = len([s for s in scores if s <= 6])
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0
    response_rate = (
        round(len(responded) / len(all_surveys) * 100, 1) if all_surveys else 0
    )

    recent = responded[:20]
    for r in recent:
        r.pop("_id", None)

    return {
        "avg_score": avg_score,
        "promoters": promoters,
        "passives": passives,
        "detractors": detractors,
        "response_rate": response_rate,
        "total_surveys": len(all_surveys),
        "total_responses": len(responded),
        "nps_score": (
            round(((promoters - detractors) / len(responded) * 100), 1)
            if responded
            else 0
        ),
        "responses": recent,
    }


# ============================================================================
# FEATURE 6: WALK-IN CAPTURE
# ============================================================================


@router.post("/walkin")
async def create_walkin(
    req: WalkinRequest,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Register a walk-in visitor"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id", "")
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    walkin_id = (
        f"WLK-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    )

    # Check if phone matches existing customer
    existing_customer = db.get_collection("customers").find_one({"mobile": req.phone})

    walkin = {
        "walkin_id": walkin_id,
        "store_id": active_store,
        "phone": req.phone,
        "name": req.name
        or (existing_customer.get("name") if existing_customer else None),
        "interest": req.interest,
        "notes": req.notes,
        "existing_customer_id": (
            existing_customer.get("customer_id") if existing_customer else None
        ),
        "follow_up_created": True,
        "converted": False,
        "created_at": datetime.now().isoformat(),
        "created_by": current_user.get("user_id", "unknown"),
    }

    db.get_collection("walkins").insert_one(walkin)

    # Auto-create follow-up for next day
    db.get_collection("follow_ups").insert_one(
        {
            "follow_up_id": f"FU-{uuid.uuid4().hex[:8].upper()}",
            "store_id": active_store,
            "customer_id": (
                existing_customer.get("customer_id") if existing_customer else walkin_id
            ),
            "customer_name": req.name or req.phone,
            "type": "general",
            "reason": f"Walk-in follow-up (Interest: {req.interest})",
            "due_date": (datetime.now() + timedelta(days=1)).isoformat(),
            "status": "pending",
            "created_at": datetime.now().isoformat(),
        }
    )

    walkin.pop("_id", None)
    return {"message": "Walk-in registered", "walkin": walkin}


@router.get("/walkins")
async def get_walkins(
    store_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    current_user: dict = Depends(get_current_user),
):
    """List walk-in registrations"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id", "")
    db = _get_db()
    if db is None:
        return {"walkins": [], "total": 0}

    query = {"store_id": active_store}
    if date_from:
        query["created_at"] = {"$gte": date_from}
    if date_to:
        query.setdefault("created_at", {})
        if isinstance(query["created_at"], dict):
            query["created_at"]["$lte"] = date_to
        else:
            query["created_at"] = {"$gte": query["created_at"], "$lte": date_to}

    coll = db.get_collection("walkins")
    walkins = list(coll.find(query).sort("created_at", -1).limit(limit))
    for w in walkins:
        w.pop("_id", None)

    return {"walkins": walkins, "total": len(walkins)}


# ============================================================================
# FEATURE 7: WALKOUT RECOVERY -- RETIRED (F45 D1)
# ============================================================================
# These two endpoints were a zombie duplicate of the canonical walkouts module
# (backend/api/routers/walkouts.py, mounted at /api/v1/walkouts). They wrote to
# the SAME `walkouts` collection with a mismatched 5-field schema (no 30-field
# capture, no 2/3-round follow-up, no result pipeline) AND fired a WALKOUT_RECOVERY
# WhatsApp immediately -- which is now forbidden (COMMS CHANNEL DIRECTIVE
# 2026-06-07: WhatsApp ban; walkout follow-up messages are deferred / dark only).
#
# They are RETIRED to HTTP 410 Gone: the routes remain registered (so the RBAC
# coverage-lock + no-stale-entry tests stay green) but they no longer write any
# document or fire any send. Callers must use POST/GET /api/v1/walkouts instead.


@router.post("/walkout/{customer_id}", deprecated=True)
async def record_walkout(
    customer_id: str,
    current_user: dict = Depends(get_current_user),
):
    """RETIRED (F45 D1). Use POST /api/v1/walkouts (canonical 30-field capture).

    Returns 410 Gone -- this stub no longer writes a partial-schema walkout doc
    nor fires a WALKOUT_RECOVERY message (WhatsApp is banned; sends are dark).
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "This endpoint is retired. Use POST /api/v1/walkouts for the full "
            "30-field walkout / lost-sale capture."
        ),
    )


@router.get("/walkout-recoveries", deprecated=True)
async def get_walkout_recoveries(
    current_user: dict = Depends(get_current_user),
):
    """RETIRED (F45 D1). Use GET /api/v1/walkouts (canonical list + dashboards).

    Returns 410 Gone.
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "This endpoint is retired. Use GET /api/v1/walkouts and the "
            "/api/v1/walkouts/dashboard/* endpoints."
        ),
    )


# ============================================================================
# FEATURE 8: DPDP DATA-CONSENT TEXT (editable; shown at customer creation)
# ============================================================================
# The wording a customer agrees to when we store their personal data (DPDP Act
# 2023). Editable by ADMIN under Marketing so legal/owner can tune it without a
# deploy; the `version` is stamped onto each customer's consent record so an
# agreement is always traceable to the exact text shown.

_DEFAULT_CONSENT_TEXT = (
    "I agree that Better Vision may store and use my personal details (name, "
    "contact number, prescription and purchase history) to provide optical "
    "services, process my orders, and send me service reminders and offers. I "
    "can withdraw this consent at any time by contacting the store."
)
_CONSENT_DOC_ID = "dpdp_data_consent"


class ConsentTextUpdate(BaseModel):
    text: str = Field(..., min_length=10, max_length=4000)


def _consent_collection():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("marketing_config")
    except Exception:  # noqa: BLE001
        return None


@router.get("/consent-text")
async def get_consent_text(current_user: dict = Depends(get_current_user)):
    """Current DPDP data-consent wording + its version. Any authenticated user
    can READ it (the customer-create form needs it); editing is ADMIN-only."""
    coll = _consent_collection()
    if coll is not None:
        try:
            doc = coll.find_one({"_id": _CONSENT_DOC_ID})
            if doc and doc.get("text"):
                return {
                    "text": doc["text"],
                    "version": doc.get("version", "1"),
                    "updated_at": doc.get("updated_at"),
                }
        except Exception:  # noqa: BLE001
            pass
    return {"text": _DEFAULT_CONSENT_TEXT, "version": "default", "updated_at": None}


@router.put("/consent-text")
async def update_consent_text(
    body: ConsentTextUpdate,
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Edit the DPDP consent wording (ADMIN/SUPERADMIN). Bumps the version so a
    customer's stored consent always points at the exact text they saw."""
    coll = _consent_collection()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    existing = None
    try:
        existing = coll.find_one({"_id": _CONSENT_DOC_ID})
    except Exception:  # noqa: BLE001
        pass
    try:
        prev_version = int((existing or {}).get("version", "0") or 0)
    except (TypeError, ValueError):
        prev_version = 0
    new_version = str(prev_version + 1)
    coll.update_one(
        {"_id": _CONSENT_DOC_ID},
        {
            "$set": {
                "text": body.text.strip(),
                "version": new_version,
                "updated_at": datetime.now().isoformat(),
                "updated_by": current_user.get("user_id"),
            }
        },
        upsert=True,
    )
    return {"message": "Consent text updated", "version": new_version}


# ============================================================================
# CRM-16 — Ad Performance (Google Ads + Meta Ads agency oversight dashboard)
# ============================================================================

# Roles allowed to view ad spend / ROAS data. SUPERADMIN auto-passes via
# require_roles. This is finance-sensitive data (actual spend figures) so we
# restrict to ADMIN and up -- store managers do not typically have agency access.
_AD_PERF_ROLES = ("ADMIN",)


class CampaignRowOut(BaseModel):
    channel: str
    campaign_id: str
    campaign_name: str
    spend: float
    impressions: int
    clicks: int
    conversions: int
    ctr: float
    cpl: float
    roas: float
    currency: str
    status: str


class AdPerformanceOut(BaseModel):
    rows: List[CampaignRowOut]
    total_spend: float
    total_impressions: int
    total_clicks: int
    total_conversions: int
    blended_roas: float
    total_cpl: float
    google_configured: bool
    meta_configured: bool
    fetched_at: str
    note: str


@router.get(
    "/ad-performance",
    response_model=AdPerformanceOut,
    summary="Agency ad performance (Google + Meta)",
    description=(
        "Returns merged Google Ads and Meta Ads campaign metrics for the given date "
        "range. When credentials are not configured the response still returns 200 "
        "with google_configured/meta_configured=false and an explanatory note -- "
        "the frontend shows a 'connect your ad account' empty-state instead of an "
        "error. Gated to SUPERADMIN/ADMIN."
    ),
)
async def get_ad_performance(
    from_date: str = Query(..., alias="from", description="Start date YYYY-MM-DD"),
    to_date: str = Query(..., alias="to", description="End date YYYY-MM-DD"),
    channel: Optional[str] = Query(
        None, description="Filter to 'google' or 'meta'; omit for both"
    ),
    current_user: dict = Depends(require_roles("SUPERADMIN", *_AD_PERF_ROLES)),
):
    """
    Merged Google + Meta ad performance. Fail-soft: missing creds -> 200 with
    google_configured=false / meta_configured=false rather than 500/404.
    """
    # Lightweight date validation (avoids injection into the GAQL query)
    import re as _re
    _DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if not _DATE_RE.match(from_date) or not _DATE_RE.match(to_date):
        raise HTTPException(status_code=422, detail="Dates must be in YYYY-MM-DD format")
    if channel and channel not in ("google", "meta"):
        raise HTTPException(status_code=422, detail="channel must be 'google' or 'meta'")

    db = _get_db()

    try:
        from ..services.ad_providers import fetch_ad_performance

        result = await fetch_ad_performance(db, from_date, to_date, channel)
    except Exception as exc:
        logger.error("[MARKETING] ad-performance unexpected error (fail-soft): %s", exc)
        # Return a safe empty envelope rather than 500
        from ..services.ad_providers import AdPerformanceResult

        result = AdPerformanceResult(
            note=f"Service temporarily unavailable: {exc}",
        )

    return AdPerformanceOut(
        rows=[
            CampaignRowOut(
                channel=r.channel,
                campaign_id=r.campaign_id,
                campaign_name=r.campaign_name,
                spend=r.spend,
                impressions=r.impressions,
                clicks=r.clicks,
                conversions=r.conversions,
                ctr=r.ctr,
                cpl=r.cpl,
                roas=r.roas,
                currency=r.currency,
                status=r.status,
            )
            for r in result.rows
        ],
        total_spend=result.total_spend,
        total_impressions=result.total_impressions,
        total_clicks=result.total_clicks,
        total_conversions=result.total_conversions,
        blended_roas=result.blended_roas,
        total_cpl=result.total_cpl,
        google_configured=result.google_configured,
        meta_configured=result.meta_configured,
        fetched_at=result.fetched_at,
        note=result.note,
    )


# ============================================================================
# Marketing Funnel Phase 0 — Ad-audience export (DARK, consent-gated)
# ============================================================================
# Foundation for Google Customer Match + Meta Custom Audiences. Everything here
# is EXPORT-FILE ONLY: no call ever leaves IMS to Google/Meta, no credentials
# are read. It builds hashed, consent-gated audience (ADD) and suppression
# (REMOVE) payloads; a later phase owns the actual upload once creds +
# DISPATCH_MODE go live. Gated to SUPERADMIN/ADMIN -- this discloses (hashed)
# personal data, so it sits at the same trust level as ad-spend + agency data.
# ============================================================================

_AD_AUDIENCE_ROLES = ("ADMIN",)


@router.get(
    "/ad-audience/summary",
    summary="Ad-audience export summary (counts only, no PII)",
    description=(
        "How many customers would export to a Google/Meta match audience vs the "
        "suppression (delete) list, gated on explicit AD_AUDIENCE DPDP consent + "
        "the unified opt-out gate. Returns COUNTS ONLY (no hashes). Optional "
        "store_id scopes to one store. SUPERADMIN/ADMIN only. DARK -- no external "
        "calls."
    ),
)
async def get_ad_audience_summary(
    store_id: Optional[str] = Query(None, description="Scope to one store; omit for all"),
    current_user: dict = Depends(require_roles("SUPERADMIN", *_AD_AUDIENCE_ROLES)),
):
    db = _get_db()
    from ..services.audience_export import summarize_ad_audience

    result = summarize_ad_audience(db, store_id=store_id)
    return {
        "store_id": result.store_id,
        "scanned": result.scanned,
        "audience_count": result.audience_count,
        "suppression_count": result.suppression_count,
        "email_only_count": result.email_only_count,
        "phone_count": result.phone_count,
        "skipped_no_contact": result.skipped_no_contact,
        "skipped_no_ad_consent": result.skipped_no_ad_consent,
        "generated_at": result.generated_at,
        "note": result.note,
    }


@router.get(
    "/ad-audience/export",
    summary="Ad-audience hashed export (DARK; JSON or CSV)",
    description=(
        "Build the hashed Customer-Match / Custom-Audience payload: an `audience` "
        "list (ADD -- opted-in, not suppressed) and a `suppression` list (REMOVE -- "
        "opted-out / withdrawn) in the chosen provider's field naming. No raw PII "
        "ever leaves IMS (SHA-256 only) and NO call is made to Google/Meta. "
        "provider = generic | google | meta; format = json | csv. SUPERADMIN/ADMIN."
    ),
)
async def get_ad_audience_export(
    provider: str = Query("generic", description="generic | google | meta"),
    store_id: Optional[str] = Query(None, description="Scope to one store; omit for all"),
    format: str = Query("json", description="json | csv"),
    current_user: dict = Depends(require_roles("SUPERADMIN", *_AD_AUDIENCE_ROLES)),
):
    if provider not in ("generic", "google", "meta"):
        raise HTTPException(status_code=422, detail="provider must be generic, google or meta")
    if format not in ("json", "csv"):
        raise HTTPException(status_code=422, detail="format must be json or csv")

    db = _get_db()
    from ..services.audience_export import (
        build_ad_audience_export,
        format_row,
        to_csv,
    )

    result = build_ad_audience_export(db, store_id=store_id, provider=provider)

    if format == "csv":
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(
            to_csv(result),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=ad_audience_{provider}.csv"
                )
            },
        )

    return {
        "provider": result.provider,
        "store_id": result.store_id,
        "generated_at": result.generated_at,
        "counts": {
            "scanned": result.scanned,
            "audience_count": result.audience_count,
            "suppression_count": result.suppression_count,
            "email_only_count": result.email_only_count,
            "phone_count": result.phone_count,
            "skipped_no_contact": result.skipped_no_contact,
            "skipped_no_ad_consent": result.skipped_no_ad_consent,
        },
        "audience": [format_row(r, provider) for r in result.audience],
        "suppression": [format_row(r, provider) for r in result.suppression],
        "note": result.note,
    }


# ============================================================================
# CRM-15: WHATSAPP OPT-IN / OPT-OUT (STOP LEDGER)
# ============================================================================
# TRAI/DLT and the WhatsApp Business Policy require that operators maintain a
# per-customer consent ledger for promotional messaging and honour STOP requests
# immediately.  This ties into the DPDP Act 2023 consent framework.
#
# Architecture:
#   - `whatsapp_consent_ledger` collection: immutable audit of every consent
#     event (OPT_IN / OPT_OUT / STOP) with the source (API / INBOUND_STOP /
#     CUSTOMER_REQUEST) and the actor (staff id or "system").
#   - The customers.marketing_consent field remains the single source of truth
#     for the runtime gate (checked by every send path).  The ledger is the
#     immutable audit trail.
#   - A STOP event written here ALSO flips customers.marketing_consent = False
#     atomically so the next send path check immediately respects the opt-out.
#   - An OPT_IN event written here flips marketing_consent = True so re-consent
#     is recorded and honoured.
#
# Roles: any authenticated staff can log a consent event (they relay what the
# customer verbally told them); the ledger is ADMIN-readable for compliance.
# ============================================================================

_CONSENT_LEDGER_COLL = "whatsapp_consent_ledger"


def is_opted_out(customer_id: str, db) -> bool:
    """Check if a customer is opted out via marketing_consent OR ledger.
    
    Returns True if:
    - customers.marketing_consent is explicitly False, OR
    - the most recent whatsapp_consent_ledger entry has event in {OPT_OUT, STOP}
    
    This unified check ensures all outbound paths honor all 3 opt-out signals:
    1. The customers.marketing_consent flag (direct state)
    2. INBOUND_STOP events in the ledger (TRAI/DPDP audit trail)
    3. Explicit OPT_OUT events (manual withdrawal)
    """
    if db is None:
        return False
    
    # Check the direct flag first (fast path)
    cust = db.get_collection("customers").find_one({"customer_id": customer_id}) or {}
    if cust.get("marketing_consent") is False:
        return True
    
    # Check the most recent ledger entry (fail-soft: a ledger lookup error must
    # never 500 the send path -- the marketing_consent flag above is authoritative).
    try:
        latest_event = db.get_collection(_CONSENT_LEDGER_COLL).find_one(
            {"customer_id": customer_id},
            sort=[("recorded_at", -1)]
        )
        if latest_event and latest_event.get("event") in {"OPT_OUT", "STOP"}:
            return True
    except Exception:
        pass

    return False


class ConsentEventRequest(BaseModel):
    customer_id: str
    event: Literal["OPT_IN", "OPT_OUT", "STOP"]
    source: Literal["API", "INBOUND_STOP", "CUSTOMER_REQUEST", "STAFF_ENTRY"] = "STAFF_ENTRY"
    notes: Optional[str] = None


@router.post("/whatsapp-consent")
async def record_whatsapp_consent(
    req: ConsentEventRequest,
    current_user: dict = Depends(get_current_user),
):
    """Record a WhatsApp consent event (OPT_IN / OPT_OUT / STOP) for a customer.

    Immediately updates customers.marketing_consent so every outbound send path
    reflects the new state.  The event is also written to the immutable
    whatsapp_consent_ledger for DPDP / TRAI audit.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Verify customer exists.
    customer = db.get_collection("customers").find_one({"customer_id": req.customer_id})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Compute the new consent state from the event type.
    new_consent = req.event == "OPT_IN"

    # Atomically update customers.marketing_consent.
    db.get_collection("customers").update_one(
        {"customer_id": req.customer_id},
        {
            "$set": {
                "marketing_consent": new_consent,
                "marketing_consent_updated_at": datetime.now().isoformat(),
            }
        },
    )

    # Write an immutable ledger row.
    ledger_id = f"CLE-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    ledger_row = {
        "ledger_id": ledger_id,
        "customer_id": req.customer_id,
        "customer_name": customer.get("name", ""),
        "customer_phone": customer.get("mobile", ""),
        "event": req.event,
        "source": req.source,
        "marketing_consent_after": new_consent,
        "notes": req.notes or "",
        "recorded_by": current_user.get("user_id", "unknown"),
        "recorded_at": datetime.now().isoformat(),
    }
    db.get_collection(_CONSENT_LEDGER_COLL).insert_one(ledger_row)

    verb = "opted in to" if new_consent else "opted out of"
    return {
        "message": f"Customer {verb} WhatsApp marketing",
        "ledger_id": ledger_id,
        "customer_id": req.customer_id,
        "marketing_consent": new_consent,
        "event": req.event,
    }


@router.get("/whatsapp-consent/{customer_id}")
async def get_whatsapp_consent_history(
    customer_id: str,
    limit: int = Query(50, le=200),
    current_user: dict = Depends(get_current_user),
):
    """Return the full WhatsApp consent history for a customer (audit ledger)."""
    db = _get_db()
    if db is None:
        return {"customer_id": customer_id, "events": [], "total": 0, "current_consent": None}

    customer = db.get_collection("customers").find_one({"customer_id": customer_id}) or {}
    rows = list(
        db.get_collection(_CONSENT_LEDGER_COLL)
        .find({"customer_id": customer_id})
        .sort("recorded_at", -1)
        .limit(limit)
    )
    for r in rows:
        r.pop("_id", None)

    return {
        "customer_id": customer_id,
        "current_consent": customer.get("marketing_consent"),
        "events": rows,
        "total": len(rows),
    }


@router.get("/whatsapp-consent-ledger")
async def get_whatsapp_consent_ledger(
    store_id: Optional[str] = Query(None),
    event: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Admin view of all WhatsApp consent events (DPDP / TRAI compliance audit)."""
    db = _get_db()
    if db is None:
        return {"events": [], "total": 0}

    query = {}
    if event and event.upper() in {"OPT_IN", "OPT_OUT", "STOP"}:
        query["event"] = event.upper()

    rows = list(
        db.get_collection(_CONSENT_LEDGER_COLL)
        .find(query)
        .sort("recorded_at", -1)
        .limit(limit)
    )
    for r in rows:
        r.pop("_id", None)

    return {"events": rows, "total": len(rows)}
