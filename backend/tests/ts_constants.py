"""
Read a frontend constant list from its TypeScript source, for parity tests.
=========================================================================
Two hand-maintained copies of the same list ALWAYS drift. When the list is a
dropdown in React and a server-side validator in Python, the symptom of drift is
the worst possible one: a form that 422s AFTER the user has filled it in.

So the backend list is the runtime authority (it is what actually rejects a bad
value), and a test reads the TSX/TS file and fails if the two differ. This
module is the reader. It deliberately has NO fallback: if the frontend file is
missing or the constant cannot be found, it raises, so a parity test can never
quietly pass by finding nothing to compare against.
"""

from __future__ import annotations

import os
import re
from typing import List

# backend/tests/ts_constants.py -> backend/tests -> backend -> repo root
REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


def frontend_path(*parts: str) -> str:
    return os.path.join(REPO_ROOT, "frontend", "src", *parts)


def read_object_list_values(path: str, const_name: str) -> List[str]:
    """Values of ``export const <const_name> = [ { value: '...' }, ... ]``.

    Returns them IN SOURCE ORDER (a caller that only cares about membership can
    compare sets; a caller that cares about the order the user sees can compare
    lists). Raises AssertionError -- not a skip, not an empty list -- when the
    file or the constant is absent, because a parity test that silently finds
    nothing is a parity test that can never fail.
    """
    if not os.path.exists(path):
        raise AssertionError(
            f"frontend source {path} not found; the parity test cannot run "
            "and must not pass by default"
        )
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()

    # "const NAME = [" / "export const NAME = [", with an optional TypeScript
    # type annotation in between ("const CATEGORIES: {...}[] = [").
    opener = re.search(
        r"(?:export\s+)?const\s+" + re.escape(const_name) + r"\b[^=\n]*=\s*\[",
        source,
    )
    if opener is None:
        raise AssertionError(
            f"could not find `const {const_name} = [` in {path} -- the constant "
            "was renamed or moved; update this parity test with it"
        )
    end = source.find("\n]", opener.end())
    if end < 0:
        raise AssertionError(f"could not find the end of {const_name} in {path}")

    block = source[opener.end() : end]
    values = re.findall(r"value:\s*'([^']+)'", block)
    if not values:
        raise AssertionError(
            f"{const_name} in {path} yielded no `value:` entries -- the shape of "
            "the constant changed; update this parity test with it"
        )
    return values
