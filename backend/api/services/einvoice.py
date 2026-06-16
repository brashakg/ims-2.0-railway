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
per GSTIN. The order's GSTIN (billing_gstin) determines which doc is loaded.

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
    """Load the `integrations` config doc for type="einvoice" + gstin.

    Falls back to a generic (no gstin filter) doc when gstin is blank. Returns
    {} on any error or when no matching doc exists. Never raises.
    """
    if db is None:
        return {}
    try:
        coll = db.get_collection("integrations")
        query: Dict[str, Any] = {"type": "einvoice", "enabled": True}
        if gstin:
            query["config.gstin"] = gstin
        doc = coll.find_one(query)
        if not doc:
            # Try without the enabled filter (owner might not have flipped it)
            # -- still dark because _env_enabled() gates above this.
            doc = coll.find_one({"type": "einvoice"})
        if not doc:
            return {}
        # BUG-155: secrets are Fernet-encrypted at rest; decrypt for provider use.
        from api.services import cred_crypto

        return cred_crypto.decrypt_config(doc.get("config") or {})
    except Exception as exc:  # noqa: BLE001
        logger.debug("[EINVOICE] creds load failed for gstin=%s: %s", gstin, exc)
        return {}


# ---------------------------------------------------------------------------
# E-invoice JSON builder (maps gstn_export shape)
# ---------------------------------------------------------------------------


def _build_einvoice_json(order: Dict[str, Any]) -> Dict[str, Any]:
    """Build the NIC e-invoice API request payload from an IMS order/invoice doc.

    The NIC IRP JSON schema (Schema v1.1) maps almost 1:1 from the b2b/itm_det
    shape already built in gstn_export.py. We derive the minimal mandatory
    fields here; optional fields default to empty strings (IRP ignores extras).

    Returns a dict. Never raises (malformed input -> best-effort partial doc).
    """

    def _s(val: Any, default: str = "") -> str:
        return str(val or default).strip()

    def _n(val: Any) -> float:
        try:
            return round(float(val or 0), 2)
        except (TypeError, ValueError):
            return 0.0

    # Supply type: B2B when customer has a GSTIN, else B2C.
    cust_gstin = _s(order.get("customer_gstin") or order.get("billing_gstin"))
    supply_type = "B2B" if cust_gstin else "B2C"

    # Document identity
    inv_no = _s(
        order.get("invoice_number") or order.get("order_number") or order.get("id")
    )
    inv_date_raw = order.get("invoice_date") or order.get("created_at") or ""
    inv_date = _fmt_date_ddmmyyyy(inv_date_raw)

    # Seller GSTIN -- from the store/entity on the order
    seller_gstin = _s(order.get("store_gstin") or order.get("billing_gstin"))

    # Place of supply (2-digit state code)
    pos = _s(
        order.get("place_of_supply") or order.get("state_code") or seller_gstin[:2]
        if len(seller_gstin) >= 2
        else "20"
    )

    # Tax classification: inter-state -> IGST, else CGST+SGST
    igst = _n(order.get("igst") or order.get("igst_amount"))
    cgst = _n(order.get("cgst") or order.get("cgst_amount"))
    sgst = _n(order.get("sgst") or order.get("sgst_amount"))
    taxable = _n(order.get("taxable_amount") or order.get("subtotal"))
    total = _n(order.get("grand_total") or order.get("total"))

    # Items list -- map from order.items[] when present
    items = _build_item_list(order.get("items") or [])
    if not items:
        # Fallback: single consolidated line item from order totals
        gst_rate = _n(order.get("gst_rate") or 0)
        items = [
            {
                "SlNo": "1",
                "PrdDesc": _s(order.get("description") or "Goods"),
                "IsServc": "N",
                "HsnCd": _s(order.get("hsn_code") or "9004"),
                "Qty": _n(order.get("quantity") or 1),
                "Unit": "PCS",
                "UnitPrice": _n(order.get("unit_price") or taxable),
                "TotAmt": taxable,
                "Discount": _n(order.get("discount_total")),
                "AssAmt": taxable,
                "GstRt": gst_rate,
                "IgstAmt": igst,
                "CgstAmt": cgst,
                "SgstAmt": sgst,
                "CesRt": 0.0,
                "CesAmt": 0.0,
                "CesNonAdvlAmt": 0.0,
                "StateCesRt": 0.0,
                "StateCesAmt": 0.0,
                "StateCesNonAdvlAmt": 0.0,
                "OthChrg": 0.0,
                "TotItemVal": total,
            }
        ]

    val_details = {
        "AssVal": taxable,
        "CgstVal": cgst,
        "SgstVal": sgst,
        "IgstVal": igst,
        "CesVal": 0.0,
        "StCesVal": 0.0,
        "Discount": _n(order.get("discount_total")),
        "OthChrg": 0.0,
        "RndOffAmt": 0.0,
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
            "Stcd": _s(seller_gstin[:2] if len(seller_gstin) >= 2 else "20"),
            "Ph": _s(order.get("store_phone") or ""),
            "Em": _s(order.get("store_email") or ""),
        },
        "BuyerDtls": buyer,
        "ItemList": items,
        "ValDtls": val_details,
    }
    return payload


