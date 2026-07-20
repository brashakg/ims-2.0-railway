"""
IMS 2.0 - Consent-gated ad-audience export (Phase 0 foundation, DARK)
=====================================================================
Turns IMS customers into hashed Customer-Match / Custom-Audience rows for
Google Ads and Meta -- WITHOUT ever talking to Google or Meta. This module is
export-file only: it builds the payload; a later phase (NEXUS/conversion_outbox)
owns the actual upload once creds + DISPATCH_MODE are live.

What this gives Phase 1 for free
--------------------------------
* An ``audience`` list: customers who EXPLICITLY opted in to AD_AUDIENCE sharing
  and are not otherwise suppressed -- the people we may target / match.
* A ``suppression`` list: customers who opted OUT (global marketing/DND opt-out
  OR an explicit AD_AUDIENCE withdrawal) but still carry a contact identifier --
  the "add-AND-delete" delete side, so we stop paying Google/Meta to re-acquire
  someone who told us to stop. This is the suppression-first foundation the
  roadmap calls the fastest visible win.

Consent gating (roadmap: "export gated on is_opted_out + _active_purposes_from_ledger")
--------------------------------------------------------------------------------------
A customer is placed in the AUDIENCE only when BOTH hold:
  1. AD_AUDIENCE is an ACTIVE purpose in their DPDP consent ledger
     (customers._active_purposes_from_ledger), AND
  2. they are NOT opted out (marketing.is_opted_out -- the single unified gate
     over marketing_consent + the WhatsApp STOP ledger).
A customer is placed in SUPPRESSION when they are opted out, or their latest
AD_AUDIENCE ledger event is a withdrawal. Everyone else with no AD_AUDIENCE
opt-in is simply skipped (never shared, nothing to delete).

Owner decision honoured: an EMAIL-ONLY customer is a valid ad contact. Rows keep
their contact_tier so email-only contacts are tagged and auto-upgrade to a
phone tier the moment a phone is captured (keys are recomputed live every run --
there is no stored hash to go stale).

DARK contract: no network I/O, no credentials read, no customer writes. Fail-soft
everywhere -- a missing DB or a bad row yields an empty/short result, never a 500.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from api.services.match_keys import build_match_keys, contact_tier

logger = logging.getLogger(__name__)

# Hard ceiling on customers scanned per run so a dark export can never run away
# on a large collection (mirrors campaign_segments._SCAN_LIMIT sizing).
DEFAULT_SCAN_LIMIT = 5000

# The DPDP purpose that authorises third-party ad-platform sharing.
AD_AUDIENCE_PURPOSE = "AD_AUDIENCE"

_VALID_PROVIDERS = ("generic", "google", "meta")

# Per-provider column names for the two hashed identifiers. Google Customer Match
# upload CSV uses "Email"/"Phone" (hashed); Meta Custom Audience uses the
# EMAIL_SHA256 / PHONE_SHA256 schema keys. "generic" keeps our internal names.
_PROVIDER_FIELDS: Dict[str, Dict[str, str]] = {
    "generic": {"phone_sha256": "phone_sha256", "email_sha256": "email_sha256"},
    "google": {"phone_sha256": "Phone", "email_sha256": "Email"},
    "meta": {"phone_sha256": "PHONE_SHA256", "email_sha256": "EMAIL_SHA256"},
}


@dataclass
class AudienceRow:
    """One export row. `keys` holds only the hashed identifiers present."""

    customer_id: str
    action: str  # "ADD" (audience) | "REMOVE" (suppression)
    contact_tier: str  # PHONE_AND_EMAIL | PHONE_ONLY | EMAIL_ONLY
    keys: Dict[str, str] = field(default_factory=dict)


@dataclass
class AudienceExportResult:
    """Full export payload plus the counts a summary view needs."""

    provider: str = "generic"
    audience: List[AudienceRow] = field(default_factory=list)
    suppression: List[AudienceRow] = field(default_factory=list)
    # Counts (always populated, even in summary-only mode).
    scanned: int = 0
    audience_count: int = 0
    suppression_count: int = 0
    email_only_count: int = 0
    phone_count: int = 0  # audience rows carrying a phone hash
    skipped_no_contact: int = 0
    skipped_no_ad_consent: int = 0
    store_id: Optional[str] = None
    generated_at: str = ""
    note: str = ""


# ---------------------------------------------------------------------------
# Consent helpers
# ---------------------------------------------------------------------------


def _active_purposes(customer_id: str) -> set:
    """Active DPDP purposes for a customer (canonical ledger replay). Fail-soft."""
    try:
        from api.routers.customers import _active_purposes_from_ledger

        return set(_active_purposes_from_ledger(customer_id))
    except Exception:  # noqa: BLE001
        return set()


def _is_opted_out(customer_id: str, db) -> bool:
    """Unified marketing/DND opt-out gate (canonical). Fail-soft to False."""
    try:
        from api.routers.marketing import is_opted_out

        return bool(is_opted_out(customer_id, db))
    except Exception:  # noqa: BLE001
        return False


def _ad_audience_withdrawn(customer_id: str, db) -> bool:
    """True iff the customer's LATEST AD_AUDIENCE consent event is a withdrawal.

    Reads the dpdp_consent_ledger directly (newest-first) and inspects only the
    most recent row that mentions AD_AUDIENCE. Used to route a partial
    ad-audience opt-out (customer still marketing-consented, just not for ad
    sharing) onto the suppression/delete list.
    """
    if db is None:
        return False
    try:
        rows = (
            db.get_collection("dpdp_consent_ledger")
            .find({"customer_id": customer_id}, {"_id": 0})
            .sort("created_at", -1)
        )
        for row in rows:
            if AD_AUDIENCE_PURPOSE in (row.get("purposes") or []):
                return row.get("event_type") == "WITHDRAWN"
    except Exception:  # noqa: BLE001
        return False
    return False


# ---------------------------------------------------------------------------
# Core scan
# ---------------------------------------------------------------------------


def _customers_query(store_id: Optional[str]) -> Dict[str, Any]:
    """Reuse the canonical multi-field store scoping from campaign_segments."""
    try:
        from api.services.campaign_segments import _customers_query as _cq

        return _cq(store_id)
    except Exception:  # noqa: BLE001
        return {"store_id": store_id} if store_id else {}


def _build(
    db,
    *,
    store_id: Optional[str],
    provider: str,
    scan_limit: int,
    collect_rows: bool,
) -> AudienceExportResult:
    """Shared engine for both the summary (collect_rows=False) and the full
    export (collect_rows=True). Always returns a valid result -- never raises."""
    result = AudienceExportResult(
        provider=provider,
        store_id=store_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    if provider not in _VALID_PROVIDERS:
        result.note = f"Unknown provider '{provider}'; expected one of {_VALID_PROVIDERS}."
        return result

    if db is None:
        result.note = "Database unavailable -- returning empty export."
        return result

    try:
        cursor = (
            db.get_collection("customers")
            .find(
                _customers_query(store_id),
                {
                    "_id": 0,
                    "customer_id": 1,
                    "mobile": 1,
                    "phone": 1,
                    "email": 1,
                    "marketing_consent": 1,
                },
            )
            .limit(int(scan_limit))
        )
        customers = list(cursor)
    except Exception as exc:  # noqa: BLE001
        logger.error("[AUDIENCE_EXPORT] customer scan failed (fail-soft): %s", exc)
        result.note = "Customer scan failed -- returning empty export."
        return result

    for customer in customers:
        result.scanned += 1
        customer_id = customer.get("customer_id")
        keys = build_match_keys(customer)
        if not keys:
            result.skipped_no_contact += 1
            continue
        tier = contact_tier(keys)

        if not customer_id:
            # No id -> cannot check consent -> cannot lawfully share. Skip.
            result.skipped_no_ad_consent += 1
            continue

        opted_out = _is_opted_out(customer_id, db)
        ad_active = AD_AUDIENCE_PURPOSE in _active_purposes(customer_id)

        if ad_active and not opted_out:
            result.audience_count += 1
            if "phone_sha256" in keys:
                result.phone_count += 1
            if tier == "EMAIL_ONLY":
                result.email_only_count += 1
            if collect_rows:
                result.audience.append(
                    AudienceRow(
                        customer_id=customer_id,
                        action="ADD",
                        contact_tier=tier,
                        keys=dict(keys),
                    )
                )
        elif opted_out or _ad_audience_withdrawn(customer_id, db):
            result.suppression_count += 1
            if collect_rows:
                result.suppression.append(
                    AudienceRow(
                        customer_id=customer_id,
                        action="REMOVE",
                        contact_tier=tier,
                        keys=dict(keys),
                    )
                )
        else:
            # Has a contact key but never opted in to ad sharing: not shared,
            # nothing to delete.
            result.skipped_no_ad_consent += 1

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def summarize_ad_audience(
    db,
    *,
    store_id: Optional[str] = None,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
) -> AudienceExportResult:
    """Counts-only view: how many customers are exportable / suppressed / etc.
    Returns NO hashes (safe for a dashboard). Never raises."""
    return _build(
        db,
        store_id=store_id,
        provider="generic",
        scan_limit=scan_limit,
        collect_rows=False,
    )


def build_ad_audience_export(
    db,
    *,
    store_id: Optional[str] = None,
    provider: str = "generic",
    scan_limit: int = DEFAULT_SCAN_LIMIT,
) -> AudienceExportResult:
    """Full DARK export: hashed audience ADD rows + suppression REMOVE rows in
    the requested provider's field naming. No network calls. Never raises."""
    return _build(
        db,
        store_id=store_id,
        provider=provider,
        scan_limit=scan_limit,
        collect_rows=True,
    )


