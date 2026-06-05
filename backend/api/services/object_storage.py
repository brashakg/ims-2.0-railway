"""
IMS 2.0 - Object storage adapter (durable image hosting)
========================================================
A small swappable seam for persisting product-image bytes (the EDITED output of
the design-queue auto-edit, and -- later -- the BVI `/uploads/` re-host that the
Shopify cutover needs). Pick the backend with IMAGE_STORAGE_PROVIDER:

  s3     -> any S3-compatible bucket (AWS S3 / Cloudflare R2 / MinIO). OPTIONAL
            dep (boto3); unavailable until installed + creds set. Recommended for
            production (R2 = zero egress; Shopify/CDN pulls are free).
  local  -> write under a local dir + serve via a base URL. Default / dev.

Env (s3): IMAGE_S3_BUCKET, IMAGE_S3_ENDPOINT (optional, for R2/MinIO),
          IMAGE_S3_ACCESS_KEY, IMAGE_S3_SECRET_KEY, IMAGE_S3_PUBLIC_BASE
          (public URL prefix), IMAGE_S3_REGION (optional).
Env (local): IMAGE_LOCAL_DIR (default ./uploads/edited),
             IMAGE_LOCAL_BASE_URL (default /uploads/edited).

FAIL-SOFT: `put` raises on a real failure; `available()` lets the caller pick a
working backend or fail-soft (keep RAW) when none is configured.
"""

from __future__ import annotations

import os
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class ObjectStorage(Protocol):
    name: str

    def available(self) -> bool: ...

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> str:
        """Persist bytes under `key`; return a durable, fetchable URL. Raises on
        failure."""


class LocalDiskStorage:
    """Dev/default backend: write under a local dir, return a base-URL path.
    Durable only as far as the host disk is -- fine for dev, NOT for the Railway
    ephemeral container (use s3 in prod)."""

    name = "local"

    def __init__(self, root: Optional[str] = None, base_url: Optional[str] = None):
        self._root = root or os.getenv("IMAGE_LOCAL_DIR", "uploads/edited")
        self._base_url = (
            base_url or os.getenv("IMAGE_LOCAL_BASE_URL", "/uploads/edited")
        ).rstrip("/")

    def available(self) -> bool:
        return True

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> str:
        safe_key = key.lstrip("/")
        path = os.path.join(self._root, safe_key)
        os.makedirs(os.path.dirname(path) or self._root, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return f"{self._base_url}/{safe_key}"


class S3Storage:
    """S3-compatible backend (AWS S3 / Cloudflare R2 / MinIO). Optional boto3."""

    name = "s3"

    def __init__(self):
        self._bucket = os.getenv("IMAGE_S3_BUCKET", "")
        self._public_base = os.getenv("IMAGE_S3_PUBLIC_BASE", "").rstrip("/")
        self._endpoint = os.getenv("IMAGE_S3_ENDPOINT") or None
        self._access_key = os.getenv("IMAGE_S3_ACCESS_KEY") or None
        self._secret_key = os.getenv("IMAGE_S3_SECRET_KEY") or None
        self._region = os.getenv("IMAGE_S3_REGION") or None

    def available(self) -> bool:
        try:
            import boto3  # noqa: F401
        except Exception:  # noqa: BLE001 -- optional dep not installed
            return False
        return bool(self._bucket and self._access_key and self._secret_key)

    def _client(self):
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
        )

    def put(self, key: str, data: bytes, content_type: str = "image/png") -> str:
        if not self.available():
            raise RuntimeError("S3 storage not configured (bucket/creds/boto3)")
        safe_key = key.lstrip("/")
        self._client().put_object(
            Bucket=self._bucket, Key=safe_key, Body=data, ContentType=content_type
        )
        if self._public_base:
            return f"{self._public_base}/{safe_key}"
        if self._endpoint:
            return f"{self._endpoint.rstrip('/')}/{self._bucket}/{safe_key}"
        return f"https://{self._bucket}.s3.amazonaws.com/{safe_key}"


def get_object_storage() -> ObjectStorage:
    """Resolve the configured storage backend.

    Priority:
      1. DB integrations doc (type="storage", enabled=True) -- allows the
         owner to manage S3/R2 creds in Settings -> Integrations without
         touching Railway env vars.
      2. Legacy env vars (IMAGE_STORAGE_PROVIDER / IMAGE_S3_*).
      3. Local-disk fallback (dev / Railway ephemeral; not durable).
    """
    # 1. Try DB config first (fail-soft)
    try:
        from api.services.integration_config import get_storage_config

        db_cfg = get_storage_config()
        db_provider = (db_cfg.get("provider") or "").strip().lower()
        if db_provider == "s3" and db_cfg.get("bucket") and db_cfg.get("access_key"):
            s = S3Storage()
            # Override with DB values -- reconstruct with explicit kwargs
            s._bucket = db_cfg["bucket"]
            s._access_key = db_cfg["access_key"]
            s._secret_key = db_cfg.get("secret_key", "")
            s._endpoint = db_cfg.get("endpoint") or None
            s._public_base = (db_cfg.get("public_base") or "").rstrip("/")
            s._region = db_cfg.get("region") or None
            return s
        if db_provider in ("local", "disk"):
            return LocalDiskStorage()
    except Exception:  # noqa: BLE001 -- never let config lookup break storage
        pass

    # 2. Env-var path (legacy / override)
    provider = (os.getenv("IMAGE_STORAGE_PROVIDER", "") or "").strip().lower()
    if provider == "s3":
        return S3Storage()
    if provider in ("local", "disk"):
        return LocalDiskStorage()
    # auto: prefer a working S3, else local
    s3 = S3Storage()
    if s3.available():
        return s3
    return LocalDiskStorage()
