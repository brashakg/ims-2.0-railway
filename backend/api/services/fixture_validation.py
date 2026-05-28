"""
IMS 2.0 - Display fixture / placement payload validation
========================================================
Pure validators for the display_fixtures + display_placements collections
(v2-2a). No DB access; fully unit-testable.

Two distinct payload shapes get normalized + checked here:

  - validate_fixture_payload(payload, existing=None) -> dict
      The CRUD layer (display_fixtures.py) calls this on POST and PATCH.
      It returns a normalized dict (code upper-cased, whitespace stripped)
      or raises ValueError with a human-readable reason.

  - validate_placement_payload(payload, fixture=None, existing_total=None) -> dict
      The CRUD layer (display_placements.py) calls this on POST and PATCH.
      When `fixture` is supplied, also enforces merch-consistency (a CL
      placement at a frame-only fixture is rejected).

The capacity check is intentionally a HELPER (over_capacity), not a hard
block. The Display Layout tab will WARN on over-capacity but never refuse
a write -- the floor sometimes legitimately overstocks a fixture for a
sale or a delivery wave. The validator stays pure of that policy.

Mirrors the convention in services/org_validation.py: enum tuples + pure
functions, no FastAPI imports.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Enumerations -- single source of truth shared with the DB schema (kept in
# sync manually with database/schemas.py DISPLAY_FIXTURE_SCHEMA).
# ---------------------------------------------------------------------------

# Physical fixture types. Each renders a distinct icon in the FE (v2-2b).
#   window  - shop window (street-facing display)
#   wall    - wall-mounted display rack
#   pillar  - free-standing pillar / column display
#   counter - counter / showcase under glass
#   cabinet - lockable cabinet (luxury / premium)
#   gondola - free-standing aisle display
#   drawer  - back-stock drawer (storage)
#   fridge  - temp-controlled chamber (CL fridge, 2-8C)
FIXTURE_TYPES: tuple = (
    "window",
    "wall",
    "pillar",
    "counter",
    "cabinet",
    "gondola",
    "drawer",
    "fridge",
)

# Where the fixture physically lives.
#   ground  - customer-facing shop floor
#   storage - back-of-house store-room
#   clinic  - optometrist chamber (CL fridge / sample lenses)
FIXTURE_FLOORS: tuple = ("ground", "storage", "clinic")

# Customer-zone tag. A/B/C are walk-zones on the shop floor. "-" means the
# fixture is not in a customer zone (storage / clinic).
FIXTURE_ZONES: tuple = ("A", "B", "C", "-")

# Catalog types a fixture is designed for. Subset of these maps onto the
# product catalog rough buckets:
#   Frame   - spectacle frames + sunglasses
#   Lens    - optical lenses (loose + ready-made)
#   CL      - contact lenses (incl. coloured)
#   Access. - cases, cleaners, chains, etc.
CATALOG_TYPES: tuple = ("Frame", "Lens", "CL", "Access.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trim_str(value: Any) -> str:
    """Return value as a stripped string. Non-strings -> empty string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _ensure_in(name: str, value: Any, allowed: Sequence[str]) -> str:
    """Raise ValueError if value is not in allowed. Returns the value."""
    if value not in allowed:
        raise ValueError(
            "{name} must be one of {allowed}; got {actual!r}".format(
                name=name, allowed=list(allowed), actual=value
            )
        )
    return value


def _ensure_positive_int(name: str, value: Any) -> int:
    """Reject non-positive / non-int. Accepts bool only if exactly truthy int."""
    # bool is a subclass of int in Python -- block it explicitly so True / False
    # cannot satisfy capacity / qty fields.
    if isinstance(value, bool):
        raise ValueError("{name} must be a positive integer".format(name=name))
    if not isinstance(value, int):
        raise ValueError("{name} must be a positive integer".format(name=name))
    if value <= 0:
        raise ValueError("{name} must be a positive integer".format(name=name))
    return value


def _ensure_subset(name: str, values: Any, allowed: Sequence[str]) -> List[str]:
    """Coerce to list; reject non-list / non-strings; reject members not in
    allowed. Returns the cleaned, de-duped, order-preserved list."""
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError("{name} must be a list".format(name=name))
    out: List[str] = []
    seen = set()
    for v in values:
        if not isinstance(v, str):
            raise ValueError(
                "{name} entries must be strings; got {actual!r}".format(
                    name=name, actual=v
                )
            )
        s = v.strip()
        if not s:
            continue
        if s not in allowed:
            raise ValueError(
                "{name} entry {entry!r} not in {allowed}".format(
                    name=name, entry=s, allowed=list(allowed)
                )
            )
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Fixture payload
# ---------------------------------------------------------------------------


