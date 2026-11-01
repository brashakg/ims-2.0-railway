"""The Catalog page's list rules: sort, filters, filter options, and who added
or edited each product (owner 2026-10-12, mockup artboard Main).

ONE place for each rule, read by the list (GET /products) AND its filter
options (GET /products/facets), so a count beside an option is the number of
rows that option shows:

  * SORTS            -- the server sorts the WHOLE filtered result, then pages
                        it, with product_id as the tiebreak: page 2 never
                        repeats or skips a row of page 1.
  * same_value       -- an option matches its rows trimmed and
                        case-insensitive (' Black ' == 'black'); the user's
                        text is regex-escaped.
  * ist_day_bounds   -- From / To are INDIA days, both included: a product
                        added at 00:10 IST belongs to that IST day.
  * LAST_EDITED_AT   -- THE moment of a product's last catalogue edit, read
                        by the Recently edited sort, the Last edited range
                        and the Added column (as last_edited_at); written
                        only through edit_stamp, with who made it.
  * choices          -- every option filter is a tick-box multi-select
                        (owner 2026-11-01): 'a,b' ticks a OR b; one value
                        is an old single-value link. Capped (MAX_CHOICES).
  * list_query       -- every filter, AND-ed (the ticks inside one filter
                        OR-ed); archived (deleted) products only under the
                        admins' Flags: Archived option.
  * stamp_people     -- the full name of who added / last edited a product,
                        from users, falling back to the name stored on it.

Every parameter is OPTIONAL; a caller that sends none of them gets the list
exactly as before (the legacy paths in products.list_products).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..utils.ist import ist_day_start_utc
from .name_resolver import user_name_map

# THE last edit of a product. WHO is updated_by; WHEN is edited_at, which
# only edit_stamp writes, in the same write as updated_by. updated_at is NOT
# it: every repo.update moves it (archive, restore, a PO cost, a drawer price
# mirror) without saying who, so pairing it with updated_by named the last
# editor for a write they never made. A product edited before edited_at
# existed counts by its updated_at, which ProductRepository.update freezes
# into edited_at before any later write that is not an edit can move it; a
# product never edited counts by its created_at (owner ruling 2026-10-12:
# Recently edited is the default sort).
def last_edited_at(added: Any = "$created_at") -> Dict[str, Any]:
    """The rule as a Mongo expression; `added` is where the row keeps its add
    moment (an import with no spine keeps its import date, catalog.py)."""
    return {
        "$cond": [
            {"$gt": [{"$ifNull": ["$updated_by", ""]}, ""]},
            {"$ifNull": ["$edited_at", "$updated_at"]},
            added,
        ]
    }


LAST_EDITED_AT: Dict[str, Any] = last_edited_at()


def edit_stamp(user_id: Optional[str], name: Optional[str]) -> Dict[str, Any]:
    """The fields EVERY catalogue edit door writes with its change: who
    (id + display name) and when. Nothing else writes edited_at."""
    return {
        "updated_by": user_id,
        "updated_by_name": name or user_id,
        "edited_at": datetime.now(timezone.utc),
    }


# key -> (sort expression, direction).
_NAME = {
    "$toLower": {
        "$ifNull": ["$name", {"$ifNull": ["$title", {"$ifNull": ["$model", ""]}]}]
    }
}
SORTS: Dict[str, Tuple[Any, int]] = {
    "edited": (LAST_EDITED_AT, -1),
    "newest": ("$created_at", -1),
    "oldest": ("$created_at", 1),
    "name_asc": (_NAME, 1),
    "name_desc": (_NAME, -1),
    "brand_asc": ({"$toLower": {"$ifNull": ["$brand", ""]}}, 1),
    "mrp_desc": ("$mrp", -1),
    "mrp_asc": ("$mrp", 1),
}
SORT_PATTERN = "^(" + "|".join(SORTS) + ")$"

# Filter parameter -> the snake_case attribute it reads. 125 rows also carry
# camelCase copies, but no row is camelCase-only (prod census 2026-10-12).
ATTRIBUTE_FILTERS: Dict[str, str] = {
    "frame_color": "attributes.frame_color",
    "lens_colour": "attributes.lens_colour",
    "gender": "attributes.gender",
    "shape": "attributes.shape",
    "frame_type": "attributes.frame_type",
    "frame_material": "attributes.frame_material",
}

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
NEVER_EDITED = "none"
# Archived = deleted_at holds a time. One list, so the two clauses are exact
# opposites: a row is in exactly one ('' or missing counts as not archived,
# as switches_on_archived's truthiness reads it).
_NOT_DELETED = [None, ""]
ARCHIVED = {"deleted_at": {"$nin": _NOT_DELETED}}
NOT_ARCHIVED = {"deleted_at": {"$in": _NOT_DELETED}}

USE_RESTORE = "This product is archived - use Restore"


def switches_on_archived(doc: Optional[Dict[str, Any]], is_active: Any) -> bool:
    """THE rule for an archived product (deleted_at set): only Restore brings
    it back. True when a write would switch one on any other way -- every
    edit door refuses it (409, USE_RESTORE), so the till never sells a
    product the Catalog list hides."""
    return is_active is True and bool((doc or {}).get("deleted_at"))


def open_flag(type_: Optional[str] = None) -> Dict[str, Any]:
    """Products with an OPEN flag (of `type_` when given)."""
    cond: Dict[str, Any] = {"status": "open"}
    if type_:
        cond["type"] = type_
    return {"flags": {"$elemMatch": cond}}


# What a value is trimmed of, in the options' grouping AND the list's match
# (one set): every character Python's strip() or the browser's trim() calls
# a space. Spelled out, never \s -- MongoDB's \s is ASCII only (PCRE without
# UCP), so 'Black' + a non-breaking space would count under Black in the
# options yet not list under it.
_WS = "".join(chr(c) for c in range(0x3001) if chr(c).isspace()) + chr(0xFEFF)
_WS_RUN = "[" + _WS + "]*"

# The longest value a filter takes (GET /products Query max_length) and so
# the longest the options offer: an option the list then refuses would be a
# 422 where the count promised rows. The page keeps 120 characters of each
# address value too (catalogListState.readFilters).
VALUE_MAX = 120


# Ticks one filter takes. Far past any real choice, and no address builds a
# huge $in or regex.
MAX_CHOICES = 50
# The longest raw multi-value parameter (GET /products Query max_length).
LIST_MAX = MAX_CHOICES * (VALUE_MAX + 1)


def choices(raw: Any) -> List[str]:
    """A filter's ticked values from 'a,b,c' (one value = an old single-value
    link): trimmed, blanks and repeats dropped. Raises ValueError, in plain
    words, past MAX_CHOICES ticks or for a value past VALUE_MAX.
    ponytail: a comma splits, so a stored value holding a comma is ticked as
    its parts; none in the catalogue does -- escape commas if one ever must."""
    if not isinstance(raw, str):
        return []
    out = list(dict.fromkeys(p.strip() for p in raw.split(",") if p.strip()))
    if len(out) > MAX_CHOICES:
        raise ValueError(f"Tick at most {MAX_CHOICES} options in one filter.")
    if any(len(p) > VALUE_MAX for p in out):
        raise ValueError(f"An option is longer than {VALUE_MAX} characters.")
    return out


def same_value(texts: Any) -> Dict[str, Any]:
    """The stored value equals `texts` (one, or any of a list), trimmed (_WS)
    and case-insensitive."""
    many = [texts] if isinstance(texts, str) else list(texts)
    alts = "|".join(re.escape(t.strip(_WS)) for t in many)
    return {"$regex": "^" + _WS_RUN + "(?:" + alts + ")" + _WS_RUN + "$", "$options": "i"}


def _day(text: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        raise ValueError(f"{text} is not a real date.") from None


def _ist_start(day: date, plus_days: int = 0) -> Optional[Any]:
    """IST midnight of `day` (+ days) in the stored UTC frame. None past
    either end of the calendar (From 0001-01-01, To 9999-12-31): that bound
    is beyond every stored moment, so there is no bound."""
    try:
        return ist_day_start_utc(day + timedelta(days=plus_days))
    except OverflowError:
        return None


def ist_day_bounds(
    date_from: Optional[str], date_to: Optional[str]
) -> Tuple[Optional[Any], Optional[Any]]:
    """(>= bound, < bound) in the stored UTC frame for India days From..To,
    both days included. Raises ValueError, in plain words, for a date that
    does not exist or From after To."""
    d_from, d_to = _day(date_from), _day(date_to)
    if d_from and d_to and d_from > d_to:
        raise ValueError("The From date is after the To date.")
    return (
        _ist_start(d_from) if d_from else None,
        _ist_start(d_to, 1) if d_to else None,
    )


# Every stored moment is at or after this one (an aware UTC instant).
_ANY_DATE = datetime.min.replace(tzinfo=timezone.utc)


def _range(gte, lt) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if gte is not None:
        out["$gte"] = gte
    if lt is not None:
        out["$lt"] = lt
    return out


def _flag_clause(flag: str, me: Optional[str]) -> Dict[str, Any]:
    """One Flags tick: 'archived' (the deleted products -- the only place they
    show), 'any' (an open flag), 'mine' (an open flag on a product `me`
    added) or a flag type."""
    if flag == "archived":
        return ARCHIVED
    if flag == "any":
        return {"$and": [NOT_ARCHIVED, open_flag()]}
    if flag == "mine":
        return {"$and": [NOT_ARCHIVED, open_flag(), {"created_by": me or "-"}]}
    return {"$and": [NOT_ARCHIVED, open_flag(flag)]}


def list_query(
    *,
    category: Iterable[str] = (),
    brand: Iterable[str] = (),
    is_active: Optional[bool] = True,
    created_by: Iterable[str] = (),
    updated_by: Iterable[str] = (),
    attributes: Optional[Dict[str, Iterable[str]]] = None,
    date_on: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    flag: Iterable[str] = (),
    me: Optional[str] = None,
) -> Dict[str, Any]:
    """Every chosen filter, AND-ed; the ticks inside one filter (lists, from
    choices) OR-ed. `flag` ticks: see _flag_clause (the caller checks the
    role for 'archived'). Raises ValueError for a bad date range."""
    clauses: List[Dict[str, Any]] = []
    flags = list(flag or [])
    if flags:
        # A flag reaches its cataloguer whether the product is on sale or not
        # (a provisional PO buy is born inactive): the Flags filter lists
        # flagged products active or not, as the banner counts them.
        clauses.append({"$or": [_flag_clause(f, me) for f in flags]})
    else:
        clauses.append(NOT_ARCHIVED)
        if is_active is not None:
            clauses.append({"is_active": is_active})
    category, brand = list(category or []), list(brand or [])
    if category:
        clauses.append({"category": {"$in": category}})
    if brand:
        clauses.append({"brand": same_value(brand)})
    created_by = list(created_by or [])
    if created_by:
        clauses.append({"created_by": {"$in": created_by}})
    editors = list(updated_by or [])
    if editors:
        people = [u for u in editors if u != NEVER_EDITED]
        never = [None, ""] if NEVER_EDITED in editors else []
        clauses.append({"updated_by": {"$in": people + never}})
    for param, values in (attributes or {}).items():
        values = list(values or [])
        if values and param in ATTRIBUTE_FILTERS:
            clauses.append({ATTRIBUTE_FILTERS[param]: same_value(values)})
    if date_from or date_to:
        gte, lt = ist_day_bounds(date_from, date_to)
        if date_on == "edited":
            # THE last-edit moment, the Recently edited sort's own expression.
            # The >= bound is always a date (the first instant there is when
            # From is not chosen -- a type gate, not a day), so a row whose
            # moment is missing or not a date never matches -- exactly as a
            # stored-field range would treat it.
            cond = [{"$gte": [LAST_EDITED_AT, gte or _ANY_DATE]}]
            if lt is not None:
                cond.append({"$lt": [LAST_EDITED_AT, lt]})
            clauses.append({"$expr": {"$and": cond}})
        elif gte is not None or lt is not None:
            clauses.append({"created_at": _range(gte, lt)})
    return {"$and": clauses}


def page(collection, query: Dict[str, Any], sort: str, skip: int, limit: int) -> List[Dict]:
    """One page of `query` in `sort` order, sorted on the server across the
    whole result; product_id breaks ties so pages never overlap or skip.
    Each row carries last_edited_at (LAST_EDITED_AT) for the Added column."""
    expr, direction = SORTS[sort]
    pipeline: List[Dict[str, Any]] = [
        {"$match": query},
        {"$addFields": {"_sort_key": expr, "last_edited_at": LAST_EDITED_AT}},
        {"$sort": {"_sort_key": direction, "product_id": 1}},
    ]
    if skip:
        pipeline.append({"$skip": int(skip)})
    if limit:
        pipeline.append({"$limit": int(limit)})
    pipeline.append({"$project": {"_sort_key": 0}})
    out = []
    for doc in collection.aggregate(pipeline):
        if "_id" in doc and not isinstance(doc["_id"], str):
            doc["_id"] = str(doc["_id"])
        out.append(doc)
    return out


# ponytail: grouping reads the whole filtered result (the catalogue is ~600
# rows); a $group stage takes over if it ever nears this cap.
GROUP_SCAN_CAP = 5000


def group_page(
    collection, query: Dict[str, Any], sort: str, skip: int, limit: int, *, archived: bool = False
) -> Tuple[List[Dict], int]:
    """One page of MODELS (owner 2026-10-18, G1): every filter matches the
    variants, then the matching rows fold into one row per model -- the
    model's lead when it matched, else its best-placed variant -- in the
    order the sort put the model's first match. Each folded row carries
    `family` {lead_id, count, members[]} (the whole model -- its archived
    items only in the `archived` view, never in the catalogue: product_id,
    sku, name, colour, size, prices, on/off, is_lead) when the model has more
    than one item. Returns (rows, number of models)."""
    from .product_master import family_option_values, lead_order

    order: List[Any] = []
    heads: Dict[Any, Dict[str, Any]] = {}
    for doc in page(collection, query, sort, 0, GROUP_SCAN_CAP):
        lead_id = doc.get("variant_of") or doc.get("product_id")
        if lead_id not in heads:
            order.append(lead_id)
            heads[lead_id] = doc
        elif doc.get("product_id") == lead_id:
            heads[lead_id] = doc
    page_ids = order[skip : skip + limit] if limit else order[skip:]
    families: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    if page_ids:
        for m in page(
            collection,
            {"$or": [{"product_id": {"$in": page_ids}}, {"variant_of": {"$in": page_ids}}]},
            "oldest", 0, 0,
        ):
            if bool(m.get("deleted_at")) == archived:
                families[m.get("variant_of") or m.get("product_id")].append(m)
    rows = []
    for lead_id in page_ids:
        row = heads[lead_id]
        members = sorted(families.get(lead_id) or [], key=lead_order)
        if len(members) > 1:
            options, _ = family_option_values(members)
            row["family"] = {
                "lead_id": lead_id,
                "count": len(members),
                "members": [
                    {
                        "product_id": m.get("product_id"),
                        "sku": m.get("sku"),
                        "name": m.get("name"),
                        "color": options.get(m.get("sku"), {}).get("option_color", ""),
                        "size": options.get(m.get("sku"), {}).get("option_size", ""),
                        "mrp": m.get("mrp"),
                        "offer_price": m.get("offer_price"),
                        "is_active": m.get("is_active", True) is not False,
                        "is_lead": m.get("product_id") == lead_id,
                        **{field: m.get(field) for field in (
                            "created_by", "created_by_name", "created_at",
                            "updated_by", "updated_by_name", "last_edited_at",
                        )},
                    }
                    for m in members
                ],
            }
        rows.append(row)
    return rows, len(order)


def stamp_people(db, docs: Iterable[Dict[str, Any]]) -> None:
    """THE name of who added (created_by) and who last edited (updated_by)
    each product, in place on created_by_name / updated_by_name: the live
    users name first, the name stored on the product second. One users read
    for the whole batch."""
    rows = [d for d in docs if isinstance(d, dict)]
    rows += [m for d in rows for m in (d.get("family") or {}).get("members", [])]
    names = user_name_map(
        db, [d.get(f) for d in rows for f in ("created_by", "updated_by")]
    )
    for d in rows:
        for f in ("created_by", "updated_by"):
            uid = d.get(f)
            if uid:
                d[f + "_name"] = names.get(str(uid)) or d.get(f + "_name") or None


def _value_options(values: Iterable[Any]) -> List[Dict[str, Any]]:
    """Distinct values with counts, grouped the way same_value matches them;
    each option shows its most common spelling. A value longer than
    VALUE_MAX is no option (no filter could take it). ponytail: such a
    product still lists unfiltered; nothing in the catalogue comes close."""
    counts: Counter = Counter()
    spellings: Dict[str, Counter] = defaultdict(Counter)
    for v in values:
        v = v.strip(_WS) if isinstance(v, str) else ""
        if not v or len(v) > VALUE_MAX:
            continue
        key = v.lower()
        counts[key] += 1
        spellings[key][v] += 1
    opts = [
        {"value": spellings[k].most_common(1)[0][0], "count": n} for k, n in counts.items()
    ]
    return sorted(opts, key=lambda o: (-o["count"], o["value"].lower()))


def facets(db, collection, *, include_inactive: bool, me: str, is_admin: bool) -> Dict:
    """The filter options for the current catalogue: distinct values with
    counts. Counted over exactly the rows the list shows with no other filter
    (not archived; active only unless Include inactive), so picking 'Black
    (179)' alone lists 179 products. The open-flag counts (the red banner,
    the Flags options) cover every product that is not archived, active or
    not -- exactly what the Flags filter lists (list_query).

    ponytail: one projected read counted in Python -- fine to tens of
    thousands of rows; a $facet pipeline if the catalogue ever outgrows it."""
    proj = {
        "_id": 0,
        "brand": 1,
        "attributes": 1,
        "created_by": 1,
        "created_by_name": 1,
        "updated_by": 1,
        "updated_by_name": 1,
        "flags": 1,
        "is_active": 1,
    }
    every = list(collection.find(NOT_ARCHIVED, proj))
    rows = every if include_inactive else [r for r in every if r.get("is_active") is True]
    stamp_people(db, rows)

    out: Dict[str, Any] = {"total": len(rows), "brand": _value_options(r.get("brand") for r in rows)}
    for param, path in ATTRIBUTE_FILTERS.items():
        key = path.split(".", 1)[1]
        out[param] = _value_options((r.get("attributes") or {}).get(key) for r in rows)

    def _people(field: str) -> List[Dict[str, Any]]:
        counts: Counter = Counter()
        label: Dict[str, str] = {}
        for r in rows:
            uid = r.get(field)
            if uid:
                counts[uid] += 1
                label[uid] = r.get(field + "_name") or str(uid)
        return sorted(
            ({"value": u, "label": label[u], "count": n} for u, n in counts.items()),
            key=lambda o: (-o["count"], o["label"].lower()),
        )

    out["created_by"] = _people("created_by")
    edited = _people("updated_by")
    never = sum(1 for r in rows if not r.get("updated_by"))
    out["updated_by"] = edited + (
        [{"value": NEVER_EDITED, "label": "Never edited", "count": never}] if never else []
    )

    open_any = 0
    mine = 0
    by_type: Counter = Counter()
    for r in every:
        open_types = {
            f.get("type")
            for f in (r.get("flags") or [])
            if isinstance(f, dict) and f.get("status") == "open"
        }
        if open_types:
            open_any += 1
            if me and r.get("created_by") == me:
                mine += 1
            for t in open_types:
                by_type[t] += 1
    out["flags"] = {"open": open_any, "mine": mine, "by_type": dict(by_type)}
    if is_admin:
        out["flags"]["archived"] = collection.count_documents(ARCHIVED)
    return out
