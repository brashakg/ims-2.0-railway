"""Regression guard: PyMongo 4.x removed __bool__ on Collection/Database objects.

Code like `if collection:` raises NotImplementedError at runtime — silently
hiding every error in a stack trace and returning 500 to the user.

The QA sweep on 2026-05-27 found this in settings.py:871 — every
integrations Configure Save returned 500 because the handler used
`if collection:` after `_get_settings_collection(...)`. The same pattern
silently broke every other settings GET that defensively checked the
collection too.

The fix is to write `if collection is not None:` everywhere.

This test fails CI if the bad pattern reappears anywhere under
backend/api/routers/. Whitelist comments where the variable is genuinely
NOT a pymongo Collection (use `# pylint: disable=...` or rename it).
"""

from __future__ import annotations

import re
from pathlib import Path

# Pattern: `if <name>:` or `if not <name>:` at the start of a line (any indent)
# where <name> is one of the pymongo-collection variable names we know about.
# We restrict the names so unrelated truthy checks (`if config:` etc.) don't
# fire false positives.
_COLLECTION_NAMES = (
    "coll",
    "collection",
)

_PATTERNS = [
    re.compile(
        rf"^\s*if(\s+not)?\s+({name})\s*:\s*$",
        re.MULTILINE,
    )
    for name in _COLLECTION_NAMES
]

_BACKEND_ROOT = Path(__file__).resolve().parent.parent / "api"


def test_no_pymongo_collection_truthiness_in_routers() -> None:
    """No `if collection:` style checks under backend/api/routers/ or services/.

    PyMongo 4.x raises NotImplementedError on `bool(collection)`. The handler
    crashes with a 500 before the actual DB write runs. Use
    `if collection is not None:` instead.
    """
    bad: list[str] = []
    scan_dirs = [_BACKEND_ROOT / "routers", _BACKEND_ROOT / "services"]
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for py_file in scan_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for pattern in _PATTERNS:
                for match in pattern.finditer(text):
                    # Locate the line number for a useful error message.
                    line_no = text[: match.start()].count("\n") + 1
                    bad.append(f"{py_file.relative_to(_BACKEND_ROOT.parent)}:{line_no}: {match.group(0).strip()}")
    assert not bad, (
        "PyMongo 4.x raises on `bool(collection)`. Use `if X is not None:` instead.\n"
        "Found:\n  " + "\n  ".join(bad)
    )
