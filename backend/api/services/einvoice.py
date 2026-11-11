"""
IMS 2.0 - GST e-invoicing provider seam (IRN + signed QR) -- FIN-1
====================================================================
Build DARK by default: every call returns {status: "SIMULATED", ...} and does
NOTHING (no network, no DB write) until ALL THREE conditions hold:
  1. IMS_EINVOICE_ENABLED=1 (env)
  2. GSP/IRP credentials stored in the `integrations` Mongo collection for
     the GSTIN of the order (type: "einvoice", config.gstin, config.gsp_url,
     config.username, config.password / config.client_id / config.client_secret)
  3. The order does NOT already have an IRN on it (idempotency skip)

Credential shape (integrations.config for type="einvoice"):
  {
    "gstin": "20XXXXXXX...",   # which GSTIN these creds belong to
    "gsp_url": "https://...",  # GSP/IRP API base URL (no trailing slash)
    "username": "...",         # GSP portal login / API username
    "password": "...",         # GSP portal password (Fernet-encrypted at rest)
    "client_id": "...",        # OAuth client id if GSP uses OAuth2
    "client_secret": "..."     # OAuth client secret
  }

Multiple GSPs / multiple GSTINs are supported: store one `integrations` doc
per GSTIN. The seller GSTIN -- the order's OWN shop's (order.store_id ->
store.gstin, ``_seller``) -- determines which doc is loaded.

E-invoice JSON shape maps from gstn_export.py's b2b/itm_det model (1:1). The
IRP API used is the standard NIC e-invoice sandbox/production endpoint; GSPs
typically proxy it with the same shape, varying only the auth header.

FAIL-SOFT contract: every public function returns a structured dict and NEVER
raises into the caller. Network errors, missing creds, IRP rejections, and
malformed orders all become {status: "FAILED", reason: "..."} or SIMULATED.
No exception leaks out of this module.

ASCII-only source (no emojis). No new heavy deps added -- httpx is already in
requirements.txt. QR rendering uses `qrcode` if installed; otherwise the raw
signed_qr string is returned with a TODO render note (fail-soft).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from api.utils.ist import ist_date_str, now_ist
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EINVOICE_TIMEOUT = float(os.getenv("GSP_TIMEOUT", "30.0"))

# Status tokens returned in every result dict.
STATUS_SIMULATED = "SIMULATED"
STATUS_GENERATED = "GENERATED"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def einvoice_enabled(db, gstin: str = "") -> bool:
    """Return True only when the DARK gate is lifted AND creds are present.

    Gate 1: IMS_EINVOICE_ENABLED env var must be "1" / "true" / "yes".
    Gate 2: An `integrations` doc with type="einvoice" for `gstin` must exist
            and carry gsp_url + username + (password or client_id).

    Fail-soft: any error returns False (stay dark).
    """
    if not _env_enabled():
        return False
    cfg = _load_creds(db, gstin)
    return bool(
        cfg.get("gsp_url")
        and cfg.get("username")
        and (cfg.get("password") or cfg.get("client_id"))
    )


def _env_enabled() -> bool:
    """True when IMS_EINVOICE_ENABLED is set to a truthy value."""
    val = os.getenv("IMS_EINVOICE_ENABLED", "").strip().lower()
    return val in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Credential loader
# ---------------------------------------------------------------------------


def _load_creds(db, gstin: str) -> Dict[str, Any]:
    """Load the ENABLED `integrations` config doc for type="einvoice" whose
    config.gstin is exactly `gstin`.

    Fails closed (audit L3-20): a blank gstin, a disabled doc or another
    GSTIN's doc is never returned -- one legal entity's IRP credentials never
    sign another's invoice. Returns {} on any error or when no such doc
    exists. Never raises.
    """
    gstin = str(gstin or "").strip()
    if db is None or not gstin:
        return {}
    try:
        doc = db.get_collection("integrations").find_one(
            {"type": "einvoice", "enabled": True, "config.gstin": gstin}
        )
        if not doc:
            return {}
        # BUG-155: secrets are Fernet-encrypted at rest; decrypt for provider use.
        from api.services import cred_crypto

        return cred_crypto.decrypt_config(doc.get("config") or {})
    except Exception as exc:  # noqa: BLE001
        logger.debug("[EINVOICE] creds load failed for gstin=%s: %s", gstin, exc)
        return {}


# ---------------------------------------------------------------------------
# The seller
# ---------------------------------------------------------------------------


def _seller(db, order: Dict[str, Any]):
    """THE seller of an order's e-invoice -> (shop doc, reason or None): the
    order's OWN shop (order.store_id), whose ``gstin`` is the GSTIN its tax
    invoice, GSTR-1 and the delivery challan issue from. ``reason`` refuses:
    a routed online order its booking held (online_fulfillment_route
    .seller_problem, the one seller check), or a shop with no GSTIN."""
    from .online_fulfillment_route import seller_problem

    def find(sid):
        try:
            return db.get_collection("stores").find_one({"store_id": sid}, {"_id": 0}) if sid else None
        except Exception:  # noqa: BLE001 -- unreadable: no seller GSTIN, refused below
            return None

    store = find(order.get("store_id")) or {}
    bad = seller_problem(order, store, find)
    if bad:
        return store, bad["message"]
    if not str(store.get("gstin") or "").strip():
        return store, (
            f"The order's shop {order.get('store_id') or '(none)'} has no GSTIN, "
            "so no e-invoice can be issued for it."
        )
    return store, None


# ---------------------------------------------------------------------------
# E-invoice JSON builder (maps gstn_export shape)
# ---------------------------------------------------------------------------


def _customer(db, order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The order's customer record as the GST returns read it (its state and
    GSTIN, for the place of supply and the buyer GSTIN); None for a walk-in.
    A failed read raises: the caller refuses rather than guess the buyer."""
    cid = order.get("customer_id")
    if not cid:
        return None
    from api.routers.reports.gst_base import CUSTOMER_SUPPLY_FIELDS

    return db.get_collection("customers").find_one(
        {"customer_id": cid}, {"_id": 0, **CUSTOMER_SUPPLY_FIELDS}
    )


