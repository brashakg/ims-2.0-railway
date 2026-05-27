"""
IMS 2.0 - CI grep guard against the legacy 'stock' collection
==============================================================
Background (Branch B, May 2026): the canonical stock collection is
`stock_units` (per database/schemas.py + connection.py + dependencies.py).
The legacy name `stock` was never formally provisioned - it only existed
where bare `db.get_collection("stock")` was being read. Since every formal
WRITE site goes to `stock_units`, those legacy reads silently returned
empty -> Power Grid empty, oversell reconcile reported a phantom 193 SKUs
at risk, and the TASKMASTER auto-reorder agent quietly read 0 on-hand for
every SKU and stopped firing reorder PRs.

This guard test fails CI if anyone re-introduces a legacy `get_collection("stock")`
read in backend/api/ or backend/agents/. It uses a pure-Python AST walk
instead of ripgrep so the test works the same way in CI as on a Windows
dev laptop (no rg/grep binary required).

Two allowed exceptions:
  * Migration scripts under backend/database/migrations/ (the data-move
    out of the legacy name).
  * The defensive `for coll in ("stock", "stock_units"):` loop in
    stores.py - belt-and-suspenders for one release, removed in the
    follow-up cleanup PR.
"""

from __future__ import annotations

import ast
import pathlib


BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCAN_DIRS = ("api", "agents")
ALLOWED_BASENAMES = {
    # The defensive loop is intentionally `for coll in ("stock", "stock_units")`,
    # which contains the literal string "stock" but is NOT a get_collection
    # call - the AST walk below already excludes it (we only fail when
    # "stock" appears as the SOLE arg to get_collection). Keeping the
    # filename on the allow-list as documentation for the next reader.
}


def _legacy_calls_in_file(path: pathlib.Path) -> list:
    """Return a list of (line_no, source) for every `get_collection("stock")`
    or `get_collection('stock')` call in the file. Empty list = clean."""
    try:
        src = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Binary or odd encoding - not Python code we care about.
        return []
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []

    hits: list = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match foo.get_collection(...) or get_collection(...)
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "get_collection":
            pass
        elif isinstance(func, ast.Name) and func.id == "get_collection":
            pass
        else:
            continue
        if not node.args:
            continue
        first = node.args[0]
        # The bug pattern is the literal "stock" passed as a string.
        if isinstance(first, ast.Constant) and first.value == "stock":
            hits.append((node.lineno, ast.unparse(node) if hasattr(ast, "unparse") else "get_collection('stock')"))
    return hits


def test_no_legacy_stock_collection_reads():
    """If this fails: a legacy `get_collection("stock")` read got back in.

    Fix: change it to `get_collection("stock_units")`. The split-brain bug
    this guards has now cost three prod-visible regressions (Power Grid,
    online-stock oversell phantom, TASKMASTER silent death). Don't bring
    it back.
    """
    offenders: list = []
    for sub in SCAN_DIRS:
        base = BACKEND_ROOT / sub
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            # Skip migrations - they're allowed to touch the legacy name
            # during the one-time data move.
            if "migrations" in py.parts:
                continue
            if py.name in ALLOWED_BASENAMES:
                continue
            hits = _legacy_calls_in_file(py)
            for line_no, _src in hits:
                offenders.append(f"{py.relative_to(BACKEND_ROOT)}:{line_no}")

    assert not offenders, (
        "Legacy `get_collection(\"stock\")` reads found:\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\n\nThe canonical stock collection is `stock_units` (see "
        "database/schemas.py:585 + 673 + connection.py:316). All writes "
        "go to stock_units; reads MUST go there too. Change "
        '`get_collection("stock")` -> `get_collection("stock_units")`.'
    )
