"""
IMS 2.0 - Lens catalog payload validation (Branch B' sub-PR 1)
==============================================================
Pure validators for the lens_catalog + lens_stock_lines + lens_enum_config
collections. No DB access except where the caller hands us a loaded enum
config dict; fully unit-testable.

Functions:
  slugify_lens_line(brand, series, index, material, lens_type, coating)
      -> canonical lens_line_id slug
  validate_lens_catalog_payload(payload, enum_config, existing=None)
      -> normalized dict, raises ValueError on bad enum / range / shape
  validate_lens_stock_line_payload(payload, lens_line, existing=None)
      -> normalized dict, checks (sph, cyl, add) against lens_line ranges
         and add-nullness vs lens_line.has_add
  validate_bulk_import_payload(matrix, lens_line)
      -> normalized list[(sph, cyl, add, qty)] -- rejects ill-formed cells
  validate_enum_config_payload(enum_type, items)
      -> normalized items list for a given enum_type
  compute_available(on_hand, reserved) -> max(0, on_hand - reserved)

The enum lookup convention: lens_enum_config is shaped as
  {enum_type -> list_of_items}
e.g. {"coatings": [...], "indexes": [1.5, 1.56, ...], "brands": [...]}.
The validator never touches Mongo; the router loads the config and hands
us the dict.

Q1: coating is a SINGLE string, not a list. Combos like DUAL_COAT are their
    own coating codes.
Q2: stock cell key is (sph, cyl, add). SV (has_add=False) requires add=None.
Q5: enum config drives ALL string fields except brand/series (which are
    free-text inside their enum lists since brands change frequently).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The six enum_type keys lens_enum_config knows about. Anything else is
# rejected by the router.
ENUM_TYPES: Tuple[str, ...] = (
    "coatings",
    "brands",
    "series",
    "indexes",
    "materials",
    "lens_types",
)

# Q6 seed defaults. Used by the migration runner (idempotent -- never
# clobbers owner edits). Indexes are floats; coatings/materials/lens_types
# are strings; brands + series start empty so the owner enters their own.
DEFAULT_ENUM_ITEMS: Dict[str, List[Any]] = {
    "coatings": [
        "ANTI_BLUE",
        "GREEN_COAT",
        "BLUE_COAT",
        "DUAL_COAT",
        "TRIPLE_COAT",
        "HC",
        "AR",
        "PHOTOCHROMIC",
        "POLARIZED",
        "UV",
        "MIRROR",
    ],
    "brands": [],
    "series": [],
    "indexes": [1.50, 1.56, 1.60, 1.67, 1.74],
    "materials": ["CR39", "POLY", "MR8", "MR174", "TRIVEX", "GLASS"],
    "lens_types": ["SV", "BIFOCAL", "PROGRESSIVE", "OFFICE", "READING"],
}


# Required keys on a lens_catalog create payload. Validated before the
# enum-membership check so a clearly malformed body returns a useful
# message instead of "coating not in [...]".
_REQUIRED_CATALOG_FIELDS: Tuple[str, ...] = (
    "brand",
    "series",
    "index",
    "material",
    "lens_type",
    "coating",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: Any) -> str:
    """Lower-kebab a string for use inside a lens_line_id slug. Strips
    diacritics so 'Brand' / 'Bra-nd' / 'BRAND' all map to the same slug.
    Returns empty string on empty input."""
    if value is None:
        return ""
    s = str(value)
    # Normalize unicode -> ASCII (drop diacritics).
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = _SLUG_RE.sub("-", s.lower()).strip("-")
    return s


def _slug_index(value: Any) -> str:
    """Slug a refractive index for the slug component. 1.60 -> '1p60';
    1.5 -> '1p50'. Two decimal places so 1.5 and 1.50 collapse to the
    same slug."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return _slug(value)
    return "{f:.2f}".format(f=f).replace(".", "p")


