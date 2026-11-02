"""Test helpers for the S3 per-piece transfer flow.

Older transfer tests drove ship / receive by QUANTITY (ship took any AVAILABLE
units; receive took "2 received, 1 damaged"). The flow is now per piece: the
sending shop scans pieces OUT (pack), ship moves exactly those, the receiving
shop scans pieces IN. These helpers keep those tests about what they test --
the paper, the books, the guards -- by packing and scanning the pieces a
quantity names, the way staff do on the box page.
"""

from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def _db(transfers):
    db = transfers._get_db()
    assert db is not None, "the per-piece flow needs a database (mongomock is fine)"
    return db


def pack_all(transfers, tid, user, per_line=None):
    """Pack each line with the first free pieces at the sending shop: the
    quantity asked, or per_line[line_id]. Returns the packed barcodes."""
    t = transfers._get_transfer(tid)
    units = _db(transfers).get_collection("stock_units")
    codes = []
    for line in t.get("items") or []:
        want = (per_line or {}).get(line.get("id"), transfers._qty(line.get("quantity_requested")))
        free = units.find(
            {"product_id": line.get("product_id"), "store_id": t.get("from_location_id"),
             "status": "AVAILABLE", "barcode": {"$nin": codes}},
        )
        got = [u["barcode"] for u in free if u.get("barcode")][:want]
        codes.extend(got)
    if codes:
        _run(transfers.pack_transfer(tid, transfers.TransferPack(barcodes=codes), user))
    return codes


def ship(transfers, tid, user, courier="DTDC", tracking="D41092277", pack=True, per_line=None):
    """Pack (unless already packed) and ship with a courier + tracking no."""
    t = transfers._get_transfer(tid)
    if pack and not any(ln.get("packed_stock_ids") for ln in t.get("items") or []):
        pack_all(transfers, tid, user, per_line)
    return _run(transfers.ship_transfer(tid, tracking, None, courier, False, user))


def receive(transfers, tid, user, lines=None):
    """Scan pieces in. `lines`: {line_id: (received, damaged)} -- `received`
    pieces of the line, the last `damaged` of them damaged (good first, as the
    old quantity receive did). None = every shipped piece, none damaged."""
    t = transfers._get_transfer(tid)
    units = _db(transfers).get_collection("stock_units")
    out = None
    for line in t.get("items") or []:
        if lines is not None and line.get("id") not in lines:
            continue
        shipped = [str(s) for s in line.get("shipped_stock_ids") or []]
        got, damaged = lines[line.get("id")] if lines is not None else (len(shipped), 0)
        for n, sid in enumerate(shipped[:got]):
            code = (units.find_one({"stock_id": sid}) or {}).get("barcode") or sid
            bad = n >= got - damaged
            out = _run(transfers.receive_transfer(
                tid,
                transfers.TransferPieceReceive(
                    barcode=code, damaged=bad, damage_reason="damaged in the box" if bad else None
                ),
                user,
            ))
    return out


def receive_complete(transfers, tid, user, lines=None):
    receive(transfers, tid, user, lines)
    return _run(transfers.complete_transfer(tid, None, user))


def move_units(transfers, transfer):
    """Ship a raw transfer dict and land every piece at the destination
    through the transfer module's own moves (_apply_ship_stock_move, then the
    re-home write _rehome_patch) -- for tests about what a move does to a
    unit's record, not about the screens. Packs the first free pieces of
    each line (quantity_requested)."""
    units = _db(transfers).get_collection("stock_units")
    for line in transfer.get("items") or []:
        want = transfers._qty(line.get("quantity_requested"))
        free = units.find({"product_id": line.get("product_id"),
                           "store_id": transfer.get("from_location_id"), "status": "AVAILABLE"})
        line["packed_stock_ids"] = [str(u["stock_id"]) for u in free][:want]
    transfers._apply_ship_stock_move(transfer)
    for line in transfer.get("items") or []:
        for sid in line.get("shipped_stock_ids") or []:
            units.update_one(
                {"stock_id": sid, "status": "TRANSFERRED", "transfer_id": transfer.get("id")},
                {"$set": transfers._rehome_patch(transfer, "AVAILABLE")},
            )
            line.setdefault("received_stock_ids", []).append(sid)
        transfers._settle_line_counts(line)
    return transfer
