"""
IMS 2.0 - UPI QR generation + payment auto-reconcile   [POS-6]
===============================================================
Two responsibilities in one module:

  1. UPI deep-link builder  (pure Python, no creds required)
     build_upi_link(vpa, merchant, amount, order_ref)  ->  str
     The NPCI UPI URI scheme is a public standard; building the link
     needs nothing from a payment gateway.

  2. Razorpay/UPI payment auto-reconcile
     reconcile_razorpay_event(db, payload)
     Called by NEXUS while it drains one verified Razorpay inbox row, so
     inline and queued delivery post the same event the same way.  DARK
     when no Razorpay creds.

     Only a captured INR payment, matched to its bill by order_ref, is
     posted -- through OrderRepository.add_payment, the manual tender's own
     write (never above what the bill still owes, once per payment id).  A
     payment that names a bill but cannot be credited raises, so its inbox
     row reads failed (retried, then dead-lettered), never succeeded.  The
     webhook's 200 to Razorpay never depends on it.

Store VPA lookup:
  The store's UPI Virtual Payment Address lives on the `stores` collection
  under the `upi_vpa` field (set in Organization, the store's UPI VPA).  When
  the field is absent the endpoint returns 400 with a clear message telling
  the operator which store config to fill in.

DARK default:
  The reconcile hook exits immediately when Razorpay creds are not
  configured in the `integrations` collection (key_id / key_secret).
  It never calls any gateway.

No emojis in Python (Windows cp1252 safety rule).
"""

from __future__ import annotations

from api.utils.event_time import utc_now_iso

import logging
import urllib.parse
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# UPI deep-link builder (public NPCI standard -- no creds)
# ============================================================================


def build_upi_link(
    vpa: str,
    merchant: str,
    amount: float,
    order_ref: str,
    currency: str = "INR",
) -> str:
    """Build an NPCI UPI deep-link for an order.

    Format: upi://pay?pa=<vpa>&pn=<merchant>&am=<amount>&cu=<currency>&tn=<ref>

    All parameters are percent-encoded.  `amount` is formatted to 2 decimal
    places (UPI expects a decimal string, not integer paise).  Returns the
    link string; never raises.
    """
    params = {
        "pa": str(vpa or "").strip(),
        "pn": str(merchant or "Better Vision").strip(),
        "am": "{:.2f}".format(max(0.0, float(amount or 0.0))),
        "cu": str(currency or "INR").strip().upper(),
        "tn": str(order_ref or "").strip(),
    }
    return "upi://pay?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def build_qr_data_uri(upi_link: str) -> Optional[str]:
    """If `qrcode` (the PyPI package) is installed, render the UPI link as a
    base-64 PNG data-URI (for embedding in an <img> tag or printing).  Returns
    None when the library is absent -- the endpoint falls back to the link only.

    `qrcode` is an optional dep; most IMS deploys may not have it.  Fail-soft.
    """
    try:
        import io
        import base64
        import qrcode  # type: ignore[import]  # optional dep  # pylint: disable=import-error

        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(upi_link)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception as exc:  # noqa: BLE001
        logger.debug("[UPI_QR] qrcode lib unavailable or failed: %s", exc)
        return None


# ============================================================================
# Store VPA resolver -- reads `stores` collection
# ============================================================================


def _resolve_store_vpa(db, store_id: str) -> Optional[str]:
    """Look up the store's UPI VPA from the `stores` collection.  Fail-soft.

    Also checks the `integrations` collection for a per-tenant UPI config
    (key `upi`) as a fallback (for multi-store setups sharing one VPA).

    Returns the VPA string, or None when unavailable.
    """
    if db is None or not store_id:
        return None

    # 1. Per-store VPA (preferred, per-store income separation).
    try:
        coll = db.get_collection("stores")
        doc = coll.find_one({"store_id": store_id})
        if doc:
            vpa = (doc.get("upi_vpa") or "").strip()
            if vpa:
                return vpa
    except Exception as exc:  # noqa: BLE001
        logger.debug("[UPI_QR] store VPA lookup failed: %s", exc)

    # 2. Tenant-wide UPI integration config.
    try:
        from agents.nexus_providers import _load_integration_config

        cfg = _load_integration_config(db, "upi") or {}
        vpa = (cfg.get("vpa") or "").strip()
        if vpa:
            return vpa
    except Exception as exc:  # noqa: BLE001
        logger.debug("[UPI_QR] integration VPA lookup failed: %s", exc)

    return None


def _resolve_merchant_name(db, store_id: str) -> str:
    """Best-effort merchant display name from the store doc.  Fail-soft."""
    try:
        if db is None or not store_id:
            return "Better Vision"
        coll = db.get_collection("stores")
        doc = coll.find_one({"store_id": store_id})
        if doc:
            return doc.get("store_name") or doc.get("name") or "Better Vision"
    except Exception:  # noqa: BLE001
        pass
    return "Better Vision"



# ============================================================================
# UPI auto-reconcile (Razorpay payment.captured webhook)
# ============================================================================


class ReconcileMismatch(Exception):
    """The event names an IMS bill but cannot be credited to it. Raised so the
    event's inbox row records status=failed (retried hourly, then a dead letter on the
    sync-health card) -- never 'succeeded' on unfinished work."""