def format_row(row: AudienceRow, provider: str) -> Dict[str, Any]:
    """Render one AudienceRow as a plain dict using the provider's field names."""
    fields = _PROVIDER_FIELDS.get(provider, _PROVIDER_FIELDS["generic"])
    out: Dict[str, Any] = {
        "customer_id": row.customer_id,
        "action": row.action,
        "contact_tier": row.contact_tier,
    }
    for internal, external in fields.items():
        if internal in row.keys:
            out[external] = row.keys[internal]
    return out


def to_csv(result: AudienceExportResult) -> str:
    """Render the full export as CSV text (audience + suppression together).

    Columns: action, contact_tier, <provider phone col>, <provider email col>.
    A genuine dark export artifact -- caller decides whether to stream it as a
    file download. Rows with a missing identifier leave that cell blank.
    """
    fields = _PROVIDER_FIELDS.get(result.provider, _PROVIDER_FIELDS["generic"])
    phone_col = fields["phone_sha256"]
    email_col = fields["email_sha256"]
    header = ["action", "contact_tier", phone_col, email_col]
    lines = [",".join(header)]
    for row in list(result.audience) + list(result.suppression):
        lines.append(
            ",".join(
                [
                    row.action,
                    row.contact_tier,
                    row.keys.get("phone_sha256", ""),
                    row.keys.get("email_sha256", ""),
                ]
            )
        )
    return "\n".join(lines) + "\n"
