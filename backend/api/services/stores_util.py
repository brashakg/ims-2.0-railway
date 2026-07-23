"""
IMS 2.0 - Store-type helpers (ONLINE-store guard rails)
=======================================================
W1.4 / RC-B: an ONLINE store (store_type == "ONLINE", e.g. BV-ONLINE-01 /
WO-ONLINE-01) owns NO physical stock, has NO till and NO walk-ins -- it sells
the pooled stock of every physical shop via the Shopify storefront. Nothing
outside the online-store module used to read store_type, so POS, purchase/GRN,
stock transfers and the cash register all happily accepted an ONLINE store.

This module is the single backend source of truth for "is this store ONLINE?".
It mirrors the frontend detector (frontend/src/utils/storeMode.ts): explicit
store_type wins, with the known online-store ids as a belt-and-braces fallback
so the guards still hold if a store doc is missing or the DB lookup flakes.

Cost: one indexed find_one on the tiny `stores` collection (store_id is the
repository id field). Fail-open by design for unknown ids: a flaky lookup must
NEVER false-block a physical store's revenue path -- the known-id list still
catches the live online stores even with the DB down.
"""

from __future__ import annotations

from typing import Optional

ONLINE_STORE_TYPE = "ONLINE"

# Known ONLINE store ids (created 2026-07-20). Belt-and-braces fallback that
# keeps the guards effective even when the store doc / DB is unavailable.
# Mirrors ONLINE_STORE_IDS in frontend/src/utils/storeMode.ts -- keep in sync.
KNOWN_ONLINE_STORE_IDS = frozenset({"BV-ONLINE-01", "WO-ONLINE-01"})


def _resolve_db(db):
    """Return a usable db handle: the one passed in, else a lazily-resolved
    connection. Never raises -- guard call sites must stay exception-safe."""
    if db is not None:
        return db
    try:
        from database.connection import get_db

        conn = get_db()
        return getattr(conn, "db", None)
    except Exception:  # noqa: BLE001
        return None


def is_online_store(db, store_id: Optional[str]) -> bool:
    """True when ``store_id`` refers to an ONLINE (stockless, pooled-
    fulfilment) store.

    Decision order: known-id allow-list (no DB needed), then the store doc's
    store_type via one indexed find_one. ``db`` may be None -- it is resolved
    lazily; any lookup failure returns False (fail-open) so a physical store
    is never false-blocked by an infrastructure hiccup.
    """
    sid = str(store_id or "").strip()
    if not sid:
        return False
    if sid in KNOWN_ONLINE_STORE_IDS:
        return True
    handle = _resolve_db(db)
    if handle is None:
        return False
    try:
        doc = handle.get_collection("stores").find_one(
            {"store_id": sid}, {"store_type": 1}
        )
    except Exception:  # noqa: BLE001
        return False
    if doc is None:
        return False
    return str(doc.get("store_type") or "").strip().upper() == ONLINE_STORE_TYPE