def validate_fixture_payload(
    payload: Dict[str, Any], existing: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Normalize + validate a display_fixtures payload.

    `existing` is supplied on PATCH so partial updates can fall back to the
    stored values for required fields (i.e. a PATCH that only changes
    capacity doesn't need to re-send type/floor/zone).

    Returns the normalized dict (a NEW dict, not mutated in place). Raises
    ValueError on the first problem with a human-readable message -- the
    router lifts these to 400.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    src = dict(payload)  # shallow copy; never mutate the caller's dict
    out: Dict[str, Any] = {}

    def _pick(field: str) -> Any:
        """Return the value of field from the payload, falling back to the
        existing doc on PATCH. Used for required-field validation."""
        if field in src:
            return src[field]
        if existing is not None and field in existing:
            return existing[field]
        return None

    # store_id is required on CREATE -- the router enforces ownership before
    # calling us, so we only need a non-empty string check here.
    store_id = _trim_str(_pick("store_id"))
    if not store_id:
        raise ValueError("store_id is required")
    out["store_id"] = store_id

    # code: required, stripped + upper-cased, no whitespace inside.
    code_raw = _pick("code")
    code = _trim_str(code_raw)
    if not code:
        raise ValueError("code is required")
    # Disallow internal whitespace -- W 01 with a space would silently differ
    # from W-01 / W_01 in the UNIQUE index. Force the staff to dash / dot.
    if any(ch.isspace() for ch in code):
        raise ValueError("code must not contain whitespace")
    out["code"] = code.upper()

    # name: required, stripped.
    name = _trim_str(_pick("name"))
    if not name:
        raise ValueError("name is required")
    out["name"] = name

    # type / floor / zone: required enums.
    out["type"] = _ensure_in("type", _pick("type"), FIXTURE_TYPES)
    out["floor"] = _ensure_in("floor", _pick("floor"), FIXTURE_FLOORS)
    out["zone"] = _ensure_in("zone", _pick("zone"), FIXTURE_ZONES)

    # capacity: positive integer.
    out["capacity"] = _ensure_positive_int("capacity", _pick("capacity"))

    # merch: subset of CATALOG_TYPES (may be empty -- a generic display rack
    # can opt out of category gating).
    out["merch"] = _ensure_subset("merch", _pick("merch"), CATALOG_TYPES)

    # Optional bools default to False if absent in BOTH payload AND existing.
    # On PATCH the existing doc supplies the fallback so we don't accidentally
    # clobber a True back to False.
    for flag in ("lockable", "mannequin", "spotlit", "no_qr"):
        if flag in src:
            v = src[flag]
            if not isinstance(v, bool):
                raise ValueError("{flag} must be a boolean".format(flag=flag))
            out[flag] = v
        elif existing is not None and flag in existing:
            out[flag] = bool(existing[flag])
        else:
            # Don't write the default -- routers add their own defaults so
            # PATCH semantics stay clean.
            pass

    # Optional strings: trim; allow null/empty meaning "clear it".
    for sfield in ("temp_ctrl", "key_holder", "notes"):
        if sfield in src:
            raw = src[sfield]
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                # explicit clear
                out[sfield] = None
            else:
                if not isinstance(raw, str):
                    raise ValueError("{f} must be a string".format(f=sfield))
                out[sfield] = raw.strip()
        elif existing is not None and sfield in existing:
            out[sfield] = existing[sfield]

    # is_active: lives on the schema as a bool. Default True on create.
    if "is_active" in src:
        if not isinstance(src["is_active"], bool):
            raise ValueError("is_active must be a boolean")
        out["is_active"] = src["is_active"]
    elif existing is not None and "is_active" in existing:
        out["is_active"] = bool(existing["is_active"])
    else:
        out["is_active"] = True

    return out


# ---------------------------------------------------------------------------
# Placement payload
# ---------------------------------------------------------------------------


# Maps each catalog "type" tag to the canonical product.category values it
# implies. Used by _placement_merch_compatible to confirm that a SKU's
# category is something the fixture is designed to hold. The lookup is one-way
# (catalog type -> product categories) so a fixture tagged "Frame" accepts
# both FRAME and SUNGLASS / READING_GLASSES, since those all sit on the same
# kind of wall rack.
CATALOG_TYPE_TO_CATEGORIES: Dict[str, tuple] = {
    "Frame": ("FRAME", "SUNGLASS", "READING_GLASSES"),
    "Lens": ("OPTICAL_LENS",),
    "CL": ("CONTACT_LENS", "COLORED_CONTACT_LENS"),
    "Access.": (
        "ACCESSORIES",
        "WATCH",
        "SMARTWATCH",
        "SMARTGLASSES",
        "WALL_CLOCK",
        "SERVICES",
    ),
}


def _placement_merch_compatible(
    product_category: Optional[str], fixture_merch: Iterable[str]
) -> bool:
    """Return True iff the product's category fits any of the fixture's
    merch tags. An empty merch list means "no gating" -- compatible with
    anything. Unknown category -> compatible (fail-open so a freshly-defined
    category doesn't break placements before CATALOG_TYPE_TO_CATEGORIES is
    extended)."""
    merch = list(fixture_merch or [])
    if not merch:
        return True
    if not product_category:
        return True
    # Build the union of acceptable product.category values for this fixture.
    acceptable: set = set()
    for tag in merch:
        acceptable.update(CATALOG_TYPE_TO_CATEGORIES.get(tag, ()))
    if not acceptable:
        # Fixture tagged with only unknown merch tags -- fail open.
        return True
    return product_category in acceptable


def validate_placement_payload(
    payload: Dict[str, Any],
    fixture: Optional[Dict[str, Any]] = None,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize + validate a display_placements payload.

    Required fields on CREATE: sku, store_id, fixture_id, qty.
    `existing` is the prior doc on PATCH (so partial updates work).
    `fixture` is the parent display_fixtures doc when known -- if supplied,
    enforces merch consistency between fixture.merch and the placement's
    implied product category. The caller can pass a `product_category` key
    on the payload to enable that check; without it, the check is skipped.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    src = dict(payload)
    out: Dict[str, Any] = {}

    def _pick(field: str) -> Any:
        if field in src:
            return src[field]
        if existing is not None and field in existing:
            return existing[field]
        return None

    sku = _trim_str(_pick("sku"))
    if not sku:
        raise ValueError("sku is required")
    out["sku"] = sku

    store_id = _trim_str(_pick("store_id"))
    if not store_id:
        raise ValueError("store_id is required")
    out["store_id"] = store_id

    fixture_id = _trim_str(_pick("fixture_id"))
    if not fixture_id:
        raise ValueError("fixture_id is required")
    out["fixture_id"] = fixture_id

    out["qty"] = _ensure_positive_int("qty", _pick("qty"))

    # position is OPTIONAL; trim if supplied, allow explicit clear via None
    # or whitespace-only.
    if "position" in src:
        raw = src["position"]
        if raw is None:
            out["position"] = None
        elif isinstance(raw, str):
            s = raw.strip()
            out["position"] = s if s else None
        else:
            raise ValueError("position must be a string")
    elif existing is not None and "position" in existing:
        out["position"] = existing["position"]

    if "is_primary" in src:
        if not isinstance(src["is_primary"], bool):
            raise ValueError("is_primary must be a boolean")
        out["is_primary"] = src["is_primary"]
    elif existing is not None and "is_primary" in existing:
        out["is_primary"] = bool(existing["is_primary"])
    else:
        out["is_primary"] = False

    # Merch consistency -- only enforced if the caller wired up both the
    # fixture doc and a product_category hint. A placement of a CONTACT_LENS
    # SKU at a fixture tagged only ["Frame"] is rejected.
    if fixture is not None:
        # Cross-store guard: placement.store_id must match fixture.store_id.
        f_store = _trim_str(fixture.get("store_id"))
        if f_store and f_store != out["store_id"]:
            raise ValueError(
                "fixture belongs to a different store ({fix} vs {plc})".format(
                    fix=f_store, plc=out["store_id"]
                )
            )
        product_category = src.get("product_category")
        if product_category is not None:
            if not _placement_merch_compatible(
                str(product_category), fixture.get("merch") or []
            ):
                raise ValueError(
                    "fixture merch {merch} does not accept "
                    "category {cat!r}".format(
                        merch=list(fixture.get("merch") or []),
                        cat=product_category,
                    )
                )

    return out


# ---------------------------------------------------------------------------
# Capacity helpers (informational only -- the router warns, never blocks)
# ---------------------------------------------------------------------------


def placement_total_at_fixture(placements: Iterable[Dict[str, Any]]) -> int:
    """Sum the qty across a list of placement docs. Non-int qty entries are
    silently coerced via int() with a 0 fallback so a corrupt row can't
    crash the count."""
    total = 0
    for p in placements or []:
        q = p.get("qty") if isinstance(p, dict) else None
        try:
            total += int(q)
        except (TypeError, ValueError):
            continue
    return total


def over_capacity(
    fixture: Dict[str, Any], placements_after_change: Iterable[Dict[str, Any]]
) -> bool:
    """True iff the sum of placement qty AFTER the proposed change would
    exceed fixture.capacity. Used by the router to attach a `warning` field
    to the response, never to refuse a write."""
    if not isinstance(fixture, dict):
        return False
    try:
        cap = int(fixture.get("capacity") or 0)
    except (TypeError, ValueError):
        return False
    if cap <= 0:
        return False
    total = placement_total_at_fixture(placements_after_change)
    return total > cap
