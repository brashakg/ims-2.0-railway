"""Fail-loud replacement for bare ``inspect.getsource`` in tests.

WHY THIS MODULE EXISTS
======================
``inspect.getsource(fn)`` is NOT safe to assert against in a long test session.
It resolves the source in two independent steps:

  1. ``fn.__code__.co_firstlineno`` -- baked in when the module was COMPILED.
  2. the file's current text, fetched through ``linecache``.

``inspect.findsource`` calls ``linecache.checkcache(file)`` first, which drops
the cached lines whenever the file's mtime/size changed on disk. So if the .py
file is modified AFTER the module was imported -- another process editing it, a
``git checkout``/``pull``/branch switch landing mid-run, an editor autosave --
step 2 returns the NEW text while step 1 still holds the OLD line number. The
offset then lands in a DIFFERENT function.

This is not hypothetical. In a full-suite run,
``test_pos_p3_items::test_payment_data_persists_idempotency_key`` failed because
``getsource(add_payment)`` returned a SINGLE line: the last line of
``confirm_order``, the function immediately preceding it. Nothing was wrong with
the production code; the assertion's subject was simply the wrong function. The
same test passed in isolation.

The DANGEROUS case is not that failure -- it is the silent one. If the shifted
offset happens to land in a function that DOES contain the asserted substring,
the test PASSES while proving nothing at all about the function it names.

WHAT THIS MODULE DOES
=====================
``verified_source(fn)`` performs the same lookup and then refuses to return a
block that cannot possibly be the requested function:

  * the block must contain a ``def <name>`` / ``async def <name>`` line;
  * the block must start with a decorator, a ``def``/``async def``, or (for a
    class) a ``class`` line -- never a bare statement;
  * the block must exceed a plausible minimum line count.

Any violation raises AssertionError with the full forensic context (file,
co_firstlineno, on-disk size/mtime, the first lines actually returned), so this
failure mode announces itself instead of silently degrading an assertion into a
tautology.

NOTE: prefer a BEHAVIOURAL test over any use of this helper. Source text cannot
distinguish "the line exists" from "the line RUNS". Use ``verified_source`` only
where the guarantee is genuinely static (an architectural constraint such as
"module A must never import module B") and therefore has no runtime observable.
"""

from __future__ import annotations

import inspect
import os
import re
from typing import Any


class SourceLookupError(AssertionError):
    """getsource returned something that cannot be the requested object."""


def _context(obj: Any, block: str) -> str:
    """Forensic detail for a failed lookup."""
    try:
        file = inspect.getsourcefile(obj) or inspect.getfile(obj)
    except Exception:  # noqa: BLE001
        file = "<unknown>"
    code = getattr(obj, "__code__", None)
    firstlineno = getattr(code, "co_firstlineno", None)
    try:
        stat = os.stat(file)
        on_disk = f"size={stat.st_size} mtime={stat.st_mtime}"
    except OSError:
        on_disk = "<not statable>"
    head = "\n".join("      | " + ln for ln in block.splitlines()[:5]) or "      | <empty>"
    return (
        f"\n    object       : {getattr(obj, '__qualname__', obj)!r}"
        f"\n    source file  : {file}"
        f"\n    co_firstlineno: {firstlineno}"
        f"\n    file on disk : {on_disk}"
        f"\n    returned {len(block.splitlines())} line(s), starting:\n{head}"
        "\n    -> The file on disk is almost certainly out of step with the"
        "\n       imported module (edited mid-run). Re-run on a quiescent tree."
    )


def verified_source(obj: Any, *, min_lines: int = 4, name: str | None = None) -> str:
    """``inspect.getsource(obj)`` that fails loudly on an implausible result.

    Args:
        obj: a function, method, class or module.
        min_lines: reject a block shorter than this (a desynchronised lookup
            typically returns 1-2 lines).
        name: expected symbol name; defaults to ``obj.__name__``.

    Raises:
        SourceLookupError: the returned block cannot be ``obj``'s source.
    """
    block = inspect.getsource(obj)
    symbol = name or getattr(obj, "__name__", None)

    if inspect.ismodule(obj):
        # getsource(module) returns the WHOLE file, so it carries no line-offset
        # risk -- but an empty/truncated read is still worth catching.
        if len(block.splitlines()) < max(min_lines, 10):
            raise SourceLookupError(
                f"module source for {symbol!r} is implausibly short."
                + _context(obj, block)
            )
        return block

    lines = block.splitlines()
    if len(lines) < min_lines:
        raise SourceLookupError(
            f"source block for {symbol!r} is only {len(lines)} line(s); expected "
            f"at least {min_lines}." + _context(obj, block)
        )

    first = next((ln for ln in lines if ln.strip()), "")
    stripped = first.strip()
    if not (
        stripped.startswith("@")
        or stripped.startswith("def ")
        or stripped.startswith("async def ")
        or stripped.startswith("class ")
    ):
        raise SourceLookupError(
            f"source block for {symbol!r} does not start with a decorator, def or "
            f"class line." + _context(obj, block)
        )

    if symbol:
        kind = "class" if inspect.isclass(obj) else "(?:async +)?def"
        if not re.search(rf"^\s*{kind}\s+{re.escape(symbol)}\b", block, re.MULTILINE):
            raise SourceLookupError(
                f"source block does not define {symbol!r} -- getsource returned a "
                f"DIFFERENT symbol's body." + _context(obj, block)
            )

    return block
