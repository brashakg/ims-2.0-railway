"""
IMS 2.0 - Hub Phase 3: vendor price-list import (Excel / CSV / PDF)
==================================================================
The owner mostly receives vendor price lists as PDFs, often with a SKU dialect
that differs from the IMS master only by punctuation / leading zeros
(owner example: IMS "RB 3025 001/21" vs vendor "0RB3025001/21"). This service:

  1. PARSES a price list -- CSV + Excel directly, every PDF via the AI extractor
     (owner DECISION B: AI for EVERY pdf), into a list of raw row dicts.
  2. MATCHES each row's vendor SKU against the catalogued products + the
     `vendor_sku_aliases` flywheel, classifying it MATCHED / SUGGESTED / NEW
     (fuzzy: case + whitespace + separator fold + leading-zero strip + a
     similarity score).
  3. MAPS a row into the canonical product-create payload (as_draft=True) so a
     confirmed NEW row lands as a catalog_status=DRAFT for human review -- never
     a direct-commit.

This module is PURE + deterministic for the match/map core (the high-value part);
the file parsers are thin I/O wrappers and the PDF path is fail-soft when no
Anthropic key is configured. No emoji (Windows cp1252). No DB writes here -- the
router persists via the product_master spine + the alias collection.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ims.catalog_import")

# Row match classes (frozen interface -- the FE review grid keys on these).
MATCH_MATCHED = "MATCHED"  # exact alias hit, or normalized-exact SKU match
MATCH_SUGGESTED = "SUGGESTED"  # fuzzy match above threshold -- staff one-click confirm
MATCH_NEW = "NEW"  # no confident match -> a new DRAFT product

# Default fuzzy-accept threshold for SUGGESTED (a normalized-exact match is
# always MATCHED regardless). Tuned so "RB3025" vs "RB3026" (one char) does NOT
# auto-suggest a wrong product but punctuation/spacing variants do.
DEFAULT_SUGGEST_THRESHOLD = 0.86


# ---------------------------------------------------------------------------
# SKU normalisation + similarity (PURE)
# ---------------------------------------------------------------------------


def normalize_sku(value: Any) -> str:
    """Fold a SKU to its comparison form so vendor dialects collide with IMS:
    uppercase, strip every non-alphanumeric (space / slash / dash / dot), then
    strip a leading run of zeros. So "RB 3025 001/21", "0RB3025001/21" and
    "rb-3025-001-21" all fold to "RB302500121". Blank -> "".
    """
    if value is None:
        return ""
    s = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    # Strip leading zeros ONLY for an alpha-prefixed code (the owner's case:
    # "0RB3025..." -> "RB3025..."). A purely-numeric code keeps its leading zeros
    # so "001" and "1" stay DISTINCT (they are different items, not a dialect of
    # the same one).
    if any(c.isalpha() for c in s):
        s = s.lstrip("0")
    return s


def sku_similarity(a: Any, b: Any) -> float:
    """Similarity (0..1) between two SKUs on their normalized forms. Two SKUs
    that normalize identically score 1.0. Pure; never raises."""
    na, nb = normalize_sku(a), normalize_sku(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


# ---------------------------------------------------------------------------
# Row classification against aliases + catalogued products (PURE)
# ---------------------------------------------------------------------------


def classify_vendor_sku(
    vendor_sku: Any,
    *,
    alias_index: Dict[str, str],
    products: List[Dict[str, Any]],
    threshold: float = DEFAULT_SUGGEST_THRESHOLD,
) -> Dict[str, Any]:
    """Classify one vendor SKU against the flywheel + catalogued products.

    `alias_index` maps a NORMALIZED vendor_sku -> product_id (the learned
    flywheel for this vendor). `products` is the candidate spine list (each a
    dict with at least `product_id` and `sku`). Returns:
        {status, product_id, score, candidate_sku}
    where status is MATCHED (alias or normalized-exact), SUGGESTED (best fuzzy
    >= threshold), or NEW (nothing confident). Pure + deterministic: ties break
    on the lexicographically smallest product_id so re-runs are stable.
    """
    norm = normalize_sku(vendor_sku)
    if not norm:
        return {
            "status": MATCH_NEW,
            "product_id": None,
            "score": 0.0,
            "candidate_sku": None,
        }

    # 1) Flywheel: a learned alias is an exact, confident hit.
    if norm in alias_index:
        return {
            "status": MATCH_MATCHED,
            "product_id": alias_index[norm],
            "score": 1.0,
            "candidate_sku": None,
        }

    # 2) Normalized-exact against a catalogued product SKU -> MATCHED.
    # 3) else track the best fuzzy candidate for a SUGGESTED (scalar vars, not a
    #    subscripted Optional tuple).
    best_score = -1.0
    best_pid: Optional[str] = None
    best_sku: Any = None
    for p in products or []:
        pid = p.get("product_id")
        psku = p.get("sku")
        if not pid or not psku:
            continue
        pnorm = normalize_sku(psku)
        if not pnorm:
            continue
        if pnorm == norm:
            return {
                "status": MATCH_MATCHED,
                "product_id": pid,
                "score": 1.0,
                "candidate_sku": psku,
            }
        score = SequenceMatcher(None, norm, pnorm).ratio()
        # strictly-greater wins; tie -> smaller product_id (stable re-runs)
        if score > best_score or (
            score == best_score and best_pid is not None and str(pid) < str(best_pid)
        ):
            best_score, best_pid, best_sku = score, pid, psku

    if best_pid is not None and best_score >= threshold:
        return {
            "status": MATCH_SUGGESTED,
            "product_id": best_pid,
            "score": round(best_score, 4),
            "candidate_sku": best_sku,
        }
    return {
        "status": MATCH_NEW,
        "product_id": None,
        "score": round(best_score, 4) if best_pid is not None else 0.0,
        "candidate_sku": best_sku,
    }


def build_alias_index(alias_docs: List[Dict[str, Any]]) -> Dict[str, str]:
    """Build {normalized_vendor_sku -> product_id} from vendor_sku_aliases docs.
    Later docs win on a normalized collision (most-recent learned alias)."""
    index: Dict[str, str] = {}
    for d in alias_docs or []:
        vs = d.get("vendor_sku")
        pid = d.get("product_id")
        if vs and pid:
            index[normalize_sku(vs)] = pid
    return index


# ---------------------------------------------------------------------------
# Column mapping + row -> canonical product payload (PURE)
# ---------------------------------------------------------------------------

# Canonical field -> the vendor header aliases we auto-detect (lowercased).
_HEADER_HINTS: Dict[str, Tuple[str, ...]] = {
    "sku": ("sku", "code", "item code", "item_code", "article", "model code", "style"),
    "brand_name": ("brand", "make", "company"),
    "model_no": ("model", "model no", "model_no", "style no", "ref"),
    "colour_code": ("colour", "color", "colour code", "color code", "shade"),
    "category": ("category", "type", "product type"),
    "mrp": ("mrp", "list price", "rrp", "retail"),
    "offer_price": ("offer", "offer price", "selling price", "sp"),
    "cost_price": (
        "cost",
        "cost price",
        "rate",
        "net",
        "purchase price",
        "wsp",
        "dealer price",
    ),
    "hsn_code": ("hsn", "hsn code", "hsn_code"),
    "product_name": ("name", "description", "product", "particulars", "item"),
}


def guess_column_map(headers: List[str]) -> Dict[str, str]:
    """Best-effort {canonical_field -> actual_header} from a row's headers.
    Exact (case-insensitive) header-hint match wins; first hint that matches a
    header is taken. Headers not recognised are simply left unmapped."""
    lower = {str(h).strip().lower(): h for h in headers if h is not None}
    mapping: Dict[str, str] = {}
    for field, hints in _HEADER_HINTS.items():
        for hint in hints:
            if hint in lower:
                mapping[field] = lower[hint]
                break
    return mapping


def _to_float(value: Any) -> Optional[float]:
    """Parse a price cell tolerant of a leading currency symbol + thousands
    commas (e.g. "Rs. 7,990.00" -> 7990.0). Strips commas + a leading currency
    prefix, then requires the REMAINDER to be a single clean number (optionally
    scientific). A multi-dot / embedded-junk cell (e.g. "12.34.56") returns None
    -> the field becomes a gap (DRAFT), never a silently-wrong positive price."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "")
    # Find every well-formed number token (optional sign, decimal, scientific).
    # A clean price cell has EXACTLY ONE -- "Rs. 7990.00" -> ["7990.00"], "1.5e3"
    # -> ["1.5e3"]. A multi-dot / multi-number junk cell ("12.34.56", "2 @ 500")
    # yields zero or >1 tokens -> None, so the field becomes a gap (DRAFT) rather
    # than a silently-wrong positive price.
    tokens = re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", s)
    if len(tokens) != 1:
        return None
    try:
        return float(tokens[0])
    except ValueError:
        return None


