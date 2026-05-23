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
