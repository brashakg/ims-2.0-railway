"""
IMS 2.0 - F17/#25 Chart-of-accounts seed (idempotent)
=====================================================
Seeds the minimal chart of accounts the manual journal-entry (maker-checker)
feature needs. ``allow_manual_je`` gates which heads accept a manual JE; system-
managed balances (GST output/input, stock) are seeded with it False so the
accountant can never hand-post against them.

Idempotent: a re-run upserts the same rows (a no-op once seeded). The app also
self-seeds on startup (api/main.py lifespan) and on first chart-of-accounts read,
so this script is for an explicit one-off / verification run.

Usage (run via Railway so it reaches the production Mongo):
  railway run --service MongoDB bash -c \
    'MONGODB_URL="$MONGO_PUBLIC_URL" .venv/Scripts/python.exe \
     backend/scripts/seed_chart_of_accounts.py'
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def main() -> int:
    from database.connection import get_db
    from api.services.je_service import ensure_indexes, seed_chart_of_accounts

    db = get_db().db
    if db is None:
        print("[seed_chart_of_accounts] No DB connection; nothing seeded.")
        return 1
    ensure_indexes(db)
    n = seed_chart_of_accounts(db)
    print(f"[seed_chart_of_accounts] Ensured {n} chart-of-accounts row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
