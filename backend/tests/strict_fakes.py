"""Strict in-memory MongoDB doubles for BEHAVIOURAL tests.

WHY "STRICT"
============
The suite already contains a permissive fake (``tests/test_walkouts.FakeDB``).
Its matcher understands ``$gte/$lte/$ne/$exists`` and SILENTLY IGNORES every
other operator -- so a filter such as ``{"expires_at": {"$lt": now}}`` matches
EVERY document instead of none. A test built on that fake can assert a
carefully-worded expectation and still prove nothing, because the filter it
depends on was a no-op.

Everything here fails loudly instead:

  * an unknown query operator raises ``UnsupportedMongoFeature``;
  * an unknown update operator raises;
  * an unknown aggregation stage/accumulator raises.

A test that needs a feature this file does not implement gets a clear error
telling it so, rather than a quietly wrong answer. Add the operator here (with
real semantics) rather than loosening the check.

These doubles are deliberately small: they exist so a behavioural test can drive
the REAL router/repository code path without a live MongoDB, and assert on the
observable outcome (what ended up stored / what the endpoint returned).
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List, Optional

_MISSING = object()


class UnsupportedMongoFeature(AssertionError):
    """The fake was asked for a Mongo feature it does not faithfully emulate."""


# ---------------------------------------------------------------------------
# Query matching
# ---------------------------------------------------------------------------

_QUERY_OPS = {
    "$eq",
    "$ne",
    "$gt",
    "$gte",
    "$lt",
    "$lte",
    "$in",
    "$nin",
    "$exists",
    "$regex",
    "$options",
    "$not",
}


def _get_path(doc: Dict[str, Any], key: str):
    """Dotted-path lookup, mirroring Mongo's dotted field access.

    An ARRAY met mid-path fans out: ``patients.mobile`` on a doc whose
    ``patients`` is a list of subdocuments yields the list of every element's
    ``mobile`` (elements lacking it are skipped), exactly the set Mongo tests a
    query value against. This used to return _MISSING the moment a list was
    hit, so ``{"patients.mobile": x}`` matched NOTHING -- a fake that is blind
    to precisely the family-member lookup a test would be trying to prove.
    """
    cur: Any = doc
    parts = key.split(".")
    for i, part in enumerate(parts):
        if isinstance(cur, list):
            if part.isdigit():
                raise UnsupportedMongoFeature(
                    "positional array index in query path (%r) is not implemented" % key
                )
            rest = ".".join(parts[i:])
            found = []
            for el in cur:
                if isinstance(el, dict):
                    v = _get_path(el, rest)
                    if v is not _MISSING:
                        found.extend(v if isinstance(v, list) else [v])
            return found if found else _MISSING
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _eq(actual, expected) -> bool:
    """Mongo equality: a scalar query value matches an ARRAY field when any
    element equals it (``{"tags": "VIP"}`` / ``{"patients.mobile": "98..."}``);
    an array query value must equal the array itself."""
    if isinstance(actual, list) and not isinstance(expected, list):
        return expected in actual
    return actual == expected


def _set_path(doc: Dict[str, Any], key: str, value) -> None:
    """Dotted-path WRITE, the mirror of :func:`_get_path`.

    Mongo's ``$set``/``$inc`` on ``"a.b"`` creates the nested subdocument
    ``{"a": {"b": ...}}``. This fake used to store the literal key ``"a.b"``
    instead, so a dotted write followed by a dotted query silently never
    matched -- a test written against it would prove nothing about Mongo.
    """
    parts = key.split(".")
    cur: Any = doc
    for part in parts[:-1]:
        if part.isdigit():
            raise UnsupportedMongoFeature(
                "positional/array dotted paths (%r) are not implemented" % key
            )
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    if parts[-1].isdigit():
        raise UnsupportedMongoFeature(
            "positional/array dotted paths (%r) are not implemented" % key
        )
    cur[parts[-1]] = value


def _del_path(doc: Dict[str, Any], key: str) -> None:
    """Dotted-path delete ($unset), the mirror of :func:`_set_path`."""
    parts = key.split(".")
    cur: Any = doc
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else None
        if not isinstance(cur, dict):
            return
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def _cmp_ok(actual, op: str, expected) -> bool:
    """Ordered comparison that never raises on mismatched types.

    Mongo orders across BSON types; we only need "different types never
    compare", which is what a real query would effectively give us here.
    """
    if actual is _MISSING or actual is None:
        return False
    try:
        if op == "$gt":
            return actual > expected
        if op == "$gte":
            return actual >= expected
        if op == "$lt":
            return actual < expected
        if op == "$lte":
            return actual <= expected
    except TypeError:
        return False
    raise UnsupportedMongoFeature(f"comparison operator {op!r} not implemented")


def _match_ops(actual, spec: Dict[str, Any]) -> bool:
    unknown = set(spec) - _QUERY_OPS
    if unknown:
        raise UnsupportedMongoFeature(
            f"query operator(s) {sorted(unknown)} not implemented by StrictCollection. "
            "Implement them faithfully here -- do NOT let them match everything."
        )
    actual_val = None if actual is _MISSING else actual
    for op, val in spec.items():
        if op == "$options":
            continue  # handled with $regex
        if op == "$eq":
            if not _eq(actual_val, val):
                return False
        elif op == "$ne":
            if _eq(actual_val, val):
                return False
        elif op in ("$gt", "$gte", "$lt", "$lte"):
            if not _cmp_ok(actual, op, val):
                return False
        elif op == "$in":
            if not any(_eq(actual_val, v) for v in list(val)):
                return False
        elif op == "$nin":
            if any(_eq(actual_val, v) for v in list(val)):
                return False
        elif op == "$exists":
            if bool(val) != (actual is not _MISSING):
                return False
        elif op == "$regex":
            flags = re.IGNORECASE if "i" in (spec.get("$options") or "") else 0
            candidates = actual_val if isinstance(actual_val, list) else [actual_val]
            if not any(
                isinstance(c, str) and re.search(val, c, flags) for c in candidates
            ):
                return False
        elif op == "$not":
            if _match_ops(actual, val):
                return False
    return True


def matches(doc: Dict[str, Any], flt: Optional[Dict[str, Any]]) -> bool:
    """Evaluate a Mongo filter against a document. Raises on anything unknown."""
    if not flt:
        return True
    for key, expected in flt.items():
        if key == "$or":
            if not any(matches(doc, sub) for sub in expected):
                return False
            continue
        if key == "$and":
            if not all(matches(doc, sub) for sub in expected):
                return False
            continue
        if key == "$nor":
            if any(matches(doc, sub) for sub in expected):
                return False
            continue
        if key.startswith("$"):
            raise UnsupportedMongoFeature(
                f"top-level query operator {key!r} not implemented by StrictCollection"
            )
        actual = _get_path(doc, key)
        if isinstance(expected, dict) and any(k.startswith("$") for k in expected):
            if not _match_ops(actual, expected):
                return False
        else:
            if not _eq(None if actual is _MISSING else actual, expected):
                return False
    return True


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _project(doc: Dict[str, Any], projection) -> Dict[str, Any]:
    if not projection:
        return dict(doc)
    if any("." in k for k in projection):
        raise UnsupportedMongoFeature("dotted projections not implemented")
    includes = [k for k, v in projection.items() if v and k != "_id"]
    out: Dict[str, Any]
    if includes:
        out = {k: doc[k] for k in includes if k in doc}
        if projection.get("_id", 1) and "_id" in doc:
            out["_id"] = doc["_id"]
    else:
        out = {k: v for k, v in doc.items() if projection.get(k, 1)}
    return out


# ---------------------------------------------------------------------------
# Cursor / collection / database
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, docs: Iterable[Dict[str, Any]]):
        self._docs = list(docs)
        self._sort: Optional[List] = None
        self._skip = 0
        self._limit: Optional[int] = None

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, str):
            self._sort = [(key_or_list, direction if direction is not None else 1)]
        else:
            self._sort = list(key_or_list)
        return self

    def skip(self, n):
        self._skip = int(n or 0)
        return self

    def limit(self, n):
        self._limit = int(n or 0) or None
        return self

    def _materialise(self):
        out = list(self._docs)
        for key, direction in reversed(self._sort or []):
            out.sort(
                key=lambda d, k=key: (_get_path(d, k) is _MISSING, _get_path(d, k)),
                reverse=(direction == -1),
            )
        if self._skip:
            out = out[self._skip :]
        if self._limit:
            out = out[: self._limit]
        return out

    def __iter__(self):
        return iter(self._materialise())

    def __len__(self):
        return len(self._materialise())


_UPDATE_OPS = {"$set", "$unset", "$push", "$inc", "$setOnInsert", "$addToSet", "$pull"}


class StrictCollection:
    """In-memory collection that raises on anything it cannot emulate faithfully."""

    def __init__(self, name: str = "collection", docs: Optional[List[Dict]] = None):
        self.name = name
        self.docs: List[Dict[str, Any]] = [dict(d) for d in (docs or [])]

    # -- reads ------------------------------------------------------------
    def find_one(self, filter=None, projection=None, sort=None, **kwargs):
        """``sort`` is HONOURED, not ignored.

        This used to swallow ``sort`` silently, so production code doing the
        very common ``find_one(flt, sort=[("ts", -1)])`` -- "give me the most
        recent row" -- got the OLDEST matching document from the fake. A test
        asserting on the latest row then passed while asserting the opposite
        of what Mongo does: a lying double of exactly the kind this module
        exists to prevent. Routed through the cursor so the sort rule has one
        implementation.
        """
        cur = _Cursor(d for d in self.docs if matches(d, filter))
        if sort:
            cur = cur.sort(sort)
        for d in cur.limit(1):
            return _project(d, projection)
        return None

    def find(self, filter=None, projection=None, **kwargs):
        return _Cursor(_project(d, projection) for d in self.docs if matches(d, filter))

    def count_documents(self, filter=None, **kwargs):
        return sum(1 for d in self.docs if matches(d, filter))

    def distinct(self, key, filter=None):
        seen = []
        for d in self.docs:
            if matches(d, filter):
                v = _get_path(d, key)
                if v is not _MISSING and v not in seen:
                    seen.append(v)
        return seen

    # -- writes -----------------------------------------------------------
    def insert_one(self, doc):
        stored = dict(doc)
        self.docs.append(stored)
        return type("R", (), {"inserted_id": stored.get("_id"), "acknowledged": True})()

    def insert_many(self, docs):
        ids = []
        for d in docs:
            ids.append(self.insert_one(d).inserted_id)
        return type("R", (), {"inserted_ids": ids, "acknowledged": True})()

    def _apply(self, doc, update):
        unknown = set(update) - _UPDATE_OPS
        if unknown:
            raise UnsupportedMongoFeature(
                f"update operator(s) {sorted(unknown)} not implemented by StrictCollection"
            )
        # Every operator goes through _get_path/_set_path so a DOTTED key
        # writes the nested subdocument Mongo would write (and is therefore
        # visible to a dotted query), not a flat literal "a.b" key.
        for k, v in (update.get("$set") or {}).items():
            _set_path(doc, k, v)
        for k in (update.get("$unset") or {}):
            _del_path(doc, k)
        for k, v in (update.get("$inc") or {}).items():
            cur = _get_path(doc, k)
            _set_path(doc, k, (0 if cur is _MISSING or cur is None else cur) + v)
        for k, v in (update.get("$push") or {}).items():
            if isinstance(v, dict) and any(kk.startswith("$") for kk in v):
                raise UnsupportedMongoFeature("$push modifiers ($each/$slice) not implemented")
            arr = _get_path(doc, k)
            if not isinstance(arr, list):
                arr = []
            arr.append(v)
            _set_path(doc, k, arr)
        for k, v in (update.get("$addToSet") or {}).items():
            arr = _get_path(doc, k)
            if not isinstance(arr, list):
                arr = []
            if v not in arr:
                arr.append(v)
            _set_path(doc, k, arr)
        for k, v in (update.get("$pull") or {}).items():
            arr = _get_path(doc, k)
            if isinstance(arr, list):
                # Mongo applies a DOCUMENT condition to each array element as a
                # query ("$pull: {patients: {patient_id: X}}" removes every
                # element whose patient_id is X, whatever other fields it
                # carries). A scalar condition is plain equality. The old
                # whole-element equality never matched a real member row (they
                # carry name/mobile/relation too), so a $pull silently removed
                # nothing and a test asserting removal proved nothing.
                def _pulled(x, cond=v):
                    if isinstance(cond, dict) and isinstance(x, dict):
                        return matches(x, cond)
                    return matches({"_v": x}, {"_v": cond})

                _set_path(doc, k, [x for x in arr if not _pulled(x)])

    def update_one(self, filter, update, upsert=False, **kwargs):
        for d in self.docs:
            if matches(d, filter):
                self._apply(d, update)
                return type(
                    "R", (), {"matched_count": 1, "modified_count": 1, "upserted_id": None}
                )()
        if upsert:
            seed = {k: v for k, v in (filter or {}).items() if not isinstance(v, dict)}
            seed.update(update.get("$setOnInsert") or {})
            self._apply(seed, {k: v for k, v in update.items() if k != "$setOnInsert"})
            self.docs.append(seed)
            return type(
                "R", (), {"matched_count": 0, "modified_count": 0, "upserted_id": seed.get("_id")}
            )()
        return type("R", (), {"matched_count": 0, "modified_count": 0, "upserted_id": None})()

    def find_one_and_update(
        self,
        filter=None,
        update=None,
        projection=None,
        sort=None,
        upsert=False,
        return_document=False,
        **kwargs,
    ):
        """Guarded single-document read-modify-write -- the atomic claim shape
        this codebase uses (PROTOCOL P0-1: one document, one collection, never
        a cross-collection transaction).

        Returns None when the filter matches nothing, which is exactly how a
        caller detects that it LOST a race. ``return_document`` follows pymongo:
        False/ReturnDocument.BEFORE (the default) returns the pre-update doc,
        True/ReturnDocument.AFTER the post-update one.
        """
        if sort is not None:
            raise UnsupportedMongoFeature(
                "find_one_and_update(sort=...) is not implemented -- the fake "
                "has no stable ordering to sort by"
            )
        for d in self.docs:
            if matches(d, filter):
                before = copy.deepcopy(d)
                self._apply(d, update or {})
                return _project(d if return_document else before, projection)
        if upsert:
            # The atomic CLAIM shape (an upsert keyed on a unique field with
            # $setOnInsert): nothing matched, so the doc is inserted from the
            # filter's equality fields + $setOnInsert, and -- exactly as pymongo
            # -- BEFORE returns None (the caller reads "None => I inserted it,
            # the slot is mine"; a doc => someone got there first). Mirrors
            # update_one's upsert path above.
            seed = {k: v for k, v in (filter or {}).items() if not isinstance(v, dict)}
            seed.update((update or {}).get("$setOnInsert") or {})
            self._apply(seed, {k: v for k, v in (update or {}).items() if k != "$setOnInsert"})
            self.docs.append(seed)
            return _project(seed, projection) if return_document else None
        return None

    def update_many(self, filter, update, **kwargs):
        n = 0
        for d in self.docs:
            if matches(d, filter):
                self._apply(d, update)
                n += 1
        return type("R", (), {"matched_count": n, "modified_count": n})()

    def delete_one(self, filter, **kwargs):
        for i, d in enumerate(self.docs):
            if matches(d, filter):
                del self.docs[i]
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    def delete_many(self, filter=None, **kwargs):
        keep = [d for d in self.docs if not matches(d, filter)]
        n = len(self.docs) - len(keep)
        self.docs = keep
        return type("R", (), {"deleted_count": n})()

    def create_index(self, *_a, **_k):
        return "idx"

    # -- aggregation ------------------------------------------------------
    def aggregate(self, pipeline, **kwargs):
        docs = [dict(d) for d in self.docs]
        for stage in pipeline or []:
            if len(stage) != 1:
                raise UnsupportedMongoFeature(f"malformed aggregation stage {stage!r}")
            (name, spec), = stage.items()
            if name == "$match":
                docs = [d for d in docs if matches(d, spec)]
            elif name == "$sort":
                for key, direction in reversed(list(spec.items())):
                    docs.sort(
                        key=lambda d, k=key: (
                            _get_path(d, k) is _MISSING,
                            _get_path(d, k),
                        ),
                        reverse=(direction == -1),
                    )
            elif name == "$limit":
                docs = docs[: int(spec)]
            elif name == "$skip":
                docs = docs[int(spec) :]
            elif name == "$count":
                docs = [{spec: len(docs)}]
            elif name == "$group":
                docs = _group(docs, spec)
            else:
                raise UnsupportedMongoFeature(
                    f"aggregation stage {name!r} not implemented by StrictCollection"
                )
        return iter(docs)


def _field_value(doc, expr):
    """Resolve a $group accumulator argument (``"$field"`` or a literal)."""
    if isinstance(expr, str) and expr.startswith("$"):
        v = _get_path(doc, expr[1:])
        return None if v is _MISSING else v
    return expr


def _group(docs, spec):
    if "_id" not in spec:
        raise UnsupportedMongoFeature("$group without _id")
    id_expr = spec["_id"]
    if isinstance(id_expr, dict):
        raise UnsupportedMongoFeature("$group with a composite _id is not implemented")
    buckets: Dict[Any, List[Dict]] = {}
    order: List[Any] = []
    for d in docs:
        key = _field_value(d, id_expr)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(d)
    out = []
    for key in order:
        row = {"_id": key}
        for field, acc in spec.items():
            if field == "_id":
                continue
            if not isinstance(acc, dict) or len(acc) != 1:
                raise UnsupportedMongoFeature(f"$group accumulator {acc!r} not implemented")
            (acc_name, acc_arg), = acc.items()
            members = buckets[key]
            if acc_name == "$sum":
                row[field] = sum(
                    float(_field_value(d, acc_arg) or 0) if not isinstance(acc_arg, (int, float))
                    else acc_arg
                    for d in members
                )
            elif acc_name == "$first":
                row[field] = _field_value(members[0], acc_arg) if members else None
            elif acc_name == "$max":
                vals = [_field_value(d, acc_arg) for d in members]
                vals = [v for v in vals if v is not None]
                row[field] = max(vals) if vals else None
            elif acc_name == "$min":
                vals = [_field_value(d, acc_arg) for d in members]
                vals = [v for v in vals if v is not None]
                row[field] = min(vals) if vals else None
            else:
                raise UnsupportedMongoFeature(
                    f"$group accumulator {acc_name!r} not implemented"
                )
        out.append(row)
    return out


class StrictDB:
    """Database double: hands out :class:`StrictCollection` instances."""

    is_connected = True

    def __init__(self):
        self._collections: Dict[str, StrictCollection] = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = StrictCollection(name)
        return self._collections[name]

    def seed(self, name, docs):
        """Convenience: insert ``docs`` into ``name`` and return the collection."""
        coll = self.get_collection(name)
        for d in docs:
            coll.insert_one(d)
        return coll

    def list_collection_names(self):
        return list(self._collections)

    def __getitem__(self, name):
        return self.get_collection(name)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self.get_collection(name)