def _build_einvoice_json(
    order: Dict[str, Any],
    seller: Optional[Dict[str, Any]] = None,
    customer: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the NIC e-invoice API request payload from an IMS order/invoice doc.

    Every taxable value and tax figure is the order's CANONICAL one (audit
    L3-19): THE rules the GST returns file from (reports.gst_base) -- the
    till's stamped per-line ``taxable_value`` / ``tax_amount`` / ``gst_rate``,
    the order's ``tax_amount`` / ``grand_total`` / ``round_off``, the head the
    tax invoice printed (``_order_supply``) and the per-rate CGST/SGST/IGST
    split (``_rate_heads``). GSTN cross-populates GSTR-1 from the IRN, so the
    payload can never carry a figure the invoice and the return do not.

    ``seller`` is the order's own shop doc (``_seller``): its ``gstin`` is the
    seller GSTIN and its first two digits the seller's state code.
    ``customer`` is the order's customer record (``_customer``), which the
    head rule reads for the buyer's state on an invoice minted before that was
    stamped on the order.

    Decision D2 (Claude under the owner's delegation, 2026-11-11; to confirm
    with the CA before e-invoicing is ever enabled):
      * a line shows its NET unit price (after every discount, GST taken out)
        with Discount 0, and the header Discount is 0 -- the discount is
        already inside each line's taxable value, so the IRP's own TotAmt =
        Qty x UnitPrice and AssAmt = TotAmt - Discount always hold and the
        e-invoice equals the bill and GSTR-1;
      * the buyer GSTIN is GSTR-1's own rule (gst_base._invoice_gstin): the
        GSTIN frozen on the order at the mint, else -- an invoice older than
        that stamp -- the customer record's. The IRN and the return can never
        disagree on B2B vs B2C.

    Returns a dict. RAISES ValueError (gst_base.UnfileableOrder) for an order
    the returns cannot file either -- an unreadable amount, a line with no GST
    rate, a tax that fits no slab: it is never sent as zeros or a guess
    (generate_irn answers FAILED, as it does a bill with no taxable value).
    """
    from api.routers.reports.gst_base import (
        _invoice_gstin,
        _money,
        _order_rate_lines,
        _order_supply,
        _order_taxable_and_tax,
        _rate_heads,
    )

    def _s(val: Any, default: str = "") -> str:
        return str(val or default).strip()

    def _n(val: Any) -> float:
        # THE returns' money parser (a comma string, 'Rs', Decimal128): an
        # amount the IRP is sent is the amount GSTR-1 files, never a silent 0.
        return round(_money(val, order), 2)

    # Supply type: B2B when GSTR-1 files this sale under a buyer GSTIN, else
    # B2C -- THE returns' rule, never a second reading of the order.
    cust_gstin = _s(_invoice_gstin(order, (customer or {}).get("gstin") or ""))
    supply_type = "B2B" if cust_gstin else "B2C"

    # Document identity
    inv_no = _s(
        order.get("invoice_number") or order.get("order_number") or order.get("id")
    )
    # BUG-104, VALUE rule (+5:30). DocDtls.Dt is the STATUTORY document date
    # registered on the IRP, and GSTN cross-populates GSTR-1 from the IRN. The
    # created_at fallback is a stored naive datetime.now() == the UTC wall
    # clock (Railway runs UTC), so slicing its UTC day would register a
    # 1-Apr-00:00-05:30-IST invoice dated 31-March PRIOR-FY while the (fixed)
    # GSTR-1 files it 1-April -- one invoice, two statutory dates.
    # ist_date_str shifts datetimes to the IST calendar day and passes
    # date-only strings (invoice_date) through unchanged.
    inv_date_raw = order.get("invoice_date") or order.get("created_at") or ""
    inv_date = _fmt_date_ddmmyyyy(ist_date_str(inv_date_raw) or inv_date_raw)

    # Seller GSTIN -- the order's own shop's (``_seller``), never a guess.
    seller_gstin = _s((seller or {}).get("gstin"))

    # The order's taxable value and tax (grand_total - round_off - tax_amount),
    # the head its tax invoice printed, and one line per bill line carrying
    # its own stamped taxable / tax at its own rate -- the GST returns' rules.
    taxable, tax = _order_taxable_and_tax(order)
    interstate, pos_code, _assumed = _order_supply(order, seller, customer)
    issues: list = []
    qtys: list = []
    sources: list = []
    rate_lines = _order_rate_lines(
        order.get("items") or [], taxable, tax, order, issues, qtys, sources
    )
    unfileable = [i["issue"] for i in issues if i.get("level") == "error"]
    if unfileable:
        raise ValueError(unfileable[0])

    # Place of supply (2-digit state code): the buyer's, else the shop's own.
    pos = _s(pos_code or seller_gstin[:2])
    grand = order.get("grand_total")
    total = _n(grand if grand is not None else order.get("total_amount"))

    # _rate_heads splits each RATE's tax into its heads (what the invoice
    # prints) and shares them over that rate's lines, kept in bill order;
    # `slots` puts every line back at its own place on the bill.
    slots: Dict[Any, list] = {}
    for at, line in enumerate(rate_lines):
        slots.setdefault(line[0], []).append(at)
    rows: list = [None] * len(rate_lines)
    for head in _rate_heads(rate_lines, interstate):
        for at, (hsn, l_taxable, l_cgst, l_sgst, l_igst) in zip(
            slots[head["rate"]], head["lines"]
        ):
            src = sources[at] or {}  # None: the order-totals fallback line
            qty = qtys[at] or 1.0
            rows[at] = {
                "PrdDesc": _s(
                    src.get("product_name") or src.get("name") or src.get("sku")
                    or order.get("description") or "Goods"
                )[:300],
                "IsServc": "N",
                "HsnCd": _s(hsn),
                "Qty": qty,
                "Unit": "PCS",
                # Decision D2: the NET unit price (3 decimals, the IRP's
                # precision), no discount shown -- it is inside l_taxable.
                "UnitPrice": round(l_taxable / qty, 3),
                "TotAmt": l_taxable,
                "Discount": 0.0,
                "AssAmt": l_taxable,
                "GstRt": round(float(head["rate"]), 2),
                "IgstAmt": l_igst,
                "CgstAmt": l_cgst,
                "SgstAmt": l_sgst,
                "CesRt": 0.0,
                "CesAmt": 0.0,
                "CesNonAdvlAmt": 0.0,
                "StateCesRt": 0.0,
                "StateCesAmt": 0.0,
                "StateCesNonAdvlAmt": 0.0,
                "OthChrg": 0.0,
                "TotItemVal": round(l_taxable + l_cgst + l_sgst + l_igst, 2),
            }
    items = [{"SlNo": str(n), **row} for n, row in enumerate(rows, start=1)]

    val_details = {
        # The header is the sum of its lines on every head (the IRP checks it).
        "AssVal": round(sum(r["AssAmt"] for r in rows), 2),
        "CgstVal": round(sum(r["CgstAmt"] for r in rows), 2),
        "SgstVal": round(sum(r["SgstAmt"] for r in rows), 2),
        "IgstVal": round(sum(r["IgstAmt"] for r in rows), 2),
        "CesVal": 0.0,
        "StCesVal": 0.0,
        "Discount": 0.0,  # decision D2: already inside every line's AssAmt
        "OthChrg": 0.0,
        # The bill's stored round off (owner ruling 2026-10-08): the IRP's own
        # field, so TotInvVal (the rounded total) = AssVal + taxes + RndOffAmt
        # while every taxable and tax figure above stays the lines' own.
        "RndOffAmt": _n(order.get("round_off")),
        "TotInvVal": total,
        "TotInvValFc": 0.0,
    }

    buyer: Dict[str, Any] = {
        "Gstin": cust_gstin or "URP",  # URP = Unregistered Person
        "LglNm": _s(order.get("customer_name") or "Consumer"),
        "TrdNm": _s(
            order.get("customer_trade_name") or order.get("customer_name") or "Consumer"
        ),
        "Pos": pos,
        "Addr1": _s(
            order.get("billing_address") or order.get("customer_address") or "."
        ),
        "Addr2": "",
        "Loc": _s(order.get("billing_city") or order.get("customer_city") or ""),
        "Pin": _s(order.get("billing_pin") or order.get("customer_pin") or "000000"),
        "Stcd": pos,
        "Ph": _s(order.get("customer_mobile") or ""),
        "Em": _s(order.get("customer_email") or ""),
    }

    payload: Dict[str, Any] = {
        "Version": "1.1",
        "TranDtls": {
            "TaxSch": "GST",
            "SupTyp": supply_type,
            "RegRev": "N",
            "EcmGstin": None,
            "IgstOnIntra": "N",
        },
        "DocDtls": {
            "Typ": "INV",
            "No": inv_no,
            "Dt": inv_date,
        },
        "SellerDtls": {
            "Gstin": seller_gstin,
            "LglNm": _s(order.get("store_legal_name") or order.get("store_name") or ""),
            "TrdNm": _s(order.get("store_name") or ""),
            "Addr1": _s(order.get("store_address") or "."),
            "Addr2": "",
            "Loc": _s(order.get("store_city") or ""),
            "Pin": _s(order.get("store_pin") or "000000"),
            "Stcd": seller_gstin[:2],
            "Ph": _s(order.get("store_phone") or ""),
            "Em": _s(order.get("store_email") or ""),
        },
        "BuyerDtls": buyer,
        "ItemList": items,
        "ValDtls": val_details,
    }
    return payload


def _fmt_date_ddmmyyyy(val: Any) -> str:
    """Convert an IMS date (YYYY-MM-DD, ISO, or datetime) to DD/MM/YYYY.

    The NIC IRP API requires exactly this format for DocDtls.Dt. Returns
    today's date in that format when parsing fails (always produce a valid doc).
    """
    s = str(val or "").strip()
    # Already DD/MM/YYYY
    if len(s) == 10 and s[2] == "/" and s[5] == "/":
        return s
    # ISO YYYY-MM-DD (with possible time suffix)
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        y, m, d = s[:4], s[5:7], s[8:10]
        return f"{d}/{m}/{y}"
    # Fallback: today. BUG-104: this is the same statutory DocDtls date, so
    # "today" must be the IST calendar day, not the UTC box day (which is
    # yesterday between 00:00 and 05:30 IST -- and prior-FY on 1 April).
    return now_ist().strftime("%d/%m/%Y")


# ---------------------------------------------------------------------------
# GSP / IRP network call
# ---------------------------------------------------------------------------


async def _call_irp(cfg: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST the e-invoice JSON to the GSP/IRP endpoint and return the parsed
    response body. This is the ONLY network call in this module.

    GSPs vary in their auth schemes. We support:
    - Basic-Auth (username + password in Authorization header)
    - Bearer token (pre-fetched or client_id/client_secret flow not yet built;
      callers can pass a pre-fetched token via cfg["bearer_token"])

    Returns the raw parsed JSON body. Raises httpx/ValueError on transport
    failure; the caller (generate_irn) catches and fail-softs.
    """
    gsp_url = str(cfg.get("gsp_url") or "").rstrip("/")
    endpoint = f"{gsp_url}/einvoice/generate"

    username = cfg.get("username") or ""
    password = cfg.get("password") or ""
    bearer = cfg.get("bearer_token") or ""

    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    elif username and password:
        import base64

        cred = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {cred}"

    async with httpx.AsyncClient(timeout=_EINVOICE_TIMEOUT) as client:
        resp = await client.post(endpoint, headers=headers, content=json.dumps(payload))

    if resp.status_code not in (200, 201):
        raise ValueError(f"IRP responded {resp.status_code}: {resp.text[:400]}")
    return resp.json()


