"""A real (small) validator for the MongoDB ``$jsonSchema`` documents in
``database/schemas.py``.

WHY NOT ``jsonschema``
=====================
The ``jsonschema`` package is not a dependency of this project, and it would be
the WRONG tool even if it were: a Mongo validator keys off ``bsonType``, which
standard JSON Schema does not know. ``jsonschema`` would silently treat every
``{"bsonType": "date"}`` as an empty (always-true) schema and report a clean
pass on a document Mongo would reject with error 121 -- exactly the hollow
green the repo has been bitten by before. So this module implements the subset
of ``$jsonSchema`` that ``schemas.py`` actually uses, against the BSON types a
Python value really serialises to through pymongo.

WHAT IT CHECKS
==============
``bsonType`` (scalar or list-of-alternatives), ``enum``, ``required``,
``properties``, ``items``, ``additionalProperties``, ``minLength`` /
``maxLength``, ``minimum`` / ``maximum``.

Anything it does not implement raises :class:`UnsupportedSchemaFeature` rather
than passing quietly -- a keyword this file cannot honour must not be mistaken
for a keyword this file approved.

BSON TYPE SEMANTICS (the whole point)
=====================================
``bsonType`` is an EXACT BSON type, not a loose JSON type:

  * ``"double"``  -- BSON double only. A Python ``int`` encodes as int32/int64
                     and FAILS. (``"number"`` is the alias that takes both.)
  * ``"int"``     -- int32 only. A Python ``int`` outside the int32 range
                     encodes as int64 and FAILS.
  * ``"decimal"`` -- Decimal128 only. A Python ``float`` is a double and FAILS.
  * ``"date"``    -- BSON UTC datetime. An ISO ``str`` FAILS.
  * ``"bool"``    -- Python ``bool``. Note ``bool`` is a subclass of ``int`` in
                     Python but is a DISTINCT BSON type, so ``True`` is not an
                     ``"int"`` and ``1`` is not a ``"bool"``.
  * ``None``      -- BSON null, its own type. A field typed ``"string"`` whose
                     value is ``None`` FAILS; ``["string", "null"]`` is how a
                     nullable string is declared.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List

_INT32_MIN = -(2 ** 31)
_INT32_MAX = 2 ** 31 - 1

_SUPPORTED_KEYWORDS = {
    "bsonType",
    "enum",
    "required",
    "properties",
    "items",
    "additionalProperties",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "description",
    "title",
}


class UnsupportedSchemaFeature(AssertionError):
    """The schema used a $jsonSchema keyword this checker does not emulate."""


def _class_name(value: Any) -> str:
    return type(value).__name__


def _matches_bson_type(value: Any, bson_type: str) -> bool:
    """True when ``value``, serialised by pymongo, IS the named BSON type."""
    if bson_type == "null":
        return value is None
    if value is None:
        # BSON null satisfies no other bsonType.
        return False

    if bson_type == "bool":
        return isinstance(value, bool)
    if bson_type == "string":
        return isinstance(value, str)
    if bson_type == "object":
        return isinstance(value, dict)
    if bson_type == "array":
        return isinstance(value, list)
    if bson_type == "date":
        # datetime only. A date-without-time is not encodable as BSON date.
        return isinstance(value, _dt.datetime)
    if bson_type == "int":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and _INT32_MIN <= value <= _INT32_MAX
        )
    if bson_type == "long":
        return isinstance(value, int) and not isinstance(value, bool)
    if bson_type == "double":
        return isinstance(value, float)
    if bson_type == "decimal":
        return _class_name(value) == "Decimal128"
    if bson_type == "objectId":
        return _class_name(value) == "ObjectId"
    if bson_type == "number":
        return (
            (isinstance(value, (int, float)) and not isinstance(value, bool))
            or _class_name(value) == "Decimal128"
        )
    raise UnsupportedSchemaFeature(
        f"bsonType {bson_type!r} is not implemented by bson_schema_check"
    )


def _describe(value: Any) -> str:
    if value is None:
        return "null"
    return f"{_class_name(value)}({value!r})"


def validate(document: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    """Validate ``document`` against a Mongo ``$jsonSchema``.

    Returns a list of human-readable error strings; empty means the document
    would be accepted by a Mongo validator carrying this schema.
    """
    errors: List[str] = []
    _validate_node(document, schema, "", errors)
    return errors


def assert_valid(document: Dict[str, Any], schema: Dict[str, Any], label: str = "document") -> None:
    """Raise ``AssertionError`` with every violation if the document is invalid."""
    errors = validate(document, schema)
    if errors:
        raise AssertionError(
            f"{label} would be REJECTED by its $jsonSchema validator "
            f"(MongoDB error 121):\n  - " + "\n  - ".join(errors)
        )


def _validate_node(value: Any, schema: Dict[str, Any], path: str, errors: List[str]) -> None:
    if not isinstance(schema, dict):
        raise UnsupportedSchemaFeature(f"schema node at {path or '<root>'} is not a dict")

    unknown = set(schema) - _SUPPORTED_KEYWORDS
    if unknown:
        raise UnsupportedSchemaFeature(
            f"schema node at {path or '<root>'} uses unimplemented keyword(s): "
            f"{sorted(unknown)}"
        )

    where = path or "<root>"

    if "bsonType" in schema:
        declared = schema["bsonType"]
        alternatives = declared if isinstance(declared, list) else [declared]
        if not any(_matches_bson_type(value, t) for t in alternatives):
            errors.append(
                f"{where}: declared bsonType {declared!r} but value is {_describe(value)}"
            )
            # A wrong type makes every downstream keyword meaningless.
            return

    if "enum" in schema:
        allowed = schema["enum"]
        if value not in allowed:
            errors.append(f"{where}: value {_describe(value)} is not in enum {allowed!r}")
            return

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{where}: length {len(value)} < minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{where}: length {len(value)} > maxLength {schema['maxLength']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where}: {value} > maximum {schema['maximum']}")

    if isinstance(value, dict):
        for field in schema.get("required", []):
            if field not in value:
                errors.append(f"{where}: required field {field!r} is missing")
        properties = schema.get("properties", {})
        for key, sub_value in value.items():
            if key in properties:
                child = f"{path}.{key}" if path else key
                _validate_node(sub_value, properties[key], child, errors)
            elif schema.get("additionalProperties") is False:
                # NOTE: no schema in schemas.py sets this today, which is WHY
                # extra fields are harmless there. Implemented so a future
                # schema that does set it is genuinely checked.
                errors.append(f"{where}: additional property {key!r} is not allowed")

    if isinstance(value, list) and "items" in schema:
        for index, element in enumerate(value):
            _validate_node(element, schema["items"], f"{path}[{index}]", errors)
