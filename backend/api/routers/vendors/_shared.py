"""Vendors package - shared imports, router, constants and vendor GSTIN rules."""

import logging
import re
import io
import hashlib

from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional
from datetime import datetime, timedelta
import uuid
from ..auth import get_current_user, require_roles
from ...dependencies import (
    get_vendor_repository,
    get_purchase_order_repository,
    get_grn_repository,
    get_stock_repository,
    get_vendor_portal_token_repository,
    get_audit_repository,
    get_product_repository,
    get_store_repository,
    validate_store_access,
    can_access_store_scoped,
    resolve_store_scope,
)
from ...services import ap_engine
from ...services import org_validation as ov
from ...utils.ist import fy_start_year_ist, ist_date_str, now_ist
from ...services import product_master as _pm
from ...services.purchase_invoice_engine import (
    normalize_invoice_no as _normalize_invoice_no,
)
from ...services.reorder_policy import auto_reorder_disabled as _auto_reorder_disabled
from ...services.file_store import (
    get_file_store,
    ALLOWED_MIME_TYPES,
    MAX_FILE_SIZE_BYTES,
)


def _po_catalog_gate_on() -> bool:
    """Hub Phase 2: is the PO catalog gate enabled? DARK by default so the manual
    free-text Create-PO flow keeps working until the Buy Desk product picker ships.
    The GRN ghost-stock gate is independent and ALWAYS on. Fail-soft: OFF on error."""
    try:
        from ...services.policy_engine import get_policy

        return bool(get_policy("pm.po_catalog_gate", default=False))
    except Exception:  # noqa: BLE001
        return False


router = APIRouter()
# The package name, so every record keeps the exact logger name the flat
# module emitted ("api.routers.vendors"); tests attach handlers to it.
logger = logging.getLogger(__package__)


# Roles permitted to mutate vendors, purchase orders and goods-receipt notes.
# Mirrors the frontend /purchase/* route guards. SUPERADMIN auto-passes.
_VENDOR_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")

# The metadata.kind this router's own GRN upload stamps. Anything else in the
# shared GridFS bucket belongs to another feature and must never be bound to,
# or served from, a GRN.
_GRN_DOCUMENT_KIND = "grn_document"

# ONE message for every attachment rejection -- forged id, wrong kind, another
# store's document -- so the create endpoint cannot be used as an existence or
# ownership oracle over the shared bucket.
_ATTACHMENT_INVALID_DETAIL = {
    "code": "ATTACHMENT_INVALID",
    "message": (
        "The attached file is no longer available or invalid. Please re-upload."
    ),
}

# Tighter set for money-out / accounts-payable writes (bills, payments, debit
# notes). Recording a payable or releasing cash is an accounting action, so it
# is limited to ADMIN / ACCOUNTANT (SUPERADMIN auto-passes via require_roles).
_AP_ROLES = ("ADMIN", "ACCOUNTANT")

# A PO can have goods received against it while it is en route or partially
# delivered. "PARTIAL" is the legacy single-word status; "PARTIALLY_RECEIVED"
# is what the PO repository's find_pending/find_overdue use -- accept both so a
# part-received PO stays receivable for the remaining lines.
_RECEIVABLE_PO_STATUSES = (
    "SENT",
    "ACKNOWLEDGED",
    "PARTIAL",
    "PARTIALLY_RECEIVED",
)


def _get_db():
    """Direct DB handle for the accounts-payable collections (vendor_bills,
    vendor_payments, vendor_debit_notes). Matches finance.py's pattern -- these
    collections have no repository factory and are queried directly."""
    from database.connection import get_db

    return get_db().db


# W1.4 / OS-006: shared ONLINE store-type detector. An ONLINE store owns no
# stock, so a PO can never deliver to it and a GRN can never receive at it.
from ...services.stores_util import is_online_store  # noqa: E402


# ============================================================================
# SCHEMAS
# ============================================================================


# Indian GSTIN format: 2-digit state code + 10-char PAN + 1 entity + Z + 1 check.
# Shape only -- the state-code list and the check digit are enforced by
# org_validation (the ONE GSTIN validator in this codebase; entities, stores and
# the purchase GST engine all use it, so a vendor cannot be judged by a laxer
# rule than the invoice it will appear on).
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")


def validate_vendor_gstin(value: Optional[str]) -> Optional[str]:
    """Normalise + fully validate a vendor GSTIN, or raise naming the problem.

    Returns the uppercased GSTIN, or None when nothing was supplied (an
    UNREGISTERED / COMPOSITION / OVERSEAS vendor legitimately has none).

    A shape-only check is not enough here: a single mistyped character can turn
    a Maharashtra GSTIN into a Jharkhand one, which silently flips a purchase
    between IGST and CGST+SGST. The GSTN check digit catches exactly that, so
    the number is refused with a message that says WHICH part is wrong.
    """
    if value is None or str(value).strip() == "":
        return None
    cleaned = str(value).strip().upper()
    if not _GSTIN_RE.match(cleaned):
        raise ValueError(
            "GSTIN must be 15 characters in the format "
            "NN-AAAAA-9999A-9Z9 (e.g. 27AAPFU0939F1ZV). "
            f"This one is {len(cleaned)} character(s) and does not match."
        )
    if cleaned[:2] not in ov.INDIAN_STATE_CODES:
        raise ValueError(
            f"GSTIN starts with state code '{cleaned[:2]}', which is not an "
            "Indian GST state code. The first two digits are the state."
        )
    if not ov.validate_gstin(cleaned):
        raise ValueError(
            "GSTIN check digit does not match the rest of the number - one of "
            "the 15 characters is mistyped. Please re-read it off the vendor's "
            "invoice."
        )
    return cleaned


def derive_vendor_state(gstin: Optional[str], typed_state: Optional[str]):
    """Return (state_code, state_name) for a vendor.

    The GSTIN WINS whenever there is one: its first two digits are the state,
    so asking the user to also pick a state only creates a way for the two to
    disagree. With no GSTIN (unregistered vendor) the typed state is normalised
    to the canonical GST code where it can be resolved ("MH" / "Maharashtra" ->
    "27"); an unrecognised value is kept verbatim with no code, so the record
    still says what the user meant instead of silently blanking.
    """
    code = ov.gstin_state_code(gstin) if gstin else None
    if code and code in ov.INDIAN_STATE_CODES:
        return code, ov.INDIAN_STATE_CODES[code]
    typed = (typed_state or "").strip()
    if not typed:
        return None, typed
    normalised = str(ov.normalize_state_code(typed) or "").strip()
    if normalised in ov.INDIAN_STATE_CODES:
        return normalised, ov.INDIAN_STATE_CODES[normalised]
    return None, typed
