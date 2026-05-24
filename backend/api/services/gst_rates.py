"""
IMS 2.0 - GST rate + HSN canonical table (single source of truth)
=================================================================

ONE place that maps an optical-retail product/order category to its
(HSN code, GST rate). The product master (products.py create-path) and the
POS billing engine (orders.py _gst_rate_for_category) both read this table so
the rate a product is created with ALWAYS equals what POS bills for it.

This is the BACKEND mirror of the frontend's
`frontend/src/constants/gst.ts` (getGSTRateByCategory / getHSNByCategory).
Keep the two in sync: if a rate changes here, change it there too.

------------------------------------------------------------------------------
GST 2.0 rates (effective 22 September 2025; the 12% slab was eliminated).
Source: 56th GST Council Meeting press release, 3 Sep 2025, Annexure-I:
  - 9001 Contact lenses; Spectacle lenses ......... 12% -> 5%   (Sr. 351)
  - 9003 Frames and mountings for spectacles ...... 12% -> 5%   (Sr. 352)
  - 9004 Spectacles, corrective [incl. goggles
         for correcting vision] ................... 12% -> 5%   (Sr. 353)
Non-corrective sunglasses / fashion goggles (9004) STAY at 18% (they are NOT
in the reduction list; PIB FAQ: "spectacles and other goggles other than for
correcting vision continue to attract 18%").
Watches 9101 / 9102 and smartwatches (8517 / 9102) ... 18% (unchanged).
Accessories (cases / cloths / solutions) ............. 18%.

GST-REVIEW (flagged for the accountant, left at the EXISTING IMS rate, not
guessed): SMARTGLASSES — electronic eyewear with a display can be argued under
Ch. 85 (18%) or as corrective eyewear (5%) if prescription-fitted. Kept at 18%
to match prior IMS behaviour; confirm with the accountant before changing.
------------------------------------------------------------------------------

NOTE: rates are floats to match the existing `gst_rate` field shape. The HSN
codes here are 6-digit (turnover > Rs 5 Cr). The product create-path only
falls back to the HSN below when the caller did not supply one; the 4-digit
HSN choice (turnover <= Rs 5 Cr) is handled by the frontend at data entry.
"""

