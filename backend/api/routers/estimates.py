"""
IMS 2.0 - Estimates / Quotations Router
=======================================

A lightweight, NON-BINDING estimate/quotation document. Distinct from a sales
order: it reserves NO stock, allocates NO invoice serial, and charges NO GST
against itself. It exists so staff can hand a customer a priced quote (with an
estimated GST breakup + validity date) before the customer commits.

It REUSES the order pricing/GST math: each line's taxable + tax is computed by
api.routers.orders._compute_per_category_gst (the same per-rate engine POS
bills with, honouring the GST_PRICING_MODE flag), so an estimate total equals
what the order would bill -- without any of the order's stock / serial / ledger
side effects.

The render endpoint produces a self-contained HTML page via
api.services.print_render.render_estimate (reusing print_legal primitives) and
feeds the same shape the frontend EstimateQuotationPrint.tsx already expects.

Collection: `estimates`.

Routes (mounted at /api/v1/estimates):
  POST   /api/v1/estimates                 [SALES_STAFF/CASHIER/SM/ADMIN/SUPERADMIN]
  GET    /api/v1/estimates                 list (store-scoped)
  GET    /api/v1/estimates/{estimate_id}   get one
  GET    /api/v1/estimates/{estimate_id}/render   -> text/html

Writes are gated to POS-capable roles + ADMIN/SUPERADMIN.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from .auth import get_current_user
from ..dependencies import validate_store_access
from ..services.print_render import render_estimate

router = APIRouter()

_COLLECTION = "estimates"

# Roles permitted to CREATE an estimate. Mirrors the POS write set + ADMIN tier.
_ESTIMATE_WRITE_ROLES = (
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "SALES_CASHIER",
    "SALES_STAFF",
)


def _get_db():
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _coll():
    db = _get_db()
    return db.get_collection(_COLLECTION) if db is not None else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_write(current_user: dict) -> None:
    roles = current_user.get("roles", []) if current_user else []
    if not any(r in _ESTIMATE_WRITE_ROLES for r in roles):
        raise HTTPException(
            status_code=403,
            detail="Not permitted to create estimates.",
        )


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class EstimateItem(BaseModel):
    description: str = Field(..., min_length=1)
    product_id: Optional[str] = None
    category: Optional[str] = None
    item_type: Optional[str] = None
    hsn_code: Optional[str] = None
    quantity: int = Field(default=1, ge=1)
    mrp: Optional[float] = Field(default=None, ge=0)
    # offer_price is the per-unit price the estimate quotes (defaults to MRP).
    offer_price: float = Field(..., ge=0)
    discount_percent: float = Field(default=0.0, ge=0, le=100)


class EstimateCreate(BaseModel):
    customer_name: str = Field(default="", max_length=200)
    customer_phone: str = Field(default="", max_length=20)
    customer_address: str = Field(default="", max_length=500)
    customer_id: Optional[str] = None
    customer_gstin: Optional[str] = None
    store_id: Optional[str] = None
    items: List[EstimateItem] = Field(..., min_length=1)
    cart_discount_percent: float = Field(default=0.0, ge=0, le=100)
    validity_days: int = Field(default=15, ge=1, le=365)
    valid_until: Optional[str] = None
    terms: str = Field(default="", max_length=2000)
    interstate: bool = False

    @field_validator("items")
    @classmethod
    def _non_empty(cls, v: List[EstimateItem]) -> List[EstimateItem]:
        if not v:
            raise ValueError("At least one line item is required")
        return v


# ---------------------------------------------------------------------------
# Pricing -- reuse the order GST engine so estimate total == billed total
# ---------------------------------------------------------------------------


def _price_estimate(payload: EstimateCreate) -> Dict[str, Any]:
    """Compute per-line taxable + tax and the document totals, REUSING the
    order pricing engine. Returns (priced_items, totals). Never raises on a
    rate lookup -- resolve_gst_rate is fail-soft."""
    from .orders import _compute_per_category_gst

    items: List[Dict[str, Any]] = []
    for it in payload.items:
        qty = max(1, int(it.quantity or 1))
        unit = float(it.offer_price or 0)
        disc_pct = max(0.0, min(100.0, float(it.discount_percent or 0)))
        gross_unit = round(unit * (1.0 - disc_pct / 100.0), 2)
        line_total = round(gross_unit * qty, 2)
        discount_amount = round((unit - gross_unit) * qty, 2)
        items.append(
            {
                "description": it.description,
                "product_id": it.product_id,
                "category": it.category,
                "item_type": it.item_type,
                "hsn_code": it.hsn_code,
                "quantity": qty,
                "qty": qty,
                "mrp": it.mrp,
                "offer_price": unit,
                "discount_percent": disc_pct,
                "discount_amount": discount_amount,
                # _compute_per_category_gst keys off `item_total` (line subtotal
                # after per-item discount) and stamps gst_rate/taxable/tax in place.
                "item_total": line_total,
            }
        )

    summary = _compute_per_category_gst(items, payload.cart_discount_percent or 0.0)

    # Surface line_total alongside the engine-stamped taxable/tax for the renderer.
    for it in items:
        it["line_total"] = it.get("item_total")

    grand_total = round(
        float(summary.get("taxable") or 0) + float(summary.get("tax") or 0), 2
    )
    totals = {
        "subtotal": summary.get("subtotal", 0.0),
        "taxable": summary.get("taxable", 0.0),
        "tax": summary.get("tax", 0.0),
        "cart_discount_amount": summary.get("cart_discount_amount", 0.0),
        "total_discount": summary.get("total_discount", 0.0),
        "dominant_rate": summary.get("dominant_rate", 0.0),
        "pricing_model": summary.get("pricing_model", "inclusive"),
        "grand_total": grand_total,
    }
    return {"items": items, "totals": totals}


def _scrub(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if doc is None:
        return None
    doc.pop("_id", None)
    return doc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("")
@router.post("/")
async def create_estimate(
    payload: EstimateCreate, current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """Create a non-binding estimate. Computes line + document GST estimate
    totals via the order pricing engine. No stock claim, no invoice serial."""
    _require_write(current_user)

    store_id = validate_store_access(payload.store_id, current_user)

    priced = _price_estimate(payload)

    now = datetime.now(timezone.utc)
    if payload.valid_until:
        valid_until = payload.valid_until
    else:
        valid_until = (now + timedelta(days=int(payload.validity_days or 15))).isoformat()

    estimate_id = str(uuid.uuid4())
    estimate_number = "EST-{0}".format(estimate_id[:8].upper())

    doc: Dict[str, Any] = {
        "_id": estimate_id,
        "estimate_id": estimate_id,
        "estimate_number": estimate_number,
        "store_id": store_id,
        "customer_name": payload.customer_name or "",
        "customer_phone": payload.customer_phone or "",
        "customer_address": payload.customer_address or "",
        "customer_id": payload.customer_id,
        "customer_gstin": payload.customer_gstin,
        "interstate": bool(payload.interstate),
        "items": priced["items"],
        "totals": priced["totals"],
        "cart_discount_percent": payload.cart_discount_percent or 0.0,
        "terms": payload.terms or "",
        "valid_until": valid_until,
        "status": "DRAFT",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "created_by": current_user.get("user_id") or current_user.get("username"),
    }

    coll = _coll()
    if coll is not None:
        try:
            coll.insert_one(dict(doc))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=503, detail="Database write failed"
            ) from e

    return _scrub(dict(doc))  # type: ignore[return-value]


@router.get("")
@router.get("/")
async def list_estimates(
    store_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """List estimates. Store-scoped for non-HQ roles."""
    coll = _coll()
    if coll is None:
        return {"estimates": [], "total": 0}

    roles = current_user.get("roles", []) if current_user else []
    is_hq = any(r in ("SUPERADMIN", "ADMIN", "AREA_MANAGER") for r in roles)

    query: Dict[str, Any] = {}
    if store_id:
        # A store-scoped caller may only filter to a store they can access.
        validate_store_access(store_id, current_user)
        query["store_id"] = store_id
    elif not is_hq:
        scope = set(current_user.get("store_ids") or [])
        active = current_user.get("active_store_id")
        if active:
            scope.add(active)
        query["store_id"] = {"$in": list(scope)} if scope else "__none__"

    rows: List[Dict[str, Any]] = []
    try:
        cursor = coll.find(query).sort("created_at", -1).limit(limit)
        for d in cursor:
            scrubbed = _scrub(d)
            if scrubbed is not None:
                rows.append(scrubbed)
    except Exception:  # noqa: BLE001
        rows = []
    return {"estimates": rows, "total": len(rows)}


def _load_estimate(estimate_id: str, current_user: dict) -> Dict[str, Any]:
    coll = _coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        doc = coll.find_one({"estimate_id": estimate_id})
    except Exception:  # noqa: BLE001
        doc = None
    if doc is None:
        raise HTTPException(status_code=404, detail="Estimate not found")
    validate_store_access(doc.get("store_id"), current_user)
    return doc


@router.get("/{estimate_id}")
async def get_estimate(
    estimate_id: str, current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:
    """Fetch one estimate (store-scoped)."""
    doc = _load_estimate(estimate_id, current_user)
    return _scrub(dict(doc))  # type: ignore[return-value]


@router.get("/{estimate_id}/render", response_class=HTMLResponse)
async def render_estimate_html(
    estimate_id: str,
    auto_print: bool = Query(False),
    current_user: dict = Depends(get_current_user),
) -> HTMLResponse:
    """Render the estimate as a self-contained printable HTML page."""
    doc = _load_estimate(estimate_id, current_user)

    store = {}
    entity = {}
    db = _get_db()
    if db is not None:
        try:
            store = db.get_collection("stores").find_one(
                {"store_id": doc.get("store_id")}
            ) or {}
        except Exception:  # noqa: BLE001
            store = {}
        eid = (store or {}).get("entity_id")
        if eid:
            try:
                entity = db.get_collection("entities").find_one(
                    {"entity_id": eid}
                ) or {}
            except Exception:  # noqa: BLE001
                entity = {}

    html = render_estimate(
        entity=entity,
        store=store,
        estimate_number=doc.get("estimate_number") or estimate_id,
        estimate_date=doc.get("created_at"),
        valid_until=doc.get("valid_until"),
        customer_name=doc.get("customer_name") or "",
        customer_phone=doc.get("customer_phone") or "",
        customer_address=doc.get("customer_address") or "",
        items=doc.get("items") or [],
        totals=doc.get("totals") or {},
        interstate=bool(doc.get("interstate")),
        terms=doc.get("terms") or "",
        auto_print=bool(auto_print),
    )
    return HTMLResponse(content=html)
