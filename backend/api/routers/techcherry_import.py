"""
IMS 2.0 — TechCherry one-time data migration endpoint
========================================================
SUPERADMIN-only batch upsert from the legacy TechCherry POS into IMS.

Three entity types, one endpoint:
  POST /api/v1/admin/techcherry/import
    body: { type: "products" | "customers" | "orders",
            store_id: "BV-PUN-01",
            rows: [...],            # batch of records
            overwrite: true }       # if false, skip on dedupe; if true, $set fields

Dedupe keys per type:
  - products  : barcode (fallback: name+brand)
  - customers : phone (10-digit normalised)
  - orders    : invoice_no

Returns counts:
  { inserted: N, updated: N, skipped: N, errors: [...] }

Why a separate router (not in admin.py):
  - admin.py is integration-config heavy (Shopify/Shiprocket/Razorpay).
    Migration writes are operationally a different thing.
  - Easy to remove this router once the one-time TechCherry migration
    is done — drop the file, drop the include in main.py.

SUPERADMIN-only (not just ADMIN) because this writes mass amounts of
business data and can't be undone without a restore.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..services.phone import normalize_indian_mobile

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth gate — strict SUPERADMIN
# ---------------------------------------------------------------------------


async def _require_superadmin(current_user: dict = Depends(get_current_user)) -> dict:
    roles = (current_user or {}).get("roles", []) or []
    if "SUPERADMIN" not in roles:
        raise HTTPException(status_code=404, detail="Not found")
    return current_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_phone(raw: Any) -> str:
    """Normalize a TechCherry phone to the ONE canonical Indian-mobile form via
    api.services.phone.normalize_indian_mobile -- a valid number (with/without
    +91, 0, spaces, dashes) collapses to the bare 10-digit 6-9 form so the
    imported doc dedups + matches against natively-created customers.

    Bulk-import contract preserved: this is best-effort migration, so a legacy
    value that is NOT a valid Indian mobile (landline, junk, foreign) must NOT
    crash the batch row -- normalize_indian_mobile raises ValueError there, so
    we swallow it and return '' (caller skips/keys by name, exactly as before).
    Returns '' when nothing usable is left."""
    try:
        return normalize_indian_mobile(raw) or ""
    except ValueError:
        return ""


def _safe_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _safe_int(v: Any) -> int:
    return int(_safe_float(v))


def _parse_date(v: Any) -> Optional[datetime]:
    """TechCherry exports dates in DD-MM-YYYY or DD/MM/YYYY. Returns UTC."""
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Fallback: ISO
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _power_quality_issues(row: Dict[str, Any]) -> List[str]:
    """POWER-VALIDATE ONLY for a legacy historical import row.

    Returns a list of human-readable problems found in the row's Rx powers
    (range / 0.25-grid / axis), checking BOTH any row-level sph/cyl/add/axis AND
    each line item's powers. NEVER raises and NEVER causes a skip -- a historical
    row with a bad power is still imported; the caller only COUNTS + records the
    note. Reuses the canonical clinical validators (no duplicated ranges).
    """
    from ..services.rx_validation import _validate_axis, _validate_rx_value

    issues: List[str] = []

    def _check(holder: Dict[str, Any], where: str) -> None:
        if not isinstance(holder, dict):
            return
        # Tolerate the common spellings legacy exports use.
        def _g(*keys):
            for k in keys:
                if holder.get(k) not in (None, ""):
                    return holder.get(k)
            return None

        sph = _g("sph", "SPH", "sphere")
        cyl = _g("cyl", "CYL", "cylinder")
        add = _g("add", "ADD", "addition")
        axis = _g("axis", "AXIS")
        for value, field in ((sph, "sph"), (cyl, "cyl"), (add, "add")):
            if value in (None, ""):
                continue
            try:
                _validate_rx_value(str(value), field)
            except ValueError as exc:
                issues.append(f"{where}: {exc}")
        if axis not in (None, "") or cyl not in (None, ""):
            try:
                _validate_axis(axis, cyl=cyl)
            except ValueError as exc:
                issues.append(f"{where}: {exc}")

    # Row-level powers (some legacy exports flatten the Rx onto the order row).
    _check(row, "row")
    # Per-line-item powers.
    for idx, itm in enumerate(row.get("items") or []):
        _check(itm, f"item[{idx}]")
    return issues


def _get_db():
    """Lazy DB import to keep this router self-contained — same pattern as
    other routers."""
    try:
        from database.connection import get_db

        d = get_db()
        return d.db if d else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class ImportRequest(BaseModel):
    """Single import batch.

    `type` decides which collection + dedupe key applies.
    `store_id` is the IMS store the data attaches to (e.g. 'BV-PUN-01').
    `rows` is the actual data — shape varies by type but each row is a
    TechCherry-flavoured dict (keys match what the operator captured from
    the TechCherry report DOM).
    `overwrite=True` means duplicates get `$set` updated; `False` means
    skip and count as dedup'd.
    """

    type: Literal["products", "customers", "orders"]
    store_id: str = Field(..., min_length=1)
    rows: List[Dict[str, Any]] = Field(..., max_length=2000)
    overwrite: bool = True
    source: str = "techcherry"  # stamped on every row for traceability


class ImportResponse(BaseModel):
    type: str
    store_id: str
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: List[str] = Field(default_factory=list)
    sample_dedup_keys: List[str] = Field(default_factory=list)
    # POWER-VALIDATE ONLY (legacy historical import): rows whose Rx powers are out
    # of clinical range are NEVER skipped/rejected -- they are imported as-is and
    # reported here as a data-quality note so staff can review the source data.
    data_quality_notes: List[str] = Field(default_factory=list)
    out_of_range_power_rows: int = 0


# ---------------------------------------------------------------------------
# Mappers — TechCherry row → IMS document
# ---------------------------------------------------------------------------


def _map_product(
    row: Dict[str, Any], store_id: str, source: str
) -> Optional[Dict[str, Any]]:
    """TechCherry stock columns:
    Prod Name, Prod Grp, Unit, HSN, Stock In_Hand, Stock Value, Barcode,
    Qty, Pur Prc, Ttl Pur Val, Sale Prc, Ttl Sale Val, eCom
    """
    name = (
        row.get("name") or row.get("prod_name") or row.get("Prod Name") or ""
    ).strip()
    barcode = (row.get("barcode") or row.get("Barcode") or "").strip()
    if not name and not barcode:
        return None
    if barcode.upper() == "NA":
        barcode = ""

    group = (
        row.get("group") or row.get("prod_grp") or row.get("Prod Grp") or ""
    ).strip()
    # TechCherry concatenates brand + category in "Prod Grp" (e.g. "RAYBAN FRAME").
    # Heuristic: first word = brand, rest = category.
    brand = ""
    category = group
    if group:
        parts = group.split(" ", 1)
        if len(parts) == 2:
            brand, category = parts[0], parts[1]
        else:
            category = group

    sale_price = _safe_float(row.get("sale_prc") or row.get("Sale Prc"))
    pur_price = _safe_float(row.get("pur_prc") or row.get("Pur Prc"))
    stock_qty = _safe_int(row.get("stock") or row.get("Stock In_Hand"))
    hsn = (row.get("hsn") or row.get("HSN") or "").strip()
    unit = (row.get("unit") or row.get("Unit") or "PCS").strip()

    return {
        "store_id": store_id,
        "name": name,
        "brand": brand,
        "category": category,
        "barcode": barcode,
        "sku": barcode or name[:60],
        "mrp": sale_price,
        "offer_price": sale_price,
        "cost_price": pur_price,
        "stock_quantity": stock_qty,
        "reorder_point": 0,  # TechCherry has no reorder concept exported
        "hsn_code": hsn,
        "unit": unit,
        "is_active": True,
        "source": source,
        "techcherry_imported_at": datetime.now(timezone.utc),
    }


def _map_customer(
    row: Dict[str, Any], store_id: str, source: str
) -> Optional[Dict[str, Any]]:
    """TechCherry CRM customer fields (best-effort — exact column names
    will be confirmed once the customer report DOM is read):
       Name, Mobile/Phone, Email, Address, City, GSTIN, OpeningBal, ...
    """
    name = (
        row.get("name") or row.get("Name") or row.get("customer_name") or ""
    ).strip()
    phone = _normalise_phone(row.get("phone") or row.get("Mobile") or row.get("Phone"))
    if not phone and not name:
        return None
    return {
        "customer_id": phone or name.replace(" ", "_").lower()[:40],
        "name": name,
        "phone": phone,
        # Normalize-on-write: the number is the customer's IDENTITY. TechCherry
        # legacy rows only carry it under `phone`, but the UNIQUE (sparse) index
        # is on `mobile`, so an import that left `mobile` absent would be exempt
        # from the index and could create duplicate accounts (split AR). Mirror
        # the number into `mobile` so the existing unique index actually enforces
        # one account per number across both natively-created + imported docs.
        # `None` (no phone) stays absent so the sparse index still exempts it.
        "mobile": phone or None,
        "email": (row.get("email") or row.get("Email") or "").strip(),
        "address": (row.get("address") or row.get("Address") or "").strip(),
        "city": (row.get("city") or row.get("City") or "").strip(),
        "gstin": (row.get("gstin") or row.get("GSTIN") or "").strip(),
        "opening_balance": _safe_float(row.get("opening_bal") or row.get("OpeningBal")),
        "preferred_store_id": store_id,
        "is_active": True,
        "source": source,
        "techcherry_imported_at": datetime.now(timezone.utc),
    }


def _map_order(
    row: Dict[str, Any], store_id: str, source: str
) -> Optional[Dict[str, Any]]:
    """TechCherry Transaction Detail fields (best-effort):
    VchNo / InvoiceNo, Date, CustomerName, Mobile, GrandTotal,
    TaxableAmount, TaxAmount, Discount, PaymentMode, Items[...]
    """
    invoice = (
        row.get("invoice_no")
        or row.get("InvoiceNo")
        or row.get("VchNo")
        or row.get("vch_no")
        or ""
    )
    invoice = str(invoice).strip()
    if not invoice:
        return None

    return {
        "order_number": invoice,
        "store_id": store_id,
        "customer_name": (
            row.get("customer_name") or row.get("CustomerName") or ""
        ).strip(),
        "customer_phone": _normalise_phone(row.get("phone") or row.get("Mobile")),
        "created_at": _parse_date(
            row.get("date") or row.get("Date") or row.get("InvoiceDate")
        )
        or datetime.now(timezone.utc),
        "grand_total": _safe_float(row.get("grand_total") or row.get("GrandTotal")),
        "subtotal": _safe_float(row.get("taxable_amount") or row.get("TaxableAmount")),
        "tax_amount": _safe_float(row.get("tax_amount") or row.get("TaxAmount")),
        "total_discount": _safe_float(row.get("discount") or row.get("Discount")),
        "payment_method": (
            row.get("payment_mode") or row.get("PaymentMode") or ""
        ).strip(),
        "items": row.get("items") or [],
        "status": "DELIVERED",  # TechCherry historic orders are completed by definition
        "source": source,
        "techcherry_imported_at": datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/import",
    response_model=ImportResponse,
    summary="One-time TechCherry → IMS batch upsert (SUPERADMIN)",
    description=(
        "Upserts a batch of products / customers / orders from TechCherry "
        "into IMS. Dedupe key depends on `type`: barcode for products, "
        "phone for customers, invoice_no for orders. `overwrite=true` "
        "$set-updates existing docs; `overwrite=false` counts them as "
        "skipped. Stamps every row with `source='techcherry'` and "
        "`techcherry_imported_at=<utc-now>` so the migration is rewindable. "
        "Max 2000 rows per call — caller batches client-side."
    ),
)
async def import_batch(
    req: ImportRequest,
    current_user: dict = Depends(_require_superadmin),
) -> ImportResponse:
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    if req.type == "products":
        collection_name = "products"
        dedup_field = "barcode"
        mapper = _map_product
    elif req.type == "customers":
        collection_name = "customers"
        dedup_field = "phone"
        mapper = _map_customer
    else:  # orders
        collection_name = "orders"
        dedup_field = "order_number"
        mapper = _map_order

    col = db.get_collection(collection_name)
    if col is None:
        raise HTTPException(
            status_code=503, detail=f"{collection_name} collection unavailable"
        )

    resp = ImportResponse(type=req.type, store_id=req.store_id)
    dedup_samples: List[str] = []

    for raw_row in req.rows:
        try:
            # POWER-VALIDATE ONLY (orders carry Rx powers): record a data-quality
            # note for out-of-range powers but NEVER skip/reject the historical
            # row. Done before mapping so a row that is otherwise importable still
            # gets validated; fail-soft so a validator hiccup never blocks import.
            if req.type == "orders":
                try:
                    power_issues = _power_quality_issues(raw_row)
                except Exception:  # noqa: BLE001 - quality check must not block import
                    power_issues = []
                if power_issues:
                    resp.out_of_range_power_rows += 1
                    ref = (
                        raw_row.get("invoice_no")
                        or raw_row.get("InvoiceNo")
                        or raw_row.get("VchNo")
                        or raw_row.get("vch_no")
                        or f"row#{resp.inserted + resp.updated + resp.skipped + 1}"
                    )
                    if len(resp.data_quality_notes) < 50:
                        resp.data_quality_notes.append(
                            f"{ref}: out-of-range Rx power(s) imported as-is "
                            f"({'; '.join(power_issues)[:200]})"
                        )

            doc = mapper(raw_row, req.store_id, req.source)
            if not doc:
                resp.errors.append("row missing required identifier — skipped")
                continue

            key_value = doc.get(dedup_field) or ""
            if not key_value:
                # No dedupe key — insert as-is (only happens for products
                # without a barcode where we fall back to sku=name)
                col.insert_one(doc)
                resp.inserted += 1
                continue

            # Customers also need scoping: phone is global. Products + orders
            # scope by store_id.
            query: Dict[str, Any]
            if req.type == "customers":
                # The number is the identity but lives under `phone` (imported)
                # OR `mobile` (natively-created). Dedup against BOTH so a
                # re-import, or an import of a number that already exists as a
                # native customer, updates/skips instead of creating a duplicate.
                query = {"$or": [{"phone": key_value}, {"mobile": key_value}]}
            else:
                query = {dedup_field: key_value}
            if req.type in ("products", "orders"):
                query["store_id"] = req.store_id

            existing = col.find_one(query, {"_id": 1})
            if existing:
                if req.overwrite:
                    col.update_one({"_id": existing["_id"]}, {"$set": doc})
                    resp.updated += 1
                else:
                    resp.skipped += 1
            else:
                col.insert_one(doc)
                resp.inserted += 1

            if len(dedup_samples) < 5:
                dedup_samples.append(key_value)

        except Exception as e:
            resp.errors.append(f"{type(e).__name__}: {str(e)[:200]}")

    resp.sample_dedup_keys = dedup_samples
    return resp


@router.get(
    "/status",
    summary="Per-collection counts of TechCherry-imported rows",
    description="How many products / customers / orders are tagged source='techcherry'.",
)
async def import_status(
    current_user: dict = Depends(_require_superadmin),
) -> Dict[str, Any]:
    db = _get_db()
    out: Dict[str, Any] = {"as_of": datetime.now(timezone.utc).isoformat()}
    if db is None:
        out["error"] = "db unavailable"
        return out
    for name in ("products", "customers", "orders"):
        try:
            col = db.get_collection(name)
            out[name] = {
                "total": col.count_documents({"source": "techcherry"}),
                "this_month": col.count_documents(
                    {
                        "source": "techcherry",
                        "techcherry_imported_at": {
                            "$gte": datetime.now(timezone.utc).replace(
                                hour=0, minute=0, second=0, microsecond=0
                            )
                        },
                    }
                ),
            }
        except Exception as e:
            out[name] = {"error": str(e)[:200]}
    return out