# Canonical category -> (hsn_code, gst_rate). Keys are UPPERCASE.
# The first block is the REAL product/order category enum (schemas.py
# PRODUCT_SCHEMA + ORDER item_type). The second block keeps the legacy
# aliases that older data / other call sites may still use, so nothing that
# previously billed at 5% regresses.
GST_CATEGORY_TABLE: dict = {
    # --- canonical product categories (schemas.py PRODUCT_SCHEMA.category) ---
    "FRAME": ("900311", 5.0),                 # 9003 frames -> 5%
    "OPTICAL_LENS": ("900150", 5.0),          # 9001 spectacle lenses -> 5%
    "READING_GLASSES": ("900490", 5.0),       # 9004 corrective (readymade readers) -> 5%
    "CONTACT_LENS": ("900130", 5.0),          # 9001 contact lenses -> 5%
    "COLORED_CONTACT_LENS": ("900130", 5.0),  # 9001 contact lenses -> 5%
    "SUNGLASS": ("900410", 18.0),             # 9004 non-corrective sunglasses -> 18%
    "WATCH": ("910111", 18.0),                # 9101 watches -> 18%
    "SMARTWATCH": ("910221", 18.0),           # 9102 / 8517 smartwatches -> 18%
    "SMARTGLASSES": ("852580", 18.0),         # GST-REVIEW: electronic eyewear; 18% (see module docstring)
    "WALL_CLOCK": ("910500", 18.0),           # 9105 clocks -> 18%
    "ACCESSORIES": ("392690", 18.0),          # cases / cloths / accessories -> 18%
    "SERVICES": ("998599", 18.0),             # optical services -> 18%
    # Hearing aids: HSN 9021 complete devices are NIL/exempt; parts are 18%.
    # (Not in the PRODUCT_SCHEMA enum yet, but the AddProductPage UI offers it,
    # so map it here so master == billing if a hearing aid is ever sold.)
    "HEARING_AID": ("902140", 0.0),
    # --- canonical ORDER item_type values that differ from product category ---
    "LENS": ("900150", 5.0),                  # order item_type for a spectacle lens -> 5%
    "ACCESSORY": ("392690", 18.0),            # order item_type (singular) -> 18%
    "SERVICE": ("998599", 18.0),              # order item_type (singular) -> 18%
    # --- legacy / alternate aliases kept for back-compat (no regression) ---
    "FRAMES": ("900311", 5.0),
    "EYEGLASS_FRAME": ("900311", 5.0),
    "SPECTACLE_FRAME": ("900311", 5.0),
    "RX_LENSES": ("900150", 5.0),
    "LENSES": ("900150", 5.0),
    "EYEGLASS_LENS": ("900150", 5.0),
    "OPTICAL_LENSES": ("900150", 5.0),
    "SPECTACLE_LENS": ("900150", 5.0),
    "SPECTACLE_LENSES": ("900150", 5.0),
    "CONTACT_LENSES": ("900130", 5.0),
    "COLOUR_CONTACTS": ("900130", 5.0),
    "COLORED_CONTACT_LENSES": ("900130", 5.0),
    "SPECTACLE": ("900490", 5.0),
    "COMPLETE_SPECTACLE": ("900490", 5.0),
    "SUNGLASSES": ("900410", 18.0),
    "WRIST_WATCHES": ("910111", 18.0),
    "SMARTWATCHES": ("910221", 18.0),
    "WALL_CLOCKS": ("910500", 18.0),
    "HEARING_AIDS": ("902140", 0.0),
    # --- short UI codes used at product create on AddProductPage.tsx ---
    # (mirrors the frontend CATEGORIES list so the master rate matches even if
    # a product is persisted with a short code).
    "FR": ("900311", 5.0),      # Frame
    "LS": ("900150", 5.0),      # Optical Lens
    "RG": ("900490", 5.0),      # Reading Glasses (corrective)
    "CL": ("900130", 5.0),      # Contact Lens
    "SG": ("900410", 18.0),     # Sunglass
    "WT": ("910111", 18.0),     # Wrist Watch
    "CK": ("910500", 18.0),     # Clock
    "HA": ("902140", 0.0),      # Hearing Aid (NIL/exempt)
    "ACC": ("392690", 18.0),    # Accessories
    "SMTSG": ("852580", 18.0),  # Smart Sunglass     (GST-REVIEW)
    "SMTFR": ("852580", 18.0),  # Smart Glasses      (GST-REVIEW)
    "SMTWT": ("910221", 18.0),  # Smart Watch
}

# Default GST rate for any category not in the table above (conservative
# standard rate, matching the prior IMS fallback and the frontend default).
DEFAULT_GST_RATE = 18.0


def gst_rate_for_category(category: str) -> float:
    """Return the GST rate (percent, as a float) for a product/order category.

    Falls back to DEFAULT_GST_RATE (18.0) for unknown categories.
    """
    return GST_CATEGORY_TABLE.get((category or "").strip().upper(), (None, DEFAULT_GST_RATE))[1]


def hsn_for_category(category: str) -> str | None:
    """Return the 6-digit HSN code for a product category, or None if unknown."""
    entry = GST_CATEGORY_TABLE.get((category or "").strip().upper())
    return entry[0] if entry else None


# ============================================================================
# EDITABLE HSN -> GST MASTER (DB override layer over the static table above)
# ============================================================================
# The GST_CATEGORY_TABLE above is the canonical CODE default. This layer adds an
# OWNER-editable override: a Mongo `hsn_gst_master` collection managed by
# SUPERADMIN in Settings -> HSN & GST Rates. When the govt revises GST, the
# owner edits a rate here and POS bills the new rate with NO code change.
#
# resolve_gst_rate() is what billing calls. Resolution order:
#   1. editable master, by exact HSN code
#   2. editable master, by category_hint (POS items carry category, not HSN)
#   3. fall back to the static GST_CATEGORY_TABLE via gst_rate_for_category()
# Fail-soft: DB/cache down -> step 3 only -> identical to the #251 behaviour.