def _trim_str(value: Any) -> str:
    """Stripped string. None / non-string -> '' (after str())."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _ensure_float(name: str, value: Any) -> float:
    """Coerce to float. Bools rejected (Python bool is a subclass of int).
    Raises ValueError on the first problem."""
    if isinstance(value, bool):
        raise ValueError("{name} must be a number".format(name=name))
    if value is None:
        raise ValueError("{name} is required".format(name=name))
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError("{name} must be a number".format(name=name))


def _ensure_int_ge_zero(name: str, value: Any) -> int:
    """Non-negative int. Bool rejected."""
    if isinstance(value, bool):
        raise ValueError("{name} must be a non-negative integer".format(name=name))
    if not isinstance(value, int):
        # Accept stringy ints too (paste-matrix imports come in as JSON
        # numbers from the browser, but a CSV upload may send "3").
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValueError("{name} must be a non-negative integer".format(name=name))
    if value < 0:
        raise ValueError("{name} must be a non-negative integer".format(name=name))
    return value


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------


def slugify_lens_line(
    brand: Any,
    series: Any,
    index: Any,
    material: Any,
    lens_type: Any,
    coating: Any,
) -> str:
    """Build the canonical lens_line_id slug. All fields go through _slug();
    the refractive index gets its own helper so 1.60 -> '1p60'. Raises
    ValueError if any required component slugs to an empty string -- the
    router lifts that to 400.

    Example:
      brand='BrandX', series='SeriesY', index=1.60, material='MR8',
      lens_type='SV', coating='ANTI_BLUE'
      -> 'brandx-seriesy-1p60-mr8-sv-anti-blue'
    """
    parts = [
        ("brand", _slug(brand)),
        ("series", _slug(series)),
        ("index", _slug_index(index)),
        ("material", _slug(material)),
        ("lens_type", _slug(lens_type)),
        ("coating", _slug(coating)),
    ]
    for name, slug in parts:
        if not slug:
            raise ValueError(
                "{name} is required to build lens_line_id".format(name=name)
            )
    return "-".join(slug for _, slug in parts)


# ---------------------------------------------------------------------------
# Enum config lookup helpers
# ---------------------------------------------------------------------------


def _enum_items(enum_config: Optional[Dict[str, Any]], key: str) -> List[Any]:
    """Pull the items list for an enum_type out of the loaded enum_config
    dict. Returns [] when enum_config is None or the key is missing -- the
    router calls _empty_enum_warning() and refuses the write."""
    if not enum_config:
        return []
    items = enum_config.get(key)
    if isinstance(items, list):
        return list(items)
    return []


def _check_in_enum(
    field: str, value: Any, enum_config: Optional[Dict[str, Any]], enum_key: str
) -> Any:
    """Raise ValueError if `value` is not in the live enum_config[enum_key]
    list. Empty enum lists (e.g. the seeded-empty brands list) raise a
    helpful 'no values configured' message so the owner is nudged to
    populate Settings."""
    items = _enum_items(enum_config, enum_key)
    if not items:
        raise ValueError(
            "{enum_key} enum has no configured values; "
            "edit them in Settings before creating a lens line".format(
                enum_key=enum_key
            )
        )
    if value not in items:
        raise ValueError(
            "{field} {value!r} not in configured {enum_key} {items}".format(
                field=field, value=value, enum_key=enum_key, items=items
            )
        )
    return value


def _check_index_in_enum(value: float, enum_config: Optional[Dict[str, Any]]) -> float:
    """Index needs a tolerance compare -- 1.6 and 1.60 must both match
    the seeded 1.60. Compare to 4 decimal places (more than enough for
    optical refractive indexes)."""
    items = _enum_items(enum_config, "indexes")
    if not items:
        raise ValueError(
            "indexes enum has no configured values; "
            "edit them in Settings before creating a lens line"
        )
    target = round(float(value), 4)
    for candidate in items:
        try:
            if round(float(candidate), 4) == target:
                return float(candidate)
        except (TypeError, ValueError):
            continue
    raise ValueError(
        "index {value!r} not in configured indexes {items}".format(
            value=value, items=items
        )
    )


# ---------------------------------------------------------------------------
# Range validators
# ---------------------------------------------------------------------------


def _normalize_range(name: str, value: Any) -> Dict[str, float]:
    """Coerce a {min, max, step} dict, defaulting step to 0.25. Validates
    min <= max and step > 0."""
    if not isinstance(value, dict):
        raise ValueError(
            "{name} must be a dict with min/max/step keys".format(name=name)
        )
    lo = _ensure_float("{name}.min".format(name=name), value.get("min"))
    hi = _ensure_float("{name}.max".format(name=name), value.get("max"))
    raw_step = value.get("step", 0.25)
    step = _ensure_float("{name}.step".format(name=name), raw_step)
    if step <= 0:
        raise ValueError("{name}.step must be > 0".format(name=name))
    if lo > hi:
        raise ValueError(
            "{name}.min ({lo}) must be <= {name}.max ({hi})".format(
                name=name, lo=lo, hi=hi
            )
        )
    return {"min": lo, "max": hi, "step": step}


def _in_range(value: float, rng: Dict[str, float]) -> bool:
    """True iff `value` is within [min, max]. Step alignment is NOT checked
    (the float-math hell of multiples-of-0.25 is not worth the maintenance
    burden; the UI side rounds, and the DB accepts whatever lands)."""
    lo = rng.get("min")
    hi = rng.get("max")
    if lo is None or hi is None:
        return False
    return lo - 1e-9 <= value <= hi + 1e-9


# ---------------------------------------------------------------------------
# lens_catalog payload
# ---------------------------------------------------------------------------


def validate_lens_catalog_payload(
    payload: Dict[str, Any],
    enum_config: Optional[Dict[str, Any]] = None,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize + validate a lens_catalog payload.

    `enum_config` is the loaded lens_enum_config dict -- {enum_type: items}.
    `existing` is supplied on PATCH; partial updates fall back to the stored
    doc for required fields the caller omitted.

    Returns a NEW dict. Raises ValueError with a human message; the router
    lifts those to 400.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    src = dict(payload)

    def _pick(field: str) -> Any:
        if field in src:
            return src[field]
        if existing is not None and field in existing:
            return existing[field]
        return None

    # Required identity fields -- check shape first, then enum membership.
    out: Dict[str, Any] = {}

    brand = _trim_str(_pick("brand"))
    if not brand:
        raise ValueError("brand is required")
    _check_in_enum("brand", brand, enum_config, "brands")
    out["brand"] = brand

    series = _trim_str(_pick("series"))
    if not series:
        raise ValueError("series is required")
    # Series membership is checked against the brand-specific list when
    # the enum_config carries one; otherwise we accept any non-empty
    # string. The router populates this from lens_enum_config["series"]
    # which is a list of {brand: [series...]} dicts.
    _series_items = _series_for_brand(enum_config, brand)
    if _series_items:
        if series not in _series_items:
            raise ValueError(
                "series {series!r} not in configured series for brand "
                "{brand!r} {items}".format(
                    series=series, brand=brand, items=_series_items
                )
            )
    out["series"] = series

    index_val = _ensure_float("index", _pick("index"))
    out["index"] = _check_index_in_enum(index_val, enum_config)

    material = _trim_str(_pick("material"))
    if not material:
        raise ValueError("material is required")
    _check_in_enum("material", material, enum_config, "materials")
    out["material"] = material

    lens_type = _trim_str(_pick("lens_type"))
    if not lens_type:
        raise ValueError("lens_type is required")
    _check_in_enum("lens_type", lens_type, enum_config, "lens_types")
    out["lens_type"] = lens_type

    coating = _trim_str(_pick("coating"))
    if not coating:
        raise ValueError("coating is required")
    _check_in_enum("coating", coating, enum_config, "coatings")
    out["coating"] = coating

    # Ranges -- defaulted on first create, fall through to existing on PATCH.
    sph_raw = _pick("sph_range")
    if sph_raw is None:
        sph_raw = {"min": -8.0, "max": 6.0, "step": 0.25}
    out["sph_range"] = _normalize_range("sph_range", sph_raw)

    cyl_raw = _pick("cyl_range")
    if cyl_raw is None:
        cyl_raw = {"min": -4.0, "max": 0.0, "step": 0.25}
    out["cyl_range"] = _normalize_range("cyl_range", cyl_raw)

    # has_add: required boolean. add_range required iff has_add=True.
    has_add_raw = _pick("has_add")
    if has_add_raw is None:
        # Auto-derive from lens_type when caller didn't pass it. SV is the
        # only single-vision lens_type by convention; everything else is
        # multifocal. The owner can override on PATCH.
        has_add = lens_type.upper() != "SV"
    elif isinstance(has_add_raw, bool):
        has_add = has_add_raw
    else:
        raise ValueError("has_add must be a boolean")
    out["has_add"] = has_add

    add_raw = _pick("add_range")
    if has_add:
        if add_raw is None:
            add_raw = {"min": 0.75, "max": 3.50, "step": 0.25}
        out["add_range"] = _normalize_range("add_range", add_raw)
    else:
        # SV: add_range must be null. The owner cannot ship an "SV with add".
        if add_raw not in (None,):
            # Allow empty dicts as null on PATCH so the FE can clear it.
            if isinstance(add_raw, dict) and not add_raw:
                out["add_range"] = None
            else:
                raise ValueError("add_range must be null when has_add=False")
        else:
            out["add_range"] = None

    # MRP / cost: required numeric, non-negative. cost_price optional.
    mrp = _ensure_float("mrp", _pick("mrp"))
    if mrp < 0:
        raise ValueError("mrp must be non-negative")
    out["mrp"] = mrp

    if "cost_price" in src or (existing is not None and "cost_price" in existing):
        cost = _pick("cost_price")
        if cost is not None:
            cost_f = _ensure_float("cost_price", cost)
            if cost_f < 0:
                raise ValueError("cost_price must be non-negative")
            out["cost_price"] = cost_f

    # mrp_table optional list (shape deferred to B'2).
    mrp_table = _pick("mrp_table")
    if mrp_table is not None:
        if not isinstance(mrp_table, list):
            raise ValueError("mrp_table must be a list")
        out["mrp_table"] = mrp_table

    # GST + HSN -- defaulted to 5 / 9001 per the owner's optical-lens band.
    gst_raw = _pick("gst_rate")
    if gst_raw is None:
        out["gst_rate"] = 5.0
    else:
        gst = _ensure_float("gst_rate", gst_raw)
        if gst < 0 or gst > 28:
            raise ValueError("gst_rate must be between 0 and 28")
        out["gst_rate"] = gst

    hsn = _trim_str(_pick("hsn_code"))
    out["hsn_code"] = hsn or "9001"

    # is_active default True on create, fall through on PATCH.
    is_active_raw = _pick("is_active")
    if is_active_raw is None:
        out["is_active"] = True
    elif isinstance(is_active_raw, bool):
        out["is_active"] = is_active_raw
    else:
        raise ValueError("is_active must be a boolean")

    # notes optional.
    if "notes" in src:
        raw = src["notes"]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            out["notes"] = None
        else:
            if not isinstance(raw, str):
                raise ValueError("notes must be a string")
            out["notes"] = raw.strip()
    elif existing is not None and "notes" in existing:
        out["notes"] = existing.get("notes")

    return out


def _series_for_brand(enum_config: Optional[Dict[str, Any]], brand: str) -> List[str]:
    """The series enum is stored as a list of {brand: [series...]} dicts.
    Return the list of series codes configured for `brand`. Empty list
    means "any series accepted" -- the validator falls open in that case
    so a brand without explicit series doesn't block lens-line creation."""
    items = _enum_items(enum_config, "series")
    out: List[str] = []
    for entry in items:
        if isinstance(entry, dict):
            for k, v in entry.items():
                if k == brand and isinstance(v, list):
                    for s in v:
                        if isinstance(s, str):
                            out.append(s)
    return out


