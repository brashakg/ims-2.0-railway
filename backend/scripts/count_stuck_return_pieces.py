"""READ-ONLY: pieces linked to a shipped (or later) supplier return that never
left stock (PR #1234, owner 2026-10-18).

A supplier return that is shipped, received by the vendor, replaced or
credited has sent every piece it names back to the supplier, so each linked
stock unit should be RTV (returned to vendor). This lists every unit linked to
such a return (stock_units.rtv_vendor_id = its return id) that is NOT RTV.
Nothing is written. A piece the return NAMES (its stock_ids, every return
made since PR #1234) is finished by pressing Ship again on its return; a piece
only linked (an older return that named none) needs a decision.

It also lists every return still 'saving' after 10 minutes (a create whose
request died) with the pieces it named: pressing Create again on the same
form undoes it; otherwise a manager acts on its pieces (lift them on the
Quarantine screen -- the dead link is cleared -- or name them on a new
return).

  railway run --service <backend> -- .venv\\Scripts\\python.exe backend\\scripts\\count_stuck_return_pieces.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SHIPPED_OR_LATER = ("shipped", "received_by_vendor", "replaced", "credit_issued")


def stuck_pieces(db):
    """[(return_id, return_status, stock_id, barcode, unit_status, named)] -- reads only."""
    from api.services.item_events import StockState, canonical_state

    returns = {
        r["return_id"]: (r.get("status"), set(r.get("stock_ids") or []))
        for r in db.get_collection("vendor_returns").find(
            {"status": {"$in": list(SHIPPED_OR_LATER)}}, {"return_id": 1, "status": 1, "stock_ids": 1})
        if r.get("return_id")
    }
    if not returns:
        return []
    rows = []
    for unit in db.get_collection("stock_units").find(
            {"rtv_vendor_id": {"$in": list(returns)}},
            {"stock_id": 1, "barcode": 1, "status": 1, "rtv_vendor_id": 1}):
        if canonical_state(unit.get("status")) is StockState.RTV:
            continue
        rid = unit["rtv_vendor_id"]
        rstatus, named = returns[rid]
        rows.append((rid, rstatus, unit.get("stock_id"), unit.get("barcode"), unit.get("status"),
                     unit.get("stock_id") in named))
    return sorted(rows, key=lambda r: (str(r[0]), str(r[2])))


def dead_claims(db):
    """[(return_id, store_id, claimed_at, pieces it named)] for returns still
    'saving' after SAVING_MINUTES -- reads only."""
    from datetime import datetime, timedelta, timezone

    from api.services.quarantine_hold import SAVING_MINUTES, stamp

    dead = stamp(datetime.now(timezone.utc) - timedelta(minutes=SAVING_MINUTES))
    return sorted(
        (r.get("return_id"), r.get("store_id"), r.get("claimed_at"), list(r.get("stock_ids") or []))
        for r in db.get_collection("vendor_returns").find({"status": "saving"})
        if str(r.get("claimed_at") or "") < dead
    )


def _connect():
    from database.connection import DatabaseConfig, get_db, init_db

    mongo_url = os.getenv("MONGO_PUBLIC_URL") or os.getenv("MONGODB_URL") or os.getenv("MONGO_URL")
    config = (
        DatabaseConfig.from_uri(mongo_url, database="ims_2_0") if mongo_url else DatabaseConfig.from_env()
    )
    return get_db() if init_db(config) else None


def main() -> int:
    conn = _connect()
    if conn is None or not conn.is_connected:
        print("[ERROR] Could not connect to MongoDB (run via railway run).")
        return 2
    rows = stuck_pieces(conn.db)
    print(f"pieces on a shipped supplier return that are not RTV: {len(rows)}")
    for rid, rstatus, sid, barcode, ustatus, named in rows:
        print(f"  {rid} ({rstatus})  piece {barcode or '-'} [{sid}]  status {ustatus}  "
              f"{'named: press Ship again' if named else 'linked only: needs a decision'}")
    claims = dead_claims(conn.db)
    print(f"returns still 'saving' after 10 minutes: {len(claims)}")
    for rid, store, at, pieces in claims:
        print(f"  {rid} at {store} since {at}  pieces {', '.join(pieces) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
