"""
IMS 2.0 - Reminder/segment rail (Engine E6, Wave 3)
===================================================
The SINGLE config-driven outbound rail for all recurring / triggered customer
messages. A ``reminder_rules`` document describes WHO to contact (a segment_key),
WHEN (CRON or EVENT trigger), on WHICH channel, with WHICH template, and whether
a voucher is attached. At evaluation time the rule resolves its audience through
the EXISTING segment resolvers (campaign_segments), then passes each recipient
through an ordered gate stack before queueing through the EXISTING send path.

E6 is the POLICY / ELIGIBILITY layer only. It does NOT introduce a second sender,
a second consent store, or a second clock. Every reused helper is imported, never
reimplemented:

  - consent       -> marketing.is_opted_out (3-signal flag + ledger)
  - quiet-hours   -> agents.quiet_hours.in_quiet_hours / next_quiet_end_utc_iso
  - send          -> notification_service.send_notification (PENDING honest-status,
                     DISPATCH_MODE-gated; with DISPATCH_MODE=off nothing leaves)
  - freq-cap key  -> policy_engine.get_policy("comms.cap_per_customer_30d") (E2)

BUILD-DARK CONTRACT (COMMS CHANNEL DIRECTIVE 2026-06-07):
  WhatsApp is disabled (Meta healthcare policy). This module sends NO live
  customer message: every send path rides send_notification, which writes a
  PENDING row and only ever dispatches under DISPATCH_MODE=live (the Railway
  default is ``off``). Seeded rules are active=False so nothing auto-sends on
  deploy. dry_run / preview NEVER write a notification or a ledger row.

Frequency cap is a SOFT ceiling (CORRECTIONS P1): the count-then-write is racy
across workers; we tolerate +/-1 (TRAI tolerance) rather than serialize the
sell-path with a global counter. OTP / transactional sends (is_transactional)
SHORT-CIRCUIT consent + quiet-hours + freq-cap FIRST.

No emojis in this file (Windows cp1252). Single-document writes only.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Locked default (DECISIONS sec 2 #10). E2's comms.cap_per_customer_30d overrides
# this when E2 is live; falls back to 3 if the key is absent.
DEFAULT_CAP_PER_CUSTOMER_30D = 3
FREQ_CAP_WINDOW_DAYS = 30

# Categories that DO NOT count against (and are NOT written to) the marketing cap.
_NON_MARKETING_CATEGORIES = {"OTP", "SERVICE", "TRANSACTIONAL", "REMINDER"}

# Follow-up modes that route to a STAFF TASK rather than an outbound message.
_TASK_MODES = {"CALL", "IN_PERSON", "IN-PERSON"}

# OTP tuning.
OTP_EXPIRY_MINUTES = 5
OTP_MAX_ATTEMPTS = 5


# ---------------------------------------------------------------------------
# Small helpers (fail-soft imports so a unit test can monkeypatch each piece)
# ---------------------------------------------------------------------------


def _now_utc(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now


def _now_iso(now: Optional[datetime] = None) -> str:
    return _now_utc(now).isoformat()


def _is_opted_out(db, customer_id: str) -> bool:
    """Delegate to the canonical 3-signal consent check (marketing.is_opted_out).
    Fail-soft: an import/lookup error must NOT block a send decision falsely --
    treat as not-opted-out (the gate is best-effort defence; campaigns.py shares
    it)."""
    if not customer_id:
        return False
    try:
        from api.routers.marketing import is_opted_out

        return bool(is_opted_out(customer_id, db))
    except Exception as exc:  # noqa: BLE001
        logger.debug("is_opted_out unavailable (%s) -- treating as consented", exc)
        return False


def _in_quiet_hours(now: Optional[datetime] = None) -> bool:
    try:
        from agents.quiet_hours import in_quiet_hours

        return bool(in_quiet_hours(now))
    except Exception as exc:  # noqa: BLE001
        logger.debug("quiet_hours unavailable (%s) -- not deferring", exc)
        return False


def _next_quiet_end_utc_iso(now: Optional[datetime] = None) -> Optional[str]:
    try:
        from agents.quiet_hours import next_quiet_end_utc_iso

        return next_quiet_end_utc_iso(now)
    except Exception as exc:  # noqa: BLE001
        logger.debug("next_quiet_end unavailable (%s)", exc)
        return None


def _cap_limit(rule: Optional[Dict[str, Any]] = None) -> int:
    """The 30-day cap, resolved via E2 get_policy with a locked code default.
    Scope is the rule's store/entity when present so a store can lower it."""
    scope: Optional[Dict[str, Any]] = None
    if rule:
        scope = {}
        if rule.get("store_id"):
            scope["store_id"] = rule["store_id"]
        if rule.get("entity_id"):
            scope["entity_id"] = rule["entity_id"]
        scope = scope or None
    try:
        from api.services.policy_engine import get_policy

        val = get_policy(
            "comms.cap_per_customer_30d", scope, default=DEFAULT_CAP_PER_CUSTOMER_30D
        )
        return int(val)
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_policy cap unavailable (%s) -- using default", exc)
        return DEFAULT_CAP_PER_CUSTOMER_30D