# ---------------------------------------------------------------------------
# lens_stock_lines payload
# ---------------------------------------------------------------------------


def validate_lens_stock_line_payload(
    payload: Dict[str, Any],
    lens_line: Optional[Dict[str, Any]] = None,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize a lens_stock_lines payload. Verifies (sph, cyl, add) fall
    inside the parent lens_line's ranges; enforces add-nullness against
    has_add. `lens_line` is the parent doc (loaded by the router).

    Returns a NEW dict with the normalized fields.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    src = dict(payload)

    def _pick(field: str) -> Any:
        if field in src:
            return src[field]
        if existing is not None and field in existing:
            return existing[field]
        return None

    out: Dict[str, Any] = {}

    lens_line_id = _trim_str(_pick("lens_line_id"))
    if not lens_line_id:
        raise ValueError("lens_line_id is required")
    out["lens_line_id"] = lens_line_id

    store_id = _trim_str(_pick("store_id"))
    if not store_id:
        raise ValueError("store_id is required")
    out["store_id"] = store_id

    sph = _ensure_float("sph", _pick("sph"))
    cyl_raw = _pick("cyl")
    if cyl_raw is None:
        cyl = 0.0
    else:
        cyl = _ensure_float("cyl", cyl_raw)
    out["sph"] = sph
    out["cyl"] = cyl

    has_add = bool(lens_line.get("has_add")) if lens_line else False
    add_raw = _pick("add")
    if has_add:
        if add_raw is None:
            raise ValueError(
                "add is required for multifocal lens line " "(has_add=True)"
            )
        add = _ensure_float("add", add_raw)
        out["add"] = add
    else:
        if add_raw not in (None,):
            # Reject any non-null add on an SV line.
            raise ValueError(
                "add must be null for single-vision lens line " "(has_add=False)"
            )
        out["add"] = None

    # Range checks if the parent line carries ranges.
    if lens_line:
        sph_rng = lens_line.get("sph_range") or {}
        if sph_rng and not _in_range(out["sph"], sph_rng):
            raise ValueError(
                "sph {sph} outside line sph_range "
                "[{lo}, {hi}]".format(
                    sph=out["sph"],
                    lo=sph_rng.get("min"),
                    hi=sph_rng.get("max"),
                )
            )
        cyl_rng = lens_line.get("cyl_range") or {}
        if cyl_rng and not _in_range(out["cyl"], cyl_rng):
            raise ValueError(
                "cyl {cyl} outside line cyl_range "
                "[{lo}, {hi}]".format(
                    cyl=out["cyl"],
                    lo=cyl_rng.get("min"),
                    hi=cyl_rng.get("max"),
                )
            )
        if has_add:
            add_rng = lens_line.get("add_range") or {}
            if (
                add_rng
                and out["add"] is not None
                and not _in_range(out["add"], add_rng)
            ):
                raise ValueError(
                    "add {add} outside line add_range "
                    "[{lo}, {hi}]".format(
                        add=out["add"],
                        lo=add_rng.get("min"),
                        hi=add_rng.get("max"),
                    )
                )

    # on_hand / reserved / reorder_point / safety_stock: non-negative ints.
    for fld in ("on_hand", "reserved", "reorder_point", "safety_stock"):
        raw = _pick(fld)
        if raw is None:
            out[fld] = 0
        else:
            out[fld] = _ensure_int_ge_zero(fld, raw)

    return out


# ---------------------------------------------------------------------------
# Bulk import (2D power matrix paste)
# ---------------------------------------------------------------------------


def validate_bulk_import_payload(
    matrix: Any,
    lens_line: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Normalize a bulk-import payload into a list of cell dicts ready to
    upsert. `matrix` accepts either:
      - list of dicts {sph, cyl?, add?, qty}     (canonical)
      - CSV string with header line "sph,cyl,add,qty" or "sph,cyl,qty"
        (single-vision)
    For SV lines, `add` is normalised to None (the validator rejects
    non-null add on SV).
    Returns a list of normalised cell dicts. Raises ValueError on the
    first malformed row -- the router lifts to 400 and rolls back any
    partial upsert.
    """
    if matrix is None:
        raise ValueError("matrix is required")

    if isinstance(matrix, str):
        rows = _parse_csv_matrix(matrix)
    elif isinstance(matrix, list):
        rows = matrix
    else:
        raise ValueError("matrix must be a list of cell dicts or a CSV string")

    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("matrix row {idx}: must be a dict".format(idx=idx))
        # Wrap each cell-level error with the row index so partial-import
        # debugging is tractable.
        try:
            cell = validate_lens_stock_line_payload(
                {
                    "lens_line_id": lens_line.get("lens_line_id"),
                    "store_id": row.get("store_id"),
                    "sph": row.get("sph"),
                    "cyl": row.get("cyl"),
                    "add": row.get("add"),
                    "on_hand": row.get("qty"),
                },
                lens_line=lens_line,
            )
        except ValueError as exc:
            raise ValueError("matrix row {idx}: {msg}".format(idx=idx, msg=exc))
        out.append(cell)
    return out


def _parse_csv_matrix(text: str) -> List[Dict[str, Any]]:
    """Hand-rolled CSV parser -- the import body is small (a power matrix is
    at most a few hundred cells) so we keep dependencies out. Header row is
    required so the field order is unambiguous; common patterns:
      sph,cyl,qty                         (SV)
      sph,cyl,add,qty                     (multifocal)
      sph,cyl,add,qty,store_id            (multi-store bulk-import)
    Lines starting with '#' are comments. Empty lines are skipped.
    """
    lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split(",")]
    if not header or "qty" not in header:
        raise ValueError(
            "CSV matrix header must include 'qty'; got {header}".format(header=header)
        )
    out: List[Dict[str, Any]] = []
    for i, line in enumerate(lines[1:], start=1):
        cells = [c.strip() for c in line.split(",")]
        if len(cells) != len(header):
            raise ValueError(
                "CSV row {i}: expected {n} columns, got {m}".format(
                    i=i, n=len(header), m=len(cells)
                )
            )
        row: Dict[str, Any] = {}
        for k, v in zip(header, cells):
            if v == "" or v.lower() in ("null", "none"):
                row[k] = None
                continue
            # qty is int; sph/cyl/add are floats; anything else is a string.
            if k == "qty":
                try:
                    row[k] = int(v)
                except ValueError:
                    raise ValueError(
                        "CSV row {i}: qty {v!r} is not an integer".format(i=i, v=v)
                    )
            elif k in ("sph", "cyl", "add"):
                try:
                    row[k] = float(v)
                except ValueError:
                    raise ValueError(
                        "CSV row {i}: {k} {v!r} is not a number".format(i=i, k=k, v=v)
                    )
            else:
                row[k] = v
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Enum config payload
# ---------------------------------------------------------------------------


def validate_enum_config_payload(
    enum_type: str,
    items: Any,
) -> List[Any]:
    """Validate the items list for a given enum_type. Returns the normalized
    list. Raises ValueError on bad shape.

      coatings/brands/materials/lens_types: list of non-empty strings,
                                            de-duped (case-sensitive).
      indexes: list of positive floats > 1.0, de-duped (compared to 4dp).
      series: list of {brand: [series...]} dicts.
    """
    if enum_type not in ENUM_TYPES:
        raise ValueError(
            "Unknown enum_type {enum_type!r}; one of {allowed}".format(
                enum_type=enum_type, allowed=list(ENUM_TYPES)
            )
        )
    if not isinstance(items, list):
        raise ValueError("items must be a list")

    if enum_type in ("coatings", "brands", "materials", "lens_types"):
        out_str: List[str] = []
        seen = set()
        for entry in items:
            if not isinstance(entry, str):
                raise ValueError(
                    "{enum_type} entries must be strings".format(enum_type=enum_type)
                )
            v = entry.strip()
            if not v:
                continue
            if v in seen:
                continue
            seen.add(v)
            out_str.append(v)
        return out_str

    if enum_type == "indexes":
        out_idx: List[float] = []
        seen_idx = set()
        for entry in items:
            if isinstance(entry, bool):
                raise ValueError("indexes entries must be numbers")
            try:
                v_f = float(entry)
            except (TypeError, ValueError):
                raise ValueError("indexes entries must be numbers")
            if v_f <= 1.0:
                raise ValueError(
                    "index {v} must be > 1.0 (refractive indexes are > 1)".format(v=v_f)
                )
            rounded = round(v_f, 4)
            if rounded in seen_idx:
                continue
            seen_idx.add(rounded)
            out_idx.append(v_f)
        return out_idx

    # series: list of {brand: [series...]} dicts.
    out_series: List[Dict[str, List[str]]] = []
    seen_brands: set = set()
    for entry in items:
        if not isinstance(entry, dict) or len(entry) != 1:
            raise ValueError(
                "series entries must be single-key dicts " "{{brand: [series...]}}"
            )
        for k, v in entry.items():
            if not isinstance(k, str) or not k.strip():
                raise ValueError("series brand key must be a non-empty string")
            if not isinstance(v, list) or not all(isinstance(s, str) for s in v):
                raise ValueError(
                    "series value for brand {k!r} must be a list of "
                    "strings".format(k=k)
                )
            brand = k.strip()
            if brand in seen_brands:
                continue
            seen_brands.add(brand)
            cleaned = []
            seen_inner: set = set()
            for s in v:
                t = s.strip()
                if not t or t in seen_inner:
                    continue
                seen_inner.add(t)
                cleaned.append(t)
            out_series.append({brand: cleaned})
    return out_series


# ---------------------------------------------------------------------------
# Available computation (Q4 atomicity foundation)
# ---------------------------------------------------------------------------


def compute_available(on_hand: int, reserved: int) -> int:
    """Effective available = max(0, on_hand - reserved). Never negative --
    the atomic CAS in the router keeps the DB invariant, but a stale read
    or a manual `set_on_hand` that drops below `reserved` would otherwise
    surface negative numbers in the FE."""
    try:
        oh = int(on_hand or 0)
    except (TypeError, ValueError):
        oh = 0
    try:
        rs = int(reserved or 0)
    except (TypeError, ValueError):
        rs = 0
    return oh - rs if oh > rs else 0
