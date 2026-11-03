"""The rendered matrix must describe the actual POLICY entries.

Parse the Markdown independently of the generator so an omitted row, extra
route, duplicate key or changed role/flag cannot pass by sharing a renderer bug.
The table itself is not committed (docs/reference/RBAC_MATRIX.md says why);
"every live route has a POLICY row" is tests/test_rbac_policy.py's job.
"""

from collections import Counter
from copy import deepcopy
from pathlib import Path
import re

import pytest

from api.services.rbac_policy import AUTHENTICATED, POLICY, PUBLIC
from rbac_matrix import load_generator, matrix_text

ROOT = Path(__file__).resolve().parents[2]
NOTES = ROOT / "docs" / "reference" / "RBAC_MATRIX.md"
ROW = re.compile(
    r"^\| `(\w+)` \| `([^`\r\n]+)` \| ([^|\r\n]*)\| ([^|\r\n]*)\|(?: ([^|\r\n]*)\|)?$",
    re.M,
)


def matrix_rows(document):
    rows = {}
    for method, path, allowed, scope, enforced in ROW.findall(document):
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
        "The renderer dropped or invented rows."
    )
    drift = {key: (actual[key], value) for key, value in expected.items() if actual[key] != value}
    assert not drift, f"Documented role/scope/enforcement drift: {drift}"


def test_entire_matrix_matches_policy():
    assert_parity(matrix_text(), POLICY)


def test_matrix_summary_matches_all_policy_rows():
    summary = dict(re.findall(r"^\| (.+?) \| \*{0,2}(\d+)\*{0,2} \|$", matrix_text(), re.M))
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
        assert_parity(matrix_text(), changed)


def test_duplicate_documented_route_is_rejected():
    document = matrix_text()
    line = ROW.search(document).group(0)
    with pytest.raises(AssertionError, match="Duplicate documented route"):
        assert_parity(document + line + "\n", POLICY)


def test_generator_writes_the_full_matrix_and_is_stable(tmp_path):
    generator = load_generator()
    path = tmp_path / "matrix.md"
    assert generator.main(["--output", str(path)]) == 0
    first = path.read_text(encoding="utf-8")
    assert_parity(first, POLICY)
    assert generator.main(["--output", str(path)]) == 0
    assert path.read_text(encoding="utf-8") == first


def test_generator_refuses_duplicate_policy_keys():
    with pytest.raises(ValueError):
        load_generator().render_section(POLICY + [POLICY[0]])


def test_the_full_table_stays_out_of_the_committed_notes():
    # Committing the table made every route-adding PR conflict on this file.
    notes = NOTES.read_text(encoding="utf-8")
    assert "## Full matrix" not in notes
    assert "BEGIN GENERATED RBAC MATRIX" not in notes
