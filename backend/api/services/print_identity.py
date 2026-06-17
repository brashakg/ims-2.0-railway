"""
IMS 2.0 - Print issuing-identity resolver (store + entity + overrides)
=====================================================================

Every printed document must carry the identity of the store that ISSUED it
(legal/trade name, address, GSTIN, phone, logo) sourced from the document's
OWN store and that store's legal entity -- never a hardcoded company name and
never blindly the logged-in user's active store.

This module centralises that resolution so every server-side print route
(delivery challan, estimate, Rx card, ...) loads the same way:

  store   = stores.find_one({"store_id": <doc.store_id>})
  entity  = entities.find_one({"entity_id": store.entity_id})
  ov      = print_template_overrides.find_one(
                {"entity_id": ..., "template_key": <key>})["fields"]

All lookups are fail-soft (return {} on a DB hiccup) BUT the caller is given a
hard guard (assert_issuing_identity) so a statutory document cannot silently
print with NO issuing-store identity -- that violates the project's
"Fail Loudly" rule. ASCII-only source (Windows cp1252 safe).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException


def _db():
    """Return the live Mongo db handle or None (never raises)."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def load_store(store_id: Optional[str]) -> Dict[str, Any]:
    """Load a store doc by store_id. Empty dict when unavailable."""
    if not store_id:
        return {}
    db = _db()
    if db is None:
        return {}
    try:
        return db.get_collection("stores").find_one({"store_id": store_id}) or {}
    except Exception:  # noqa: BLE001
        return {}


def load_entity_for_store(store: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Resolve the legal entity for a store via its entity_id. Empty when none."""
    eid = (store or {}).get("entity_id")
    if not eid:
        return {}
    db = _db()
    if db is None:
        return {}
    try:
        return db.get_collection("entities").find_one({"entity_id": eid}) or {}
    except Exception:  # noqa: BLE001
        return {}


def load_overrides(entity: Optional[Dict[str, Any]], template_key: str) -> Dict[str, Any]:
    """Load the per-entity print_template_overrides "fields" dict for a template.

    The print_overrides.py CRUD writes one row per (entity_id, template_key)
    with a "fields" dict (signatory_name, footer_terms, logo_url, ...). Returns
    {} when no row exists, the entity has no id, or the DB is down.
    """
    eid = (entity or {}).get("entity_id")
    if not eid or not template_key:
        return {}
    db = _db()
    if db is None:
        return {}
    try:
        row = db.get_collection("print_template_overrides").find_one(
            {"entity_id": eid, "template_key": template_key}
        )
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(row, dict):
        return {}
    fields = row.get("fields")
    return fields if isinstance(fields, dict) else {}


def resolve_issuing_identity(
    store_id: Optional[str], template_key: str = "tax_invoice"
) -> Dict[str, Any]:
    """Resolve the full issuing identity for a document's own store.

    Returns {"store": {...}, "entity": {...}, "overrides": {...}}. Any element
    may be {} when unavailable; the caller decides how strict to be via
    assert_issuing_identity.
    """
    store = load_store(store_id)
    entity = load_entity_for_store(store)
    overrides = load_overrides(entity, template_key)
    return {"store": store, "entity": entity, "overrides": overrides}


def assert_issuing_identity(
    store: Optional[Dict[str, Any]],
    *,
    require_gstin: bool = False,
    entity: Optional[Dict[str, Any]] = None,
) -> None:
    """Fail loudly when a statutory document cannot identify its issuing store.

    Raises HTTPException(404) when the store could not be resolved (no name) so
    a document never prints with a blank, identity-less header. When
    require_gstin is True (a GST tax document), additionally raises
    HTTPException(400) when no GSTIN resolved for the store's state -- mirroring
    orders.py get_invoice's 'store GSTIN is not configured' guard.
    """
    name = ""
    if isinstance(store, dict):
        name = str(
            store.get("store_name") or store.get("name") or ""
        ).strip()
    if not name:
        raise HTTPException(
            status_code=404,
            detail="Issuing store could not be resolved for this document. "
            "Configure the store under Organization before printing.",
        )
    if require_gstin:
        from .print_legal import _gstin_for_state, _pick

        state_code = _pick(store, "state_code")
        gstin, _ = _gstin_for_state(entity, state_code)
        # Fall back to a GSTIN persisted directly on the store doc (org module
        # derives + stores it) before failing.
        if not gstin:
            gstin = _pick(store, "gstin")
        if not gstin:
            raise HTTPException(
                status_code=400,
                detail="Store GSTIN is not configured for this state. "
                "A GST document cannot be issued without it.",
            )