def map_row_to_product(
    row: Dict[str, Any], column_map: Dict[str, str]
) -> Dict[str, Any]:
    """Map one raw import row into the canonical product-create payload the
    product_master door expects, with as_draft=True so an incomplete row lands
    as a catalog_status=DRAFT (never a hard 422 / never auto-published).

    Returns the kwargs dict (category/attributes/mrp/offer_price/cost_price/sku/
    as_draft). Missing fields are simply absent -> the done-rule reports them as
    gaps. Pure; never raises.
    """
    get = lambda field: (
        row.get(column_map[field]) if field in column_map else None
    )  # noqa: E731

    attributes: Dict[str, Any] = {}
    for attr in ("brand_name", "model_no", "colour_code"):
        val = get(attr)
        if val is not None and str(val).strip():
            attributes[attr] = str(val).strip()
    name = get("product_name")
    if name is not None and str(name).strip():
        attributes["name"] = str(name).strip()

    payload: Dict[str, Any] = {
        "attributes": attributes,
        "as_draft": True,
    }
    sku = get("sku")
    if sku is not None and str(sku).strip():
        payload["sku"] = str(sku).strip()
    category = get("category")
    if category is not None and str(category).strip():
        payload["category"] = str(category).strip()
    for money in ("mrp", "offer_price", "cost_price"):
        v = _to_float(get(money))
        if v is not None:
            payload[money] = v
    hsn = get("hsn_code")
    if hsn is not None and str(hsn).strip():
        payload["hsn_code"] = str(hsn).strip()
    return payload


