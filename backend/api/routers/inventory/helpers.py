"""Shared inventory helpers: barcode mint, db handle, on-hand rollup, FEFO/expiry."""

from ._shared import (
    Dict,
    HTTPException,
    List,
    Optional,
    _on_hand_status_clause,
    date,
    datetime,
    is_online_store,
    uuid,
)

# ============================================================================
# HELPERS
# ============================================================================


def generate_barcode(store_id: str, product_id: str) -> str:
    """Generate unique barcode for stock item"""
    short_uuid = str(uuid.uuid4())[:8].upper()
    return f"{store_id[:3]}-{short_uuid}"


def _get_db():
    """Get raw MongoDB database for collections without a dedicated repository"""
    try:
        from ...dependencies import get_db

        conn = get_db()
        if conn is not None and conn.is_connected:
            return conn.db
    except Exception:
        pass
    return None


def _reject_stock_mint_on_online_store(store_id: Optional[str], action: str) -> None:
    """F9: refuse to mint physical stock_units onto an ONLINE store.

    BV-ONLINE-01 / WO-ONLINE-01 are POOLED and STOCKLESS by design -- the
    storefront sells the physical shops' combined stock and the online store
    must never own serialized units of its own (they would be invisible to every
    shop, unsellable at any POS, and would double the on-hand rollups).

    Uses the shared ``is_online_store`` detector -- the same one behind the POS,
    PO delivery-store, GRN-accept and till guards -- so there is exactly ONE
    definition of "online store" in the backend, including its fail-open
    convention (an unknown id / flaky lookup never false-blocks a physical
    shop, while the two known online ids are caught with no DB at all)."""
    if is_online_store(_get_db(), store_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "This is an online store, which holds no stock of its own -- it "
                "sells the physical shops' pooled inventory. Switch to a "
                f"physical shop to {action}."
            ),
        )


# "This unit is physically here" is decided by item_events.on_hand_match.
#
# WHAT READS IT (measured, and differentially probed over 18 storage shapes by
# backend/tests/test_on_hand_is_one_rule.py): the count's coverage snapshot and
# its variance, the stock-count scan screen, /non-moving, /aging,
# /overstock-analysis, the CL drawer listing, the CL power grid's near-expiry
# flag (via is_on_hand -- it must agree with the grid's own on_hand column),
# the Stock Ledger (via canonical_state, because it groups BY status) and
# _on_hand_by_product.
#
# WHAT DOES NOT, and is NOT claimed to: `transfer_recommendations` below still
# matches a bare "AVAILABLE", because its other half is
# StockRepository.find_low_stock and the two must move together (POS-owned
# repository -- owner sign-off). Every ALLOCATION door in this router
# (find_one_and_update on status=="AVAILABLE") is a different question -- "may
# I take THIS unit" -- and is deliberately strict.
#
# It used to be written out separately in each place and the copies drifted, on
# CASE. The count's coverage listed ["AVAILABLE", "RESERVED"]; the aging,
# overstock, non-moving and CL readers listed their own variants; the ledger
# bucketed on `status == "RESERVED"`. A unit in the legacy lowercase
# "available" / "reserved" / "IN_STOCK" shape, or with no `status` field at
# all, was on hand to one reader and gone to the next -- so skipping that
# product cost the counter nothing and a half-walked shelf locked as a clean
# day-end. The owner ruled the blind count IS the day-end (2026-08-25), which
# is exactly the lie that must not be possible.
#
# The one deliberate difference between the two questions is RESERVED, and it
# is the `include_reserved` flag, not a second list:
#   * NOT on hand for a SALE. The unit is committed to somebody else's order,
#     so catalog availability / endless aisle / valuation must not offer it.
#   * IS on hand for a COUNT. It is still standing in this shop, so the
#     counter walking the shelf will find it and must be expected to.
#
# `_on_hand_status_clause` is item_events.on_hand_match, imported at the top of
# this module. Do not re-implement it here.