def _parse_irp_response(body: Dict[str, Any]) -> Dict[str, Any]:
    """Extract IRN + AckNo + AckDt + SignedQRCode from the IRP response body.

    The NIC response shape (and most GSPs mirroring it):
      {
        "Status": "1",              # "1" = success
        "InfoDtls": [{
            "InfCd": "EINV-GEN",
            "Desc": {
                "Irn": "...",
                "AckNo": 12345,
                "AckDt": "2026-06-05 10:00:00",
                "SignedQRCode": "...",
                "SignedInvoice": "..."
            }
        }],
        "ErrorDetails": [...]
      }

    GSP wrappers sometimes flatten this into data.Irn / data.AckNo. We try
    both shapes. Returns {} on parse failure (caller treats as FAILED).
    """
    if not isinstance(body, dict):
        return {}

    # Shape 1: NIC standard InfoDtls
    info_list = body.get("InfoDtls") or []
    if isinstance(info_list, list) and info_list:
        desc = info_list[0].get("Desc") or {}
        if isinstance(desc, dict) and desc.get("Irn"):
            return {
                "irn": desc.get("Irn") or "",
                "ack_no": str(desc.get("AckNo") or ""),
                "ack_date": str(desc.get("AckDt") or ""),
                "signed_qr": desc.get("SignedQRCode") or "",
                "signed_invoice": desc.get("SignedInvoice") or "",
            }

    # Shape 2: flattened GSP wrapper (data.Irn / Irn at root)
    for container in (body.get("data") or {}, body):
        if not isinstance(container, dict):
            continue
        irn = container.get("Irn") or container.get("irn")
        if irn:
            return {
                "irn": str(irn),
                "ack_no": str(container.get("AckNo") or container.get("ack_no") or ""),
                "ack_date": str(
                    container.get("AckDt") or container.get("ack_date") or ""
                ),
                "signed_qr": container.get("SignedQRCode")
                or container.get("signed_qr")
                or "",
                "signed_invoice": container.get("SignedInvoice")
                or container.get("signed_invoice")
                or "",
            }

    return {}


