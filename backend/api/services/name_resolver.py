"""
IMS 2.0 - Shared human-name resolver
====================================
Single, fail-soft place to turn an internal id (store_id / user_id /
employee_id / vendor_id) into the human-readable NAME a person should see.

Owner backlog #4: raw UUIDs / store codes were leaking onto the UI (e.g. an
abuse alert reading "Optometrist: 97d2a24c-..."). Routers should ADD a
``*_name`` field beside any ``*_id`` they return so the frontend can show the
name. To avoid every router re-implementing the same batched Mongo lookup
(finance.py already had private ``_store_name_map`` / ``_user_name_map``), this
module owns the lookups once.

Contract for EVERY function here:
  * BATCHED -- one Mongo query per id-set, never N+1 per row.
  * FAIL-SOFT -- a missing db / collection / id never raises; the map simply
    omits that id (callers fall back to a short id or "-"). NEVER crash a
    response just because a name can't be resolved.
  * ASCII only (Windows cp1252).

Identity model (verified in code):
  * stores collection:   keyed by ``store_id``; display = ``store_name`` (else
                         ``store_code`` / id).
  * users collection:    keyed by ``user_id`` (also ``id``); display =
                         ``full_name`` -> ``name`` -> ``username`` -> id.
                         Employees ARE users (payroll reads full_name from
                         the users collection), so user + employee resolve here.
  * vendors collection:  keyed by ``vendor_id``; display = ``trade_name`` ->
                         ``legal_name`` -> id.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional


def _clean_ids(ids: Iterable) -> list:
    """De-dupe + drop falsy ids, coercing to str."""
    seen = set()
    out = []
    for i in ids:
        if not i:
            continue
        s = str(i)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def store_name_map(db, store_ids: Optional[Iterable] = None) -> Dict[str, str]:
    """store_id -> store_name. When ``store_ids`` is None, maps every store.

    Falls back to store_code then the id when a store has no name. Fail-soft {}.
    """
    out: Dict[str, str] = {}
    if db is None:
        return out
    try:
        coll = db.get_collection("stores")
        if coll is None:
            return out
        proj = {"_id": 0, "store_id": 1, "store_name": 1, "store_code": 1}
        if store_ids is None:
            cursor = coll.find({}, proj)
        else:
            ids = _clean_ids(store_ids)
            if not ids:
                return out
            cursor = coll.find({"store_id": {"$in": ids}}, proj)
        for s in cursor:
            sid = s.get("store_id")
            if sid:
                out[str(sid)] = s.get("store_name") or s.get("store_code") or str(sid)
    except Exception:  # noqa: BLE001 - resolver must never raise
        return out
    return out


def user_name_map(db, user_ids: Iterable) -> Dict[str, str]:
    """user_id -> display name (full_name -> name -> username -> id).

    Matches on either ``user_id`` or ``id`` (some docs use ``id``). Employees
    live in the same users collection, so this also resolves employee ids.
    Fail-soft {}.
    """
    out: Dict[str, str] = {}
    if db is None:
        return out
    ids = _clean_ids(user_ids)
    if not ids:
        return out
    try:
        coll = db.get_collection("users")
        if coll is None:
            return out
        for u in coll.find(
            {"$or": [{"user_id": {"$in": ids}}, {"id": {"$in": ids}}]},
            {"_id": 0, "user_id": 1, "id": 1, "full_name": 1, "name": 1, "username": 1},
        ):
            name = u.get("full_name") or u.get("name") or u.get("username")
            if not name:
                continue
            uid = u.get("user_id")
            if uid:
                out[str(uid)] = name
            alt = u.get("id")
            if alt and str(alt) not in out:
                out[str(alt)] = name
    except Exception:  # noqa: BLE001
        return out
    return out


# Employees are users in this app -- keep an explicit alias so callers reading
# "employee_id" code stay readable.
employee_name_map = user_name_map


def vendor_name_map(db, vendor_ids: Iterable) -> Dict[str, str]:
    """vendor_id -> display name (trade_name -> legal_name -> id). Fail-soft {}."""
    out: Dict[str, str] = {}
    if db is None:
        return out
    ids = _clean_ids(vendor_ids)
    if not ids:
        return out
    try:
        coll = db.get_collection("vendors")
        if coll is None:
            return out
        for v in coll.find(
            {"vendor_id": {"$in": ids}},
            {"_id": 0, "vendor_id": 1, "trade_name": 1, "legal_name": 1},
        ):
            vid = v.get("vendor_id")
            if vid:
                out[str(vid)] = v.get("trade_name") or v.get("legal_name") or str(vid)
    except Exception:  # noqa: BLE001
        return out
    return out


def stamp_user_names(db, docs: Iterable, fields: Iterable[str]) -> None:
    """Add a ``<field>_name`` sibling beside each raw user id, in place.

    The house contract (see the module docstring) is that a router ADDS the
    human name beside the id it already returns, instead of leaving screens to
    print "user-superadmin" at the reader. This is the batched, generic form of
    that: pass the sub-documents that hold the ids and the id field names.

    ONE users read for the whole batch (never N+1). Fail-soft in both
    directions: a None/absent doc is skipped, and an id that no longer resolves
    simply gets NO ``_name`` sibling -- the caller then falls back to printing
    the id, which stays traceable and is never an invented name.
    """
    rows = [d for d in docs if isinstance(d, dict)]
    flds = [f for f in fields]
    if not rows or not flds:
        return
    names = user_name_map(db, [d.get(f) for d in rows for f in flds])
    if not names:
        return
    for d in rows:
        for f in flds:
            raw = d.get(f)
            if raw and not d.get(f + "_name"):
                resolved = names.get(str(raw))
                if resolved:
                    d[f + "_name"] = resolved


def order_actor_id(order) -> str:
    """Which user an ORDER's sale is CREDITED to (the id; name map is below).

    orders.py stamps the POS salesperson picker onto ``salesperson_id``, with
    ``salesperson_name`` denormalised beside it. ``sales_person_id`` is the
    WALKOUTS spelling and an order has never carried it -- kept here only as a
    harmless legacy fallback, mirroring walkouts.py's own reader, so the credit
    rule answers the same wherever it is asked.

    ``created_by`` is the person who BILLED the sale and is deliberately LAST:
    reading it any earlier hands every staff report to the cashier. It stays as
    the fallback so historical orders written before the picker existed keep
    attributing to a real person instead of "Unknown".
    """
    if not isinstance(order, dict):
        return "Unknown"
    return str(
        order.get("salesperson_id")
        or order.get("sales_person_id")
        or order.get("created_by")
        or "Unknown"
    )


def order_actor_name_map(db, orders: Iterable) -> Dict[str, str]:
    """Credited-actor id -> display name for a BATCH of order docs.

    ONE users read for the whole batch (never N+1). The live users row wins;
    the name denormalised on the order is the fallback, so a since-deleted
    account still prints the person who sold. An id that resolves to neither is
    simply absent -- the caller then prints the id, which stays traceable and is
    never an invented name (same contract as ``stamp_user_names``).
    """
    rows = [o for o in orders if isinstance(o, dict)]
    out = user_name_map(db, [order_actor_id(o) for o in rows])
    for o in rows:
        aid = order_actor_id(o)
        if aid in out:
            continue
        stored = o.get("salesperson_name") or o.get("sales_person_name")
        if stored:
            out[aid] = str(stored)
    return out
