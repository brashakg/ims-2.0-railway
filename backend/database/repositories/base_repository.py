"""
IMS 2.0 - Base Repository
==========================
Abstract base class for all repositories
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, TypeVar, Generic
from datetime import datetime
import uuid

try:
    from api.utils.ist import now_ist_naive as _now
except ImportError:
    _now = datetime.now  # type: ignore[assignment]

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
    """
    Abstract base repository with common CRUD operations
    """

    def __init__(self, collection):
        """
        Initialize repository with a MongoDB collection

        Args:
            collection: MongoDB collection or MockCollection instance
        """
        self.collection = collection

    @property
    @abstractmethod
    def entity_name(self) -> str:
        """Name of the entity (for logging)"""
        pass

    @property
    @abstractmethod
    def id_field(self) -> str:
        """Primary ID field name"""
        pass

    def _generate_id(self) -> str:
        """Generate unique ID"""
        return str(uuid.uuid4())

    def _add_timestamps(self, data: Dict, is_update: bool = False) -> Dict:
        """Add created_at/updated_at timestamps"""
        now = _now()
        if not is_update:
            data["created_at"] = now
        data["updated_at"] = now
        return data

    @staticmethod
    def _clean_id(doc: Optional[Dict]) -> Optional[Dict]:
        """Stringify a BSON ObjectId `_id` so FastAPI can JSON-serialise it.

        Docs created through this repo get a string `_id` (== id_field, see
        create()), but docs inserted directly via `insert_one` — e.g. the
        May 2026 TechCherry migration of 5,022 customers / 10,805 products /
        322 orders — carry a real MongoDB ObjectId. Returning that raw to a
        FastAPI endpoint 500s with
            ValueError: [TypeError("'ObjectId' object is not iterable")]
        which is exactly what made the Customers page fail to load once a
        TechCherry store (BV-PUN-01) was in scope. Converting to str here
        fixes every read path at once instead of per-endpoint. Lookups use
        the business id_field (customer_id, order_id, ...), never `_id`, so
        stringifying is safe.
        """
        if doc is not None:
            _id = doc.get("_id")
            if _id is not None and not isinstance(_id, str):
                doc["_id"] = str(_id)
        return doc

    # =========================================================================
    # CRUD Operations
    # =========================================================================

    def create(self, data: Dict, *, raise_on_duplicate: bool = False) -> Optional[Dict]:
        """
        Create new document

        Args:
            data: Document data
            raise_on_duplicate: when True, a unique-index DuplicateKeyError is
                RE-RAISED instead of swallowed, so a caller can map a race-lost
                insert to a clean 409 (instead of a silent None -> 500). Default
                False preserves the historical fail-soft behaviour for every other
                caller. Matched by class name so no hard pymongo import is needed
                (and MockCollection, which never raises it, is unaffected).

        Returns:
            Created document with ID
        """
        try:
            # Generate ID if not provided
            if self.id_field not in data:
                data[self.id_field] = self._generate_id()

            # Add timestamps
            data = self._add_timestamps(data)

            # Set _id to match our ID field
            data["_id"] = data[self.id_field]

            result = self.collection.insert_one(data)
            return data
        except Exception as e:
            if raise_on_duplicate and e.__class__.__name__ == "DuplicateKeyError":
                raise
            print(f"Error creating {self.entity_name}: {e}")
            return None

    def find_by_id(self, id: str) -> Optional[Dict]:
        """
        Find document by ID

        Args:
            id: Document ID

        Returns:
            Document or None
        """
        try:
            return self._clean_id(self.collection.find_one({self.id_field: id}))
        except Exception as e:
            print(f"Error finding {self.entity_name}: {e}")
            return None

    def find_one(self, filter: Dict) -> Optional[Dict]:
        """
        Find single document by filter

        Args:
            filter: MongoDB filter

        Returns:
            Document or None
        """
        try:
            return self._clean_id(self.collection.find_one(filter))
        except Exception as e:
            print(f"Error finding {self.entity_name}: {e}")
            return None

    def find_many(
        self,
        filter: Dict = None,
        sort: List[tuple] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict]:
        """
        Find multiple documents

        Args:
            filter: MongoDB filter
            sort: Sort specification [(field, direction)]
            skip: Number to skip
            limit: Maximum to return

        Returns:
            List of documents
        """
        try:
            cursor = self.collection.find(filter or {})

            if sort:
                cursor = cursor.sort(sort)
            if skip:
                cursor = cursor.skip(skip)
            if limit:
                cursor = cursor.limit(limit)

            return [self._clean_id(doc) for doc in cursor]
        except Exception as e:
            print(f"Error finding {self.entity_name}s: {e}")
            return []

    def update(self, id: str, data: Dict) -> bool:
        """
        Update document by ID

        Args:
            id: Document ID
            data: Fields to update

        Returns:
            Success boolean
        """
        try:
            # Add updated timestamp
            data["updated_at"] = _now()

            result = self.collection.update_one({self.id_field: id}, {"$set": data})
            return result.modified_count > 0
        except Exception as e:
            print(f"Error updating {self.entity_name}: {e}")
            return False

    def delete(self, id: str) -> bool:
        """
        Delete document by ID (hard delete)

        Args:
            id: Document ID

        Returns:
            Success boolean
        """
        try:
            result = self.collection.delete_one({self.id_field: id})
            return result.deleted_count > 0
        except Exception as e:
            print(f"Error deleting {self.entity_name}: {e}")
            return False

    def soft_delete(self, id: str) -> bool:
        """
        Soft delete by setting is_active=False

        Args:
            id: Document ID

        Returns:
            Success boolean
        """
        return self.update(id, {"is_active": False, "deleted_at": _now()})

    def count(self, filter: Dict = None) -> int:
        """
        Count documents

        Args:
            filter: MongoDB filter

        Returns:
            Count
        """
        try:
            return self.collection.count_documents(filter or {})
        except Exception as e:
            print(f"Error counting {self.entity_name}s: {e}")
            return 0

    def exists(self, filter: Dict) -> bool:
        """
        Check if document exists

        Args:
            filter: MongoDB filter

        Returns:
            Exists boolean
        """
        return self.find_one(filter) is not None

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    def create_many(self, documents: List[Dict]) -> List[Dict]:
        """
        Create multiple documents

        Args:
            documents: List of documents

        Returns:
            List of created documents
        """
        try:
            for doc in documents:
                if self.id_field not in doc:
                    doc[self.id_field] = self._generate_id()
                doc = self._add_timestamps(doc)
                doc["_id"] = doc[self.id_field]

            self.collection.insert_many(documents)
            return documents
        except Exception as e:
            print(f"Error bulk creating {self.entity_name}s: {e}")
            return []

    def update_many(self, filter: Dict, data: Dict) -> int:
        """
        Update multiple documents

        Args:
            filter: MongoDB filter
            data: Fields to update

        Returns:
            Number updated
        """
        try:
            data["updated_at"] = datetime.now()
            result = self.collection.update_many(filter, {"$set": data})
            return result.modified_count
        except Exception as e:
            print(f"Error bulk updating {self.entity_name}s: {e}")
            return 0

    # =========================================================================
    # Query Helpers
    # =========================================================================

    def find_active(self, filter: Dict = None) -> List[Dict]:
        """Find only active documents"""
        query = {"is_active": True}
        if filter:
            query.update(filter)
        return self.find_many(query)

    def search(self, text: str, fields: List[str], filter: Dict = None) -> List[Dict]:
        """
        Tokenized text search across fields.

        A multi-word query is split on whitespace; EVERY token must match at
        least one of `fields` (case-insensitive prefix match), and ALL tokens
        must match somewhere. This is how a cashier actually types -- e.g.
        "Fastrack P357" finds a doc with brand="Fastrack" + model="P357BK1".

        The old implementation matched the WHOLE phrase as one regex against
        each field individually, so a cross-field multi-word query found
        nothing (no single field contained "Fastrack P357"). Single-token
        queries are unchanged (one token, OR across fields). Tokens are
        regex-escaped and anchored with ^ so a SKU like "P357BK1" matches
        P357BK1* but not RAY-P357BK1. Prefix matching via ^ enables the
        database to use a compound index on (field, 1) instead of full scans.

        Args:
            text: Search text (one or more whitespace-separated tokens)
            fields: Fields to search
            filter: Additional filter

        Returns:
            Matching documents
        """
        try:
            import re

            tokens = [t for t in (text or "").split() if t]
            if not tokens:
                # Empty query -> apply only the caller's filter (match-all
                # within scope), matching the pre-tokenization empty-string
                # behaviour where `$regex: ""` matched everything.
                return self.find_many(filter or {})

            and_clauses = []
            for tok in tokens:
                # Anchor with ^ for prefix matching so indexes can be used.
                # ^ prevents full scans and keeps the result semantics
                # (e.g., searching "ray" no longer matches "spray" or "primary").
                regex = {"$regex": "^" + re.escape(tok), "$options": "i"}
                and_clauses.append({"$or": [{field: regex} for field in fields]})

            query = {"$and": and_clauses}
            if filter:
                query["$and"].append(filter)

            return self.find_many(query)
        except Exception as e:
            print(f"Error searching {self.entity_name}s: {e}")
            return []

    def aggregate(self, pipeline: List[Dict]) -> List[Dict]:
        """
        Run aggregation pipeline

        Args:
            pipeline: Aggregation stages

        Returns:
            Aggregation results
        """
        try:
            return [self._clean_id(doc) for doc in self.collection.aggregate(pipeline)]
        except Exception as e:
            print(f"Error aggregating {self.entity_name}s: {e}")
            return []
