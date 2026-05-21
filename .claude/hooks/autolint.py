#!/usr/bin/env python
"""PostToolUse auto-lint hook for IMS 2.0.

Runs pylint's E/F (error/fatal) gate on a just-edited backend/api Python file
so issues like E0601 (used-before-assignment) surface the instant they're
introduced instead of breaking CI minutes later.

Contract:
  * Fail-SAFE: any problem locating the venv/pylint => exit 0 (never blocks).
  * Only acts on backend/api/**/*.py; every other edit is a no-op.
  * Exit 2 + stderr => Claude Code feeds the lint output back so it can fix it.

Reads the PostToolUse hook payload (JSON) from stdin.
"""
import json
import os
import subprocess
import sys


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0

    file_path = (data.get("tool_input") or {}).get("file_path") or ""
    norm = file_path.replace("\\", "/")

    # Scope: only lint backend API Python files.
    if not norm.endswith(".py") or "/backend/api/" not in norm:
        return 0
    if not os.path.isfile(file_path):
        return 0

    # This script lives at <repo>/.claude/hooks/autolint.py
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    py = next(
        (
            c
            for c in (
                os.path.join(repo, ".venv", "Scripts", "python.exe"),
                os.path.join(repo, ".venv", "bin", "python"),
            )
            if os.path.isfile(c)
        ),
        None,
    )
    if py is None:
        return 0  # no venv -> don't block

    try:
        result = subprocess.run(
            [
                py,
                "-m",
                "pylint",
                file_path,
                "--disable=all",
                "--enable=E,F",
                "--extension-pkg-allow-list=pydantic",
                "--disable=no-name-in-module,no-member,import-error",
            ],
            capture_output=True,
            text=True,
            timeout=90,
            cwd=os.path.join(repo, "backend"),
        )
    except Exception:
        return 0  # pylint missing / timed out -> don't block

    if result.returncode != 0:
        sys.stderr.write(
            "[autolint] pylint E/F issues in %s - fix before continuing:\n%s\n"
            % (os.path.basename(file_path), (result.stdout or "").strip())
        )
        return 2  # block + surface stderr to Claude

    return 0


if __name__ == "__main__":
    sys.exit(main())