def _build_item_list(items: list) -> list:
    """Map order.items[] -> NIC ItemList[]. Fail-soft: bad items are skipped."""
    out = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        try:

            def _n(val: Any) -> float:
                try:
                    return round(float(val or 0), 2)
                except (TypeError, ValueError):
                    return 0.0

            taxable = _n(
                item.get("taxable_amount") or item.get("price") or item.get("subtotal")
            )
            qty = _n(item.get("quantity") or 1)
            unit_price = _n(
                item.get("unit_price") or (taxable / qty if qty else taxable)
            )
            discount = _n(item.get("discount") or item.get("discount_amount"))
            igst = _n(item.get("igst") or item.get("igst_amount"))
            cgst = _n(item.get("cgst") or item.get("cgst_amount"))
            sgst = _n(item.get("sgst") or item.get("sgst_amount"))
            gst_rate = _n(item.get("gst_rate") or item.get("tax_rate"))
            line_total = _n(
                item.get("total")
                or item.get("item_total")
                or taxable + igst + cgst + sgst
            )
            out.append(
                {
                    "SlNo": str(idx),
                    "PrdDesc": str(
                        item.get("name") or item.get("product_name") or "Item"
                    )[:300],
                    "IsServc": "N",
                    "HsnCd": str(item.get("hsn_code") or item.get("hsn") or "9004"),
                    "Qty": qty,
                    "Unit": "PCS",
                    "UnitPrice": unit_price,
                    "TotAmt": taxable,
                    "Discount": discount,
                    "AssAmt": taxable,
                    "GstRt": gst_rate,
                    "IgstAmt": igst,
                    "CgstAmt": cgst,
                    "SgstAmt": sgst,
                    "CesRt": 0.0,
                    "CesAmt": 0.0,
                    "CesNonAdvlAmt": 0.0,
                    "StateCesRt": 0.0,
                    "StateCesAmt": 0.0,
                    "StateCesNonAdvlAmt": 0.0,
                    "OthChrg": 0.0,
                    "TotItemVal": line_total,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[EINVOICE] skipping malformed item %d: %s", idx, exc)
    return out


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
    # Fallback: today
    today = datetime.now(timezone.utc)
    return today.strftime("%d/%m/%Y")


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


def _persist_irn(db, order_id: str, irn_fields: Dict[str, Any]) -> None:
    """Persist IRN + ack_no + ack_date + signed_qr + einvoice_status onto the
    orders collection doc. Idempotent: safe to call multiple times. Fail-soft.
    """
    if not order_id or not db:
        return
    try:
        now = datetime.now(timezone.utc)
        update: Dict[str, Any] = {
            "irn": irn_fields.get("irn") or "",
            "ack_no": irn_fields.get("ack_no") or "",
            "ack_date": irn_fields.get("ack_date") or "",
            "einvoice_signed_qr": irn_fields.get("signed_qr") or "",
            "einvoice_status": "GENERATED",
            "einvoice_generated_at": now,
        }
        # Try orders collection first (POS orders), then invoices
        for collection_name in ("orders", "invoices"):
            coll = db.get_collection(collection_name)
            result = coll.update_one(
                {
                    "$or": [
                        {"id": order_id},
                        {"order_id": order_id},
                        {"invoice_id": order_id},
                    ]
                },
                {"$set": update},
            )
            if result.matched_count > 0:
                logger.info(
                    "[EINVOICE] persisted IRN %s on %s/%s",
                    irn_fields.get("irn"),
                    collection_name,
                    order_id,
                )
                return
        logger.warning(
            "[EINVOICE] order/invoice %s not found for IRN persist", order_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[EINVOICE] persist_irn failed for %s: %s", order_id, exc)


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
    GENERATED -- IRN successfully obtained from IRP and persisted.
    FAILED    -- gate was up but IRP call failed; order is unchanged.
    """
    order = order or {}
    order_id = str(
        order.get("id") or order.get("order_id") or order.get("invoice_id") or ""
    )

    # ── Idempotency: skip if IRN already on the doc ──────────────────────
    existing_irn = order.get("irn") or order.get("einvoice_irn")
    if existing_irn:
        return {
            "status": STATUS_SKIPPED,
            "irn": existing_irn,
            "ack_no": order.get("ack_no"),
            "ack_date": order.get("ack_date"),
            "signed_qr": order.get("einvoice_signed_qr"),
            "reason": "IRN already exists on this order -- skipping",
            "einvoice_json": None,
        }

    gstin = str(order.get("store_gstin") or order.get("billing_gstin") or "")

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
    einvoice_json = _build_einvoice_json(order)

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
    if order_id:
        _persist_irn(db, order_id, irn_fields)

    return {
        "status": STATUS_GENERATED,
        "irn": irn_fields.get("irn"),
        "ack_no": irn_fields.get("ack_no"),
        "ack_date": irn_fields.get("ack_date"),
        "signed_qr": irn_fields.get("signed_qr"),
        "reason": None,
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

    gstin = str(order.get("store_gstin") or order.get("billing_gstin") or "")

    if not _env_enabled():
        return {
            "status": STATUS_SIMULATED,
            "reason": "DARK: IMS_EINVOICE_ENABLED is not set",
        }

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
            if order_id and db:
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
