"""
IMS 2.0 - Hub Phase 3: vendor price-list import router.

Two endpoints (CATALOG_MANAGER / ADMIN / SUPERADMIN only):

  POST /catalog-import/preview  -- parse an uploaded price list (CSV / Excel /
        PDF), classify each row's vendor SKU MATCHED/SUGGESTED/NEW against the
        catalogued products + the vendor_sku_aliases flywheel, and map each row
        to the canonical as_draft product payload. NO writes -- a dry preview the
        FE review grid renders.

  POST /catalog-import/commit   -- persist the human-reviewed decisions: CREATE
        lands a new catalog_status=DRAFT product via the product_master spine
        door (and teaches the flywheel the vendor SKU); LINK only writes the
        vendor_sku_alias; SKIP ignores. Never auto-publishes, never direct-commits
        a complete product (everything lands DRAFT for review).

Owner DECISION B: every PDF is extracted by the AI (claude_client), fail-soft.
No emoji (Windows cp1252).
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import require_roles
from ..dependencies import get_product_repository
from ..services import catalog_import as _ci
from ..services import product_master as _pm

router = APIRouter()
logger = logging.getLogger("ims.catalog_import_router")

# CATALOG_MANAGER / ADMIN (+ SUPERADMIN auto-passes) -- mirrors products._CATALOG_ROLES.
_CATALOG_ROLES = ("ADMIN", "CATALOG_MANAGER")

_ALIAS_COLLECTION = "vendor_sku_aliases"


def _get_db():
    from database.connection import get_db

    return get_db().db


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ImportPreviewRequest(BaseModel):
    vendor_id: str
    # csv / pdf -> `content` is text; xlsx -> `content` is base64 of the .xlsx.
    format: str = Field(..., pattern="^(csv|xlsx|pdf)$")
    content: str
    # Optional explicit canonical-field -> vendor-header map; auto-detected when omitted.
    column_map: Optional[Dict[str, str]] = None


class ImportCommitRow(BaseModel):
    action: str = Field(..., pattern="^(CREATE|LINK|SKIP)$")
    vendor_sku: Optional[str] = None
    product_id: Optional[str] = None  # required for LINK; set on the alias after CREATE
    payload: Optional[Dict[str, Any]] = None  # canonical as_draft payload for CREATE


class ImportCommitRequest(BaseModel):
    vendor_id: str
    rows: List[ImportCommitRow] = Field(..., min_length=1, max_length=1000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_alias_index(db, vendor_id: str) -> Dict[str, str]:
    if db is None:
        return {}
    try:
        docs = list(db.get_collection(_ALIAS_COLLECTION).find({"vendor_id": vendor_id}))
        return _ci.build_alias_index(docs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[IMPORT] alias load failed: %s", exc)
        return {}


def _load_candidate_products(db) -> List[Dict[str, Any]]:
    """Active catalogued spine products (product_id + sku only) for fuzzy
    matching. Filters is_active so a vendor SKU never MATCHES a soft-deleted
    product (soft_delete leaves the doc + its sku)."""
    if db is None:
        return []
    try:
        cur = db.get_collection("products").find(
            {"is_active": {"$ne": False}}, {"product_id": 1, "sku": 1, "_id": 0}
        )
        return [d for d in cur if d.get("product_id") and d.get("sku")]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[IMPORT] product load failed: %s", exc)
        return []


def _write_alias(db, vendor_id: str, vendor_sku: str, product_id: str) -> bool:
    """Teach the flywheel a vendor_sku -> product_id mapping (idempotent upsert).
    Returns True on a successful write so the caller can report a failed LINK as
    an error rather than miscounting it as linked."""
    if db is None or not vendor_sku or not product_id:
        return False
    try:
        db.get_collection(_ALIAS_COLLECTION).update_one(
            {"vendor_id": vendor_id, "vendor_sku": vendor_sku},
            {"$set": {"product_id": product_id, "source": "IMPORT"}},
            upsert=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[IMPORT] alias write failed for %s: %s", vendor_sku, exc)
        return False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/preview")
async def preview_import(
    body: ImportPreviewRequest,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Parse + classify + map a vendor price list. No writes."""
    # 1) parse rows by format
    if body.format == "csv":
        try:
            rows = _ci.parse_csv(body.content)
        except Exception as exc:  # noqa: BLE001 - fail-soft: never 500 on bad input
            raise HTTPException(status_code=400, detail=f"Could not parse CSV: {exc}")
    elif body.format == "xlsx":
        try:
            raw = base64.b64decode(body.content)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400, detail=f"Invalid base64 content: {exc}"
            )
        try:
            rows = _ci.parse_xlsx(raw)
        except ImportError:
            raise HTTPException(
                status_code=400,
                detail="Excel import unavailable on this server (openpyxl not installed). "
                "Use CSV, or paste the rows.",
            )
    else:  # pdf -> AI (DECISION B)
        rows = await _ci.parse_pdf_via_ai(body.content)
        if not rows:
            raise HTTPException(
                status_code=400,
                detail="Could not extract rows from this PDF (AI extraction "
                "unavailable or returned nothing). Check the Anthropic key, or "
                "import as CSV/Excel.",
            )

    if not rows:
        return {"vendor_id": body.vendor_id, "rows": [], "total": 0, "column_map": {}}

    # 2) column map (explicit or auto-detected from the first row's headers)
    column_map = body.column_map or _ci.guess_column_map(list(rows[0].keys()))

    # 3) load the flywheel + candidate products
    db = _get_db()
    alias_index = _load_alias_index(db, body.vendor_id)
    products = _load_candidate_products(db)

    # 4) classify + map each row (no writes)
    sku_header = column_map.get("sku")
    out: List[Dict[str, Any]] = []
    counts = {_ci.MATCH_MATCHED: 0, _ci.MATCH_SUGGESTED: 0, _ci.MATCH_NEW: 0}
    for row in rows:
        vendor_sku = row.get(sku_header) if sku_header else None
        match = _ci.classify_vendor_sku(
            vendor_sku, alias_index=alias_index, products=products
        )
        counts[match["status"]] = counts.get(match["status"], 0) + 1
        out.append(
            {
                "vendor_sku": vendor_sku,
                "match": match,
                "payload": _ci.map_row_to_product(row, column_map),
                "raw": row,
            }
        )

    return {
        "vendor_id": body.vendor_id,
        "rows": out,
        "total": len(out),
        "counts": counts,
        "column_map": column_map,
    }


