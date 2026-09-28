"""
IMS 2.0 - Per-unit barcode minting
==================================
Business rule (from the operator): a product's SKU is stable across purchases,
but every physical UNIT gets a UNIQUE barcode when it enters stock. That code is
the shop's own (IMS) barcode; the manufacturer's UPC/EAN lives on the product
(services/gtin.py) and is never minted here.

Owner ruling 2026-09-28: a unit barcode is letters and digits only, e.g.
'BV0000012345', so a code typed at the till passes its /^[A-Z0-9]{8,}$/ check
(frontend/src/components/pos/BarcodeScanner.tsx). `mint_unit_barcode` is the ONE
minter -- GRN accept, /stock/add, opening stock, return restock and serial
capture all call it. Codes minted before the ruling ('BV--91FA3858' from GRN,
'2000000000015' EAN-13s from /stock/add) stay on their units; every lookup is an
exact match on stock_units.barcode, so both formats still resolve.

Only `allocate_sequence` touches Mongo, and it fails soft (None) so a stock
intake is never blocked by the counter.
"""

from __future__ import annotations

import re
import uuid
from typing import Optional

_COUNTER_NAME = "unit_barcode_seq"
_NOT_ALNUM = re.compile(r"[^A-Z0-9]")


def allocate_sequence(counter_coll, name: str = _COUNTER_NAME) -> Optional[int]:
    """Atomically claim the next monotonic sequence from a counter doc.

    Uses a single find_one_and_update so concurrent multi-worker intakes each
    get a unique sequence with no torn reads. Fail-soft: no collection / any
    error -> None (caller falls back, never blocks the intake).
    """
    if counter_coll is None:
        return None
    try:
        from pymongo import ReturnDocument

        doc = counter_coll.find_one_and_update(
            {"_id": name},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if doc and isinstance(doc.get("seq"), int):
            return doc["seq"]
    except Exception:  # noqa: BLE001 - fail-soft, intake must not break
        return None
    return None


def mint_unit_barcode(db, store_id: Optional[str]) -> str:
    """THE barcode for one new physical unit: store prefix + 10 digits.

    The prefix is the first two letters/digits of the store id ('BV-DHN-02' ->
    'BV'). It is cosmetic: every shop of a brand shares it, so uniqueness comes
    from the body -- ONE chain-wide atomic counter (the same `unit_barcode_seq`
    the retired EAN-13 mint used), so two shops can never mint the same code.

    `db` is a raw Mongo database (or None). No counter reachable: 10 random hex
    characters instead, so a receipt is never blocked and still never gets a
    hyphen.
    """
    counter = None
    if db is not None:
        try:
            counter = db.get_collection("counters")
        except Exception:  # noqa: BLE001 - fail-soft, intake must not break
            counter = None
    seq = allocate_sequence(counter)
    # ponytail: the no-counter fallback is 40 random bits, not a guarantee; it
    # only runs when Mongo is unreachable (the insert then fails anyway).
    body = str(seq).zfill(10) if seq is not None else uuid.uuid4().hex[:10].upper()
    return _NOT_ALNUM.sub("", str(store_id or "").upper())[:2] + body