_COLLECTION = "hsn_gst_master"
_CACHE_KEY = "hsn_gst_master:lookup"

# Map the many category spellings to the category_hint stored on master rows.
_CATEGORY_HINT = {
    "FRAME": "FRAME", "FRAMES": "FRAME", "EYEGLASS_FRAME": "FRAME",
    "SPECTACLE_FRAME": "FRAME", "FR": "FRAME",
    "OPTICAL_LENS": "LENS", "LENS": "LENS", "LENSES": "LENS", "RX_LENSES": "LENS",
    "EYEGLASS_LENS": "LENS", "OPTICAL_LENSES": "LENS", "SPECTACLE_LENS": "LENS",
    "SPECTACLE_LENSES": "LENS", "LS": "LENS",
    "CONTACT_LENS": "CONTACT_LENS", "CONTACT_LENSES": "CONTACT_LENS",
    "COLORED_CONTACT_LENS": "CONTACT_LENS", "COLORED_CONTACT_LENSES": "CONTACT_LENS",
    "COLOUR_CONTACTS": "CONTACT_LENS", "CL": "CONTACT_LENS",
    "READING_GLASSES": "SPECTACLE", "SPECTACLE": "SPECTACLE",
    "COMPLETE_SPECTACLE": "SPECTACLE", "RG": "SPECTACLE",
    "SUNGLASS": "SUNGLASSES", "SUNGLASSES": "SUNGLASSES", "SG": "SUNGLASSES",
    "WATCH": "WATCH", "WRIST_WATCHES": "WATCH", "WATCHES": "WATCH", "WT": "WATCH",
    "SMARTWATCH": "SMARTWATCH", "SMARTWATCHES": "SMARTWATCH",
    "SMART_WATCH": "SMARTWATCH", "SMTWT": "SMARTWATCH",
    "ACCESSORIES": "ACCESSORIES", "ACCESSORY": "ACCESSORIES", "ACC": "ACCESSORIES",
    "SERVICE": "SERVICE", "SERVICES": "SERVICE", "SVC": "SERVICE",
    "HEARING_AID": "HEARING_AID", "HEARING_AIDS": "HEARING_AID", "HA": "HEARING_AID",
}

# GST 2.0 seed for the editable master. Mirrors GST_CATEGORY_TABLE's rates +
# 6-digit HSNs. Idempotent seeder inserts only missing codes; never overwrites
# an owner/CA edit.
HSN_GST_SEED = [
    {"hsn_code": "900130", "description": "Contact lenses", "gst_rate": 5.0, "category_hint": "CONTACT_LENS"},
    {"hsn_code": "900150", "description": "Spectacle / optical lenses", "gst_rate": 5.0, "category_hint": "LENS"},
    {"hsn_code": "900311", "description": "Frames and mountings for spectacles", "gst_rate": 5.0, "category_hint": "FRAME"},
    {"hsn_code": "900490", "description": "Corrective spectacles / goggles / readers", "gst_rate": 5.0, "category_hint": "SPECTACLE"},
    {"hsn_code": "900410", "description": "Sunglasses (non-corrective)", "gst_rate": 18.0, "category_hint": "SUNGLASSES"},
    {"hsn_code": "910111", "description": "Wrist watches", "gst_rate": 18.0, "category_hint": "WATCH"},
    {"hsn_code": "910221", "description": "Smart watches", "gst_rate": 18.0, "category_hint": "SMARTWATCH"},
    {"hsn_code": "392690", "description": "Spectacle cases & optical accessories", "gst_rate": 18.0, "category_hint": "ACCESSORIES"},
    {"hsn_code": "998599", "description": "Optical repair / fitting services", "gst_rate": 18.0, "category_hint": "SERVICE"},
    {"hsn_code": "902140", "description": "Hearing aids (NIL / exempt)", "gst_rate": 0.0, "category_hint": "HEARING_AID"},
]