# ---------------------------------------------------------------------------
# Parsers: CSV (stdlib), Excel (openpyxl, lazy), PDF (AI, fail-soft)
# ---------------------------------------------------------------------------


def parse_csv(text: str) -> List[Dict[str, Any]]:
    """Parse CSV text into a list of row dicts keyed on the header row."""
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in reader]


def parse_xlsx(content: bytes) -> List[Dict[str, Any]]:
    """Parse the first sheet of an .xlsx into row dicts keyed on the header row.
    Requires openpyxl; raises ImportError (caller maps to a clear 400) when the
    optional dependency is unavailable."""
    import openpyxl  # lazy: optional dependency

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    try:
        headers = [str(h).strip() if h is not None else "" for h in next(rows)]
    except StopIteration:
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        if r is None or all(c is None for c in r):
            continue
        out.append({headers[i]: r[i] for i in range(min(len(headers), len(r)))})
    return out


_PDF_EXTRACT_SYSTEM = (
    "You are extracting a vendor optical price list into structured rows. "
    'Return a JSON object {"rows": [...]} where each row is an object with any of '
    "these keys WHEN PRESENT: sku, brand, model, colour, category, mrp, "
    "offer_price, cost, hsn, name. Use the vendor's own SKU/code verbatim in sku. "
    "Numbers must be plain (no currency symbols or commas). Omit a key if absent. "
    "Do not invent rows."
)


def pdf_rows_to_import_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """AI rows already use canonical-ish header names; pass through dict rows."""
    return [r for r in (rows or []) if isinstance(r, dict)]


async def parse_pdf_via_ai(text: str) -> List[Dict[str, Any]]:
    """Extract price-list rows from PDF-derived text via the Anthropic extractor
    (owner DECISION B: AI for every PDF). Fail-soft: returns [] when no key is
    configured or the call fails, so the caller reports 'AI unavailable' rather
    than crash. The PDF->text step (the upload) is done by the caller."""
    if not text or not text.strip():
        return []
    try:
        from agents.claude_client import call_claude_json, is_claude_available

        if not is_claude_available():
            return []
        # A price list of 100-500 rows blows past the agent's 1024-token default
        # and would silently truncate the JSON (importing only a subset). Ask for
        # a large budget so a normal list extracts whole.
        data = await call_claude_json(_PDF_EXTRACT_SYSTEM, text, max_tokens=8000)
    except Exception as exc:  # noqa: BLE001 - import must never crash
        logger.warning("[IMPORT] PDF AI extract failed: %s", exc)
        return []
    if not isinstance(data, dict):
        return []
    rows = data.get("rows")
    return pdf_rows_to_import_rows(rows) if isinstance(rows, list) else []