def reconcile_razorpay_event(db, payload: Dict[str, Any]) -> str:
    """Post ONE verified Razorpay inbox event to its IMS bill.

    Called by NEXUS's razorpay handler while it drains THAT inbox row, so the
    inline and queued deliveries run the same code on the same event, and
    the row only reads succeeded once this returned. Returns the outcome
    stamped on the row; raises (ReconcileMismatch or anything else) when the
    event names an IMS bill and could not be credited.

    Only payment.captured credits: payment.authorized is a hold, not money,
    and its captured event follows. The bill is matched on the order_number
    carried in the UPI tn= note (notes.order_ref / notes.order_number /
    description). DARK only when a config read that WORKED shows Razorpay
    creds are not configured; a failed read raises like any other outage.
    """
    if str((payload or {}).get("event") or "").lower() != "payment.captured":
        return "ignored"
    if not _razorpay_configured(db):
        return "dark"
    entity = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
    notes = entity.get("notes") or {}
    order_ref = (
        notes.get("order_ref") or notes.get("order_number") or entity.get("description") or ""
    )
    order = db.get_collection("orders").find_one({"order_number": order_ref}) if order_ref else None
    if not order:
        # Not an IMS bill (no ref, or a ref IMS never issued): nothing to post.
        return "no_matching_order"
    return _do_reconcile(db, order.get("order_id") or "", entity)


def _razorpay_configured(db) -> bool:
    """True when the `integrations/razorpay` doc carries key_id + key_secret.

    Strict: False ends the event as dark for good, so only a read that worked
    may answer it. A failed read raises and the row stays failed and retried.
    """
    from agents.nexus_providers import _load_integration_config

    cfg = _load_integration_config(db, "razorpay", strict=True)
    return bool(cfg.get("key_id") and cfg.get("key_secret"))


def _do_reconcile(db, order_id: str, upi_txn: Dict[str, Any]) -> str:
    """Credit one captured rupee payment to the bill, exactly once.

    Capture and currency are read from the payment itself, never inferred
    from an amount that happens to match. The money is posted through
    OrderRepository.add_payment -- the manual tender's own write: over-tender
    is refused against what the bill still owes (never added on top of a
    paid bill), the write is a compare-and-set on what was read, and the
    `razorpay:<id>` idempotency key makes a concurrent twin land once.
    """
    pid = str(upi_txn.get("id") or upi_txn.get("payment_id") or "").strip()
    if not pid:
        raise ReconcileMismatch("payment carries no Razorpay payment id")
    if upi_txn.get("status") != "captured" or upi_txn.get("captured") is not True:
        raise ReconcileMismatch(f"payment {pid} is not captured (status {upi_txn.get('status')!r})")
    currency = str(upi_txn.get("currency") or "").upper()
    if currency != "INR":
        raise ReconcileMismatch(f"payment {pid} is in {currency or 'no currency'}, not INR")
    amount_paise = upi_txn.get("amount")
    if type(amount_paise) is not int or amount_paise <= 0:
        raise ReconcileMismatch(f"payment {pid} has no positive paise amount ({amount_paise!r})")
    amount_rupees = round(amount_paise / 100.0, 2)

    from database.repositories.order_repository import OrderRepository

    repo = OrderRepository(db.get_collection("orders"))
    order = repo.find_by_id(order_id)
    if not order:
        raise ReconcileMismatch(f"order {order_id} not found for payment {pid}")
    # Rows written before the idempotency key existed carry only the id.
    if any(isinstance(p, dict) and p.get("provider_payment_id") == pid
           for p in order.get("payments") or []):
        _confirm_draft(db, repo, order_id)  # a retry finishes a lifecycle step a crash cut off
        return "already_recorded"

    payment_row = {
        "payment_id": f"upi-auto-{pid}",
        "method": "UPI",
        "amount": amount_rupees,
        "provider": "razorpay",
        "provider_payment_id": pid,
        "auto_reconciled": True,
        "recorded_at": utc_now_iso(),
        "idempotency_key": f"razorpay:{pid}",
    }
    try:
        recorded = repo.add_payment(order_id, payment_row)
    except ValueError as exc:  # over-tender: more than the bill still owes
        raise ReconcileMismatch(f"payment {pid} Rs {amount_rupees:.2f}: {exc}") from exc
    if not recorded:
        raise ReconcileMismatch(
            f"order {order_id} did not take payment {pid} (cancelled, held by an "
            f"advance refund, or changed meanwhile)")
    _confirm_draft(db, repo, order_id)
    logger.info("[UPI_RECONCILE] order %s credited razorpay_id=%s amount=%.2f",
                order_id, pid, amount_rupees)
    return "credited"


def _confirm_draft(db, repo, order_id: str) -> None:
    """The manual payment door's lifecycle step, through its own rule, strict:
    raises while the bill is DRAFT, unreadable, or its workshop handoff is
    unknown, so the row stays failed and its retry comes back here."""
    from api.routers.orders.workshop import confirm_and_hand_off, ensure_workshop_job
    from database.repositories.workshop_repository import WorkshopJobRepository

    jobs = WorkshopJobRepository(db.get_collection("workshop_jobs"))
    confirm_and_hand_off(
        repo, order_id, "razorpay-webhook",
        hand_off=lambda order, by: ensure_workshop_job(order, by, jobs, repo),
    )