def _get_collection():
    try:
        from database.connection import get_db

        db = get_db()
        if db and db.is_connected:
            return db.get_collection(_COLLECTION)
    except Exception:
        pass
    return None


def _normalize_category(category) -> str:
    if not category:
        return ""
    raw = str(category).strip().upper().replace("-", "_").replace(" ", "_")
    return _CATEGORY_HINT.get(raw, raw)


def _load_lookup() -> dict:
    """Build (and cache) {by_hsn, by_cat} from the editable master. Returns
    empty maps when the DB is offline (resolve then uses the static table)."""
    cache = None
    try:
        from .cache import cache as _c

        cache = _c
        cached = cache.get(_CACHE_KEY)
        if cached is not None:
            return cached
    except Exception:
        pass

    by_hsn: dict = {}
    by_cat: dict = {}
    coll = _get_collection()
    if coll is not None:
        try:
            for doc in coll.find({"is_active": {"$ne": False}}):
                rate = doc.get("gst_rate")
                if rate is None:
                    continue
                try:
                    rate_f = float(rate)
                except (TypeError, ValueError):
                    continue
                hc = str(doc.get("hsn_code", "") or "").strip()
                if hc:
                    by_hsn[hc] = rate_f
                ch = str(doc.get("category_hint", "") or "").strip().upper()
                if ch:
                    by_cat.setdefault(ch, rate_f)
        except Exception:
            pass

    result = {"by_hsn": by_hsn, "by_cat": by_cat}
    try:
        if cache is not None:
            cache.set(_CACHE_KEY, result, ttl=cache.TTL_LONG)
    except Exception:
        pass
    return result


def invalidate_cache() -> None:
    """Drop the cached lookup so the next resolve re-reads the master.
    Call after any create/update/delete on hsn_gst_master."""
    try:
        from .cache import cache

        cache.delete(_CACHE_KEY)
    except Exception:
        pass


def resolve_gst_rate(hsn_code=None, category=None) -> float:
    """GST rate (%) for a sale line. Editable master (HSN -> category_hint)
    overrides the static GST_CATEGORY_TABLE. Never raises."""
    lookup = _load_lookup()
    by_hsn = lookup.get("by_hsn", {})
    by_cat = lookup.get("by_cat", {})

    if hsn_code:
        hc = str(hsn_code).strip()
        if hc and hc in by_hsn:
            return by_hsn[hc]

    norm = _normalize_category(category)
    if norm and norm in by_cat:
        return by_cat[norm]

    # Fall back to the canonical static table (#251). Use the normalized form so
    # case / hyphen / space / non-string category variants still resolve (all
    # category hints exist as keys in GST_CATEGORY_TABLE).
    return gst_rate_for_category(norm)


def seed_hsn_gst_master() -> int:
    """Idempotently insert missing GST 2.0 seed rows. Returns count inserted.
    Never overwrites existing rows (owner/CA edits preserved). Safe per-startup."""
    coll = _get_collection()
    if coll is None:
        return 0
    inserted = 0
    try:
        from datetime import datetime
        import uuid

        for row in HSN_GST_SEED:
            if coll.find_one({"hsn_code": row["hsn_code"]}):
                continue
            doc = dict(row)
            doc["hsn_id"] = str(uuid.uuid4())
            doc["_id"] = doc["hsn_id"]
            doc["is_active"] = True
            doc["created_at"] = datetime.utcnow()
            doc["updated_at"] = datetime.utcnow()
            coll.insert_one(doc)
            inserted += 1
    except Exception:
        pass
    if inserted:
        invalidate_cache()
    return inserted
