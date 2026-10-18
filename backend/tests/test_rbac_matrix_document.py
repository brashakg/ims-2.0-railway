"""The complete documented matrix must describe the actual POLICY entries.

Parse the Markdown independently of the generator so an omitted row, extra
route, duplicate key or changed role/flag cannot pass by sharing a renderer bug.
"""

from collections import Counter
from copy import deepcopy
import importlib.util
from pathlib import Path
import re

import pytest

from api.services.rbac_policy import AUTHENTICATED, POLICY, PUBLIC

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = ROOT / "docs" / "reference" / "RBAC_MATRIX.md"
SCRIPT = ROOT / "scripts" / "generate_rbac_matrix.py"
BEGIN = "<!-- BEGIN GENERATED RBAC MATRIX -->"
END = "<!-- END GENERATED RBAC MATRIX -->"
ROW = re.compile(
    r"^\| `(\w+)` \| `([^`\r\n]+)` \| ([^|\r\n]*)\| ([^|\r\n]*)\|(?: ([^|\r\n]*)\|)?$",
    re.M,
)


def matrix_rows(document):
    # The fallback makes the original, unmarked matrix's semantic drift
    # reproducible. Marker presence is checked separately below.
    body = document.split(BEGIN, 1)[-1].split(END, 1)[0]
    rows = {}
    for method, path, allowed, scope, enforced in ROW.findall(body):
        key = (method, path)
        assert key not in rows, f"Duplicate documented route: {key}"
        allowed = allowed.strip()
        if allowed == "AUTH":
            allowed = AUTHENTICATED
        elif allowed != PUBLIC:
            allowed = tuple(role.strip() for role in allowed.split(","))
        assert scope.strip() in ("", "S"), key
        assert enforced.strip() in ("", "E"), key
        rows[key] = (allowed, scope.strip() == "S", enforced.strip() == "E")
    return rows


def assert_parity(document, policy):
    actual = matrix_rows(document)
    expected = {
        (row["method"], row["path"]): (
            tuple(row["allowed"]) if isinstance(row["allowed"], list) else row["allowed"],
            bool(row.get("store_scoped")), bool(row.get("self_enforced")),
        ) for row in policy
    }
    assert len(expected) == len(policy), "Duplicate POLICY keys"
    assert actual.keys() == expected.keys(), (
        f"Missing {len(expected.keys() - actual.keys())} / extra {len(actual.keys() - expected.keys())} documented routes. "
        "Run: python scripts/generate_rbac_matrix.py"
    )
    drift = {key: (actual[key], value) for key, value in expected.items() if actual[key] != value}
    assert not drift, f"Documented role/scope/enforcement drift: {drift}"


def test_entire_matrix_matches_policy():
    assert_parity(DOCUMENT.read_text(encoding="utf-8"), POLICY)


def test_matrix_markers_and_summary_match_all_policy_rows():
    document = DOCUMENT.read_text(encoding="utf-8")
    assert document.count(BEGIN) == document.count(END) == 1
    assert document.index(BEGIN) < document.index(END)
    body = document.split(BEGIN)[1].split(END)[0]
    summary = dict(re.findall(r"^\| (.+?) \| \*{0,2}(\d+)\*{0,2} \|$", body, re.M))
    gates = Counter(row["allowed"] if isinstance(row["allowed"], str) else "roles" for row in POLICY)
    expected = {
        "PUBLIC (no IMS role gate)": gates[PUBLIC],
        "AUTHENTICATED": gates[AUTHENTICATED],
        "Role-gated (one or more roles)": gates["roles"],
        "**Total `/api/v1` (method x path)**": len(POLICY),
        "...of which `store_scoped`": sum(bool(row.get("store_scoped")) for row in POLICY),
        "...of which `self_enforced`": sum(bool(row.get("self_enforced")) for row in POLICY),
    }
    assert {key: int(value) for key, value in summary.items()} == expected


@pytest.fixture
def generator():
    spec = importlib.util.spec_from_file_location("generate_rbac_matrix", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("field,value", [
    ("method", "HEAD"), ("path", "/api/v1/not-documented"),
    ("allowed", ["UNDOCUMENTED_ROLE"]),
    ("store_scoped", "toggle"), ("self_enforced", "toggle"),
])
def test_whole_matrix_check_detects_changes_to_every_policy_field(field, value):
    # A row anywhere in the registry must be checked, not only an allowlist
    # of sensitive endpoints. The last row makes prefix-only parsing fail.
    changed = deepcopy(POLICY)
    changed[-1][field] = not changed[-1].get(field) if value == "toggle" else value
    with pytest.raises(AssertionError):
        assert_parity(DOCUMENT.read_text(encoding="utf-8"), changed)


def test_duplicate_documented_route_is_rejected():
    document = DOCUMENT.read_text(encoding="utf-8")
    line = ROW.search(document).group(0)
    changed = document.replace(END, line + "\n" + END)
    with pytest.raises(AssertionError, match="Duplicate documented route"):
        assert_parity(changed, POLICY)


def test_generator_check_detects_drift_without_writing_and_refresh_preserves_notes(generator, tmp_path):
    before = "Manual business explanation.\n\n"
    after = "\n\nManual historical notes must stay.\n"
    path = tmp_path / "matrix.md"
    stale = before + BEGIN + "\nStale generated content.\n" + END + after
    path.write_text(stale, encoding="utf-8")
    assert generator.main(["--document", str(path), "--check"]) == 1
    assert path.read_text(encoding="utf-8") == stale
    assert generator.main(["--document", str(path)]) == 0
    updated = path.read_text(encoding="utf-8")
    assert updated.startswith(before + BEGIN)
    assert updated.endswith(END + after)
    assert_parity(updated, POLICY)
    assert generator.main(["--document", str(path), "--check"]) == 0
    assert generator.main(["--document", str(path)]) == 0
    assert path.read_text(encoding="utf-8") == updated


@pytest.mark.parametrize("document", ["No markers", BEGIN + END + END, END + BEGIN])
def test_generator_refuses_malformed_boundaries_without_overwriting(generator, tmp_path, document):
    path = tmp_path / "matrix.md"
    path.write_text(document, encoding="utf-8")
    assert generator.main(["--document", str(path)]) == 2
    assert path.read_text(encoding="utf-8") == document


def test_committed_generated_block_is_canonical(generator):
    document = DOCUMENT.read_text(encoding="utf-8")
    assert generator.updated_document(document, POLICY) == document
