"""
IMS 2.0 - Public GTIN validation
================================
A variant's `gtin` (or `barcode`) is the PUBLIC barcode: it is pushed to
Shopify as `ProductVariant.barcode` and from there feeds the Google and Meta
Shopping catalogs. A wrong value there is customer-visible and actively harmful
-- the feeds either reject the item or, worse, match it to somebody else's
product. An EMPTY gtin is always safer than a wrong one.

This is deliberately NOT the internal barcode. The two-barcode model:

    gtin / barcode   the manufacturer's public GTIN  -> pushed to Shopify
    store_barcode    our internally minted unit code -> NEVER pushed

Until 2026-09-28 `services/barcode.py` minted unit codes as EAN-13s under GS1
prefix 20-29 ("restricted distribution" / in-store only) and those units keep
them. That is exactly why a code in that range must never be accepted as a
public GTIN: it is by definition not a manufacturer identifier.

Observed real damage this guards against (prod audit, 2026-07-29 -- 353 of
2,815 gtin-bearing variants were invalid):

    tag string   "brand_boss, shape_square, framecolor_golden, ... gender_men,"
                 (a Shopify browse-tag list that landed in the GTIN column)
    two-in-one   "8056597720373 8056597720380"  (a spec cell listing two EANs)
    model code   "TW003HG14" / "VPLD94 510509"  (not a GTIN at all)
    supplier ref "2511661"                      (a 7-digit internal item code)
    fake check   "92939293"                     (a repeated 4-digit stub)

Pure + dependency-light: no DB, no I/O, safe to call from any door.
No emojis (Windows cp1252).
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

# GS1 defines exactly these four lengths: GTIN-8, GTIN-12 (UPC-A), GTIN-13
# (EAN-13) and GTIN-14 (case/carton).
VALID_GTIN_LENGTHS = (8, 12, 13, 14)

# Reasons, mirroring the prod audit script so a rejection here is directly
# comparable with the 2026-07-29 breakdown.
REASON_NONNUMERIC = "NONNUMERIC"
REASON_ZEROS = "ZEROS"
REASON_BADLEN = "BADLEN"
REASON_RESTRICTED = "RESTRICTED"
REASON_BADCHECK = "BADCHECK"

# ASCII 0-9 only: `\d` also matches every other script's digits, so a
# Devanagari or full-width EAN passed as "numeric" and was pushed as-is.
_DIGITS = re.compile(r"^[0-9]+\Z")
# Separators a human or a spec sheet may legitimately put inside one code.
# Stripping them is what makes "805-6597-72037-3" valid and, deliberately,
# what makes "8056597720373 8056597720380" collapse to 26 digits -> BADLEN.
_SEPARATORS = re.compile(r"[\s\-]+")

_RESTRICTED_PREFIXES = frozenset(str(n) for n in range(20, 30))
_RESTRICTED_UPC_PREFIXES = frozenset("0" + p for p in _RESTRICTED_PREFIXES)


def check_digit_ok(digits: str) -> bool:
    """True iff `digits` carries a correct GS1 mod-10 check digit.

    Weights alternate 3/1 from the RIGHT, starting at 3 on the digit
    immediately left of the check digit. Applying it from the right makes the
    one implementation correct for all four GTIN lengths (a left-anchored 1/3
    weighting only happens to agree on even-length codes).
    """
    if len(digits) < 2 or not _DIGITS.match(digits):
        return False
    body, check = digits[:-1], digits[-1]
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - (total % 10)) % 10 == int(check)


def _is_restricted(digits: str) -> bool:
    """True when the GS1 prefix is restricted distribution (in-store only).

    GTIN-12/13/14 are read in their one GTIN-13 form -- a UPC-A is a GTIN-13
    with a leading 0, a GTIN-14 is a packaging INDICATOR digit plus a GTIN-13 --
    so one code gets one verdict however it is padded. Restricted there is
    prefix 20-29, and 020-029 (UPC-A number system 2). A GTIN-8 is its own
    numbering: 20-29 at the front.
    """
    if len(digits) == 8:
        return digits[:2] in _RESTRICTED_PREFIXES
    g13 = digits[-13:].zfill(13)
    return g13[:2] in _RESTRICTED_PREFIXES or g13[:3] in _RESTRICTED_UPC_PREFIXES


def normalise_candidate(raw: Any) -> str:
    """Trim and drop internal spaces/hyphens. Never raises; '' for empty/None."""
    if raw is None:
        return ""
    return _SEPARATORS.sub("", str(raw).strip())


def classify_gtin(raw: Any) -> Optional[str]:
    """Why `raw` is not a usable public GTIN, or None when it IS valid.

    None is also returned for an empty/absent value: "no GTIN" is a legitimate
    state (most of the catalog has none), it is simply not something to push.
    Use `is_valid_gtin` when you need "is there a real GTIN here".
    """
    candidate = normalise_candidate(raw)
    if not candidate:
        return None
    if not _DIGITS.match(candidate):
        return REASON_NONNUMERIC
    if set(candidate) == {"0"}:
        return REASON_ZEROS
    if len(candidate) not in VALID_GTIN_LENGTHS:
        return REASON_BADLEN
    if _is_restricted(candidate):
        # GS1 20-29 is restricted distribution / in-store only -- the range
        # IMS minted its own unit barcodes in before 2026-09-28 -- so such a
        # code is either somebody's shelf label or our own internal barcode
        # leaking into the public field. Never publish it.
        return REASON_RESTRICTED
    if not check_digit_ok(candidate):
        return REASON_BADCHECK
    return None


def is_valid_gtin(raw: Any) -> bool:
    """True only for a real, publishable GTIN. Empty -> False."""
    return bool(normalise_candidate(raw)) and classify_gtin(raw) is None


def sanitise_gtin(raw: Any) -> Optional[str]:
    """The normalised GTIN when `raw` is publishable, else None.

    This is the function every write/push door should use: it turns "anything
    the caller handed us" into "a GTIN we are willing to put in front of a
    customer, or nothing at all".
    """
    if not is_valid_gtin(raw):
        return None
    return normalise_candidate(raw)


def gtin_spellings(raw: Any) -> List[str]:
    """Every stored spelling of ONE GTIN, for an exact-match lookup.

    GS1 reads every GTIN right-aligned in 14 digits, so the UPC-A
    036000291452, 0036000291452 and 00036000291452 are one code: two products
    holding them would reach Shopify/Google as the same item. A non-numeric or
    over-long value is only itself; '' / None -> [].
    """
    code = normalise_candidate(raw)
    if not code:
        return []
    if not _DIGITS.match(code):
        return [code]
    g14 = code.zfill(14)
    return sorted(
        {code} | {g14[-n:] for n in VALID_GTIN_LENGTHS if not g14[:-n].strip("0")}
    )
