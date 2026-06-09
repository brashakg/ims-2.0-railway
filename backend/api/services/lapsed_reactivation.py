"""
IMS 2.0 - F41 Lapsed-patient reactivation (#41)
===============================================
An in-app REACTIVATION WORK-LIST: a per-store, ranked list of clinically lapsed
patients (no confirmed order AND no prescription exam in the lapse window) that a
store associate should personally reach out to and try to bring back.

This is a WORK-LIST -- a prioritisation + outcome-logging tool. It is NOT an
outbound message channel: nothing here sends WhatsApp / SMS / email. Building a
cohort writes an in-app `reactivation_cohorts` document + a `reactivation_call`
follow_up per entry; logging an outcome resolves that in-app follow_up. NEVER a
provider send. (WhatsApp is disabled -- STATUS COMMS DIRECTIVE 2026-06-07; #41
reactivation-SEND is DEFERRED, so F41 ships DARK / in-app exactly like the #39
NBA call list it mirrors.)

Design contract (mirrors nba_call_list.py)
-------------------------------------------
- Pure-ish + fail-soft: the builder takes the live `db` handle (the connection
  WRAPPER exposing `.get_collection`) and returns plain entry dicts. A missing DB
  or any query error yields an EMPTY list, never an exception.
- ZERO new cohort logic. The lapsed set is RESOLVED by the already-merged
  campaign_segments._resolve_lapsed_patient (dual-gap order+exam). VIP priority
  READS the persisted `customers.vip_churn_risk` subdoc written by ORACLE's EOD
  scan (api.services.vip_churn) -- we do NOT recompute or fork the vip_churn model.
- The lapse window + cohort size come from E2 policy_engine.get_policy
  ("reactivation.lapse_months") / ("reactivation.cohort_size"), not hard-coded
  constants (owner-tunable via Settings).
- IST clock: the "today" date is the IST calendar date (quiet_hours._IST), never
  a UTC date.

No emoji (Windows cp1252). No POS touch. No money mutation. No voucher mint.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# E2 policy keys (registered in policy_registry.py). Defaults mirror F41 packet
# (24-month lapse window, 50 entries/store) so a fresh DB still behaves correctly.
POLICY_LAPSE_MONTHS = "reactivation.lapse_months"
POLICY_COHORT_SIZE = "reactivation.cohort_size"
DEFAULT_LAPSE_MONTHS = 24
DEFAULT_COHORT_SIZE = 50

# A lapsed customer carrying a persisted vip_churn_risk gate (or VIP tag) is
# surfaced first -- a lapsed high-value patient is the highest-value reactivation.
_VIP_LABELS = ("WATCH", "HIGH")


def _get_cap(policy_key: str, default: int) -> int:
    """Resolve a cap from E2 policy_engine; fail-soft to the default (global scope)."""
    try:
        from .policy_engine import get_policy

        val = get_policy(policy_key, default=default)
        n = int(val)
        return n if n >= 1 else default
    except Exception as exc:  # noqa: BLE001
        logger.warning("[REACTIVATION] policy %s lookup failed: %s", policy_key, exc)
        return default


def _today_ist() -> str:
    """The IST calendar date as YYYY-MM-DD (the one clock, per E6)."""
    try:
        from agents.quiet_hours import _IST

        return datetime.now(_IST).date().isoformat()
    except Exception:  # noqa: BLE001
        return datetime.utcnow().date().isoformat()


def _ist_midnight_utc(date_str: str) -> datetime:
    """The UTC datetime corresponding to the NEXT IST midnight after `date_str`
    -- the TTL expiry so the day's work-list drops at end-of-IST-day."""
    try:
        from agents.quiet_hours import _IST

        d = datetime.fromisoformat(date_str)
        next_ist_midnight = datetime(d.year, d.month, d.day, tzinfo=_IST) + timedelta(days=1)
        return next_ist_midnight.astimezone().replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return datetime.utcnow() + timedelta(days=1)


def _ltv_paisa(customer: Dict[str, Any]) -> int:
    """Best-effort lifetime value in PAISA (integer)."""
    raw = customer.get("total_lifetime_value", customer.get("ltv", 0)) or 0
    try:
        return int(round(float(raw) * 100))
    except Exception:  # noqa: BLE001
        return 0


