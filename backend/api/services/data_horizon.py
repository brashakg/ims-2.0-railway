# ============================================================================
# The 30-day browse horizon (owner ruling 2026-09-01)
# ============================================================================
# Every role EXCEPT ADMIN and SUPERADMIN sees only the last 30 days when
# BROWSING - lists, history screens, queues, reports. When a staff member looks
# up ONE customer by name or phone, they get that customer's / family's /
# patient's ENTIRE history, with no date limit.
#
# The distinction is BROWSE vs NAMED LOOKUP, not read vs write. Staff need
# everything about the person in front of them and nothing about the business:
# a 30-day browse window still supports the job - today's bills, this month's
# follow-ups, recent returns - while an unbounded list view hands a departing
# employee the customer book, the sales history and the turnover. This is a
# data-exfiltration control, not a permissions tidy-up.
#
# ONE implementation, imported by every door. Nine surfaces each rolling their
# own window is precisely how the salesperson-attribution bug happened.

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

BROWSE_HORIZON_DAYS = 30

# Only these two see the whole book. Deliberately a frozenset of exact role
# names - not a "level >= 2" test - so widening it is a visible edit here and
# cannot happen by accident when a new role is added elsewhere.
UNRESTRICTED_ROLES = frozenset({"SUPERADMIN", "ADMIN"})


def is_unrestricted(current_user: Optional[Dict[str, Any]]) -> bool:
    """True when this user may browse the full history."""
    if not isinstance(current_user, dict):
        return False
    roles = current_user.get("roles") or []
    if isinstance(roles, str):
        roles = [roles]
    try:
        return bool(UNRESTRICTED_ROLES & {str(r) for r in roles})
    except TypeError:
        return False


