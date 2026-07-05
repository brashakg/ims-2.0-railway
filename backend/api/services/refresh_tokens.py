"""
IMS 2.0 - Rotating refresh-token store
======================================
Server-side persistence for the single-use rotating refresh tokens introduced
by the 2026-07 token hardening (45-min access tokens + 8h ABSOLUTE session).

Model (Mongo collection `refresh_tokens`; one doc per issued refresh token):
    token_hash              sha256 hex of the opaque token (plaintext is NEVER stored)
    user_id / username      owner
    family_id               the chain (session) id -- every rotation stays in the
                            same family; revocation nukes the whole family
    issued_at               datetime (UTC)
    expires_at              datetime (UTC) == absolute_session_start + REFRESH_ABSOLUTE_HOURS.
                            Refresh tokens do NOT slide: the whole chain dies at
                            the absolute cap no matter how often it rotates.
    absolute_session_start  datetime (UTC) of the FIRST login of this session
    rotated_from            token_hash of the token this one replaced (None for the first)
    revoked                 bool + revoked_at/revoked_reason ("rotated" | "logout" |
                            "reuse_detected" | "account_disabled")

Security semantics:
  * SINGLE-USE: consume() atomically marks a token rotated; the caller then
    issues the replacement. Presenting an already-consumed token is treated as
    a stolen-token CANARY: the entire family is revoked ("reuse_detected") and
    the caller must 401. Exception: a short grace window (REUSE_GRACE_SECONDS,
    default 60s) tolerates two browser tabs racing the same rotation -- within
    the grace the second consumer is served instead of nuking the session.
    (Real theft is normally detected long after rotation, so the canary keeps
    its teeth; in-grace theft is the standard accepted tradeoff.)
  * Consumed docs stay in the collection until the absolute session end (+24h
    TTL slack) precisely SO reuse can be detected.

Storage: Mongo when connected (cross-worker, survives restarts). When Mongo is
unavailable (local dev / DB-less unit tests) an in-process dict fallback keeps
the auth flow working single-worker; tests reset it via conftest.

No emojis in this file (Windows cp1252).
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Absolute session cap in hours -- a refresh chain can never extend a session
# past first-login + this. Matches the pre-hardening 8h access-token lifetime,
# so staff shift UX is unchanged. Env-overridable, deploy-safe default.
REFRESH_ABSOLUTE_HOURS = int(os.getenv("REFRESH_ABSOLUTE_HOURS", "8") or "8")

# Multi-tab race tolerance: a token consumed by rotation may be presented AGAIN
# within this many seconds without tripping the stolen-token canary (two tabs
# firing the same proactive refresh). 0 disables the grace entirely.
REUSE_GRACE_SECONDS = int(os.getenv("REFRESH_REUSE_GRACE_SECONDS", "60") or "60")

# Keep dead docs for a day past the absolute session end (Mongo TTL) -- they
# are what makes reuse detection possible during the session.
_TTL_SLACK_SECONDS = 24 * 3600


def _utcnow() -> datetime:
    return datetime.utcnow()


def hash_refresh_token(token: str) -> str:
    """sha256 hex of the opaque refresh token (plaintext is never persisted)."""
    return hashlib.sha256(token.encode()).hexdigest()


class RefreshTokenStore:
    """Mongo-backed (in-memory fallback) store for rotating refresh tokens."""

    COLLECTION = "refresh_tokens"

    def __init__(self) -> None:
        # token_hash -> doc. Dev/test fallback when Mongo is unreachable.
        self._mem: dict = {}
        self._mem_lock = threading.Lock()
        self._indexes_ensured = False

    # ------------------------------------------------------------------
    # storage plumbing
    # ------------------------------------------------------------------

    def _coll(self):
        """The Mongo collection, or None when no DB is connected (fallback to
        the in-memory dict). Index creation is idempotent + fail-soft."""
        try:
            from database.connection import get_db

            wrapper = get_db()
            db = getattr(wrapper, "db", None) if wrapper is not None else None
            if db is None:
                return None
            coll = db.get_collection(self.COLLECTION)
            if coll is None:
                return None
            if not self._indexes_ensured:
                try:
                    coll.create_index("token_hash", unique=True)
                    coll.create_index("family_id")
                    coll.create_index(
                        "expires_at", expireAfterSeconds=_TTL_SLACK_SECONDS
                    )
                    self._indexes_ensured = True
                except Exception as e:  # noqa: BLE001 - index is hygiene, not correctness
                    logger.debug("refresh_tokens: index ensure skipped: %s", e)
            return coll
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # issue
    # ------------------------------------------------------------------

    def issue(
        self,
        user_id: str,
        username: str,
        session_start: Optional[datetime] = None,
        family_id: Optional[str] = None,
        rotated_from: Optional[str] = None,
    ) -> Optional[Tuple[str, dict]]:
        """Mint + persist a new refresh token.

        Returns (plaintext_token, doc) -- the plaintext exists ONLY in the
        return value; the doc stores its sha256. Returns None when the absolute
        session cap has already passed (session exhausted -> caller 401s).
        """
        now = _utcnow()
        start = session_start or now
        expires_at = start + timedelta(hours=REFRESH_ABSOLUTE_HOURS)
        if expires_at <= now:
            return None

        token = secrets.token_urlsafe(48)
        doc = {
            "token_hash": hash_refresh_token(token),
            "user_id": user_id,
            "username": username,
            "family_id": family_id or uuid.uuid4().hex,
            "issued_at": now,
            "expires_at": expires_at,
            "absolute_session_start": start,
            "rotated_from": rotated_from,
            "revoked": False,
            "revoked_at": None,
            "revoked_reason": None,
        }

        coll = self._coll()
        if coll is not None:
            coll.insert_one(dict(doc))
            doc.pop("_id", None)
        else:
            with self._mem_lock:
                self._mem[doc["token_hash"]] = doc
        return token, doc

    # ------------------------------------------------------------------
    # consume (single-use rotation + reuse canary)
    # ------------------------------------------------------------------

    def consume(self, token: str) -> Tuple[str, Optional[dict]]:
        """Atomically spend a refresh token for rotation.

        Returns (status, doc):
          "ok"      -- token was live (or an in-grace multi-tab re-present);
                       caller issues the replacement in the same family.
          "expired" -- past the absolute session cap. Plain 401, no canary.
          "reused"  -- token was ALREADY spent/revoked outside the grace window.
                       The WHOLE family has been revoked (stolen-token canary);
                       caller must 401.
          "invalid" -- unknown token.
        """
        h = hash_refresh_token(token)
        now = _utcnow()
        coll = self._coll()

        if coll is not None:
            from pymongo import ReturnDocument

            doc = coll.find_one_and_update(
                {"token_hash": h, "revoked": False},
                {
                    "$set": {
                        "revoked": True,
                        "revoked_at": now,
                        "revoked_reason": "rotated",
                    }
                },
                return_document=ReturnDocument.BEFORE,
            )
            if doc is not None:
                if doc["expires_at"] <= now:
                    return "expired", doc
                return "ok", doc
            prior = coll.find_one({"token_hash": h})
        else:
            with self._mem_lock:
                mem_doc = self._mem.get(h)
                if mem_doc is not None and not mem_doc.get("revoked"):
                    snapshot = dict(mem_doc)
                    mem_doc["revoked"] = True
                    mem_doc["revoked_at"] = now
                    mem_doc["revoked_reason"] = "rotated"
                    if snapshot["expires_at"] <= now:
                        return "expired", snapshot
                    return "ok", snapshot
                prior = dict(mem_doc) if mem_doc is not None else None

        if prior is None:
            return "invalid", None

        if prior["expires_at"] <= now:
            return "expired", prior

        # Already-revoked token presented again. Two tabs racing the SAME
        # rotation within the grace window is legitimate; anything else is the
        # stolen-token canary -> revoke the entire chain.
        revoked_at = prior.get("revoked_at")
        if (
            prior.get("revoked_reason") == "rotated"
            and revoked_at is not None
            and (now - revoked_at).total_seconds() <= REUSE_GRACE_SECONDS
        ):
            return "ok", prior

        self.revoke_family(prior.get("family_id"), reason="reuse_detected")
        logger.warning(
            "refresh token REUSE detected for user=%s family=%s -- chain revoked",
            prior.get("username"),
            prior.get("family_id"),
        )
        return "reused", prior

    # ------------------------------------------------------------------
    # revocation
    # ------------------------------------------------------------------

    def revoke_family(self, family_id: Optional[str], reason: str) -> None:
        """Revoke EVERY token in a chain (logout / reuse canary / disable).
        Fail-soft: revocation errors are logged, never raised."""
        if not family_id:
            return
        now = _utcnow()
        update = {"revoked": True, "revoked_at": now, "revoked_reason": reason}
        try:
            coll = self._coll()
            if coll is not None:
                coll.update_many({"family_id": family_id}, {"$set": dict(update)})
            with self._mem_lock:
                for doc in self._mem.values():
                    if doc.get("family_id") == family_id and not doc.get("revoked"):
                        doc.update(update)
        except Exception as e:  # noqa: BLE001
            logger.warning("refresh_tokens: family revoke failed: %s", e)


# Process-wide singleton (mirrors auth.py's _token_blacklist pattern).
refresh_token_store = RefreshTokenStore()