# ---------------------------------------------------------------------------
# Persist IRN fields onto the order doc
# ---------------------------------------------------------------------------


def _order_filter(order_id: str) -> Dict[str, Any]:
    """The one id filter an order / invoice doc is found by."""
    return {"$or": [{"id": order_id}, {"order_id": order_id}, {"invoice_id": order_id}]}


def _stored_order(db, order_id: str) -> Optional[Dict[str, Any]]:
    """The order as it is stored NOW (orders, then invoices), or None. Raises
    when the database cannot be read: the caller refuses, never guesses."""
    for name in ("orders", "invoices"):
        doc = db.get_collection(name).find_one(_order_filter(order_id), {"_id": 0})
        if doc:
            return doc
    return None


def _already(order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The SKIPPED answer for an order that already carries an IRN (None when
    it does not). An IRN stored without its signed QR says so every time."""
    irn = order.get("irn") or order.get("einvoice_irn")
    if not irn:
        return None
    qr = order.get("einvoice_signed_qr")
    return {
        "status": STATUS_SKIPPED,
        "irn": irn,
        "ack_no": order.get("ack_no"),
        "ack_date": order.get("ack_date"),
        "signed_qr": qr,
        "reason": "IRN already exists on this order -- skipping"
        + ("" if qr else " (no signed QR code is stored for it)"),
        "einvoice_json": None,
    }


def _persist_irn(db, order_id: str, irn_fields: Dict[str, Any]) -> bool:
    """Persist IRN + ack_no + ack_date + signed_qr + einvoice_status onto the
    orders collection doc. Idempotent: safe to call multiple times, and a
    BLANK never overwrites a stored value -- the IRP's duplicate answer to a
    second press carries the IRN but no ack / signed QR (audit L3-21).
    Fail-soft: True when a document took the IRN, False when none did (never
    raises).

    ``db`` is the real PyMongo Database (finance/_shared._get_db), which
    refuses truth-testing: compare with None (audit L3-21).
    """
    if not order_id or db is None:
        return False
    try:
        now = datetime.now(timezone.utc)
        update: Dict[str, Any] = {
            "irn": irn_fields.get("irn"),
            "ack_no": irn_fields.get("ack_no"),
            "ack_date": irn_fields.get("ack_date"),
            "einvoice_signed_qr": irn_fields.get("signed_qr"),
        }
        update = {k: v for k, v in update.items() if v}  # never a blank
        update.update(einvoice_status="GENERATED", einvoice_generated_at=now)
        # Try orders collection first (POS orders), then invoices
        for collection_name in ("orders", "invoices"):
            coll = db.get_collection(collection_name)
            result = coll.update_one(_order_filter(order_id), {"$set": update})
            if result.matched_count > 0:
                logger.info(
                    "[EINVOICE] persisted IRN %s on %s/%s",
                    irn_fields.get("irn"),
                    collection_name,
                    order_id,
                )
                return True
        logger.warning(
            "[EINVOICE] order/invoice %s not found for IRN persist", order_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[EINVOICE] persist_irn failed for %s: %s", order_id, exc)
    return False


# ---------------------------------------------------------------------------
# QR rendering helper (fail-soft, no mandatory dep)
# ---------------------------------------------------------------------------


def render_signed_qr_png(signed_qr: str) -> Optional[bytes]:
    """Encode `signed_qr` into a QR PNG and return the bytes.

    Uses the `qrcode` package if installed; returns None (fail-soft) when the
    dep is absent. The router / print template falls back to displaying the raw
    string with a TODO note when None is returned.

    qrcode is a lightweight dep (pure-Python, MIT) but we do NOT add it to
    requirements.txt here -- the caller checks availability. If needed, add:
        qrcode==8.x  (or qrcode[pil])
    to requirements.txt and install Pillow alongside it.
    """
    if not signed_qr:
        return None
    try:
        import io
        import qrcode  # optional dep -- import-guarded  # pylint: disable=import-error

        img = qrcode.make(signed_qr)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001 -- dep absent or other error
        logger.debug("[EINVOICE] qrcode render skipped: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Core public API
# ---------------------------------------------------------------------------


async def generate_irn(db, order: Dict[str, Any]) -> Dict[str, Any]:
    """Generate an IRN for `order` and persist it.

    Returns a structured dict (NEVER raises):
      {
        "status":     SIMULATED | GENERATED | SKIPPED | FAILED,
        "irn":        str | None,
        "ack_no":     str | None,
        "ack_date":   str | None,
        "signed_qr":  str | None,
        "reason":     str | None,   # set on SIMULATED / FAILED / SKIPPED
        "einvoice_json": dict | None  # the IRP request body (audit / debug)
      }

    SIMULATED -- gate not lifted (IMS_EINVOICE_ENABLED off or no creds).
    SKIPPED   -- order already has an IRN (idempotent).
    GENERATED -- IRN obtained from the IRP. ``persisted`` says whether the
                 order took it; ``reason`` is set when it did not, or when the
                 answer carried no signed QR (the IRP's duplicate reply).
    FAILED    -- gate was up but IRP call failed; order is unchanged.
    """
    order = order or {}
    order_id = str(
        order.get("id") or order.get("order_id") or order.get("invoice_id") or ""
    )

    # ── Idempotency: skip if IRN already on the doc ──────────────────────
    skipped = _already(order)
    if skipped:
        return skipped

    # An order the refund-advance door holds is being refunded and cancelled
    # behind a credit note IMS cannot report to the IRP: never e-invoice it.
    if order.get("advance_refund_id"):
        return {
            "status": STATUS_SKIPPED,
            "irn": None,
            "ack_no": None,
            "ack_date": None,
            "signed_qr": None,
            "reason": "This order's advance is being refunded -- no e-invoice",
            "einvoice_json": None,
        }

    # ── Gate check ───────────────────────────────────────────────────────
    if not _env_enabled():
        return {
            "status": STATUS_SIMULATED,
            "irn": None,
            "ack_no": None,
            "ack_date": None,
            "signed_qr": None,
            "reason": (
                "DARK: IMS_EINVOICE_ENABLED is not set. "
                "Set IMS_EINVOICE_ENABLED=1 and configure GSP creds in the "
                "integrations collection to activate."
            ),
            "einvoice_json": None,
        }

    # ── The seller: the order's own shop, refused LOUDLY when it cannot issue
    seller, refused = _seller(db, order)
    if refused:
        return {
            "status": STATUS_FAILED,
            "irn": None,
            "ack_no": None,
            "ack_date": None,
            "signed_qr": None,
            "reason": refused,
            "einvoice_json": None,
        }
    gstin = str(seller["gstin"]).strip()

    cfg = _load_creds(db, gstin)
    if not (
        cfg.get("gsp_url")
        and cfg.get("username")
        and (cfg.get("password") or cfg.get("client_id"))
    ):
        return {
            "status": STATUS_SIMULATED,
            "irn": None,
            "ack_no": None,
            "ack_date": None,
            "signed_qr": None,
            "reason": (
                f"DARK: no GSP credentials configured for GSTIN={gstin!r}. "
                "Add an integrations doc: {type: 'einvoice', enabled: true, "
                "config: {gstin, gsp_url, username, password}}."
            ),
            "einvoice_json": None,
        }

    # ── Build the IRP request payload ────────────────────────────────────
    # From the order's canonical tax figures; an order they cannot describe
    # (unreadable money, no GST rate, nothing taxable) is refused, never sent.
    try:
        einvoice_json = _build_einvoice_json(order, seller, _customer(db, order))
        if einvoice_json["ValDtls"]["AssVal"] <= 0:
            raise ValueError("the order has no taxable value")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[EINVOICE] order %s cannot be e-invoiced: %s", order_id, exc)
        return {
            "status": STATUS_FAILED,
            "irn": None,
            "ack_no": None,
            "ack_date": None,
            "signed_qr": None,
            "reason": (
                "Not sent to the IRP -- this order cannot be e-invoiced as it stands: "
                f"{str(getattr(exc, 'problem', None) or exc)[:400]}"
            ),
            "einvoice_json": None,
        }

    # ── Retry safety (audit L3-21) ───────────────────────────────────────
    # The caller's copy can be stale (a second press, a retry after a lost
    # reply): the order as STORED now decides. One that cannot be re-read is
    # not sent -- its IRN could not be saved either.
    # ponytail: a re-read, not an atomic claim -- two presses inside one
    # provider round trip can both call; the IRP then answers the second with
    # its duplicate reply and _persist_irn never blanks what the first saved.
    # Take a claim on the order if that double call ever matters.
    try:
        skipped = _already(_stored_order(db, order_id) or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[EINVOICE] order %s could not be re-read: %s", order_id, exc)
        return {
            "status": STATUS_FAILED,
            "irn": None,
            "ack_no": None,
            "ack_date": None,
            "signed_qr": None,
            "reason": (
                "Not sent to the IRP -- the stored order could not be re-read to "
                f"confirm it has no IRN yet: {str(exc)[:300]}"
            ),
            "einvoice_json": None,
        }
    if skipped:
        return skipped

    # ── Call the IRP / GSP ───────────────────────────────────────────────
    try:
        body = await _call_irp(cfg, einvoice_json)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[EINVOICE] IRP call failed for order %s: %s", order_id, exc)
        return {
            "status": STATUS_FAILED,
            "irn": None,
            "ack_no": None,
            "ack_date": None,
            "signed_qr": None,
            "reason": f"IRP/GSP error: {str(exc)[:400]}",
            "einvoice_json": einvoice_json,
        }

    # ── Parse response ───────────────────────────────────────────────────
    irn_fields = _parse_irp_response(body)
    if not irn_fields.get("irn"):
        err_details = body.get("ErrorDetails") or body.get("error") or body
        return {
            "status": STATUS_FAILED,
            "irn": None,
            "ack_no": None,
            "ack_date": None,
            "signed_qr": None,
            "reason": f"IRP returned no IRN: {str(err_details)[:400]}",
            "einvoice_json": einvoice_json,
        }

    # ── Persist on the order ─────────────────────────────────────────────
    # An IRN the IRP issued but no document took (``persisted``), or one that
    # came without its signed QR, is said so with the IRN still in the result
    # -- never a silent "all good".
    saved = _persist_irn(db, order_id, irn_fields)
    said = []
    if not saved:
        said.append("The IRP issued this IRN but it was NOT saved on the order.")
    if not irn_fields.get("signed_qr"):
        said.append("The IRP's answer carried no signed QR code.")

    return {
        "status": STATUS_GENERATED,
        "irn": irn_fields.get("irn"),
        "ack_no": irn_fields.get("ack_no"),
        "ack_date": irn_fields.get("ack_date"),
        "signed_qr": irn_fields.get("signed_qr"),
        "reason": " ".join(said) or None,
        "persisted": saved,
        "einvoice_json": einvoice_json,
    }


# ---------------------------------------------------------------------------
# 24-hour cancel helper
# ---------------------------------------------------------------------------


async def cancel_irn(
    db, order: Dict[str, Any], cancel_reason_code: int = 1, cancel_remark: str = ""
) -> Dict[str, Any]:
    """Cancel an IRN within 24 hours of generation via the IRP cancel API.

    cancel_reason_code: 1=Duplicate, 2=Data Entry Mistake, 3=Order cancelled,
                        4=Others (NIC codes).

    Returns {status, reason} dict. SIMULATED when gate is off or IRN missing.
    NEVER raises.
    """
    order = order or {}
    irn = str(order.get("irn") or order.get("einvoice_irn") or "").strip()
    if not irn:
        return {
            "status": STATUS_SIMULATED,
            "reason": "No IRN on this order -- nothing to cancel",
        }

    if not _env_enabled():
        return {
            "status": STATUS_SIMULATED,
            "reason": "DARK: IMS_EINVOICE_ENABLED is not set",
        }
    # The IRN was issued under the order's own shop's GSTIN: its creds cancel it.
    gstin = str(_seller(db, order)[0].get("gstin") or "")

    cfg = _load_creds(db, gstin)
    if not cfg.get("gsp_url"):
        return {
            "status": STATUS_SIMULATED,
            "reason": f"DARK: no GSP creds for GSTIN={gstin!r}",
        }

    cancel_payload = {
        "Irn": irn,
        "CnlRsn": cancel_reason_code,
        "CnlRem": cancel_remark or "Cancelled via IMS",
    }

    try:
        gsp_url = str(cfg.get("gsp_url") or "").rstrip("/")
        endpoint = f"{gsp_url}/einvoice/cancel"
        username = cfg.get("username") or ""
        password = cfg.get("password") or ""
        bearer = cfg.get("bearer_token") or ""

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        elif username and password:
            import base64

            cred = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {cred}"

        async with httpx.AsyncClient(timeout=_EINVOICE_TIMEOUT) as client:
            resp = await client.post(
                endpoint, headers=headers, content=json.dumps(cancel_payload)
            )

        if resp.status_code not in (200, 201):
            return {
                "status": STATUS_FAILED,
                "reason": f"IRP cancel failed ({resp.status_code}): {resp.text[:300]}",
            }

        body = resp.json() or {}
        if body.get("Status") == "1" or body.get("status") == "success":
            # Mark cancelled on the order doc
            order_id = str(order.get("id") or order.get("order_id") or "")
            if order_id and db is not None:  # a PyMongo Database: never truth-tested
                try:
                    for cn in ("orders", "invoices"):
                        result = db.get_collection(cn).update_one(
                            {"$or": [{"id": order_id}, {"order_id": order_id}]},
                            {
                                "$set": {
                                    "einvoice_status": "CANCELLED",
                                    "einvoice_cancelled_at": datetime.now(timezone.utc),
                                }
                            },
                        )
                        if result.matched_count > 0:
                            break
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[EINVOICE] cancel persist failed: %s", exc)
            return {"status": "CANCELLED", "reason": None}

        return {
            "status": STATUS_FAILED,
            "reason": f"IRP cancel: unexpected response: {str(body)[:300]}",
        }

    except Exception as exc:  # noqa: BLE001
        logger.warning("[EINVOICE] cancel_irn failed: %s", exc)
        return {
            "status": STATUS_FAILED,
            "reason": f"cancel error: {str(exc)[:300]}",
        }