def horizon_start(
    current_user: Optional[Dict[str, Any]],
    *,
    customer_scoped: bool = False,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """Earliest ``created_at`` this request may return, or None for no limit.

    ``customer_scoped`` means the request already names ONE customer / patient /
    family. That is the owner's exemption: the whole history of the person being
    served is exactly what staff need.

    A request that merely SEARCHES is still browsing. Pass customer_scoped=True
    only when the query resolves to a specific customer - never for a fuzzy
    name match that could return many, or a one-character search becomes the
    bypass that empties the book.
    """
    if customer_scoped or is_unrestricted(current_user):
        return None
    return (now or datetime.utcnow()) - timedelta(days=BROWSE_HORIZON_DAYS)


def apply_horizon(
    query: Dict[str, Any],
    current_user: Optional[Dict[str, Any]],
    *,
    customer_scoped: bool = False,
    field: str = "created_at",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Clamp a Mongo query to the browse horizon. Mutates and returns ``query``.

    The clamp WINS over a caller-supplied lower bound. Taking the later of the
    two is the whole control: without it, ``?from_date=2020-01-01`` walks
    straight past the window and the rule is decorative. A caller asking for a
    NARROWER range than the horizon keeps its own bound - restricting yourself
    further is always allowed.
    """
    start = horizon_start(current_user, customer_scoped=customer_scoped, now=now)
    if start is None:
        return query

    existing = query.get(field)
    if isinstance(existing, dict):
        current = existing.get("$gte")
        # Later bound wins. A caller may narrow, never widen.
        if isinstance(current, datetime) and current > start:
            existing["$gte"] = current
        else:
            existing["$gte"] = start
    else:
        query[field] = {"$gte": start}
    return query


def horizon_start_iso_date(
    current_user: Optional[Dict[str, Any]],
    *,
    customer_scoped: bool = False,
) -> Optional[str]:
    """The horizon as an ISO ``YYYY-MM-DD`` string, or None for no limit.

    For the doors whose stored date is an ISO STRING (eye tests' ``test_date``,
    prescriptions' ``prescription_date``) rather than a real datetime. ISO-8601
    is lexicographically ordered, so a string compare is a correct date compare
    for this format - but a datetime bound against a string field matches
    NOTHING in Mongo, which is why this exists as its own function instead of
    letting a caller stringify ``horizon_start``.

    The IST business day, not the box's calendar day (BUG-104): Railway runs
    UTC, so for the first five and a half hours of every Indian working day a
    UTC-derived window is off by one.
    """
    if customer_scoped or is_unrestricted(current_user):
        return None
    from api.utils.ist import ist_today

    return (ist_today() - timedelta(days=BROWSE_HORIZON_DAYS)).isoformat()


def later_iso_bound(existing: Optional[str], horizon: Optional[str]) -> Optional[str]:
    """The stricter of a caller's ISO ``from`` bound and the horizon.

    Same "a caller may narrow, never widen" rule as ``apply_horizon``: an
    absent caller bound means ALL of history, so the horizon replaces it.
    """
    if horizon is None:
        return existing
    if existing is None:
        return horizon
    return max(existing, horizon)


def drop_rows_before_horizon(
    rows: Any,
    current_user: Optional[Dict[str, Any]],
    *,
    customer_scoped: bool = False,
    field: str = "created_at",
    now: Optional[datetime] = None,
) -> Any:
    """Filter already-fetched rows to the horizon.

    For doors whose repository method takes no date bound (a text search, say).
    Filtering after the fetch is weaker than a query bound - the rows were read
    -- but it is what the caller can enforce without rewriting the query, and
    the client still never receives them. Rows with a MISSING or non-datetime
    date are KEPT: dropping them would silently hide live records over a data
    shape we did not verify, and this control exists to narrow browsing, not to
    lose orders.
    """
    start = horizon_start(current_user, customer_scoped=customer_scoped, now=now)
    if start is None:
        return rows
    out = []
    for r in rows or []:
        v = r.get(field) if isinstance(r, dict) else None
        if isinstance(v, datetime) and v < start:
            continue
        out.append(r)
    return out


def query_names_one_customer(
    q: str, store_id: Optional[str], *, repo: Any = None
) -> bool:
    """True when this search string identifies ONE customer -- the owner's
    named-lookup exemption to the 30-day browse horizon.

    Two conditions, both required:
      1. the customer search resolves to exactly ONE record, and
      2. the query really is that customer's name or number.

    (2) is not redundant. "Resolved to one" is also true of a store with one
    customer in it, or a matcher looser than we assume -- and then ANY string,
    "ORD" included, is a named lookup and the window is gone. Verifying the
    match against the returned record makes the exemption depend on the query
    naming a person rather than on the size of the customer book.

    Fail-CLOSED: any error means "not a named lookup". An exemption that opens
    on an exception is not an exemption.

    ONE implementation, imported by every door that searches (orders search,
    the customer list's search branch). It lives here rather than in a router
    because two copies of this predicate drifting is how the exemption silently
    becomes a bypass on one screen and not the other.

    ``repo`` lets a door hand in the CustomerRepository it already holds -
    both callers have one, and passing it keeps the lookup on the caller's
    own repository handle. Omitted, it is resolved from api.dependencies.
    """
    try:
        needle = (q or "").strip().lower()
        if len(needle) < 2:
            return False
        if repo is None:
            from api.dependencies import get_customer_repository

            repo = get_customer_repository()
        if repo is None:
            return False
        hits = repo.search_customers(q, store_id) or []
        if len(hits) != 1:
            return False
        c = hits[0]
        digits = "".join(ch for ch in needle if ch.isdigit())
        for key in ("name", "mobile", "phone", "email"):
            v = str(c.get(key) or "").lower()
            if not v:
                continue
            if needle in v:
                return True
            if digits and len(digits) >= 6 and digits in "".join(
                ch for ch in v if ch.isdigit()
            ):
                return True
        # A family member (patients[].name / .mobile) names the account too.
        for p in c.get("patients") or []:
            if not isinstance(p, dict):
                continue
            for key in ("name", "mobile"):
                v = str(p.get(key) or "").lower()
                if v and needle in v:
                    return True
        return False
    except Exception:  # noqa: BLE001 -- never let the exemption fail OPEN
        return False
