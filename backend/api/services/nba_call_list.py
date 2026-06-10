"""
IMS 2.0 - F39 NBA (next-best-action) daily call list
=====================================================
A ranked daily list of customers a STORE associate should MANUALLY PHONE today.

This is a CALL LIST -- a prioritisation tool. It is NOT an outbound message
channel: nothing here sends WhatsApp/SMS. Logging an outcome records an in-app
follow-up doc, never a provider send. (WhatsApp is disabled -- STATUS COMMS
DIRECTIVE 2026-06-07; #39 is the in-app call list explicitly NOT affected.)

Design contract
---------------
- Pure-ish + fail-soft: the scorer takes the live `db` handle (the connection
  WRAPPER exposing `.get_collection`) and returns plain card dicts. A missing DB
  or any query error yields an EMPTY list, never an exception.
- ZERO new signal logic. Every cohort signal is RESOLVED by the already-merged
  campaign_segments resolvers (E6 reminder rail + N3/F45):
    * Rx-expiry      -> campaign_segments._resolve_rx_expiry
    * follow-ups-due -> campaign_segments._resolve_fu_due_today
    * VIP-churn-risk -> the persisted `customers.vip_churn_risk` subdoc written
                        by ORACLE's EOD scan (api.services.vip_churn). We READ it;
                        we do NOT recompute or fork the vip_churn model.
  Plus birthday + CL-reorder (also reused resolvers) for richer ranking. Nothing
  is re-implemented.
- 15 cards/day with exactly 2 reserved VIP slots -- the caps come from E2
  policy_engine.get_policy("nba.cards_per_day") / ("nba.vip_reserved_slots"),
  not hard-coded constants (owner-tunable via Settings).
- `score` is internal-only: the API layer strips it. Associates see `rank`.
- IST clock: the "today" date is the IST calendar date (api.utils.ist), never
  a UTC date -- so a 00:30-IST run lands on the right calendar day, and the
  TTL anchor is the naive-UTC instant of the next IST midnight.

No emoji (Windows cp1252). No POS touch. No money mutation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# E2 policy keys (registered in policy_registry.py). Defaults mirror DECISIONS s3
# (15 cards/day, 2 VIP slots) so a fresh DB still behaves correctly.
POLICY_CARDS_PER_DAY = "nba.cards_per_day"
POLICY_VIP_SLOTS = "nba.vip_reserved_slots"
DEFAULT_CARDS_PER_DAY = 15
DEFAULT_VIP_SLOTS = 2

# A single-order LTV at/above this marks a high-value customer for the VIP slot
# (DECISIONS s3 "LTV high-value Rs15k" -- a per-order threshold, distinct from
# the 1,00,000 total-LTV vip_churn gate which we ALSO honour below).
VIP_SINGLE_ORDER_PAISA = 1_500_000  # Rs 15,000 expressed in paisa
VIP_SINGLE_ORDER_RUPEES = 15_000

# Signal -> weight. Constants (not owner-configurable); only the card/slot caps
# are policy-driven. Higher weight = higher call priority.
SIGNAL_WEIGHTS = {
    "vip_churn_high": 40,     # persisted vip_churn_risk.risk_label == HIGH
    "rx_expiry_7d": 30,       # prescription expires within 7 days
    "fu_due_today": 28,       # a pending follow-up scheduled for today/earlier
    "vip_churn_watch": 22,    # persisted vip_churn_risk.risk_label == WATCH
    "cl_refill_due": 25,      # contact-lens reorder is due
    "birthday_today": 20,     # birthday within the next day
    "rx_expiry_30d": 10,      # prescription expires within 8-30 days
}

# suggested_action enum (single, deterministic action per card top signal).
ACTION_BOOK_EYE_TEST = "BOOK_EYE_TEST"
ACTION_CL_REORDER = "CL_REORDER"
ACTION_VIP_CALL = "VIP_RETENTION_CALL"
ACTION_BIRTHDAY = "BIRTHDAY_CALL"
ACTION_FOLLOWUP = "GENERAL_FOLLOWUP"

DISMISS_REASONS = {"not_interested", "already_called", "no_answer", "wrong_number"}


def _get_cap(policy_key: str, default: int) -> int:
    """Resolve a cap from E2 policy_engine; fail-soft to the default. The scope is
    global (the NBA caps are global-scoped in the registry)."""
    try:
        from .policy_engine import get_policy

        val = get_policy(policy_key, default=default)
        n = int(val)
        return n if n >= 0 else default
    except Exception as exc:  # noqa: BLE001
        logger.warning("[NBA] policy %s lookup failed: %s", policy_key, exc)
        return default


def _today_ist(now: Optional[datetime] = None) -> str:
    """The IST calendar date as YYYY-MM-DD (the one clock, per E6 / BUG-104).

    Rides api.utils.ist (zoneinfo with an exact +05:30 fallback), so it NEVER
    degrades to a UTC date -- the old fallback returned utcnow().date(), which
    between 00:00-05:30 IST keyed the list on the PREVIOUS day. A tz-aware
    `now` is injectable for boundary tests (e.g. 01:00 IST == 19:30 UTC of the
    prior day must key on the IST day)."""
    from ..utils.ist import IST, now_ist

    moment = now.astimezone(IST) if now is not None and now.tzinfo is not None else now_ist()
    return moment.date().isoformat()


def _ist_midnight_utc(date_str: str) -> datetime:
    """The NAIVE-UTC instant of the NEXT IST midnight after `date_str` -- the
    TTL expiry so the day's list drops at end-of-IST-day.

    Naive-UTC because Mongo treats naive BSON dates as UTC (BUG-104). The old
    code used bare `.astimezone()` (the SERVER-LOCAL zone, not UTC): correct on
    a UTC host, but on any non-UTC host it stamped a local wall-clock that
    Mongo then read as UTC, sliding the TTL (5h30m late on an IST box)."""
    try:
        from ..utils.ist import IST, to_utc_naive

        d = datetime.fromisoformat(date_str)
        # next IST midnight = start of the following IST day
        next_ist_midnight = datetime(d.year, d.month, d.day, tzinfo=IST) + timedelta(days=1)
        return to_utc_naive(next_ist_midnight)
    except Exception:  # noqa: BLE001
        return datetime.utcnow() + timedelta(days=1)


def _ltv_paisa(customer: Dict[str, Any]) -> int:
    """Best-effort lifetime value in PAISA (integer). Customer docs store rupee
    floats under total_lifetime_value / ltv; convert to paisa for an integer key."""
    raw = customer.get("total_lifetime_value", customer.get("ltv", 0)) or 0
    try:
        return int(round(float(raw) * 100))
    except Exception:  # noqa: BLE001
        return 0


def _is_vip(customer: Dict[str, Any]) -> bool:
    """A customer qualifies for a reserved VIP slot when ANY of:
      * a tag (case-insensitive) equals "VIP"
      * their persisted vip_churn_risk gate fired (they are a tracked VIP)
      * their single-order high-value LTV is >= Rs 15,000 (DECISIONS s3).
    We reuse the persisted vip_churn_risk subdoc rather than recompute it."""
    tags = [str(t).strip().upper() for t in (customer.get("tags") or [])]
    if "VIP" in tags:
        return True
    vr = customer.get("vip_churn_risk") or {}
    if vr.get("risk_label") in ("WATCH", "HIGH"):
        return True
    return _ltv_paisa(customer) >= VIP_SINGLE_ORDER_PAISA


def _loyalty_tier(db, customer_id: str) -> Optional[str]:
    """Read the customer's loyalty tier (BRONZE/SILVER/GOLD/PLATINUM) from
    loyalty_accounts. Fail-soft -> None."""
    try:
        acct = db.get_collection("loyalty_accounts").find_one(
            {"customer_id": customer_id}, {"_id": 0, "tier": 1}
        )
        return (acct or {}).get("tier")
    except Exception:  # noqa: BLE001
        return None


def _action_for(top_signal: str) -> str:
    if top_signal in ("rx_expiry_7d", "rx_expiry_30d"):
        return ACTION_BOOK_EYE_TEST
    if top_signal == "cl_refill_due":
        return ACTION_CL_REORDER
    if top_signal in ("vip_churn_high", "vip_churn_watch"):
        return ACTION_VIP_CALL
    if top_signal == "birthday_today":
        return ACTION_BIRTHDAY
    if top_signal == "vip_no_signal":
        return ACTION_VIP_CALL
    return ACTION_FOLLOWUP


def _headline_for(top_signal: str, extra: Dict[str, Any]) -> str:
    """Single sentence, sentence case, no emoji."""
    if top_signal == "rx_expiry_7d":
        exp = extra.get("rx_expiry_date")
        return f"Prescription expires soon ({exp}) -- book an eye test." if exp else \
            "Prescription expires within a week -- book an eye test."
    if top_signal == "rx_expiry_30d":
        exp = extra.get("rx_expiry_date")
        return f"Prescription expiring ({exp}) -- offer a renewal." if exp else \
            "Prescription expiring this month -- offer a renewal."
    if top_signal == "cl_refill_due":
        d = extra.get("days_since_cl")
        return f"Contact lenses likely running low ({d} days since last order)." if d else \
            "Contact-lens reorder is due."
    if top_signal == "vip_churn_high":
        od = extra.get("overdue_by_days")
        return f"VIP overdue by {od} days -- a personal retention call." if od else \
            "VIP at high churn risk -- a personal retention call."
    if top_signal == "vip_churn_watch":
        return "VIP slowing down -- check in before they lapse."
    if top_signal == "birthday_today":
        return "Birthday this week -- a warm wish and a reason to visit."
    if top_signal == "fu_due_today":
        return "A scheduled follow-up is due today."
    if top_signal == "vip_no_signal":
        return "A valued customer worth a courtesy call."
    return "Worth a call today."


def _resolve_signals(db, store_id: Optional[str], now: datetime) -> Dict[str, Dict[str, Any]]:
    """Run the REUSED campaign_segments resolvers once each and collapse them
    into a per-customer signal map. Returns {customer_id: {signal: extra}}.

    No signal logic is implemented here -- every resolver is the merged one.
    """
    from . import campaign_segments as seg

    signals: Dict[str, Dict[str, Any]] = {}

    def _add(cid: str, signal: str, extra: Optional[Dict[str, Any]] = None):
        if not cid:
            return
        signals.setdefault(cid, {})[signal] = extra or {}

    # 1. Rx expiry within 7 days (reused resolver, window=7).
    try:
        for row in seg._resolve_rx_expiry(db, store_id, window_days=7, now=now):
            _add(row.get("customer_id"), "rx_expiry_7d",
                 {"rx_expiry_date": (row.get("variables") or {}).get("expiry_date")})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[NBA] rx_expiry_7d resolve failed: %s", exc)

    # 2. Rx expiry 8-30 days (reused resolver, window=30; skip those already 7d).
    try:
        for row in seg._resolve_rx_expiry(db, store_id, window_days=30, now=now):
            cid = row.get("customer_id")
            if cid and "rx_expiry_7d" not in signals.get(cid, {}):
                _add(cid, "rx_expiry_30d",
                     {"rx_expiry_date": (row.get("variables") or {}).get("expiry_date")})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[NBA] rx_expiry_30d resolve failed: %s", exc)

    # 3. CL reorder due (reused resolver).
    try:
        for row in seg._resolve_cl_reorder(db, store_id, now=now):
            _add(row.get("customer_id"), "cl_refill_due",
                 {"days_since_cl": (row.get("variables") or {}).get("days_since_cl")})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[NBA] cl_refill resolve failed: %s", exc)

    # 4. Birthday this week (reused resolver, window=1 for "today/tomorrow").
    try:
        for row in seg._resolve_birthday(db, store_id, window_days=1, now=now):
            _add(row.get("customer_id"), "birthday_today", {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[NBA] birthday resolve failed: %s", exc)

    # 5. Follow-ups due today (reused resolver -- reads follow_ups + walkout subdocs).
    try:
        for row in seg._resolve_fu_due_today(db, store_id, now=now):
            _add(row.get("customer_id"), "fu_due_today",
                 {"follow_up_id": (row.get("variables") or {}).get("follow_up_id")})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[NBA] fu_due_today resolve failed: %s", exc)

    return signals


def _customers_in_store(db, store_id: Optional[str], max_customers: int) -> List[Dict[str, Any]]:
    """Customers attached to the store, capped. Matches the OR-of-store-fields the
    CRM adapter uses so TechCherry/native/legacy docs are all covered."""
    q: Dict[str, Any] = {}
    if store_id:
        q["$or"] = [
            {"preferred_store_id": store_id},
            {"home_store_id": store_id},
            {"primary_store_id": store_id},
            {"store_id": store_id},
        ]
    try:
        return list(db.get_collection("customers").find(q).limit(max(1, max_customers)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[NBA] customer scan failed: %s", exc)
        return []


def score_nba(
    db,
    store_id: Optional[str],
    *,
    now: Optional[datetime] = None,
    max_customers: int = 500,
) -> List[Dict[str, Any]]:
    """Score the store's customers and return up to `cards_per_day` ranked card
    dicts. Exactly `vip_reserved_slots` of the highest ranks are reserved for VIP
    customers (top of the list) regardless of raw score; the remaining slots are
    pure score-rank. Each card carries `score` (internal) and `rank`.

    Fail-soft: no DB / no signals -> []. Pure: `now` injected for determinism.
    """
    if db is None:
        return []
    now = now or datetime.now()
    cards_per_day = _get_cap(POLICY_CARDS_PER_DAY, DEFAULT_CARDS_PER_DAY)
    vip_slots = _get_cap(POLICY_VIP_SLOTS, DEFAULT_VIP_SLOTS)
    vip_slots = min(vip_slots, cards_per_day)

    signals = _resolve_signals(db, store_id, now)

    cust_coll = db.get_collection("customers")
    candidate_ids: List[str] = list(signals.keys())[:max_customers]
    candidate_set = set(candidate_ids)

    # VIP customers are ALWAYS candidates -- their reserved slots are filled
    # regardless of whether they carry any of the day's signals. Pull every
    # store customer that is VIP-tagged OR carries a persisted vip_churn_risk
    # gate, and add any not already surfaced by a signal.
    try:
        for cust in _customers_in_store(db, store_id, max_customers):
            cid = cust.get("customer_id")
            if not cid or cid in candidate_set:
                continue
            if _is_vip(cust):
                candidate_ids.append(cid)
                candidate_set.add(cid)
    except Exception:  # noqa: BLE001
        pass

    if not candidate_ids:
        return []

    candidates: List[Dict[str, Any]] = []
    for cid in candidate_ids:
        try:
            cust = cust_coll.find_one({"customer_id": cid})
        except Exception:  # noqa: BLE001
            cust = None
        if not cust:
            continue
        candidates.append(cust)

    scored: List[Dict[str, Any]] = []
    for cust in candidates:
        cid = cust.get("customer_id", "")
        cust_signals = dict(signals.get(cid, {}))

        # Fold the persisted vip_churn_risk (READ, never recomputed) into signals.
        vr = cust.get("vip_churn_risk") or {}
        label = vr.get("risk_label")
        if label == "HIGH":
            cust_signals["vip_churn_high"] = {"overdue_by_days": vr.get("overdue_by_days")}
        elif label == "WATCH":
            cust_signals["vip_churn_watch"] = {"overdue_by_days": vr.get("overdue_by_days")}

        is_vip = _is_vip(cust)
        # A non-VIP with no signal is not worth a call -- drop it. A VIP with no
        # signal still gets a (score-0) card so it can fill a reserved slot.
        if not cust_signals and not is_vip:
            continue

        total = sum(SIGNAL_WEIGHTS.get(s, 0) for s in cust_signals)
        # Top signal = highest-weight signal present (drives action + headline).
        top_signal = (
            max(cust_signals, key=lambda s: SIGNAL_WEIGHTS.get(s, 0))
            if cust_signals else "vip_no_signal"
        )
        merged_extra: Dict[str, Any] = {}
        for ex in cust_signals.values():
            merged_extra.update(ex or {})

        signal_keys = sorted(cust_signals.keys(), key=lambda s: -SIGNAL_WEIGHTS.get(s, 0))
        sub_headlines: List[str] = []
        last_purchase = cust.get("last_purchase_date") or cust.get("last_order_date")
        if last_purchase:
            sub_headlines.append(f"Last purchase {str(last_purchase)[:10]}")
        tier = _loyalty_tier(db, cid)
        if tier:
            sub_headlines.append(f"{str(tier).title()} tier")

        scored.append({
            "customer_id": cid,
            "customer_name": cust.get("name") or cust.get("full_name") or "Customer",
            "customer_mobile": cust.get("mobile") or cust.get("phone") or "",
            "score": total,
            "signals": signal_keys,
            "headline": _headline_for(top_signal, merged_extra),
            "sub_headlines": sub_headlines[:2],
            "suggested_action": _action_for(top_signal),
            "loyalty_tier": tier,
            "lifetime_value": _ltv_paisa(cust),
            "last_purchase_date": str(last_purchase)[:10] if last_purchase else None,
            "tags": list(cust.get("tags") or []),
            "is_vip": is_vip,
            "follow_up_id": None,
            "dismissed": False,
        })

    if not scored:
        return []

    # Rank: reserved VIP slots first (highest-scoring VIPs), then score-rank the
    # rest. A VIP that doesn't make a reserved slot still competes on score.
    scored.sort(key=lambda c: -c["score"])
    vips = [c for c in scored if c["is_vip"]]
    vips.sort(key=lambda c: -c["score"])
    reserved = vips[:vip_slots]
    reserved_ids = {c["customer_id"] for c in reserved}
    rest = [c for c in scored if c["customer_id"] not in reserved_ids]
    rest.sort(key=lambda c: -c["score"])

    ranked = reserved + rest
    ranked = ranked[:cards_per_day]
    out: List[Dict[str, Any]] = []
    for i, card in enumerate(ranked, start=1):
        card["rank"] = i
        card["is_vip_slot"] = i <= len(reserved)
        out.append(card)
    return out


def build_nba_doc(store_id: str, cards: List[Dict[str, Any]], *, date_str: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the persisted nba_scores document (natural-key, TTL-bearing)."""
    date_str = date_str or _today_ist()
    return {
        "nba_id": f"NBA-{date_str.replace('-', '')}-{store_id}",
        "store_id": store_id,
        "date": date_str,
        "generated_at": datetime.utcnow().isoformat(),
        "scored_count": len(cards),
        "cards": cards,
        "ttl_expires_at": _ist_midnight_utc(date_str),
    }


def public_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip the internal `score` (gaming-prevention) and excludes dismissed cards.
    What associates see: rank, signals, headline, action, tier, tags -- never the
    numeric score."""
    out: List[Dict[str, Any]] = []
    for c in cards or []:
        if c.get("dismissed"):
            continue
        c2 = {k: v for k, v in c.items() if k != "score"}
        out.append(c2)
    return out
