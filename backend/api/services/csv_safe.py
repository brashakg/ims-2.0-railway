"""CSV formula-injection neutralization (BUG-139).

A spreadsheet treats any cell whose text begins with ``=``, ``+``, ``-``, ``@``,
TAB or CR as a *formula*. So a user-controlled string that gets written verbatim
into an exported CSV -- e.g. a customer/vendor/staff name of
``=cmd|'/C calc'!A0`` or ``=HYPERLINK("http://evil","x")`` -- executes when the
accountant opens the file in Excel/Sheets (CSV-injection -> RCE / data exfil).

This mirrors the frontend neutralizer in ``frontend/src/utils/exportUtils.ts``:
prefix such strings with a single quote so the spreadsheet renders them as text.

IMPORTANT: only STRING cells are neutralized. Numeric cells (int/float) pass
through untouched so a legitimate negative number ``-5`` is NOT turned into the
text ``'-5``. Backend export rows pass numbers as numbers, strings as strings,
so this is the correct, lossless behaviour.
"""

import csv as _csv

# Leading characters a spreadsheet may interpret as the start of a formula.
_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

# UTF-8 byte-order mark. Prepended to exported CSVs so Excel opens them as UTF-8
# (preserving non-ASCII names) and treats our quote-prefixed cells as text.
BOM = "﻿"


def neutralize_formula(value):
    """Return a CSV-safe version of a single cell value.

    Strings beginning with a formula-trigger char get a leading single quote;
    everything else (numbers, None, bools) is returned unchanged.
    """
    if isinstance(value, str) and value[:1] in _DANGEROUS_PREFIXES:
        return "'" + value
    return value


def neutralize_row(row):
    """Neutralize every cell of a row -> list (safe to hand to csv.writer)."""
    return [neutralize_formula(c) for c in row]


class _SafeCsvWriter:
    """A ``csv.writer`` wrapper that neutralizes formula-trigger strings per cell.

    Drop-in for ``csv.writer(fileobj)`` -- exposes ``writerow`` / ``writerows``.
    """

    def __init__(self, fileobj, **kwargs):
        self._w = _csv.writer(fileobj, **kwargs)

    def writerow(self, row):
        self._w.writerow(neutralize_row(row))

    def writerows(self, rows):
        for r in rows:
            self.writerow(r)


def safe_writer(fileobj, **kwargs):
    """Build a formula-injection-safe csv writer over ``fileobj``."""
    return _SafeCsvWriter(fileobj, **kwargs)