# ---------------------------------------------------------------------------
# Frequency cap (SOFT ceiling) + comms_ledger write
# ---------------------------------------------------------------------------


def check_frequency_cap(
    db,
    customer_id: str,
    *,
    now: Optional[datetime] = None,
    window_days: int = FREQ_CAP_WINDOW_DAYS,
    max_msgs: Optional[int] = None,
    category: str = "MARKETING",
    rule: Optional[Dict[str, Any]] = None,
) -> bool:
    """True if this customer may still receive a MARKETING message this window.

    SOFT ceiling: counts ``comms_ledger`` MARKETING-class rows for the customer
    inside the last ``window_days`` days; returns True when count < max. OTP /
    SERVICE rows never count (they are exempt and are not written to the ledger).
    Transactional callers should short-circuit this function entirely.

    Fail-soft: DB absent / query error -> True (availability over a hard stop).
    """
    if db is None or not customer_id:
        return True
    # Transactional categories are not capped at all.
    if (category or "").upper() in _NON_MARKETING_CATEGORIES:
        return True
    limit = max_msgs if max_msgs is not None else _cap_limit(rule)
    if limit <= 0:
        # A 0 cap means "no automated marketing" -- block everything.
        return False
    cutoff = (_now_utc(now) - timedelta(days=window_days)).isoformat()
    try:
        coll = db.get_collection("comms_ledger")
        rows = list(
            coll.find({"customer_id": customer_id, "sent_at": {"$gte": cutoff}})
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("freq-cap count failed for %s: %s", customer_id, exc)
        return True
    count = 0
    for r in rows:
        cat = (r.get("category") or "MARKETING").upper()
        if cat in _NON_MARKETING_CATEGORIES:
            continue
        count += 1
    return count < limit


def record_outbound(
    db,
    customer_id: str,
    *,
    channel: str = "WHATSAPP",
    category: str = "MARKETING",
    rule_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> None:
    """Insert one ``comms_ledger`` row so the 30-day cap is accurate. Called
    AFTER a successful send_notification queue. OTP / SERVICE rows are NOT
    written (they are exempt from the cap, so recording them would wrongly
    consume a customer's marketing budget). Fail-soft -- never blocks the send.
    """
    if db is None or not customer_id:
        return
    if (category or "").upper() in _NON_MARKETING_CATEGORIES:
        return
    try:
        db.get_collection("comms_ledger").insert_one(
            {
                "ledger_id": f"CL-{uuid.uuid4().hex[:12].upper()}",
                "customer_id": customer_id,
                "channel": channel,
                "category": (category or "MARKETING").upper(),
                "rule_id": rule_id,
                "campaign_id": campaign_id,
                "sent_at": _now_iso(now),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("comms_ledger write failed for %s: %s", customer_id, exc)


# ---------------------------------------------------------------------------
# Gate stack
# ---------------------------------------------------------------------------


def passes_gates(
    db,
    rule: Dict[str, Any],
    recipient: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """Run the ordered gate stack for one recipient.

    Returns (ok, reason, meta). ``meta`` carries a ``scheduled_for`` ISO string
    when a PROMOTIONAL message must be DEFERRED (not dropped) because it is inside
    the IST quiet-hours window. The deferred message still queues PENDING with
    that scheduled_for; the drain releases it after 09:00 IST.

    Gate order (CORRECTIONS P1 -- OTP/transactional short-circuits FIRST):
      0. is_transactional -> bypass consent + quiet-hours + freq-cap entirely.
      1. consent     (marketing.is_opted_out)            -> reason "consent"
      2. quiet-hours (agents.quiet_hours.in_quiet_hours) -> DEFER (not a block)
      3. freq-cap    (check_frequency_cap, soft ceiling) -> reason "freqcap"
    A missing phone is the caller's concern (evaluate_rule reports "no_phone").
    """
    meta: Dict[str, Any] = {"scheduled_for": None}
    customer_id = recipient.get("customer_id", "")

    # Gate 0: transactional / OTP -- exempt from ALL three gates, send now.
    if rule.get("is_transactional"):
        return True, None, meta

    # Gate 1: consent.
    if _is_opted_out(db, customer_id):
        return False, "consent", meta

    # Gate 2: quiet-hours. Promotional messages inside 21:00-09:00 IST are
    # DEFERRED to the next 09:00 IST, never dropped.
    if _in_quiet_hours(now):
        sched = _next_quiet_end_utc_iso(now)
        meta["scheduled_for"] = sched

    # Gate 3: frequency cap (soft ceiling). Exempt when the rule opts out.
    if not rule.get("freq_cap_exempt"):
        if not check_frequency_cap(db, customer_id, now=now, rule=rule):
            return False, "freqcap", meta

    return True, None, meta


# ---------------------------------------------------------------------------
# Voucher gate (mint via the existing vouchers collection shape)
# ---------------------------------------------------------------------------


def _mint_voucher_for_rule(
    db,
    rule: Dict[str, Any],
    recipient: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Idempotently mint ONE voucher per (customer, rule, day) and return its
    code. Re-evaluating the same rule for the same customer on the same day does
    NOT mint a second voucher (dedupe key customer_id + rule_id + date). The
    voucher document mirrors vouchers.issue_voucher's shape (the canonical store);
    we do NOT fork a parallel mint path -- we write the same collection with the
    same fields. Fail-soft -> None (the message still goes, just without a code).
    """
    tmpl = rule.get("voucher_template")
    if not tmpl or db is None:
        return None
    customer_id = recipient.get("customer_id", "")
    rule_id = rule.get("rule_id", "")
    day = _now_utc(now).strftime("%Y-%m-%d")
    dedupe = f"{customer_id}:{rule_id}:{day}"
    try:
        coll = db.get_collection("vouchers")
    except Exception as exc:  # noqa: BLE001
        logger.warning("voucher collection unavailable: %s", exc)
        return None

    # Idempotent: a voucher already minted for this (customer, rule, day) wins.
    try:
        existing = coll.find_one({"reminder_dedupe": dedupe, "status": "ACTIVE"})
        if existing:
            return existing.get("code")
    except Exception:  # noqa: BLE001
        existing = None

    vtype = (tmpl.get("type") or "DISCOUNT").upper()
    if vtype not in ("GIFT_CARD", "DISCOUNT"):
        vtype = "DISCOUNT"
    amount = float(tmpl.get("amount") or 0)
    validity_days = int(tmpl.get("validity_days") or 30)
    expiry = (_now_utc(now) + timedelta(days=validity_days)).date().isoformat()

    # Mint via the CANONICAL path (vouchers.mint_voucher) -- NOT a parallel insert.
    # This guarantees the same ACTIVE doc shape (voucher_id/initial_amount/balance/
    # currency/status/redemptions/...) that redeem_voucher_atomic + the E1
    # money-guard read at redemption; reminder_dedupe + source ride `extra`.
    from api.routers.vouchers import mint_voucher

    try:
        doc = mint_voucher(
            coll,
            vtype=vtype,
            amount=amount,
            store_id=rule.get("store_id"),
            customer_id=customer_id,
            issued_by=f"reminder:{rule_id}",
            expiry_date_iso=expiry,
            extra={"reminder_dedupe": dedupe, "source": "reminder"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("voucher mint failed for %s: %s", customer_id, exc)
        return None
    return doc.get("code") if doc else None


# ---------------------------------------------------------------------------
# Staff-task creation for CALL / IN-PERSON follow-up modes
# ---------------------------------------------------------------------------


def _create_followup_task(
    db,
    rule: Dict[str, Any],
    recipient: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Create a staff task for a follow-up that must be actioned by a human
    (mode CALL / IN_PERSON) rather than an outbound message. Single-document
    insert into ``tasks``. Fail-soft -> False."""
    if db is None:
        return False
    customer_id = recipient.get("customer_id", "")
    name = recipient.get("name", "Customer")
    mode = (recipient.get("variables", {}) or {}).get("mode", "CALL")
    iso = _now_iso(now)
    try:
        db.get_collection("tasks").insert_one(
            {
                "task_id": f"TSK-{uuid.uuid4().hex[:10].upper()}",
                "task_type": "follow_up",
                "title": f"Follow-up ({mode}): {name}",
                "description": (
                    f"Reminder rule '{rule.get('name', rule.get('rule_id'))}' "
                    f"requires a {mode} follow-up with {name}."
                ),
                "status": "pending",
                "priority": "P3",
                "customer_id": customer_id,
                "store_id": rule.get("store_id"),
                "related_rule_id": rule.get("rule_id"),
                "follow_up_mode": mode,
                "created_at": iso,
                "created_by": f"reminder:{rule.get('rule_id')}",
            }
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("follow-up task create failed for %s: %s", customer_id, exc)
        return False


# ---------------------------------------------------------------------------
# evaluate_rule -- the public engine entrypoint
# ---------------------------------------------------------------------------


def _empty_result(rule: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    return {
        "rule_id": rule.get("rule_id", ""),
        "dry_run": bool(dry_run),
        "resolved": 0,
        "queued": 0,
        "tasks_created": 0,
        "voucher_minted": 0,
        "skipped_consent": 0,
        "skipped_freqcap": 0,
        "skipped_quiet": 0,
        "skipped_no_phone": 0,
        "errors": 0,
    }


async def evaluate_rule(
    db,
    rule: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Resolve a rule's audience and run the gate stack per recipient.

    When ``dry_run`` is True NOTHING is written: no notification_logs row, no
    comms_ledger row, no voucher, no task. The returned counts are the
    suppression breakdown for the preview panel. This is the contract the
    acceptance test (T9) locks: preview is read-only.

    When ``dry_run`` is False and a recipient passes the gates, the message is
    queued via send_notification (PENDING, DISPATCH_MODE-gated -- with the
    Railway default ``off`` nothing is dispatched to a real customer) and a
    comms_ledger row is recorded for the cap. fu_due_today recipients whose mode
    is CALL / IN_PERSON get a staff task instead of a message.
    """
    result = _empty_result(rule, dry_run)
    if db is None:
        return result

    try:
        from api.services import campaign_segments as seg
    except Exception as exc:  # noqa: BLE001
        logger.warning("campaign_segments import failed: %s", exc)
        result["errors"] += 1
        return result

    audience = seg.resolve_segment(
        db,
        rule.get("segment_key", ""),
        store_id=rule.get("store_id") or None,
        params=rule.get("segment_params") or {},
    )
    result["resolved"] = len(audience)

    template_id = rule.get("template_id", "")
    channel = (rule.get("channel") or "WHATSAPP").upper()
    store_id = rule.get("store_id") or ""
    category = "OTP" if rule.get("is_transactional") else "MARKETING"

    # Lazy import so a unit test can monkeypatch send_notification on this module.
    send_notification = None
    if not dry_run:
        try:
            from api.services.notification_service import (
                send_notification as _send,
            )

            send_notification = _send
        except Exception as exc:  # noqa: BLE001
            logger.warning("send_notification import failed: %s", exc)

    for recipient in audience:
        customer_id = recipient.get("customer_id", "")
        variables = dict(recipient.get("variables") or {})
        mode = (variables.get("mode") or "").upper()

        # fu_due_today CALL / IN_PERSON -> staff task, not a message. This branch
        # is NOT subject to the message gates (it is a human task, not an
        # outbound comm) but still respects dry_run.
        if mode in _TASK_MODES:
            if dry_run:
                result["tasks_created"] += 1
                continue
            if _create_followup_task(db, rule, recipient, now=now):
                result["tasks_created"] += 1
            else:
                result["errors"] += 1
            continue

        ok, reason, meta = passes_gates(db, rule, recipient, now=now)
        if not ok:
            if reason == "consent":
                result["skipped_consent"] += 1
            elif reason == "freqcap":
                result["skipped_freqcap"] += 1
            else:
                result["errors"] += 1
            continue

        phone = recipient.get("phone") or ""
        if not phone:
            result["skipped_no_phone"] += 1
            continue

        # Quiet-hours DEFER: the recipient passes but the message is held until
        # 09:00 IST. We still queue it (with scheduled_for) -- it is not dropped.
        scheduled_for = meta.get("scheduled_for")
        if scheduled_for:
            result["skipped_quiet"] += 1  # counted as deferred for the preview

        if dry_run:
            # Read-only: no voucher, no send, no ledger.
            continue

        # Voucher gate (idempotent mint) before send so {voucher_code} is in vars.
        if rule.get("voucher_template"):
            code = _mint_voucher_for_rule(db, rule, recipient, now=now)
            if code:
                variables["voucher_code"] = code
                result["voucher_minted"] += 1

        if send_notification is None:
            result["errors"] += 1
            continue

        try:
            res = await send_notification(
                store_id=store_id,
                customer_id=customer_id,
                customer_phone=phone,
                customer_name=recipient.get("name", "Customer"),
                template_id=template_id,
                channel=channel,
                variables=variables,
                category=category,
                triggered_by=f"reminder:{rule.get('rule_id')}",
                related_entity_type="reminder_rule",
                related_entity_id=rule.get("rule_id"),
            )
            # Stamp rule_id + scheduled_for (defer) on the just-queued row.
            nid = res.get("notification_id") if isinstance(res, dict) else None
            if nid:
                set_fields: Dict[str, Any] = {"rule_id": rule.get("rule_id")}
                if scheduled_for:
                    set_fields["scheduled_for"] = scheduled_for
                try:
                    db.get_collection("notification_logs").update_one(
                        {"notification_id": nid}, {"$set": set_fields}
                    )
                except Exception:  # noqa: BLE001
                    pass
            result["queued"] += 1
            # Record the cap ledger row (skipped for transactional inside
            # record_outbound). This keeps the 30-day cap accurate.
            record_outbound(
                db,
                customer_id,
                channel=channel,
                category=category,
                rule_id=rule.get("rule_id"),
                now=now,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("reminder send failed for %s: %s", customer_id, exc)
            result["errors"] += 1

    return result


# ---------------------------------------------------------------------------
# Seed + indexes (idempotent; run once on deploy)
# ---------------------------------------------------------------------------

# 6 GLOBAL inactive rules. active=False is the safety flag: nothing auto-sends
# on deploy (and the comms channel is currently disabled -- build-dark). The
# owner toggles each one on when a channel returns. Upsert on rule_id makes the
# seed idempotent and NON-destructive (never overwrites owner edits to a rule).
_SEED_RULES: List[Dict[str, Any]] = [
    {
        "rule_id": "RMD-SEED-RX-EXPIRY",
        "name": "Prescription expiry reminder",
        "rule_type": "rx_expiry",
        "segment_key": "rx_expiry",
        "channel": "WHATSAPP",
        "template_id": "PRESCRIPTION_EXPIRY",
        "trigger": {"kind": "CRON", "cron": "DAILY 09:00", "event_key": None},
    },
    {
        "rule_id": "RMD-SEED-BIRTHDAY",
        "name": "Birthday greeting",
        "rule_type": "birthday",
        "segment_key": "birthday",
        "channel": "WHATSAPP",
        "template_id": "BIRTHDAY_WISH",
        "trigger": {"kind": "CRON", "cron": "DAILY 09:00", "event_key": None},
    },
    {
        "rule_id": "RMD-SEED-WINBACK",
        "name": "Win-back (lapsed customers)",
        "rule_type": "winback",
        "segment_key": "winback",
        "channel": "WHATSAPP",
        "template_id": "WALKOUT_RECOVERY",
        "trigger": {"kind": "CRON", "cron": "WEEKLY 10:00", "event_key": None},
    },
    {
        "rule_id": "RMD-SEED-CL-REORDER",
        "name": "Contact-lens reorder reminder",
        "rule_type": "cl_reorder",
        "segment_key": "cl_reorder",
        "channel": "WHATSAPP",
        "template_id": "ANNUAL_CHECKUP_REMINDER",
        "trigger": {"kind": "CRON", "cron": "DAILY 10:00", "event_key": None},
    },
    {
        "rule_id": "RMD-SEED-CHURN-RISK",
        "name": "Churn-risk alert",
        "rule_type": "churn_risk",
        "segment_key": "churn_risk",
        "channel": "WHATSAPP",
        "template_id": "WALKOUT_RECOVERY",
        "trigger": {"kind": "EVENT", "cron": None, "event_key": "churn.detected"},
    },
    {
        "rule_id": "RMD-SEED-FEEDBACK",
        "name": "Post-delivery feedback / NPS",
        "rule_type": "feedback",
        "segment_key": "recent_buyers",
        "channel": "WHATSAPP",
        "template_id": "NPS_SURVEY",
        "trigger": {"kind": "CRON", "cron": "DAILY 11:00", "event_key": None},
    },
]


def ensure_reminder_indexes(db) -> None:
    """Create the reminder-rail indexes (idempotent). Fail-soft."""
    if db is None:
        return
    try:
        db.get_collection("reminder_rules").create_index("rule_id", unique=True)
        db.get_collection("reminder_rules").create_index([("active", 1), ("rule_type", 1)])
        db.get_collection("comms_ledger").create_index([("customer_id", 1), ("sent_at", 1)])
        db.get_collection("pool_otp").create_index("otp_id", unique=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reminder index creation skipped: %s", exc)


def seed_reminder_rules(db) -> int:
    """Upsert the 6 GLOBAL inactive seed rules. Idempotent + non-destructive:
    only inserts a rule that does not already exist (by rule_id) so an owner who
    later edits/activates a seeded rule is never reset. Returns the number of
    rules newly inserted. active=False on every seed -- no auto-send on deploy.
    """
    if db is None:
        return 0
    coll = db.get_collection("reminder_rules")
    now = _now_iso()
    inserted = 0
    for spec in _SEED_RULES:
        try:
            if coll.find_one({"rule_id": spec["rule_id"]}):
                continue
            doc = {
                "rule_id": spec["rule_id"],
                "scope": "GLOBAL",
                "entity_id": None,
                "store_id": None,
                "name": spec["name"],
                "rule_type": spec["rule_type"],
                "segment_key": spec["segment_key"],
                "segment_params": {},
                "channel": spec["channel"],
                "template_id": spec["template_id"],
                "trigger": spec["trigger"],
                "is_transactional": False,
                "freq_cap_exempt": False,
                "voucher_template": None,
                "active": False,  # SAFE DEFAULT -- inert until owner toggles on.
                "last_run_at": None,
                "last_resolved": None,
                "sent_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "deleted_at": None,
                "created_by": "system:seed",
                "created_at": now,
                "updated_at": now,
            }
            coll.insert_one(doc)
            inserted += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("reminder seed %s skipped: %s", spec.get("rule_id"), exc)
    return inserted


# ---------------------------------------------------------------------------
# Family-wallet pool OTP (Wave 0b transactional slice; LOCKED design)
# ---------------------------------------------------------------------------


def _hash_code(code: str) -> str:
    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()


async def send_pool_redemption_otp(
    db,
    *,
    primary_customer_id: str,
    household_id: str,
    amount: float,
    requested_by: str,
    primary_mobile: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Issue a 6-digit OTP for a family-wallet pool redemption. The OTP rides the
    transactional path (category=OTP) so it short-circuits consent + quiet-hours
    + freq-cap. Only the sha256 hash is stored, never the raw code. Returns
    {ok, otp_id, expires_at}. Fail-soft -> {ok: False, reason}.

    The redemption itself is NOT performed here -- this only proves the primary
    member authorized it; the money-guard debit happens after verify.
    """
    if db is None:
        return {"ok": False, "reason": "unavailable"}
    code = f"{secrets.randbelow(1_000_000):06d}"
    created = _now_utc(now)
    expires = created + timedelta(minutes=OTP_EXPIRY_MINUTES)
    otp_id = f"OTP-{uuid.uuid4().hex[:12].upper()}"
    doc = {
        "otp_id": otp_id,
        "household_id": household_id,
        "primary_customer_id": primary_customer_id,
        "code_hash": _hash_code(code),
        "amount": float(amount),
        "status": "PENDING",
        "attempts": 0,
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
        "consumed_at": None,
        "requested_by": requested_by,
    }
    try:
        db.get_collection("pool_otp").insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pool_otp insert failed: %s", exc)
        return {"ok": False, "reason": "otp_store_failed"}

    # Resolve the primary member's mobile if not supplied.
    phone = primary_mobile or ""
    if not phone:
        try:
            cust = (
                db.get_collection("customers").find_one(
                    {"customer_id": primary_customer_id}
                )
                or {}
            )
            phone = cust.get("mobile") or cust.get("phone") or ""
        except Exception:  # noqa: BLE001
            phone = ""

    # Queue the OTP message on the transactional path (DISPATCH_MODE-gated).
    if phone:
        try:
            from api.services.notification_service import send_notification

            await send_notification(
                store_id="",
                customer_id=primary_customer_id,
                customer_phone=phone,
                customer_name="Customer",
                template_id="POOL_REDEEM_OTP",
                channel="SMS",
                variables={"otp": code, "amount": amount},
                category="OTP",
                triggered_by=requested_by,
                related_entity_type="pool_otp",
                related_entity_id=otp_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OTP send failed: %s", exc)

    return {"ok": True, "otp_id": otp_id, "expires_at": doc["expires_at"]}


def verify_pool_redemption_otp(
    db,
    *,
    otp_id: str,
    code: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Verify a pool-redemption OTP. Concurrency-safe consume via a single
    guarded find_one_and_update: at most ONE caller flips PENDING -> VERIFIED for
    a given otp_id, so two concurrent verifies cannot both succeed.

    After OTP_MAX_ATTEMPTS wrong codes the OTP is FAILED. An expired OTP is
    EXPIRED. Returns {ok, reason?}.
    """
    if db is None:
        return {"ok": False, "reason": "unavailable"}
    try:
        coll = db.get_collection("pool_otp")
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": "unavailable"}

    doc = coll.find_one({"otp_id": otp_id})
    if not doc:
        return {"ok": False, "reason": "not_found"}
    if doc.get("status") != "PENDING":
        return {"ok": False, "reason": doc.get("status", "consumed").lower()}

    # Expiry check.
    try:
        expires = datetime.fromisoformat(
            str(doc.get("expires_at")).replace("Z", "+00:00")
        )
        cur = _now_utc(now)
        if cur.tzinfo is None:
            cur = cur.replace(tzinfo=timezone.utc)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if cur > expires:
            coll.update_one(
                {"otp_id": otp_id, "status": "PENDING"},
                {"$set": {"status": "EXPIRED"}},
            )
            return {"ok": False, "reason": "expired"}
    except Exception:  # noqa: BLE001
        pass

    # Wrong code: ATOMIC attempt bump. The attempts ceiling lives IN the
    # filter ($lt max-1), so N concurrent wrong guesses can never exceed the
    # budget (the old read-modify-write let racers reset/blow past it). A
    # bump that would reach the max instead flips PENDING -> FAILED in one
    # guarded update -- exactly one flipper wins.
    if _hash_code(code) != doc.get("code_hash"):
        try:
            bumped = coll.find_one_and_update(
                {"otp_id": otp_id, "status": "PENDING",
                 "attempts": {"$lt": OTP_MAX_ATTEMPTS - 1}},
                {"$inc": {"attempts": 1}},
            )
        except Exception:  # noqa: BLE001
            bumped = None
        if bumped is not None:
            return {"ok": False, "reason": "wrong_code"}
        try:
            failed = coll.find_one_and_update(
                {"otp_id": otp_id, "status": "PENDING"},
                {"$inc": {"attempts": 1}, "$set": {"status": "FAILED"}},
            )
        except Exception:  # noqa: BLE001
            failed = None
        if failed is not None:
            return {"ok": False, "reason": "max_attempts"}
        # Already FAILED/consumed by a racer.
        return {"ok": False, "reason": "max_attempts"}

    # Correct code: ATOMIC consume. Only one writer matches PENDING.
    try:
        updated = coll.find_one_and_update(
            {"otp_id": otp_id, "status": "PENDING"},
            {"$set": {"status": "VERIFIED", "consumed_at": _now_iso(now)}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("OTP consume failed: %s", exc)
        return {"ok": False, "reason": "consume_failed"}
    if not updated:
        # Another thread consumed it first.
        return {"ok": False, "reason": "already_consumed"}
    return {
        "ok": True,
        "amount": doc.get("amount"),
        "household_id": doc.get("household_id"),
    }
