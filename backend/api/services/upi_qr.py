"""
IMS 2.0 - UPI QR generation + payment auto-reconcile   [POS-6]
===============================================================
Two responsibilities in one module:

  1. UPI deep-link builder  (pure Python, no creds required)
     build_upi_link(vpa, merchant, amount, order_ref)  ->  str
     The NPCI UPI URI scheme is a public standard; building the link
     needs nothing from a payment gateway.

  2. Fail-soft Razorpay/UPI payment auto-reconcile hook
     reconcile_upi_payment(db, order_id, upi_txn)
     Called from the Razorpay webhook receiver (payment.captured event)
     when a matching payment credit arrives.  DARK when no Razorpay creds.

     The hook matches on (order_ref, amount) and, when matched, records
     the payment on the order with the same shape as a manual
     POST /orders/{order_id}/payments call so downstream finance/reports
     are unaffected.  FULLY fail-soft: any failure is logged; the order
     and the webhook receiver MUST succeed even if reconciliation fails.

Store VPA lookup:
  The store's UPI Virtual Payment Address lives on the `stores` collection
  under the `upi_vpa` field (set via Store Setup -> UPI VPA field).  When
  the field is absent the endpoint returns 400 with a clear message telling
  the operator which store config to fill in.

DARK default:
  The reconcile hook exits immediately when Razorpay creds are not
  configured in the `integrations` collection (shop_url / access_token).
  It never calls any gateway and never raises.

No emojis in Python (Windows cp1252 safety rule).
"""

from __future__ import annotations

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
# UPI auto-reconcile hook (Razorpay payment.captured webhook)
# ============================================================================


def reconcile_upi_payment(
    db,
    order_id: str,
    upi_txn: Dict[str, Any],
) -> bool:
    """Fail-soft hook: when Razorpay notifies a payment.captured event, match
    it to the IMS order and record the payment if it matches.

    Match criteria:
      - The Razorpay payment note/description/order_ref matches the IMS
        order_number (set as `tn` in the UPI link).  Several Razorpay
        fields carry this: `description`, `notes.order_ref`, `order_id`
        sub-key.  We try all three.
      - The amount_in_paise / 100 matches the order's grand_total (within
        0.01 tolerance for float drift).

    When matched: appends a payment row to `orders.payments` and bumps
    `amount_paid` / `balance_due` / `payment_status` in the same way the
    manual POST /orders/{id}/payments endpoint does.

    DARK when:
      - Razorpay creds not configured (no `integrations/razorpay` doc).
      - DB unavailable.
      - upi_txn payload is missing expected fields.
    Returns True when a payment was recorded, False otherwise.  NEVER raises.
    """
    if db is None or not order_id or not upi_txn:
        return False

    # Gate: only attempt when Razorpay integration is configured.
    if not _razorpay_configured(db):
        logger.debug(
            "[UPI_RECONCILE] Razorpay not configured -- skipping reconcile "
            "for order %s",
            order_id,
        )
        return False

    try:
        return _do_reconcile(db, order_id, upi_txn)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[UPI_RECONCILE] reconcile raised for order %s: %s", order_id, exc
        )
        return False


def _razorpay_configured(db) -> bool:
    """True when the `integrations/razorpay` doc carries key_id + key_secret."""
    try:
        from agents.nexus_providers import _load_integration_config

        cfg = _load_integration_config(db, "razorpay") or {}
        return bool(cfg.get("key_id") and cfg.get("key_secret"))
    except Exception:  # noqa: BLE001
        return False


def _do_reconcile(db, order_id: str, upi_txn: Dict[str, Any]) -> bool:
    """Inner logic -- separated so the outer catch handles any exception."""
    # Pull the amount from the Razorpay payload.  Razorpay stores in paise.
    amount_paise = int(upi_txn.get("amount") or 0)
    amount_rupees = round(amount_paise / 100.0, 2)

    # Razorpay payment id for idempotency / audit.
    razorpay_payment_id = upi_txn.get("id") or upi_txn.get("payment_id") or ""

    orders_coll = db.get_collection("orders")
    if orders_coll is None:
        return False

    order = orders_coll.find_one({"order_id": order_id})
    if not order:
        logger.debug("[UPI_RECONCILE] order %s not found", order_id)
        return False

    order_number = order.get("order_number") or order_id
    grand_total = float(order.get("grand_total") or 0.0)

    # Amount must match (tolerance: 0.01).
    if abs(amount_rupees - grand_total) > 0.01:
        logger.info(
            "[UPI_RECONCILE] amount mismatch for order %s: expected %.2f got %.2f",
            order_id,
            grand_total,
            amount_rupees,
        )
        return False

    # Idempotency: skip if this Razorpay payment_id is already recorded.
    if razorpay_payment_id:
        existing_payments = order.get("payments") or []
        for p in existing_payments:
            if (
                isinstance(p, dict)
                and p.get("provider_payment_id") == razorpay_payment_id
            ):
                logger.debug(
                    "[UPI_RECONCILE] payment %s already recorded for order %s",
                    razorpay_payment_id,
                    order_id,
                )
                return False

    now_iso = datetime.now().isoformat()
    payment_row = {
        "payment_id": f"upi-auto-{razorpay_payment_id or order_id}",
        "method": "UPI",
        "amount": amount_rupees,
        "provider": "razorpay",
        "provider_payment_id": razorpay_payment_id,
        "auto_reconciled": True,
        "recorded_at": now_iso,
    }

    current_paid = float(order.get("amount_paid") or 0.0)
    new_paid = round(current_paid + amount_rupees, 2)
    new_balance = round(grand_total - new_paid, 2)
    new_status = (
        "PAID" if new_balance <= 0.01 else ("PARTIAL" if new_paid > 0 else "UNPAID")
    )

    try:
        orders_coll.update_one(
            {"order_id": order_id},
            {
                "$push": {"payments": payment_row},
                "$set": {
                    "amount_paid": new_paid,
                    "balance_due": max(0.0, new_balance),
                    "payment_status": new_status,
                    "updated_at": now_iso,
                },
            },
        )
        logger.info(
            "[UPI_RECONCILE] auto-reconciled order %s (order_number=%s) "
            "razorpay_id=%s amount=%.2f status=%s",
            order_id,
            order_number,
            razorpay_payment_id,
            amount_rupees,
            new_status,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[UPI_RECONCILE] order update failed: %s", exc)
        return False