@router.post("/commit")
async def commit_import(
    body: ImportCommitRequest,
    current_user: dict = Depends(require_roles(*_CATALOG_ROLES)),
):
    """Persist reviewed decisions: CREATE -> DRAFT product (+ teach alias);
    LINK -> alias only; SKIP -> ignore. Per-row errors are collected, never abort
    the batch."""
    repo = get_product_repository()
    db = _get_db()
    actor = current_user.get("user_id")

    created: List[Dict[str, str]] = []
    linked = 0
    skipped = 0
    errors: List[Dict[str, Any]] = []

    for idx, row in enumerate(body.rows):
        if row.action == "SKIP":
            skipped += 1
            continue
        if row.action == "LINK":
            if not (row.product_id and row.vendor_sku):
                errors.append(
                    {"index": idx, "error": "LINK needs product_id + vendor_sku"}
                )
                continue
            # Validate the target product EXISTS on the spine before teaching the
            # flywheel -- a typo'd/stale/fabricated product_id would otherwise be
            # written as a max-confidence alias and auto-MATCH (score 1.0) on every
            # future import, flowing wrong stock/cost into PO/GRN. Fail-soft: if no
            # repo to verify, fall through (the alias is still corrigible).
            if repo is not None and repo.find_by_id(row.product_id) is None:
                errors.append(
                    {
                        "index": idx,
                        "error": "LINK product_id not found on the catalog spine",
                    }
                )
                continue
            if _write_alias(db, body.vendor_id, row.vendor_sku, row.product_id):
                linked += 1
            else:
                errors.append({"index": idx, "error": "alias write failed"})
            continue
        # CREATE -> a new DRAFT product via the spine door, then teach the alias.
        payload = dict(row.payload or {})
        payload["as_draft"] = True  # imports ALWAYS land DRAFT (never direct-commit)
        try:
            doc = _pm.create_via_door(
                payload, source="IMPORT", actor=actor, product_repo=repo, db=db
            )
        except _pm.ProductMasterError as err:
            errors.append({"index": idx, "error": err.message, "field": err.field})
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append({"index": idx, "error": str(exc)})
            continue
        new_pid = (doc or {}).get("product_id")
        # Hub Phase 3 DRAFT FLOOR: as_draft only relaxes the strict 422 -- a
        # COMPLETE imported payload would otherwise stamp catalog_status=ACTIVE and
        # be immediately sellable, never reviewed. Force DRAFT for every IMPORT so
        # a person must open + activate it (an edit re-stamps it ACTIVE). Honours
        # the router contract: imports never auto-publish.
        if (
            new_pid
            and repo is not None
            and (doc or {}).get("catalog_status") != _pm.CATALOG_STATUS_DRAFT
        ):
            try:
                repo.update(new_pid, {"catalog_status": _pm.CATALOG_STATUS_DRAFT})
                if isinstance(doc, dict):
                    doc["catalog_status"] = _pm.CATALOG_STATUS_DRAFT
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[IMPORT] DRAFT re-stamp failed for %s: %s", new_pid, exc
                )
        created.append({"index": str(idx), "product_id": new_pid or ""})
        if new_pid and row.vendor_sku:
            _write_alias(db, body.vendor_id, row.vendor_sku, new_pid)

    return {
        "vendor_id": body.vendor_id,
        "created": len(created),
        "created_products": created,
        "linked": linked,
        "skipped": skipped,
        "errors": errors,
    }
