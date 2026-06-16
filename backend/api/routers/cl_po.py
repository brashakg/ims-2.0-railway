"""
IMS 2.0 - N7 Contact-lens / lens Purchase-Order generator router
=================================================================
Prefix /api/v1/cl-po (mounted in main.py).

ONE main route: POST /generate. Reads per-power replenishment needs from the
SAME data the existing read endpoints serve --

  * source="replenishment": Base-Bank needs (item_events GET /items/replenishment;
    grids READERS / CL_POWER / CL_COLOUR / PLANOGRAM)
  * source="gap-planner":   lens-stock cells where available < reorder_point
    (lens_stock GET /gap-planner)

-- and drafts vendor-grouped DRAFT purchase orders whose LINES carry the power
cell (power: {sph, cyl, add} + quantity), so a supplier receives an exact
power-grid order.

Safety contract:
  * dry_run=True is the DEFAULT -- returns the grouped draft WITHOUT writing.
  * dry_run=False creates one DRAFT PO per vendor group via the EXISTING
    purchase-order repository (reuses vendors.generate_po_number). Status is
    always DRAFT, never SENT -- a human sends from the PO screen.
  * Needs whose item has no preferred vendor draft under vendor_id=None
    (frontend disables send until a vendor is picked).
  * Roles: STORE_MANAGER / AREA_MANAGER / ADMIN / SUPERADMIN, store-scoped via
    validate_store_access.
  * No money path: this never touches POS, payments or stock counts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import require_roles
from .vendors import generate_po_number
from ..dependencies import (
    get_audit_repository,
    get_purchase_order_repository,
    get_vendor_repository,
    validate_store_access,
)
from ..services import cl_po_generator as gen
from ..services.lens_catalog_validation import compute_available

logger = logging.getLogger(__name__)
router = APIRouter()

# Drafting a vendor PO is a manager-ladder decision (store-scoped through
# validate_store_access). SUPERADMIN auto-passes inside require_roles.
_PO_ROLES = ("STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN")

_SOURCES = {"gap-planner", "replenishment"}

# Power grids whose cell_key is a dioptre (drives sph extraction). Mirrors
# item_events._GRIDS power handling.
_POWER_GRIDS = {"READERS", "CL_POWER"}


def _get_db():
    """Raw MongoDB database. Same pattern as item_events._get_db."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and conn.is_connected:
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


# ============================================================================
# Request model
# ============================================================================


class CLPOGenerateRequest(BaseModel):
    """POST /cl-po/generate body."""

    store_id: str
    source: str = Field(..., description='"gap-planner" or "replenishment"')
    # Required when source="replenishment" (READERS/CL_POWER/CL_COLOUR/PLANOGRAM).
    grid: Optional[str] = None
    # Optional gap-planner filter: draft for a single lens line only.
    lens_line_id: Optional[str] = None
    # SAFE DEFAULT: preview the grouped draft without writing anything.
    dry_run: bool = True


# ============================================================================
# Need readers -- same data the existing endpoints read
# ============================================================================


