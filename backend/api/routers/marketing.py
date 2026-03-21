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
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, date, timedelta
import uuid

from .auth import get_current_user
from ..dependencies import get_db as _dep_get_db
from ..services.notification_service import send_notification, populate_template

router = APIRouter()


def _get_db():
    try:
        from database.connection import get_db
        return get_db().db
    except Exception:
        return _dep_get_db()


# ============================================================================
# SCHEMAS
# ============================================================================

class SendNotificationRequest(BaseModel):
    customer_id: str
    customer_phone: str
    customer_name: str
    template_id: str
    channel: str = "WHATSAPP"
    variables: dict = {}
    category: str = "SERVICE"

class WalkinRequest(BaseModel):
    phone: str = Field(..., pattern=r"^\d{10}$")
    name: Optional[str] = None
    interest: str = "frames"
    notes: Optional[str] = None

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
    current_user: dict = Depends(get_current_user),
):
    """Send a notification to a customer via WhatsApp/SMS"""
    active_store = store_id or current_user.get("active_store_id", "")
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


@router.get("/notifications/logs")
async def get_notification_logs(
    store_id: Optional[str] = Query(None),
    template_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    current_user: dict = Depends(get_current_user),
):
    """Get notification logs with filters"""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
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
    if not db:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Look up order and customer
    order = db.get_collection("orders").find_one({"order_id": order_id})
    if not order:
        order = db.get_collection("orders").find_one({"_id": order_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    customer_id = order.get("customer_id", "")
    customer = db.get_collection("customers").find_one({"customer_id": customer_id}) or {}

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
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
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
    if not db:
        raise HTTPException(status_code=503, detail="Database unavailable")

    customer = db.get_collection("customers").find_one({"customer_id": customer_id}) or {}
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    store_id = current_user.get("active_store_id", "")
    store = db.get_collection("stores").find_one({"store_id": store_id}) or {}

    result = await send_notification(
        store_id=store_id,
        customer_id=customer_id,
        customer_phone=customer.get("mobile", ""),
        customer_name=customer.get("name", "Customer"),
        template_id="PRESCRIPTION_EXPIRY",
        channel="WHATSAPP",
        variables={
            "store_name": store.get("name", "Better Vision"),
            "store_phone": store.get("phone", ""),
            "expiry_date": "soon",
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
    if not db:
        return {"message": "Snoozed"}

    snooze_until = (datetime.now() + timedelta(days=req.days)).isoformat()
    db.get_collection("notification_logs").update_many(
        {"customer_id": customer_id, "template_id": "PRESCRIPTION_EXPIRY"},
        {"$set": {"snooze_until": snooze_until}},
    )
    return {"message": f"Alert snoozed for {req.days} days", "snooze_until": snooze_until}


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
    if not db:
        raise HTTPException(status_code=503, detail="Database unavailable")

    customer = db.get_collection("customers").find_one({"customer_id": customer_id}) or {}
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    store_id = current_user.get("active_store_id", "")

    # Generate or get existing referral code
    ref_coll = db.get_collection("referrals")
    existing = ref_coll.find_one({"referrer_customer_id": customer_id, "store_id": store_id, "status": "INVITED"})

    if existing:
        referral_code = existing["referral_code"]
    else:
        # Generate code from customer name
        name_part = customer.get("name", "REF").split()[0].upper()[:6]
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
    return {"message": "Referral invite sent", "referral_code": referral_code, "notification": result}


@router.get("/referrals")
async def get_referrals(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    current_user: dict = Depends(get_current_user),
):
    """List referrals for a store"""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
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
    current_user: dict = Depends(get_current_user),
):
    """Credit rewards for a completed referral"""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database unavailable")

    coll = db.get_collection("referrals")
    referral = coll.find_one({"referral_id": referral_id})
    if not referral:
        raise HTTPException(status_code=404, detail="Referral not found")

    # Credit store credit to referrer
    reward = referral.get("reward_amount", 500)
    db.get_collection("customers").update_one(
        {"customer_id": referral["referrer_customer_id"]},
        {"$inc": {"store_credit": reward, "loyalty_points": int(reward / 10)}}
    )

    coll.update_one(
        {"referral_id": referral_id},
        {"$set": {"status": "REWARD_CREDITED", "reward_credited_at": datetime.now().isoformat()}}
    )

    return {"message": f"Reward of Rs.{reward} credited", "referral_id": referral_id}


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
    if not db:
        raise HTTPException(status_code=503, detail="Database unavailable")

    order = db.get_collection("orders").find_one({"order_id": order_id}) or db.get_collection("orders").find_one({"_id": order_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    customer = db.get_collection("customers").find_one({"customer_id": order.get("customer_id")}) or {}
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
    if not db:
        raise HTTPException(status_code=503, detail="Database unavailable")

    coll = db.get_collection("nps_responses")
    result = coll.update_one(
        {"nps_id": req.nps_id},
        {"$set": {
            "score": req.score,
            "feedback": req.feedback,
            "status": "RESPONDED",
            "responded_at": datetime.now().isoformat(),
        }}
    )

    # If detractor (score <= 6), create follow-up task for manager
    if req.score <= 6:
        nps = coll.find_one({"nps_id": req.nps_id}) or {}
        db.get_collection("follow_ups").insert_one({
            "follow_up_id": f"FU-{uuid.uuid4().hex[:8].upper()}",
            "store_id": nps.get("store_id", ""),
            "customer_id": nps.get("customer_id", ""),
            "customer_name": nps.get("customer_name", ""),
            "type": "general",
            "reason": f"NPS detractor (score: {req.score}): {req.feedback or 'No feedback'}",
            "due_date": (datetime.now() + timedelta(days=1)).isoformat(),
            "status": "pending",
            "created_at": datetime.now().isoformat(),
        })

    return {"message": "NPS response recorded", "score": req.score}


@router.get("/nps-dashboard")
async def get_nps_dashboard(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get NPS dashboard data"""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        return {"avg_score": 0, "promoters": 0, "passives": 0, "detractors": 0, "response_rate": 0, "responses": []}

    coll = db.get_collection("nps_responses")
    all_surveys = list(coll.find({"store_id": active_store}).sort("created_at", -1).limit(200))

    responded = [s for s in all_surveys if s.get("score") is not None]
    scores = [s["score"] for s in responded]

    promoters = len([s for s in scores if s >= 9])
    passives = len([s for s in scores if 7 <= s <= 8])
    detractors = len([s for s in scores if s <= 6])
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0
    response_rate = round(len(responded) / len(all_surveys) * 100, 1) if all_surveys else 0

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
        "nps_score": round(((promoters - detractors) / len(responded) * 100), 1) if responded else 0,
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
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database unavailable")

    walkin_id = f"WLK-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

    # Check if phone matches existing customer
    existing_customer = db.get_collection("customers").find_one({"mobile": req.phone})

    walkin = {
        "walkin_id": walkin_id,
        "store_id": active_store,
        "phone": req.phone,
        "name": req.name or (existing_customer.get("name") if existing_customer else None),
        "interest": req.interest,
        "notes": req.notes,
        "existing_customer_id": existing_customer.get("customer_id") if existing_customer else None,
        "follow_up_created": True,
        "converted": False,
        "created_at": datetime.now().isoformat(),
        "created_by": current_user.get("user_id", "unknown"),
    }

    db.get_collection("walkins").insert_one(walkin)

    # Auto-create follow-up for next day
    db.get_collection("follow_ups").insert_one({
        "follow_up_id": f"FU-{uuid.uuid4().hex[:8].upper()}",
        "store_id": active_store,
        "customer_id": existing_customer.get("customer_id") if existing_customer else walkin_id,
        "customer_name": req.name or req.phone,
        "type": "general",
        "reason": f"Walk-in follow-up (Interest: {req.interest})",
        "due_date": (datetime.now() + timedelta(days=1)).isoformat(),
        "status": "pending",
        "created_at": datetime.now().isoformat(),
    })

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
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
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
# FEATURE 7: WALKOUT RECOVERY
# ============================================================================

@router.post("/walkout/{customer_id}")
async def record_walkout(
    customer_id: str,
    req: WalkoutRequest,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Record a customer walkout for recovery follow-up"""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database unavailable")

    customer = db.get_collection("customers").find_one({"customer_id": customer_id}) or {}

    walkout_id = f"WKO-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    store = db.get_collection("stores").find_one({"store_id": active_store}) or {}

    walkout = {
        "walkout_id": walkout_id,
        "store_id": active_store,
        "customer_id": customer_id,
        "customer_name": customer.get("name", ""),
        "customer_phone": customer.get("mobile", ""),
        "frames_tried": req.frames_tried,
        "reason": req.reason,
        "notes": req.notes,
        "recovery_message_sent": False,
        "recovered": False,
        "created_at": datetime.now().isoformat(),
        "created_by": current_user.get("user_id", "unknown"),
    }

    db.get_collection("walkouts").insert_one(walkout)

    # Schedule recovery message (log as SCHEDULED for now)
    frame_names = ", ".join(req.frames_tried[:3]) if req.frames_tried else "some frames"
    validity = (datetime.now() + timedelta(days=7)).strftime("%d %b %Y")

    await send_notification(
        store_id=active_store,
        customer_id=customer_id,
        customer_phone=customer.get("mobile", ""),
        customer_name=customer.get("name", "Customer"),
        template_id="WALKOUT_RECOVERY",
        channel="WHATSAPP",
        variables={
            "store_name": store.get("name", "Better Vision"),
            "frame_names": frame_names,
            "discount_percent": "10",
            "validity_date": validity,
        },
        category="PROMOTIONAL",
        triggered_by=current_user.get("user_id", "unknown"),
        related_entity_type="walkout",
        related_entity_id=walkout_id,
    )

    walkout.pop("_id", None)
    return {"message": "Walkout recorded, recovery message scheduled", "walkout": walkout}


@router.get("/walkout-recoveries")
async def get_walkout_recoveries(
    store_id: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    current_user: dict = Depends(get_current_user),
):
    """List walkout recovery attempts"""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        return {"walkouts": [], "total": 0, "recovered": 0, "recovery_rate": 0}

    coll = db.get_collection("walkouts")
    walkouts = list(coll.find({"store_id": active_store}).sort("created_at", -1).limit(limit))
    for w in walkouts:
        w.pop("_id", None)

    recovered = len([w for w in walkouts if w.get("recovered")])
    rate = round(recovered / len(walkouts) * 100, 1) if walkouts else 0

    return {"walkouts": walkouts, "total": len(walkouts), "recovered": recovered, "recovery_rate": rate}
