"""
IMS 2.0 - to_date_str unit tests (QA F1 fix)
============================================
`api.utils.dates.to_date_str` normalizes a `created_at`-style value to a
'YYYY-MM-DD' string. Mongo stamps `created_at` as a real datetime while legacy
seeds store ISO strings; the old `value[:10]` slice raised
`TypeError: 'datetime.datetime' object is not subscriptable` and 500'd several
analytics/reports endpoints. These tests pin the normalization + the
never-raise contract.
"""

from datetime import datetime, date

from api.utils.dates import to_date_str


def test_datetime_to_iso_date():
    assert to_date_str(datetime(2026, 5, 29, 14, 23, 51)) == "2026-05-29"


def test_date_to_iso():
    assert to_date_str(date(2026, 5, 29)) == "2026-05-29"


def test_iso_string_truncated_to_date():
    assert to_date_str("2026-05-29T14:23:51.123Z") == "2026-05-29"
    assert to_date_str("2026-05-29") == "2026-05-29"


def test_none_and_junk_are_empty_never_raise():
    assert to_date_str(None) == ""
    assert to_date_str(12345) == ""  # non-str/non-date -> "" (no crash)
    assert to_date_str({"x": 1}) == ""


def test_comparison_safe_for_mixed_shapes():
    # The real-world use: comparing a row's created_at against a cutoff string.
    # A datetime row and a string row must both compare without raising.
    cutoff = "2026-05-01"
    assert to_date_str(datetime(2026, 5, 15)) >= cutoff
    assert to_date_str("2026-04-15") < cutoff
    assert (to_date_str(None) >= cutoff) is False  # empty string sorts low