def _is_vip(customer: Dict[str, Any]) -> bool:
    """A lapsed customer is VIP-prioritised when ANY of: a VIP tag, a persisted
    vip_churn_risk gate (WATCH/HIGH), or LTV >= Rs 15,000. We READ the persisted
    subdoc rather than recompute it (no fork of the vip_churn model)."""
    tags = [str(t).strip().upper() for t in (customer.get("tags") or [])]
    if "VIP" in tags:
        return True
    vr = customer.get("vip_churn_risk") or {}
    if vr.get("risk_label") in _VIP_LABELS:
        return True
    return _ltv_paisa(customer) >= 1_500_000  # Rs 15,000 in paisa


def _headline(months_lapsed: Optional[int]) -> str:
    """Single sentence, sentence case, no emoji."""
    if months_lapsed is None:
        return "No visit on record -- a personal call to bring them back."
    yrs = months_lapsed // 12
    if yrs >= 2:
        return f"Lapsed about {yrs} years ({months_lapsed} months) -- worth a reactivation call."
    return f"Lapsed {months_lapsed} months -- their prescription has likely expired."


def build_cohort(
    db,
    store_id: Optional[str],
    *,
    now: Optional[datetime] = None,
    lapse_months: Optional[int] = None,
    cohort_size: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Resolve the store's lapsed patients and return up to `cohort_size` ranked
    work-list entries (VIP-prioritised first, then most-lapsed). Each entry is an
    in-app card the staff member calls / visits and then marks an outcome on.

    Fail-soft: no DB / no lapsed patients -> []. Pure: `now` injected for
    determinism. Builds NO message and mints NO voucher (dark / in-app only)."""
    if db is None:
        return []
    now = now or datetime.now()
    lapse_months = lapse_months if lapse_months is not None else _get_cap(
        POLICY_LAPSE_MONTHS, DEFAULT_LAPSE_MONTHS
    )
    cohort_size = cohort_size if cohort_size is not None else _get_cap(
        POLICY_COHORT_SIZE, DEFAULT_COHORT_SIZE
    )

    from . import campaign_segments as seg

    audience = seg.resolve_segment(
        db,
        "lapsed_patient",
        store_id=store_id,
        params={"lapse_threshold_months": lapse_months, "now": now},
    )
    if not audience:
        return []

    cust_coll = db.get_collection("customers")
    entries: List[Dict[str, Any]] = []
    for row in audience:
        cid = row.get("customer_id")
        if not cid:
            continue
        try:
            cust = cust_coll.find_one({"customer_id": cid}) or {}
        except Exception:  # noqa: BLE001
            cust = {}
        rv = row.get("variables") or {}
        months_lapsed = rv.get("months_lapsed")
        is_vip = _is_vip(cust)
        # Sort key: VIPs first, then most-lapsed (None = infinitely lapsed = max).
        sort_months = months_lapsed if isinstance(months_lapsed, int) else 10 ** 6
        entries.append({
            "customer_id": cid,
            "customer_name": cust.get("name") or row.get("name") or "Customer",
            "customer_mobile": cust.get("mobile") or cust.get("phone") or row.get("phone") or "",
            "months_lapsed": months_lapsed,
            "last_touch_date": rv.get("last_touch_date"),
            "lifetime_value": _ltv_paisa(cust),
            "is_vip": is_vip,
            "headline": _headline(months_lapsed),
            "tags": list(cust.get("tags") or []),
            "follow_up_id": None,
            "dismissed": False,
            "_sort": (0 if is_vip else 1, -sort_months),
        })

    entries.sort(key=lambda e: e["_sort"])
    entries = entries[:cohort_size]
    out: List[Dict[str, Any]] = []
    for i, e in enumerate(entries, start=1):
        e.pop("_sort", None)
        e["rank"] = i
        out.append(e)
    return out


def build_cohort_doc(
    store_id: str, entries: List[Dict[str, Any]], *, date_str: Optional[str] = None,
    lapse_months: Optional[int] = None,
) -> Dict[str, Any]:
    """Assemble the persisted reactivation_cohorts document (natural-key, TTL)."""
    date_str = date_str or _today_ist()
    return {
        "cohort_id": f"REACT-{date_str.replace('-', '')}-{store_id}",
        "store_id": store_id,
        "date": date_str,
        "lapse_months": lapse_months,
        "generated_at": datetime.utcnow().isoformat(),
        "entry_count": len(entries),
        "entries": entries,
        "ttl_expires_at": _ist_midnight_utc(date_str),
    }


def public_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip internal sort keys and exclude dismissed entries (the staff view)."""
    out: List[Dict[str, Any]] = []
    for e in entries or []:
        if e.get("dismissed"):
            continue
        e2 = {k: v for k, v in e.items() if not k.startswith("_")}
        out.append(e2)
    return out
