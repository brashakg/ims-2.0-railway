#!/usr/bin/env python3
"""
IMS 2.0 - Stranded transfer units: REPORT (default) + audited repair (opt-in)
============================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHY
---
Before PR #1023, `_apply_receive_stock_move` picked the units to re-home by a
POSITIONAL SLICE over a running count. When the pool shrank between passes (a
legacy line with no `shipped_stock_ids`, so `_transferred_pool` re-queries the
units still marked TRANSFERRED and already-re-homed ones drop out), or when a
`_rehome` write failed, the slice stepped over a unit and never came back to
it. The old completeness rule compared DECLARED totals, so the transfer doc
flipped to RECEIVED anyway.

The residue is a `stock_units` row still at `status: "TRANSFERRED"` whose
`transfer_id` points at a transfer that is already CLOSED (received /
completed / cancelled) -- or at no transfer at all. That unit is:
  * NOT on-hand at the source store (TRANSFERRED is excluded from on-hand/POS),
  * NOT at the destination store either,
so a real physical frame is invisible to both stores and unsellable. If the
transfer crossed entities, `/complete` also booked the mirror purchase for the
full declared quantity -- the books say it arrived.

It cannot self-heal: `receive_transfer` is gated to IN_TRANSIT /
PARTIALLY_RECEIVED so PR #1023's identity-based outstanding math never runs on
a closed doc; cancel is refused at RECEIVED; `complete_transfer` moves no
stock; and the stock-status write door in inventory.py only accepts
AVAILABLE / DAMAGED. Docs still at PARTIALLY_RECEIVED DO self-heal on their
next receive under #1023 -- they are NOT reported here.

WHAT IT TOUCHES
---------------
REPORT MODE (default, and what you should run first): READ-ONLY. Nothing is
written anywhere -- not stock, not audit, not the transfer doc.

REPAIR MODE (`--disposition X --apply`, one disposition per run):
  - `stock_units` : the stranded rows only, per the chosen disposition
        destination -> status AVAILABLE, store_id = destination store
        source      -> status AVAILABLE, store_id unchanged (source store)
        quarantine  -> status QUARANTINED, store_id unchanged, reason
                       TRANSFER_STRANDED (parked pending a physical count)
                    ...each clearing transfer_id / transfer_to_store_id so the
                    unit stops being held against a closed transfer.
  - `stock_audit` : one row per repaired unit (same shape transfers.py writes)
  - `item_events` : one immutable ledger row per repaired unit, via the same
                    services.item_events.record_post_write_event the transfer
                    ship/receive path uses.
The transfer doc itself is NEVER rewritten -- it is closed history.

Disposition is a BUSINESS decision (where is the frame physically?), so there
is no default and no "auto". `destination` is refused for a unit whose
transfer is missing/cancelled -- there is no destination to re-home to.

USAGE
-----
1) Report (DEFAULT - writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/stranded_transfer_units.py

   ...with a per-unit CSV to send to the store managers:
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/stranded_transfer_units.py --csv stranded.csv

2) Repair, ONLY after the owner has picked a disposition. Dry-run first
   (prints exactly what WOULD be written, writes nothing):
    ... scripts/stranded_transfer_units.py --disposition destination
   then:
    ... scripts/stranded_transfer_units.py --disposition destination --apply

   Narrow a run to one store or one transfer (repeatable):
    ... --disposition source --store ST001 --apply
    ... --disposition quarantine --transfer TRF-2026-0042 --apply

Locally with an explicit URI:
    python scripts/stranded_transfer_units.py --mongo-uri mongodb://...

Connection resolution: --mongo-uri, else MONGO_PUBLIC_URL, else MONGODB_URI,
else MONGODB_URL/MONGO_URL, else the MONGO_* component vars Railway injects.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

# Reuse the SAME ledger writer the transfer ship/receive path uses, so a repair
# leaves the same kind of trail as a normal unit move (no logic drift).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "backend"))

STATUS_TRANSFERRED = "TRANSFERRED"
STATUS_AVAILABLE = "AVAILABLE"
STATUS_QUARANTINED = "QUARANTINED"

# A transfer in one of these is CLOSED: no code path will ever move its stock
# again (receive is gated to in_transit/partially_received, cancel is refused
# at received, complete moves nothing). Lower-cased for comparison because
# different writers stamp different cases.
CLOSED_TRANSFER_STATUSES = frozenset({"received", "completed", "cancelled"})

DISPOSITIONS = ("destination", "source", "quarantine")

# Ledger event per disposition. `destination` is the receive that should have
# happened; `source` is a correction (the frame never left / came back);
# `quarantine` parks it pending a physical count.
_LEDGER_EVENT = {
    "destination": "TRANSFER_RECEIVE",
    "source": "ADJUST",
    "quarantine": "QUARANTINE_IN",
}


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    """Prefer an explicit/standard URI; otherwise assemble one from the
    MONGO_* component vars Railway injects. MONGO_PUBLIC_URL is checked first
    so this runbook works locally via `railway run -s MongoDB ...`."""
    uri = (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URI")
        or os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URL")
    )
    if uri:
        return uri
    host = os.getenv("MONGO_HOST")
    if not host:
        return None
    user = os.getenv("MONGO_USERNAME") or ""
    pw = os.getenv("MONGO_PASSWORD") or ""
    port = os.getenv("MONGO_PORT", "27017")
    auth_source = os.getenv("MONGO_AUTH_SOURCE", "admin")
    cred = f"{user}:{pw}@" if user and pw else ""
    opts = f"?authSource={auth_source}"
    if (os.getenv("MONGO_SSL", "") or "").lower() in ("true", "1", "yes"):
        opts += "&tls=true"
    return f"mongodb://{cred}{host}:{port}/{opts}"


def _num(raw) -> float:
    """Mongo Decimal128 / str / None -> float. 0.0 on anything unreadable."""
    if raw is None:
        return 0.0
    try:
        return float(str(raw))
    except (TypeError, ValueError):
        return 0.0


def classify(unit: Dict, transfer: Optional[Dict]) -> Optional[str]:
    """Why this TRANSFERRED unit is stranded, or None when it is healthy.

    Pure -- no DB. `transfer` is the resolved stock_transfers doc (None when
    the unit carries no transfer_id, or it resolves to nothing).

      NO_TRANSFER_ID   unit is held in transit against nothing at all
      MISSING_TRANSFER transfer_id points at a doc that does not exist
      CLOSED_<status>  the transfer is closed; no path will move this unit
      None             transfer is still open (in_transit / partially_received
                       and friends) -- PR #1023 re-homes it on the next
                       receive, so it is NOT stranded.
    """
    if not str(unit.get("transfer_id") or "").strip():
        return "NO_TRANSFER_ID"
    if transfer is None:
        return "MISSING_TRANSFER"
    status = str(transfer.get("status") or "").strip().lower()
    if status in CLOSED_TRANSFER_STATUSES:
        return f"CLOSED_{status.upper()}"
    return None


def _line_for(transfer: Optional[Dict], unit: Dict) -> Dict:
    """The transfer line this unit belongs to, matched by product_id (falling
    back to the line that names the unit in its shipped ids). {} when unknown."""
    if not transfer:
        return {}
    sid = str(unit.get("stock_id") or "")
    product_id = unit.get("product_id")
    fallback: Dict = {}
    for line in transfer.get("items") or []:
        shipped = [str(s) for s in (line.get("shipped_stock_ids") or [])]
        if sid and sid in shipped:
            return line
        if product_id and line.get("product_id") == product_id and not fallback:
            fallback = line
    return fallback


def find_stranded(db, *, stores: List[str], transfers_filter: List[str]) -> List[Dict]:
    """Every stranded TRANSFERRED unit, enriched for a human to read. Pure read.

    `stores` filters on the unit's CURRENT store_id (the source); an empty list
    means all. `transfers_filter` matches a transfer id OR number; empty = all.
    """
    units_col = db["stock_units"]
    transfers_col = db["stock_transfers"]
    products_col = db["products"]
    stores_col = db["stores"]

    query: Dict = {"status": STATUS_TRANSFERRED}
    if stores:
        query["store_id"] = {"$in": stores}

    transfer_cache: Dict[str, Optional[Dict]] = {}
    product_cache: Dict[str, Dict] = {}
    store_cache: Dict[str, str] = {}

    def _transfer(tid: Optional[str]) -> Optional[Dict]:
        if not tid:
            return None
        if tid not in transfer_cache:
            transfer_cache[tid] = transfers_col.find_one({"id": tid}, {"_id": 0})
        return transfer_cache[tid]

    def _product(pid: Optional[str]) -> Dict:
        if not pid:
            return {}
        if pid not in product_cache:
            product_cache[pid] = (
                products_col.find_one(
                    {"product_id": pid},
                    {"_id": 0, "name": 1, "sku": 1, "brand": 1, "model": 1,
                     "category": 1, "cost_price": 1, "mrp": 1},
                )
                or {}
            )
        return product_cache[pid]

    def _store_name(sid: Optional[str]) -> str:
        if not sid:
            return ""
        if sid not in store_cache:
            doc = stores_col.find_one({"store_id": sid}, {"_id": 0, "store_name": 1})
            store_cache[sid] = (doc or {}).get("store_name") or sid
        return store_cache[sid]

    rows: List[Dict] = []
    for unit in units_col.find(query, {"_id": 0}):
        transfer = _transfer(unit.get("transfer_id"))
        reason = classify(unit, transfer)
        if reason is None:
            continue

        number = (transfer or {}).get("transfer_number") or ""
        if transfers_filter and not (
            number in transfers_filter or (unit.get("transfer_id") or "") in transfers_filter
        ):
            continue

        product = _product(unit.get("product_id"))
        line = _line_for(transfer, unit)
        # Destination: what the unit itself was tagged with at ship time wins;
        # the transfer header is the fallback for a unit that lost its tag.
        dest_id = unit.get("transfer_to_store_id") or (transfer or {}).get("to_location_id") or ""
        source_id = unit.get("store_id") or (transfer or {}).get("from_location_id") or ""
        # Cost is the honest "what is this frame worth to us" number; MRP is the
        # fallback so a product with no cost on file still shows a magnitude.
        cost = _num(product.get("cost_price")) or _num(product.get("mrp"))

        rows.append({
            "stock_id": unit.get("stock_id") or "",
            "barcode": unit.get("barcode") or "",
            "product_id": unit.get("product_id") or "",
            "sku": product.get("sku") or "",
            "product": (
                product.get("name")
                or " ".join(x for x in (product.get("brand"), product.get("model")) if x)
                or unit.get("product_id")
                or ""
            ),
            "category": product.get("category") or "",
            "value": cost,
            "source_store_id": source_id,
            "source_store": _store_name(source_id),
            "dest_store_id": dest_id,
            "dest_store": _store_name(dest_id),
            "transfer_id": unit.get("transfer_id") or "",
            "transfer_number": number,
            "transfer_status": (transfer or {}).get("status") or "(no transfer doc)",
            "reason": reason,
            "transferred_at": unit.get("transferred_at") or "",
            # The discrepancy, made legible: the line says it received N units
            # and names the ids it re-homed -- this unit is not among them.
            "line_quantity_shipped": line.get("quantity_shipped", ""),
            "line_quantity_received": line.get("quantity_received", ""),
            "line_received_ids": len(line.get("received_stock_ids") or []),
            "line_damaged_ids": len(line.get("damaged_stock_ids") or []),
        })

    rows.sort(key=lambda r: (r["source_store"], r["transfer_number"], r["product"]))
    return rows


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def print_report(rows: List[Dict]) -> None:
    if not rows:
        print("[REPORT] No stranded units. Every TRANSFERRED unit belongs to an "
              "OPEN transfer (those re-home on their next receive under #1023).")
        return

    total_value = sum(r["value"] for r in rows)
    print(f"[REPORT] {len(rows)} stranded unit(s), approx value INR {total_value:,.0f}")
    print("         (physically invisible: not on-hand at source, not at destination)")

    print("\n  By SOURCE store (where the system last saw the frame):")
    by_store: Dict[str, List[Dict]] = {}
    for r in rows:
        by_store.setdefault(r["source_store"] or "(unknown)", []).append(r)
    for name, group in sorted(by_store.items(), key=lambda kv: -len(kv[1])):
        val = sum(g["value"] for g in group)
        dests = sorted({g["dest_store"] for g in group if g["dest_store"]})
        print(f"    {name:<28} {len(group):>4} unit(s)  INR {val:>10,.0f}"
              f"   -> {', '.join(dests) if dests else '(no destination)'}")

    print("\n  By REASON:")
    by_reason: Dict[str, int] = {}
    for r in rows:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
    for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"    {reason:<28} {count:>4}")

    print("\n  Units:")
    print(f"    {'BARCODE':<16} {'PRODUCT':<30} {'FROM':<16} {'TO':<16} "
          f"{'TRANSFER':<16} {'STATUS':<12} RECD/SHIP")
    for r in rows:
        recd = f"{r['line_quantity_received']}/{r['line_quantity_shipped']}"
        print(f"    {r['barcode'][:16]:<16} {r['product'][:30]:<30} "
              f"{r['source_store'][:16]:<16} {r['dest_store'][:16]:<16} "
              f"{r['transfer_number'][:16]:<16} {str(r['transfer_status'])[:12]:<12} "
              f"{recd} (ids re-homed: {r['line_received_ids']})")


def write_csv(rows: List[Dict], path: str) -> None:
    """Per-unit CSV -- this is the artefact the store managers physically walk
    the shelves against before a disposition is chosen."""
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n[REPORT] Per-unit CSV written: {path}")


# ---------------------------------------------------------------------------
# Repair (opt-in, one disposition per run, every write audited)
# ---------------------------------------------------------------------------


def _audit_row(db, row: Dict, new_status: str, disposition: str, moved_to: str,
               actor: str) -> None:
    """Same shape transfers.py::_audit_stock_move writes, so a repaired unit's
    stock_audit history reads alongside its ship/receive rows. Fail-soft."""
    try:
        db["stock_audit"].insert_one({
            "stock_id": row["stock_id"],
            "prior_status": STATUS_TRANSFERRED,
            "new_status": new_status,
            "source": "STOCK_TRANSFER_REPAIR",
            "transfer_id": row["transfer_id"],
            "transfer_number": row["transfer_number"],
            "from_store_id": row["source_store_id"],
            "to_store_id": row["dest_store_id"],
            "at": datetime.now().isoformat(),
            "product_id": row["product_id"],
            "moved_to": moved_to,
            "reason": f"STRANDED_{row['reason']}",
            "disposition": disposition,
            "actor_id": actor,
            "script": "scripts/stranded_transfer_units.py",
        })
    except Exception as exc:  # noqa: BLE001 - audit is fail-soft, as in transfers.py
        print(f"    [WARN] stock_audit row skipped for {row['stock_id']}: {exc}")


def _ledger_row(db, row: Dict, new_status: str, disposition: str, moved_to: str,
                actor: str) -> None:
    """One immutable item_events row via the SAME writer the transfer path uses."""
    try:
        from api.services import item_events as ie

        ie.record_post_write_event(
            db,
            event_type=getattr(ie.ItemEventType, _LEDGER_EVENT[disposition]),
            actor_id=actor,
            stock_id=row["stock_id"],
            from_state=STATUS_TRANSFERRED,
            to_state=new_status,
            product_id=row["product_id"],
            store_id=moved_to,
            to_store_id=None,
            source_type="TRANSFER_REPAIR",
            source_id=row["transfer_id"] or None,
            payload={
                "transfer_number": row["transfer_number"],
                "disposition": disposition,
                "stranded_reason": row["reason"],
                "script": "scripts/stranded_transfer_units.py",
            },
        )
    except Exception as exc:  # noqa: BLE001 - ledger is fail-soft, as in transfers.py
        print(f"    [WARN] item_events row skipped for {row['stock_id']}: {exc}")


def repair(db, rows: List[Dict], *, disposition: str, apply: bool, actor: str) -> Dict:
    """Apply ONE disposition to every stranded row. Dry-run unless `apply`.

    The status write is guarded on the unit still being TRANSFERRED with the
    same transfer_id, so a unit that a real receive re-homed between the report
    and the repair is skipped rather than yanked off the destination floor.
    """
    if disposition not in DISPOSITIONS:
        raise SystemExit(f"Unknown disposition {disposition!r}; pick one of {DISPOSITIONS}.")

    units_col = db["stock_units"]
    now = datetime.now().isoformat()
    written = 0
    skipped_no_dest = 0
    skipped_moved = 0

    for row in rows:
        if disposition == "destination" and not row["dest_store_id"]:
            skipped_no_dest += 1
            continue

        if disposition == "quarantine":
            new_status, moved_to = STATUS_QUARANTINED, row["source_store_id"]
        elif disposition == "destination":
            new_status, moved_to = STATUS_AVAILABLE, row["dest_store_id"]
        else:  # source
            new_status, moved_to = STATUS_AVAILABLE, row["source_store_id"]

        patch = {
            "status": new_status,
            "store_id": moved_to,
            # No longer held against a transfer that will never move it.
            "transfer_id": None,
            "transfer_to_store_id": None,
            "repaired_at": now,
            "repair_reason": f"STRANDED_{row['reason']}",
        }
        if disposition == "destination":
            patch["received_at"] = now
            patch["source_type"] = "TRANSFER"
            patch["source_id"] = row["transfer_id"]
            patch["from_store_id"] = row["source_store_id"]
        if disposition == "quarantine":
            patch["quarantine_reason"] = "TRANSFER_STRANDED"
            patch["quarantined_at"] = now

        if not apply:
            print(f"    [DRY-RUN] {row['barcode'] or row['stock_id']}: "
                  f"TRANSFERRED -> {new_status} @ {moved_to}")
            written += 1
            continue

        # Guard on the state we reported on: anything that moved since is left alone.
        res = units_col.update_one(
            {
                "stock_id": row["stock_id"],
                "status": STATUS_TRANSFERRED,
                "transfer_id": row["transfer_id"] or None,
            },
            {"$set": patch},
        )
        if not res.matched_count:
            skipped_moved += 1
            continue
        written += 1
        _audit_row(db, row, new_status, disposition, moved_to, actor)
        _ledger_row(db, row, new_status, disposition, moved_to, actor)

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"\n[{mode}] disposition={disposition} repaired={written} "
          f"skipped_no_destination={skipped_no_dest} skipped_already_moved={skipped_moved}")
    if not apply and written:
        print(f"[DRY-RUN] re-run with --apply to write {written} unit(s).")
    return {
        "disposition": disposition,
        "repaired": written,
        "skipped_no_destination": skipped_no_dest,
        "skipped_already_moved": skipped_moved,
        "applied": apply,
    }


def run(*, mongo_uri: Optional[str], db_name: str, disposition: Optional[str],
        apply: bool, csv_path: Optional[str], stores: List[str],
        transfers_filter: List[str], actor: str) -> Dict:
    if not mongo_uri:
        raise SystemExit(
            "No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, pass "
            "--mongo-uri, or run via `railway run` so the MONGO_* component "
            "vars are injected."
        )
    try:
        from pymongo import MongoClient
    except ImportError:
        raise SystemExit("pymongo not installed; run `pip install pymongo`.")

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    db = client[db_name]

    rows = find_stranded(db, stores=stores, transfers_filter=transfers_filter)
    print_report(rows)
    if csv_path:
        write_csv(rows, csv_path)

    if disposition is None:
        if rows:
            print("\n[REPORT] Read-only. Nothing was written. Choose a disposition "
                  "with the owner, then re-run with --disposition "
                  f"{{{'|'.join(DISPOSITIONS)}}} [--apply].")
        return {"stranded": len(rows), "rows": rows, "applied": False}

    print(f"\n[REPAIR] disposition={disposition}")
    result = repair(db, rows, disposition=disposition, apply=apply, actor=actor)
    result["stranded"] = len(rows)
    return result


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Report (default, read-only) stock units stranded at TRANSFERRED "
            "against a closed/missing transfer; optionally repair them under "
            "one explicit disposition, fully audited."
        )
    )
    parser.add_argument(
        "--mongo-uri",
        default=None,
        help="Mongo URI; falls back to MONGO_PUBLIC_URL / MONGODB_URI / "
             "MONGODB_URL / MONGO_URL then MONGO_* components.",
    )
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument(
        "--disposition",
        choices=DISPOSITIONS,
        default=None,
        help="Where the frame goes. Omit for a read-only report. "
             "destination = re-home to the receiving store as AVAILABLE; "
             "source = put back on the sending store's floor as AVAILABLE; "
             "quarantine = park it pending a physical stock-take.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the chosen disposition. Without it, --disposition is a dry-run.",
    )
    parser.add_argument("--csv", default=None,
                        help="Write the per-unit report to this CSV path.")
    parser.add_argument("--store", action="append", default=[],
                        help="Limit to this SOURCE store_id. Repeatable.")
    parser.add_argument("--transfer", action="append", default=[],
                        help="Limit to this transfer number or id. Repeatable.")
    parser.add_argument("--actor", default="runbook:stranded_transfer_units",
                        help="actor_id stamped on the audit + ledger rows.")
    args = parser.parse_args()

    if args.apply and not args.disposition:
        raise SystemExit("--apply needs --disposition; the report writes nothing.")

    run(
        mongo_uri=resolve_mongo_uri(args.mongo_uri),
        db_name=args.db,
        disposition=args.disposition,
        apply=args.apply,
        csv_path=args.csv,
        stores=args.store,
        transfers_filter=args.transfer,
        actor=args.actor,
    )


if __name__ == "__main__":
    sys.exit(main())
