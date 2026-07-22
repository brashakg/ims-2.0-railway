"""
IMS 2.0 - Shopify Payments payouts puller (read-only)
=====================================================
Pull the list of Shopify Payments PAYOUTS (the money Shopify actually settles to
the seller's bank) and mirror them into the `shopify_payouts` Mongo collection so
Finance can reconcile settlements against online orders + fees.

Scopes: needs `read_shopify_payments_payouts` (granted). READ-ONLY vs Shopify --
this module NEVER mutates the store. The single Shopify network boundary is
`shopify_push._graphql` (the same resilient/monkeypatchable client the rest of
the bridge uses); reads are reached whenever creds resolve, independent of the
push write-gate.

FAIL-SOFT + fail-CLEAN:
  * No creds -> {"ok": False, "enabled": None, "reason": "...creds..."}.
  * Store has no Shopify Payments account -> {"ok": True, "enabled": False,
    "reason": "shopify payments not enabled"} (a clean, non-error signal, NOT a
    crash) so callers can distinguish "not set up" from "call failed".
  * Any transport/parse error -> {"ok": False, ...reason...}. Never raises.

Idempotent: `upsert_payouts` keys on the Shopify payout id, so re-pulling the
same window updates rows in place rather than duplicating.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# One page is plenty for a nightly/manual pull; callers can raise it.
_DEFAULT_LIMIT = 50
_MAX_PAGE = 100

# GraphQL: the Shopify Payments account and its payouts. A null account = the
# store hasn't enabled Shopify Payments (the clean "not enabled" signal).
_PAYOUTS_QUERY = """
query ImsShopifyPayouts($first: Int!, $after: String) {
  shopifyPaymentsAccount {
    id
    payouts(first: $first, after: $after, sortKey: ISSUED_AT, reverse: true) {
      pageInfo { hasNextPage endCursor }
      edges {
        node {
          id
          status
          issuedAt
          net { amount currencyCode }
        }
      }
    }
  }
}
"""


def _coll(db, name: str):
    """Collection access tolerant of DatabaseConnection (get_collection) and the
    in-memory Mock (subscript). Fail-soft -> None."""
    if db is None:
        return None
    try:
        getter = getattr(db, "get_collection", None)
        if callable(getter):
            return getter(name)
    except Exception:  # noqa: BLE001
        pass
    try:
        return db[name]
    except Exception:  # noqa: BLE001
        return None


def _s(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def normalize_payout(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pure: flatten one Shopify payout GraphQL node into the row we store:
    {payout_id, status, date, amount, currency}. None when it has no id."""
    if not isinstance(node, dict):
        return None
    pid = _s(node.get("id"))
    if not pid:
        return None
    net = node.get("net") if isinstance(node.get("net"), dict) else {}
    amount = net.get("amount")
    try:
        amount = float(amount) if amount not in (None, "") else None
    except (TypeError, ValueError):
        amount = None
    return {
        "payout_id": pid,
        "status": _s(node.get("status")) or None,
        "date": _s(node.get("issuedAt")) or None,
        "amount": amount,
        "currency": _s(net.get("currencyCode")) or None,
    }


async def fetch_payouts(
    db,
    limit: int = _DEFAULT_LIMIT,
    *,
    graphql: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Read up to `limit` most-recent Shopify Payments payouts (newest first).

    Returns {ok, enabled, payouts, reason}:
      * ok=False + enabled=None            -> creds absent / call failed.
      * ok=True  + enabled=False           -> Shopify Payments not set up.
      * ok=True  + enabled=True + payouts   -> the settled payouts.
    Read-only; never raises. `graphql` is injectable for tests (defaults to the
    shared shopify_push._graphql boundary)."""
    out: Dict[str, Any] = {"ok": False, "enabled": None, "payouts": [], "reason": None}

    try:
        from .shopify_push import _has_shopify_creds, _graphql
    except Exception as exc:  # noqa: BLE001
        return {**out, "reason": f"import error: {exc}"}

    if not _has_shopify_creds(db):
        return {**out, "reason": "shopify creds not configured -- payouts pull skipped"}

    gql = graphql or _graphql
    first = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_PAGE))

    try:
        body = await gql(db, _PAYOUTS_QUERY, {"first": first, "after": None})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHOPIFY_PAYOUTS] graphql call failed: %s", exc)
        return {**out, "reason": f"shopify graphql error: {exc}"}

    if body.get("errors"):
        return {**out, "reason": f"shopify errors: {str(body.get('errors'))[:200]}"}

    account = (body.get("data") or {}).get("shopifyPaymentsAccount")
    if not account:
        # Clean, non-error signal: the store never enabled Shopify Payments.
        return {"ok": True, "enabled": False, "payouts": [], "reason": "shopify payments not enabled"}

    edges = ((account.get("payouts") or {}).get("edges")) or []
    payouts: List[Dict[str, Any]] = []
    for edge in edges:
        node = edge.get("node") if isinstance(edge, dict) else None
        row = normalize_payout(node or {})
        if row:
            payouts.append(row)

    return {"ok": True, "enabled": True, "payouts": payouts, "reason": None}


def upsert_payouts(db, payouts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Idempotently upsert payout rows into `shopify_payouts`, keyed on
    payout_id. Returns {upserted, updated, total}. Fail-soft; never raises."""
    summary = {"upserted": 0, "updated": 0, "total": 0}
    coll = _coll(db, "shopify_payouts")
    if coll is None or not payouts:
        summary["total"] = len(payouts or [])
        return summary
    now_iso = datetime.now(timezone.utc).isoformat()
    for row in payouts:
        pid = _s(row.get("payout_id"))
        if not pid:
            continue
        summary["total"] += 1
        try:
            res = coll.update_one(
                {"payout_id": pid},
                {
                    "$set": {**row, "synced_at": now_iso},
                    "$setOnInsert": {"first_seen_at": now_iso},
                },
                upsert=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SHOPIFY_PAYOUTS] upsert failed for %s: %s", pid, exc)
            continue
        if getattr(res, "upserted_id", None):
            summary["upserted"] += 1
        else:
            summary["updated"] += 1
    return summary


async def pull_payouts(
    db,
    apply: bool = False,
    limit: int = _DEFAULT_LIMIT,
    *,
    graphql: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Orchestrator: fetch payouts and (when apply=True) upsert them.

    apply=False (default) is a DRY RUN -- reads Shopify, reports what WOULD be
    written, mutates nothing in Mongo. Read-only vs Shopify either way.
    Fail-soft: returns a structured summary, never raises."""
    fetched = await fetch_payouts(db, limit=limit, graphql=graphql)
    result: Dict[str, Any] = {
        "applied": bool(apply),
        "ok": fetched.get("ok"),
        "enabled": fetched.get("enabled"),
        "reason": fetched.get("reason"),
        "fetched": len(fetched.get("payouts") or []),
        "upserted": 0,
        "updated": 0,
    }
    if not fetched.get("ok") or not fetched.get("enabled"):
        return result

    if apply:
        wrote = upsert_payouts(db, fetched.get("payouts") or [])
        result["upserted"] = wrote.get("upserted", 0)
        result["updated"] = wrote.get("updated", 0)
    else:
        # Dry-run surfaces a small sample so the operator can eyeball it.
        result["sample"] = (fetched.get("payouts") or [])[:5]
    return result
