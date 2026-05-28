"""
IMS 2.0 - bool(PyMongo Database) regression guard (QA F10)
==========================================================
Routers whose `_get_db()` returns `get_db().db` hand back a RAW PyMongo
`Database`. In PyMongo 4.x, `bool(Database)` raises
`NotImplementedError: Database objects do not implement truth value testing`.
So `if not db:` / `if db:` on those handles 500s the endpoint (it crashed the
ENTIRE analytics-v2 router + marketing referrals/NPS in prod).

The fix is `if db is None:` / `if db is not None:`. This guard fails CI if the
bare-truthiness antipattern reappears in the routers known to hold a raw
`Database`, so the whole class of outage can't silently come back.

(Routers that hold the wrapper -- `get_db()` / `get_seeded_db()`, which DO
implement bool() -- are intentionally not scanned; their `if db:` is safe.)
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROUTERS = os.path.normpath(os.path.join(HERE, "..", "api", "routers"))

# Files whose module-level `_get_db()` returns `get_db().db` (a raw Database).
RAW_DB_ROUTERS = ["analytics_v2.py", "marketing.py"]

# Bare truth-test on a `db` name: `if db:`, `if not db:`, `if not db or ...`,
# `if db and ...` is the SAFE wrapper idiom and is allowed; we only ban the
# bare-`db` truthiness with nothing guarding it.
BANNED = re.compile(r"^\s*if\s+(not\s+)?db\s*:\s*$")
BANNED_INLINE = re.compile(r"^\s*if\s+not\s+db\s+or\b")


def test_raw_db_routers_use_is_none_not_bool():
    offenders = []
    for fname in RAW_DB_ROUTERS:
        path = os.path.join(ROUTERS, fname)
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if BANNED.match(line) or BANNED_INLINE.match(line):
                    offenders.append(f"{fname}:{i}: {line.strip()}")
    assert not offenders, (
        "bool(PyMongo Database) antipattern reintroduced (raises in PyMongo 4.x "
        "-> 500). Use `if db is None:` / `if db is not None:` instead:\n"
        + "\n".join(offenders)
    )
