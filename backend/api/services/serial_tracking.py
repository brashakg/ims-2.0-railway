"""Feature #6 -- Luxury / per-unit SERIAL tracking.

A unique serial number is captured at STOCK-IN for serialized / high-value
items (hearing aids, luxury frames, watches -- per-category flag via E2
policy), tracked through the SALE, and looked up later for warranty / recall.

This is the per-UNIT layer (owner decision 2026-06-09 #9: hearing-aid serial
is per-UNIT at stock-in, NOT at the catalogue level). It REUSES the existing
``stock_units`` collection (also used by F21 quarantine + N4 returns) and the
existing ``orders`` flow -- it does NOT fork either.

Serial lifecycle (status on the stock_unit doc)::

    IN_STOCK  --(at sale, atomic guarded find_one_and_update)-->  SOLD
       ^                                                            |
       |                                                            v
       +-------------------- RETURNED <----------------------------+
                                |
                                v
                            RECALLED   (terminal-ish; recall can also act
                                        directly on a SOLD unit)

Key guarantees:
- The serial is UNIQUE (partial unique index wired at startup) so two
  stock-ins of the same serial -> the second is rejected (409).
- The IN_STOCK -> SOLD transition is a guarded ``find_one_and_update`` keyed
  on ``status == IN_STOCK`` so the SAME serial can NEVER be double-sold: two
  concurrent sales of one serial -> exactly one wins.
- It attaches to the sale as an inventory side-effect ONLY -- it does NOT
  touch the order total or any money capture.

Money, if any, is integer paise. Everything is store-scoped.
"""

from __future__ import annotations

# Stock-unit serial lifecycle states.
STATUS_IN_STOCK = "IN_STOCK"
STATUS_SOLD = "SOLD"
STATUS_RETURNED = "RETURNED"
STATUS_RECALLED = "RECALLED"

# Allowed forward transitions for a serialized stock_unit.
ALLOWED_TRANSITIONS = {
    STATUS_IN_STOCK: {STATUS_SOLD, STATUS_RECALLED},
    STATUS_SOLD: {STATUS_RETURNED, STATUS_RECALLED},
    STATUS_RETURNED: {STATUS_IN_STOCK, STATUS_RECALLED},
    STATUS_RECALLED: set(),
}


def normalize_serial(s):
    """Canonicalize a raw serial for storage + uniqueness comparison.

    Trims surrounding whitespace, collapses internal whitespace, and
    upper-cases. Returns an empty string for ``None`` / non-string / blank
    input so callers can reject it cleanly. Pure -- no I/O.
    """
    if not isinstance(s, str):
        return ""
    # Collapse any run of internal whitespace to nothing-significant: keep a
    # single token form. Serials are alnum-ish; we strip and upper-case and
    # remove interior spaces so "ab 12" and "AB12" collide.
    return "".join(s.split()).upper()
