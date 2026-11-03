"""The RBAC matrix as scripts/generate_rbac_matrix.py renders it from POLICY.

The table is no longer committed (it was a merge-conflict magnet), so tests
that read "what the matrix says" render it here instead of opening a file.
"""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_rbac_matrix.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_rbac_matrix", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def matrix_text(policy=None):
    generator = load_generator()
    from api.services.rbac_policy import POLICY

    return generator.render_section(POLICY if policy is None else policy)
