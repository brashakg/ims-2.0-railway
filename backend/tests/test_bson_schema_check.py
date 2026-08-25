"""The schema checker must itself be discriminating.

``tests/bson_schema_check.py`` is the instrument every schema-parity test reads
its answer from. If it were lenient about the exact drift classes that rot these
schemas -- ISO string vs BSON date, float vs Decimal128, int vs double, None vs
string, a missing required field, an enum near-miss -- the parity tests would go
green over a broken schema. Each case below pins ONE of those.
"""

from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from bson_schema_check import (  # noqa: E402
    UnsupportedSchemaFeature,
    assert_valid,
    validate,
)


def _errs(value, node):
    return validate({"f": value}, {"bsonType": "object", "properties": {"f": node}})


class TestDateIsNotAString:
    def test_iso_string_fails_a_date_field(self):
        assert _errs("2026-08-24T10:00:00", {"bsonType": "date"})

    def test_datetime_passes(self):
        assert _errs(datetime.datetime(2026, 8, 24), {"bsonType": "date"}) == []

    def test_plain_date_is_not_a_bson_date(self):
        # datetime.date is not encodable as a BSON date; pymongo rejects it.
        assert _errs(datetime.date(2026, 8, 24), {"bsonType": "date"})


class TestNumericTypesAreExact:
    def test_float_fails_decimal(self):
        assert _errs(1250.5, {"bsonType": "decimal"})

    def test_float_passes_double(self):
        assert _errs(1250.5, {"bsonType": "double"}) == []

    def test_int_fails_double(self):
        # pymongo encodes a Python int as int32/int64, never a double.
        assert _errs(1250, {"bsonType": "double"})

    def test_int_passes_number_alias(self):
        assert _errs(1250, {"bsonType": "number"}) == []

    def test_int_beyond_int32_fails_int(self):
        assert _errs(3_000_000_000, {"bsonType": "int"})

    def test_small_int_passes_int(self):
        assert _errs(7, {"bsonType": "int"}) == []


class TestBoolIsItsOwnType:
    def test_bool_is_not_an_int(self):
        assert _errs(True, {"bsonType": "int"})

    def test_int_is_not_a_bool(self):
        assert _errs(1, {"bsonType": "bool"})

    def test_bool_is_not_a_number(self):
        assert _errs(True, {"bsonType": "number"})


class TestNullIsItsOwnType:
    def test_none_fails_a_string_field(self):
        assert _errs(None, {"bsonType": "string"})

    def test_none_passes_a_nullable_string_field(self):
        assert _errs(None, {"bsonType": ["string", "null"]}) == []

    def test_string_still_passes_a_nullable_string_field(self):
        assert _errs("x", {"bsonType": ["string", "null"]}) == []


class TestRequiredAndEnum:
    def test_missing_required_field_is_reported(self):
        errors = validate({}, {"bsonType": "object", "required": ["a"], "properties": {}})
        assert any("required field 'a' is missing" in e for e in errors)

    def test_present_but_null_still_satisfies_required(self):
        # Mongo's `required` is presence, not non-null. The TYPE check is what
        # catches a null in a string field (covered above).
        errors = validate(
            {"a": None},
            {"bsonType": "object", "required": ["a"], "properties": {}},
        )
        assert errors == []

    def test_enum_is_case_sensitive(self):
        assert _errs("pending", {"enum": ["PENDING"]})

    def test_enum_exact_match_passes(self):
        assert _errs("PENDING", {"enum": ["PENDING"]}) == []


class TestNestedShapes:
    def test_array_items_are_checked(self):
        node = {"bsonType": "array", "items": {"bsonType": "string"}}
        assert _errs(["ok", 3], node)
        assert _errs(["ok", "fine"], node) == []

    def test_nested_object_properties_are_checked(self):
        node = {"bsonType": "object", "properties": {"lat": {"bsonType": "double"}}}
        assert _errs({"lat": 1}, node)          # int is not a double
        assert _errs({"lat": 1.0}, node) == []

    def test_extra_fields_pass_when_additional_properties_is_unset(self):
        # This is exactly why every "EXTRA" field in schemas.py is harmless.
        errors = validate({"a": 1, "zzz": "junk"},
                          {"bsonType": "object", "properties": {}})
        assert errors == []

    def test_extra_fields_fail_when_additional_properties_is_false(self):
        errors = validate({"zzz": "junk"},
                          {"bsonType": "object", "properties": {},
                           "additionalProperties": False})
        assert errors


class TestFailsLoudOnWhatItCannotEmulate:
    def test_unknown_keyword_raises_instead_of_passing(self):
        with pytest.raises(UnsupportedSchemaFeature):
            validate({"f": 1}, {"bsonType": "object",
                                "properties": {"f": {"pattern": "^a"}}})

    def test_unknown_bson_type_raises(self):
        with pytest.raises(UnsupportedSchemaFeature):
            _errs("x", {"bsonType": "binData"})


class TestAssertValidMessage:
    def test_message_names_the_field_and_error_121(self):
        with pytest.raises(AssertionError) as exc:
            assert_valid({"f": "2026-01-01"},
                         {"bsonType": "object", "properties": {"f": {"bsonType": "date"}}},
                         label="widget")
        message = str(exc.value)
        assert "widget" in message and "121" in message and "f:" in message

    def test_valid_document_raises_nothing(self):
        assert_valid({"f": "x"},
                     {"bsonType": "object", "properties": {"f": {"bsonType": "string"}}})
