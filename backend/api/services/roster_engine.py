"""Roster engine -- pure coverage computation for skills-based staff rostering (Feature #29).

Owner decision (binding): all stores are clinical and the optometrist licence never
expires. Therefore EVERY store/day/shift requires optometrist coverage, and there is NO
licence-expiry machinery anywhere in this module -- only WHO is an optometrist matters.

This module is intentionally pure: it takes plain dicts / primitives and returns plain
dicts. No DB access, no I/O, deterministic, integer counts. Unit-testable in isolation.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping


def compute_coverage(
    shifts: Iterable[Mapping[str, Any]],
    skills_by_employee: Mapping[str, Mapping[str, Any]],
    required_optoms: int,
) -> List[Dict[str, Any]]:
    """Compute optometrist coverage per (store, date, shift).

    Stub -- real implementation lands next. Returns [] for now so the module imports
    cleanly and the foundation is banked.
    """
    return []