def _read_gap_planner_needs(
    store_id: str, lens_line_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Cells where (on_hand - reserved) < reorder_point, as need rows.

    Reads `lens_stock_lines` exactly like lens_stock.gap_planner (reuses its
    collection helpers so there is ONE data path). Fail-soft []: a missing DB
    must never 500 a draft preview."""
    from . import lens_stock as lens_stock_router

    coll = lens_stock_router._stock_coll()
    if coll is None:
        return []
    query: Dict[str, Any] = {"store_id": store_id}
    if lens_line_id:
        query["lens_line_id"] = lens_line_id
    try:
        docs = list(coll.find(query))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CL_PO] gap-planner read failed: %s", exc)
        return []

    line_cache: Dict[str, Dict[str, Any]] = {}

    def _line_doc(llid: str) -> Dict[str, Any]:
        if llid not in line_cache:
            try:
                line_cache[llid] = lens_stock_router._load_lens_line(llid) or {}
            except Exception:  # noqa: BLE001
                line_cache[llid] = {}
        return line_cache[llid]

    needs: List[Dict[str, Any]] = []
    for d in docs:
        avail = compute_available(d.get("on_hand", 0), d.get("reserved", 0))
        rp = int(d.get("reorder_point") or 0)
        if rp <= 0 or avail >= rp:
            continue
        llid = str(d.get("lens_line_id") or "")
        line = _line_doc(llid)
        desc = " ".join(
            s
            for s in (
                str(line.get("brand") or "").strip(),
                str(line.get("series") or "").strip(),
            )
            if s
        ) or llid
        needs.append(
            {
                "lens_line_id": llid,
                "sph": d.get("sph"),
                "cyl": d.get("cyl"),
                "add": d.get("add"),
                "qty": rp - avail,
                "description": desc,
                "unit_price": line.get("cost_price") or 0.0,
            }
        )
    return needs


def _read_replenishment_needs(store_id: str, grid: Optional[str]) -> List[Dict[str, Any]]:
    """Base-Bank needs (required = base_bank - in_hand) as need rows.

    Reuses the item_events router's resolution helpers (_resolve_target,
    _count_on_hand_at_cell + services.item_events.build_replenishment) so the
    draft reads the SAME cells GET /items/replenishment serves. For power grids
    the cell_key IS the dioptre -> sph; colour/planogram cells carry no power
    and keep the cell label in the description. Fail-soft []."""
    from . import item_events as item_events_router
    from ..services import item_events as ie

    g = (grid or "").strip().upper()
    if g not in item_events_router._GRIDS:
        raise HTTPException(
            status_code=422,
            detail=f"grid must be one of {sorted(item_events_router._GRIDS)}",
        )

    db = item_events_router._get_db()
    if db is None:
        return []

    cell_keys = set()
    try:
        for row in db.get_collection("base_bank_targets").find(
            {
                "grid": g,
                "$or": [
                    {"scope": "STORE", "store_id": store_id},
                    {"scope": "GLOBAL"},
                ],
            }
        ):
            if row.get("cell_key"):
                cell_keys.add(row["cell_key"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CL_PO] base-bank read failed: %s", exc)
        return []

    item_cache: Dict[str, str] = {}

    def _describe_item(pid: Optional[str]) -> str:
        """Lens line (brand + series) or product name for a product_line_id."""
        if not pid:
            return ""
        if pid in item_cache:
            return item_cache[pid]
        name = ""
        try:
            line = db.get_collection("lens_catalog").find_one({"lens_line_id": pid})
            if line:
                name = " ".join(
                    s
                    for s in (
                        str(line.get("brand") or "").strip(),
                        str(line.get("series") or "").strip(),
                    )
                    if s
                )
            if not name:
                prod = db.get_collection("products").find_one({"product_id": pid})
                if prod:
                    name = str(prod.get("name") or "").strip()
        except Exception:  # noqa: BLE001
            name = ""
        item_cache[pid] = name or str(pid)
        return item_cache[pid]

    needs: List[Dict[str, Any]] = []
    for cell_key in sorted(cell_keys):
        target = item_events_router._resolve_target(db, store_id, g, cell_key) or {}
        base = int(target.get("base_bank", 0) or 0)
        in_hand = item_events_router._count_on_hand_at_cell(
            db, store_id, g, cell_key, target.get("product_line_id")
        )
        rows = ie.build_replenishment(
            [{"cell_key": cell_key, "base_bank": base, "in_hand": in_hand}]
        )
        required = int((rows[0] if rows else {}).get("required", 0) or 0)
        if required <= 0:
            continue

        sph: Optional[float] = None
        if g in _POWER_GRIDS:
            try:
                sph = float(cell_key)
            except (TypeError, ValueError):
                sph = None

        pid = target.get("product_line_id")
        desc = _describe_item(pid)
        if sph is None:
            # Non-power cell (colour / planogram slot): keep the cell label so
            # the vendor still sees WHICH variant to ship.
            label = f"{g} {cell_key}"
            desc = f"{desc} [{label}]" if desc else label
        needs.append(
            {
                "product_id": pid,
                "sph": sph,
                "qty": required,
                "description": desc,
                "cell_key": cell_key,
            }
        )
    return needs


# ============================================================================
# Vendor resolution + audit
# ============================================================================


def _make_vendor_resolver(db):
    """need -> preferred vendor_id or None.

    Looks at the lens line (lens_catalog) first, then the product doc's
    preferred_vendor_id (the same field /purchase-orders/from-forecast groups
    by). Cached per item; fail-soft None."""
    cache: Dict[str, Optional[str]] = {}

    def _resolver(need: Dict[str, Any]) -> Optional[str]:
        key = need.get("lens_line_id") or need.get("product_id")
        if not key:
            return None
        key = str(key)
        if key in cache:
            return cache[key]
        vendor_id: Optional[str] = None
        if db is not None:
            try:
                line = db.get_collection("lens_catalog").find_one({"lens_line_id": key})
                if line:
                    vendor_id = line.get("preferred_vendor_id")
                if not vendor_id:
                    prod = db.get_collection("products").find_one({"product_id": key})
                    if prod:
                        vendor_id = prod.get("preferred_vendor_id")
            except Exception:  # noqa: BLE001
                vendor_id = None
        cache[key] = vendor_id or None
        return cache[key]

    return _resolver


def _audit(action: str, *, entity_id: str, actor: dict, store_id: str, detail: dict) -> None:
    """One audit row on non-dry-run creation. Fail-soft: an audit hiccup must
    never undo / 500 a draft that was already created."""
    try:
        repo = get_audit_repository()
        if repo is None:
            return
        repo.create(
            {
                "action": action,
                "entity_type": "purchase_order",
                "entity_id": entity_id,
                "store_id": store_id or actor.get("active_store_id"),
                "user_id": actor.get("user_id"),
                "severity": "INFO",
                "source": "cl_po_generator",
                "detail": detail or {},
            }
        )
    except Exception:  # noqa: BLE001
        return


# ============================================================================
# Endpoint
# ============================================================================


@router.post("/generate")
async def generate_cl_po(
    body: CLPOGenerateRequest,
    current_user: dict = Depends(require_roles(*_PO_ROLES)),
):
    """Draft vendor-grouped per-power purchase order(s) from replenishment needs.

    dry_run=True (default) returns the grouped draft WITHOUT writing. With
    dry_run=False, one DRAFT PO is created per vendor group (vendor_id may be
    null -- the FE disables send until a vendor is assigned). Never SENT."""
    store_id = validate_store_access(body.store_id, current_user)
    if not store_id:
        raise HTTPException(status_code=422, detail="store_id is required")

    source = (body.source or "").strip().lower()
    if source not in _SOURCES:
        raise HTTPException(
            status_code=422, detail=f"source must be one of {sorted(_SOURCES)}"
        )

    if source == "replenishment":
        needs = _read_replenishment_needs(store_id, body.grid)
    else:
        needs = _read_gap_planner_needs(store_id, body.lens_line_id)

    db = _get_db()
    groups = gen.group_needs_by_vendor(needs, _make_vendor_resolver(db))

    vendor_repo = get_vendor_repository()
    group_summaries: List[Dict[str, Any]] = []
    for vendor_id, lines in groups.items():
        vendor_name = None
        if vendor_id and vendor_repo is not None:
            try:
                vendor = vendor_repo.find_by_id(vendor_id)
                if vendor:
                    vendor_name = vendor.get("trade_name") or vendor.get("legal_name")
            except Exception:  # noqa: BLE001
                vendor_name = None
        subtotal = round(
            sum(ln["quantity"] * float(ln.get("unit_price") or 0) for ln in lines), 2
        )
        group_summaries.append(
            {
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "line_count": len(lines),
                "total_quantity": sum(ln["quantity"] for ln in lines),
                "subtotal": subtotal,
                "lines": lines,
            }
        )

    base_response = {
        "store_id": store_id,
        "source": source,
        "grid": (body.grid or "").strip().upper() if body.grid else None,
        "dry_run": body.dry_run,
        "groups": group_summaries,
        "total_lines": sum(g["line_count"] for g in group_summaries),
    }

    if body.dry_run:
        return {**base_response, "pos_created": 0, "created_pos": []}

    po_repo = get_purchase_order_repository()
    if po_repo is None:
        # DB-absent fail-soft: report clearly that NOTHING was written.
        return {
            **base_response,
            "pos_created": 0,
            "created_pos": [],
            "message": "Purchase-order storage unavailable; nothing was written",
        }

    created_pos: List[Dict[str, Any]] = []
    now_iso = datetime.now().isoformat()
    for idx, grp in enumerate(group_summaries):
        po_id = str(uuid.uuid4())
        # generate_po_number now allocates an ATOMIC per-store/FY serial (S5), so
        # each call in this loop already returns a distinct number -- no manual
        # suffix needed (the old minute-grained format could collide within one
        # call, which the -{idx} suffix used to guard).
        po_number = generate_po_number(store_id)
        subtotal = grp["subtotal"]
        tax = round(subtotal * 0.18, 2)
        po_doc = {
            "po_id": po_id,
            "po_number": po_number,
            "vendor_id": grp["vendor_id"],  # may be None -> FE disables send
            "vendor_name": grp["vendor_name"],
            "delivery_store_id": store_id,
            "items": grp["lines"],
            "subtotal": subtotal,
            "tax_amount": tax,
            "total_amount": round(subtotal + tax, 2),
            "status": "DRAFT",
            "source": "cl_po_generator",
            "source_detail": source,
            "grid": base_response["grid"],
            "created_by": current_user.get("user_id"),
            "created_at": now_iso,
            "notes": f"Auto-drafted per-power CL/lens PO from {source}",
        }
        try:
            po_repo.create(po_doc)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[CL_PO] PO create failed for vendor %s: %s", grp["vendor_id"], exc
            )
            continue
        created_pos.append(
            {
                "po_id": po_id,
                "po_number": po_number,
                "vendor_id": grp["vendor_id"],
                "vendor_name": grp["vendor_name"],
                "lines": grp["line_count"],
                "total_amount": po_doc["total_amount"],
            }
        )

    if created_pos:
        _audit(
            "CL_PO_GENERATED",
            entity_id=",".join(p["po_id"] for p in created_pos),
            actor=current_user,
            store_id=store_id,
            detail={
                "source": source,
                "grid": base_response["grid"],
                "pos_created": len(created_pos),
                "total_lines": base_response["total_lines"],
                "po_numbers": [p["po_number"] for p in created_pos],
            },
        )

    return {**base_response, "pos_created": len(created_pos), "created_pos": created_pos}
