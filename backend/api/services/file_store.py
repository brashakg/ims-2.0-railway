"""
IMS 2.0 — File store (GridFS-backed binary storage)
====================================================

Thin abstraction over MongoDB GridFS for binary file storage. Used by
the handoffs feature (and any future feature that needs to persist
larger-than-16MB files alongside Mongo docs).

Design notes:
- GridFS is the canonical answer for >16MB files in Mongo. Smaller
  files could go inline as base64 but the 16MB BSON cap + the 33%
  base64 overhead means 25MB PDFs (which we accept) won't fit.
- Tests don't have a real Mongo, so we expose a simple `FileStore`
  protocol with two implementations:
    * `GridFSFileStore` — production
    * `InMemoryFileStore` — tests; stores bytes in a dict
- The handoff TTL (Mongo TTL index on `expires_at`) only deletes the
  metadata doc, not the GridFS blob. A separate sweep removes orphans.
  See `cleanup_orphan_files()` below — wired into NEXUS hourly tick.
- Fail-soft contract: when the underlying store is unavailable, the
  call returns None / False rather than raising. Callers must check.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple
import uuid

logger = logging.getLogger(__name__)


# Allowed mime types for handoffs (images + PDF, per user direction)
ALLOWED_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/heic",
        "image/heif",
        "image/webp",
        "image/avif",
        "image/gif",
        "application/pdf",
    }
)

# 25 MB cap (per user direction)
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024


class FileStore:
    """Abstract file-store interface."""

    def put(
        self,
        *,
        content: bytes,
        filename: str,
        mime_type: str,
        metadata: Optional[dict] = None,
    ) -> Optional[str]:
        """Store bytes; return a file_id string or None on failure."""
        raise NotImplementedError

    def get(
        self, file_id: str, *, require_kind: Optional[str] = None
    ) -> Optional[Tuple[bytes, str, str]]:
        """Return (content, filename, mime_type) or None when missing.

        `require_kind`: when set, ALSO return None unless the stored file's
        metadata.kind matches. This lets a PUBLIC per-kind serve endpoint (e.g.
        product images) refuse to hand back a DIFFERENT kind of file from the
        shared store -- without it, a public image serve could be handed a GRN
        attachment / expense bill file_id and leak it. Callers that don't pass
        require_kind get the original unscoped behaviour."""
        raise NotImplementedError

    def delete(self, file_id: str) -> bool:
        raise NotImplementedError

    def list_ids_with_metadata_key(self, key: str) -> list:
        """Return all file_ids whose metadata contains the given key.
        Used by the orphan sweep (find files with metadata.handoff_id
        whose handoff doc no longer exists)."""
        raise NotImplementedError


class InMemoryFileStore(FileStore):
    """Test/dev fallback. Bytes live in a dict; no persistence."""

    def __init__(self):
        self._files: dict = {}

    def put(self, *, content, filename, mime_type, metadata=None) -> Optional[str]:
        file_id = str(uuid.uuid4())
        self._files[file_id] = {
            "content": content,
            "filename": filename,
            "mime_type": mime_type,
            "metadata": metadata or {},
        }
        return file_id

    def get(self, file_id, *, require_kind=None):
        rec = self._files.get(file_id)
        if rec is None:
            return None
        if require_kind is not None and (rec.get("metadata") or {}).get("kind") != require_kind:
            return None
        return (rec["content"], rec["filename"], rec["mime_type"])

    def delete(self, file_id) -> bool:
        return self._files.pop(file_id, None) is not None

    def list_ids_with_metadata_key(self, key: str) -> list:
        return [
            fid
            for fid, rec in self._files.items()
            if isinstance(rec.get("metadata"), dict) and key in rec["metadata"]
        ]


class GridFSFileStore(FileStore):
    """Production store backed by GridFS. Lazy-initialised so tests
    that never touch GridFS don't pay the import cost."""

    def __init__(self, db):
        self._db = db
        self._fs = None

    def _bucket(self):
        if self._fs is None:
            try:
                import gridfs

                self._fs = gridfs.GridFS(self._db)
            except Exception as e:
                logger.warning(f"[FILESTORE] GridFS unavailable: {e}")
                return None
        return self._fs

    def put(self, *, content, filename, mime_type, metadata=None) -> Optional[str]:
        fs = self._bucket()
        if fs is None:
            return None
        try:
            grid_id = fs.put(
                content,
                filename=filename,
                contentType=mime_type,
                metadata=metadata or {},
            )
            return str(grid_id)
        except Exception as e:
            logger.warning(f"[FILESTORE] put failed: {e}")
            return None

    def get(self, file_id, *, require_kind=None):
        fs = self._bucket()
        if fs is None:
            return None
        try:
            from bson import ObjectId

            grid_out = fs.get(ObjectId(file_id))
            if require_kind is not None:
                meta = getattr(grid_out, "metadata", None) or {}
                if meta.get("kind") != require_kind:
                    return None
            return (
                grid_out.read(),
                grid_out.filename or "",
                grid_out.content_type or "application/octet-stream",
            )
        except Exception as e:
            logger.debug(f"[FILESTORE] get failed for {file_id}: {e}")
            return None

    def delete(self, file_id) -> bool:
        fs = self._bucket()
        if fs is None:
            return False
        try:
            from bson import ObjectId

            fs.delete(ObjectId(file_id))
            return True
        except Exception as e:
            logger.debug(f"[FILESTORE] delete failed for {file_id}: {e}")
            return False

    def list_ids_with_metadata_key(self, key: str) -> list:
        try:
            files_coll = self._db["fs.files"]
            return [
                str(doc["_id"])
                for doc in files_coll.find(
                    {f"metadata.{key}": {"$exists": True}}, {"_id": 1}
                )
            ]
        except Exception:
            return []


# ============================================================================
# Module-level accessor
# ============================================================================

_INSTANCE: Optional[FileStore] = None


def get_file_store() -> Optional[FileStore]:
    """Return the active FileStore instance (GridFS in prod, lazy-init
    on first call). Returns None if Mongo is unavailable."""
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE
    try:
        from database.connection import get_db

        db = get_db()
        if db is not None and db.is_connected:
            # Reach down to the underlying pymongo Database. MUST be an
            # explicit None check: pymongo Database forbids truth-testing
            # (bool() raises NotImplementedError), so `getattr(...) or db`
            # blew up here on every prod call and the except below swallowed
            # it -- file storage reported "unavailable" whenever a REAL Mongo
            # was connected. (Dev mocks have normal truthiness, hiding it.)
            mongo_db = getattr(db, "db", None)
            if mongo_db is None:
                mongo_db = db
            _INSTANCE = GridFSFileStore(mongo_db)
            return _INSTANCE
    except Exception as e:
        logger.warning(f"[FILESTORE] init failed: {e}")
    return None


def set_file_store(store: Optional[FileStore]) -> None:
    """Replace the active FileStore (used by tests)."""
    global _INSTANCE
    _INSTANCE = store
