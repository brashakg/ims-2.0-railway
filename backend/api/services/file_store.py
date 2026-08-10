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


# ---------------------------------------------------------------------------
# SECURITY CONTRACT for FileStore.get -- read this before adding a serve route
# ---------------------------------------------------------------------------
# ONE GridFS bucket holds every binary the app stores: product images, company
# logos, GRN attachments, expense bills, task attachments, handoff files, and
# employee Aadhaar / PAN / UAN / ESIC SCANS. A file_id is therefore a bearer
# capability over the whole bucket -- whoever can name one can read it unless
# the read is scoped.
#
# THE RULE:
#   * If the endpoint takes the file_id from the REQUEST (path/query/body), it
#     MUST pass require_kind="<the kind stamped at upload>". Anything else is a
#     universal read of the bucket. This is not hypothetical: the company-logo
#     serve omitted it and could stream any employee's Aadhaar scan.
#   * If the endpoint derives the file_id from a record the caller has ALREADY
#     been authorised to read (the expense doc, the GRN, the task, the handoff,
#     the employee document row), the record is the authorisation and the read
#     may be unscoped -- pass require_kind=ANY_KIND to say so DELIBERATELY.
#
# ENFORCEMENT IS AT RUNTIME, NOT IN A LINTER. `require_kind` is a REQUIRED
# keyword-only argument: omit it and the call raises TypeError immediately.
# An earlier round tried to enforce this with an AST guard instead; a security
# panel evaded it with 11 of 16 spellings (`fid = file_id`, a handle passed as a
# parameter, a handle on `self.`, an aliased import, a walrus, a tuple-unpack,
# `getattr(fs, "get")`, ...) and shipped a LIVE route that streamed any blob.
# Static analysis has to enumerate spellings; the signature does not care how
# the call is spelled. The static guard is kept as a second layer, but it is no
# longer the thing standing between the bucket and a new serve endpoint.
#
# ANY_KIND is the DELIBERATE opt-out, and it is deliberately NOT the path of
# least resistance: it must be named at the call site, and the guard test
# treats it as unscoped rather than as "argument present, therefore fine".
ANY_KIND = "__any_kind__"


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
        self, file_id: str, *, require_kind: str
    ) -> Optional[Tuple[bytes, str, str]]:
        """Return (content, filename, mime_type) or None when missing.

        `require_kind`: when set to a kind string, ALSO return None unless the
        stored file's metadata.kind matches, so a serve endpoint refuses to hand
        back a DIFFERENT kind of file from the shared bucket -- without it, an
        image serve handed a GRN attachment / expense bill / employee ID-scan
        file_id would leak it.

        MANDATORY whenever the file_id comes from the request. Pass the module
        sentinel ``ANY_KIND`` (or None, its legacy equivalent) ONLY when the
        caller has already been authorised against the record that owns the
        file_id. See the SECURITY CONTRACT block at the top of this module."""
        raise NotImplementedError

    def get_metadata(self, file_id: str) -> Optional[dict]:
        """Return the stored metadata dict, or None when the file is missing.

        Exists so a handler that ACCEPTS a file_id from the request can
        AUTHORISE it -- check its kind, its owner, its store -- before binding
        it to a record. Proving a file merely EXISTS is not authorisation: the
        bucket is shared, so "it exists" is true of every other feature's
        documents too. Never returns bytes."""
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

    def get(self, file_id, *, require_kind):
        rec = self._files.get(file_id)
        if rec is None:
            return None
        if require_kind not in (None, ANY_KIND):
            if (rec.get("metadata") or {}).get("kind") != require_kind:
                return None
        return (rec["content"], rec["filename"], rec["mime_type"])

    def get_metadata(self, file_id) -> Optional[dict]:
        rec = self._files.get(file_id)
        if rec is None:
            return None
        return dict(rec.get("metadata") or {})

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

    def get(self, file_id, *, require_kind):
        fs = self._bucket()
        if fs is None:
            return None
        try:
            from bson import ObjectId

            grid_out = fs.get(ObjectId(file_id))
            if require_kind not in (None, ANY_KIND):
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

    def get_metadata(self, file_id) -> Optional[dict]:
        fs = self._bucket()
        if fs is None:
            return None
        try:
            from bson import ObjectId

            grid_out = fs.get(ObjectId(file_id))
            return dict(getattr(grid_out, "metadata", None) or {})
        except Exception as e:
            logger.debug(f"[FILESTORE] get_metadata failed for {file_id}: {e}")
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