def _on_hand_by_product(
    db, product_ids: List[str], store_id: Optional[str] = None
) -> Dict[str, int]:
    """Count on-hand units per product from the serialized `stock` collection
    (one row per unit). A unit is on-hand when its status is an available one
    (or absent) and quantity > 0. Optionally scoped to a store. Fail-soft -> {}.

    This is the SELLABLE question -- RESERVED is excluded. The count asks the
    PHYSICAL one; see `_on_hand_status_clause`.
    """
    if db is None or not product_ids:
        return {}
    match: dict = {
        "product_id": {"$in": list(product_ids)},
        **_on_hand_status_clause(),
    }
    if store_id:
        match["store_id"] = store_id
    out: Dict[str, int] = {}
    try:
        for row in db.get_collection("stock_units").aggregate(
            [
                {"$match": match},
                {
                    "$group": {
                        "_id": "$product_id",
                        "n": {"$sum": {"$ifNull": ["$quantity", 1]}},
                    }
                },
            ]
        ):
            out[row["_id"]] = int(row.get("n", 0) or 0)
    except Exception:
        pass
    return out


# ----------------------------------------------------------------------------
# Contact-lens (CL) FEFO + near-expiry pure helpers (unit-tested, no DB)
# ----------------------------------------------------------------------------

# Categories that count as contact lenses across the codebase. "CL" is the
# legacy short code; "CCL" is the colour-contact short code (2026-07-05 split);
# the full enums are the current schema values.
CL_CATEGORY_CODES = ["CL", "CCL", "CONTACT_LENS", "COLORED_CONTACT_LENS"]


def _parse_expiry(value) -> Optional[datetime]:
    """Coerce a stored expiry (ISO string, date, or datetime) into a datetime.

    Returns None for missing / unparseable values so callers fail soft instead
    of raising on a single bad row.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00").split("+")[0])
    except (ValueError, TypeError):
        return None


def compute_days_until_expiry(expiry, now: Optional[datetime] = None) -> Optional[int]:
    """Whole days from `now` until `expiry` (negative = already expired).

    Returns None when the expiry is missing/unparseable. Pure + testable.
    """
    now = now or datetime.utcnow()
    parsed = _parse_expiry(expiry)
    if parsed is None:
        return None
    return (parsed - now).days


def fefo_sort(stock_rows: List[dict], now: Optional[datetime] = None) -> List[dict]:
    """First-Expiry-First-Out ordering: earliest expiry first.

    `stock_rows` are dicts that carry an `expiry_date`. Rows with no/blank
    expiry sort LAST (you'd pick a dated unit before an undated one). Stable
    for equal expiries. Pure helper — does not mutate the input list.
    """
    now = now or datetime.utcnow()

    def _key(row):
        parsed = _parse_expiry(row.get("expiry_date"))
        # None expiry -> push to the end via a far-future sentinel.
        return (parsed is None, parsed or datetime.max)

    return sorted(stock_rows, key=_key)


def partition_by_expiry(
    stock_rows: List[dict],
    near_days: int = 90,
    now: Optional[datetime] = None,
) -> Dict[str, List[dict]]:
    """Split CL stock rows into expired / near-expiry / safe / undated buckets.

    `near_days` is the configurable near-expiry alert window. Each returned row
    is annotated with `days_until_expiry`. Pure helper. Bucketing rule:
      - days < 0            -> expired
      - 0 <= days <= near   -> near_expiry
      - days > near         -> safe
      - no parseable expiry -> undated
    """
    now = now or datetime.utcnow()
    expired: List[dict] = []
    near: List[dict] = []
    safe: List[dict] = []
    undated: List[dict] = []

    for row in stock_rows:
        days = compute_days_until_expiry(row.get("expiry_date"), now)
        annotated = dict(row)
        annotated["days_until_expiry"] = days
        if days is None:
            undated.append(annotated)
        elif days < 0:
            expired.append(annotated)
        elif days <= near_days:
            near.append(annotated)
        else:
            safe.append(annotated)

    expired.sort(key=lambda r: r["days_until_expiry"])
    near.sort(key=lambda r: r["days_until_expiry"])
    return {
        "expired": expired,
        "near_expiry": near,
        "safe": safe,
        "undated": undated,
    }
