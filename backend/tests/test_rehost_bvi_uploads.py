"""
Unit tests for scripts/rehost_bvi_uploads.py -- the PURE re-host logic + the
runner with mocked storage + byte-fetch (NO live DB, NO network).

Covered:
  1. classify_url   -- durable (s3/shopify) vs /uploads vs ephemeral-BVI vs
                       unknown-host (SSRF) vs blank; subdomain + spoof safety.
  2. host allowlists -- build_durable_hosts / build_bvi_hosts + env parsing.
  3. resolve_fetch_url -- SSRF-safe fetch-url resolution (uploads join, ephemeral
                       pass-through, unknown -> None, no base -> None).
  4. plan_rewrite   -- url rewrite + original_url preservation (no clobber).
  5. parity count + decide_exit_code -- the shutdown-gate logic.
  6. run_rehost     -- dry-run (no writes), live rewrite (mock put + fetch),
                       idempotent re-run skips durable, SSRF-skip unknown host,
                       and the parity exit-gate end-to-end.
"""
from __future__ import annotations

import os
import sys

# Make the scripts directory importable without installing.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import pytest  # noqa: E402

import rehost_bvi_uploads as rh  # noqa: E402
from rehost_bvi_uploads import (  # noqa: E402
    BLANK,
    DURABLE,
    EPHEMERAL_BVI,
    LOCAL_UPLOADS,
    UNKNOWN_HOST,
    build_bvi_hosts,
    build_durable_hosts,
    classify_url,
    count_non_durable,
    decide_exit_code,
    is_non_durable,
    is_rehostable,
    plan_rewrite,
    resolve_fetch_url,
    run_rehost,
)


# ---------------------------------------------------------------------------
# Shared allowlists for classification tests
# ---------------------------------------------------------------------------

DURABLE_HOSTS = build_durable_hosts(
    s3_public_base="https://img.bettervision-cdn.com",  # the durable R2/S3 base
)
BVI_HOSTS = build_bvi_hosts(public_base="https://uniparallel.com")


def _classify(url):
    return classify_url(url, durable_hosts=DURABLE_HOSTS, bvi_hosts=BVI_HOSTS)


# ---------------------------------------------------------------------------
# 1. classify_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        # durable: shopify CDN + the configured S3 public base host
        ("https://cdn.shopify.com/s/files/1/x.png", DURABLE),
        ("https://img.bettervision-cdn.com/bvi-rehost/a.jpg", DURABLE),
        ("https://sub.cdn.shopify.com/x.png", DURABLE),  # subdomain ok
        # local /uploads (relative) -> needs rehost, fetch via BVI base
        ("/uploads/abc-169.png", LOCAL_UPLOADS),
        ("uploads/def.jpg", LOCAL_UPLOADS),
        ("public/uploads/x.png", LOCAL_UPLOADS),
        ("img/x.png", LOCAL_UPLOADS),  # bare relative path
        # ephemeral BVI host (absolute) -> needs rehost, fetch directly
        ("https://uniparallel.com/uploads/x.png", EPHEMERAL_BVI),
        ("https://www.bettervision.in/img/y.jpg", EPHEMERAL_BVI),
        ("http://localhost:3000/uploads/z.png", EPHEMERAL_BVI),
        # unknown host -> SSRF skip
        ("https://evil.example.com/x.png", UNKNOWN_HOST),
        ("https://169.254.169.254/latest/meta-data", UNKNOWN_HOST),  # cloud metadata!
        ("data:image/png;base64,AAAA", UNKNOWN_HOST),
        ("file:///etc/passwd", UNKNOWN_HOST),
        # blank
        ("", BLANK),
        ("   ", BLANK),
        (None, BLANK),
    ],
)
def test_classify_url(url, expected):
    assert _classify(url) == expected


def test_classify_spoofed_durable_host_is_not_durable():
    """A host that merely CONTAINS a durable host as a substring must NOT pass
    (cdn.shopify.com.evil.test is an attacker domain)."""
    assert _classify("https://cdn.shopify.com.evil.test/x.png") == UNKNOWN_HOST


def test_classify_spoofed_bvi_host_is_not_ephemeral():
    assert _classify("https://uniparallel.com.evil.test/x.png") == UNKNOWN_HOST


def test_non_durable_and_rehostable_predicates():
    assert is_non_durable(LOCAL_UPLOADS) is True
    assert is_non_durable(EPHEMERAL_BVI) is True
    assert is_non_durable(UNKNOWN_HOST) is True   # not proven durable -> counts
    assert is_non_durable(BLANK) is True
    assert is_non_durable(DURABLE) is False

    assert is_rehostable(LOCAL_UPLOADS) is True
    assert is_rehostable(EPHEMERAL_BVI) is True
    assert is_rehostable(UNKNOWN_HOST) is False   # SSRF-skipped, not fetched
    assert is_rehostable(BLANK) is False
    assert is_rehostable(DURABLE) is False


# ---------------------------------------------------------------------------
# 2. host allowlists
# ---------------------------------------------------------------------------

def test_build_durable_hosts_includes_shopify_and_s3_base():
    hosts = build_durable_hosts(s3_public_base="https://cdn.r2.example/img")
    assert "cdn.shopify.com" in hosts
    assert "cdn.r2.example" in hosts


def test_build_durable_hosts_no_s3_base():
    hosts = build_durable_hosts(s3_public_base=None)
    assert "cdn.shopify.com" in hosts


def test_build_bvi_hosts_includes_defaults_and_base():
    hosts = build_bvi_hosts(public_base="https://shop.bvi-staging.dev")
    assert "uniparallel.com" in hosts
    assert "bettervision.in" in hosts
    assert "shop.bvi-staging.dev" in hosts


def test_split_env_hosts_parses_urls_and_bare_hosts():
    parsed = rh._split_env_hosts("https://a.com/x, b.com , , https://c.com")
    assert parsed == ["a.com", "b.com", "c.com"]


def test_env_extra_durable_host_is_honored():
    hosts = build_durable_hosts(
        s3_public_base=None, extra=rh._split_env_hosts("my-cdn.net")
    )
    assert "my-cdn.net" in hosts
    assert classify_url(
        "https://my-cdn.net/x.png", durable_hosts=hosts, bvi_hosts=BVI_HOSTS
    ) == DURABLE


# ---------------------------------------------------------------------------
# 3. resolve_fetch_url -- SSRF-safe
# ---------------------------------------------------------------------------

def test_resolve_uploads_joins_bvi_base():
    url = resolve_fetch_url(
        "/uploads/a.png", LOCAL_UPLOADS,
        bvi_public_base="https://uniparallel.com", bvi_hosts=BVI_HOSTS,
    )
    assert url == "https://uniparallel.com/uploads/a.png"


def test_resolve_uploads_adds_scheme_to_bare_base():
    url = resolve_fetch_url(
        "uploads/a.png", LOCAL_UPLOADS,
        bvi_public_base="uniparallel.com", bvi_hosts=BVI_HOSTS,
    )
    assert url == "https://uniparallel.com/uploads/a.png"


def test_resolve_uploads_without_base_is_none():
    url = resolve_fetch_url(
        "/uploads/a.png", LOCAL_UPLOADS, bvi_public_base=None, bvi_hosts=BVI_HOSTS
    )
    assert url is None


def test_resolve_ephemeral_passes_through_when_on_allowlist():
    url = resolve_fetch_url(
        "https://uniparallel.com/uploads/x.png", EPHEMERAL_BVI,
        bvi_public_base="https://uniparallel.com", bvi_hosts=BVI_HOSTS,
    )
    assert url == "https://uniparallel.com/uploads/x.png"


def test_resolve_ephemeral_off_allowlist_is_none():
    # Defensive: an EPHEMERAL_BVI code but the host is NOT actually on the list.
    url = resolve_fetch_url(
        "https://evil.test/x.png", EPHEMERAL_BVI,
        bvi_public_base="https://uniparallel.com", bvi_hosts=BVI_HOSTS,
    )
    assert url is None


def test_resolve_unknown_and_durable_are_never_fetched():
    assert resolve_fetch_url(
        "https://evil.test/x", UNKNOWN_HOST,
        bvi_public_base="https://uniparallel.com", bvi_hosts=BVI_HOSTS,
    ) is None
    assert resolve_fetch_url(
        "https://cdn.shopify.com/x", DURABLE,
        bvi_public_base="https://uniparallel.com", bvi_hosts=BVI_HOSTS,
    ) is None


def test_resolve_uploads_base_with_path_resolves_to_host_root():
    """A base with a path component still resolves /uploads against the host
    root (the SSRF gate only cares about the host, which stays on the allowlist)."""
    url = resolve_fetch_url(
        "/uploads/a.png", LOCAL_UPLOADS,
        bvi_public_base="https://uniparallel.com/admin/", bvi_hosts=BVI_HOSTS,
    )
    assert url == "https://uniparallel.com/uploads/a.png"


# ---------------------------------------------------------------------------
# 4. plan_rewrite -- url + original_url
# ---------------------------------------------------------------------------

def test_plan_rewrite_sets_url_and_preserves_original():
    row = {"image_id": "i1", "url": "/uploads/old.png"}
    patch = plan_rewrite(row, "https://cdn.r2/new.png")
    assert patch["url"] == "https://cdn.r2/new.png"
    assert patch["original_url"] == "/uploads/old.png"
    assert patch["locally_modified"] is True
    assert "rehosted_at" in patch


def test_plan_rewrite_does_not_clobber_existing_original():
    row = {"image_id": "i1", "url": "/uploads/old.png", "original_url": "/uploads/FIRST.png"}
    patch = plan_rewrite(row, "https://cdn.r2/new.png")
    # original_url already set on a prior run -> not overwritten.
    assert "original_url" not in patch
    assert patch["url"] == "https://cdn.r2/new.png"


# ---------------------------------------------------------------------------
# 5. parity count + exit-code
# ---------------------------------------------------------------------------

def test_count_non_durable():
    codes = [DURABLE, DURABLE, LOCAL_UPLOADS, EPHEMERAL_BVI, UNKNOWN_HOST, BLANK]
    assert count_non_durable(codes) == 4   # all but the two DURABLE


def test_count_non_durable_all_durable():
    assert count_non_durable([DURABLE, DURABLE]) == 0


def test_decide_exit_code():
    # dry-run never gates
    assert decide_exit_code(dry_run=True, non_durable_remaining=99) == 0
    # commit, clean -> 0 (gate pass)
    assert decide_exit_code(dry_run=False, non_durable_remaining=0) == 0
    # commit, leftovers -> 2 (gate not met)
    assert decide_exit_code(dry_run=False, non_durable_remaining=1) == 2
    assert decide_exit_code(dry_run=False, non_durable_remaining=500) == 2


# ---------------------------------------------------------------------------
# 6. run_rehost -- fake DB + mocked storage/fetch
# ---------------------------------------------------------------------------

class _FakeColl:
    def __init__(self, rows):
        # store a working copy keyed by image_id for update_one
        self._rows = [dict(r) for r in rows]

    def find(self, _query, _projection=None):
        return _FakeCursor([dict(r) for r in self._rows])

    def update_one(self, filt, update):
        image_id = filt.get("image_id")
        patch = update.get("$set", {})
        for r in self._rows:
            if r.get("image_id") == image_id:
                r.update(patch)
        return None

    def insert_one(self, doc):
        return None  # audit_logs sink


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def limit(self, n):
        return _FakeCursor(self._rows[:n])

    def __iter__(self):
        return iter(self._rows)


class _FakeDb:
    def __init__(self, colls):
        self._colls = colls

    def __getitem__(self, name):
        return self._colls.setdefault(name, _FakeColl([]))


class _FakeDurableStorage:
    name = "s3"

    def available(self):
        return True

    def put(self, key, data, content_type="image/png"):
        assert isinstance(data, (bytes, bytearray)) and data, "put got no bytes"
        return f"https://img.bettervision-cdn.com/{key}"


def _run(coll, *, dry_run, storage=None, storage_durable=None, monkeypatch=None):
    db = _FakeDb({"product_images": coll, "audit_logs": _FakeColl([])})
    if storage is None:
        storage = _FakeDurableStorage()
    if storage_durable is None:
        storage_durable = True
    if monkeypatch is not None:
        monkeypatch.setattr(rh, "_fetch_bytes", lambda url, **kw: b"IMGBYTES")
    return run_rehost(
        db,
        dry_run=dry_run,
        durable_hosts=DURABLE_HOSTS,
        bvi_hosts=BVI_HOSTS,
        bvi_public_base="https://uniparallel.com",
        storage=storage,
        storage_durable=storage_durable,
    )


def test_run_dry_run_writes_nothing():
    coll = _FakeColl(
        [
            {"image_id": "a", "product_id": "P1", "url": "/uploads/a.png"},
            {"image_id": "b", "product_id": "P1", "url": "https://cdn.shopify.com/b.png"},
            {"image_id": "c", "product_id": "P2", "url": "https://uniparallel.com/uploads/c.png"},
        ]
    )
    out = _run(coll, dry_run=True)
    assert out["dry_run"] is True
    assert out["candidates"] == 2          # a (/uploads) + c (ephemeral bvi)
    assert out["rehosted"] == 0
    assert out["by_class"][DURABLE] == 1
    assert out["non_durable_remaining"] == 2
    # No DB write happened -- urls unchanged.
    rows = {r["image_id"]: r for r in coll._rows}
    assert rows["a"]["url"] == "/uploads/a.png"
    assert "original_url" not in rows["a"]


def test_run_commit_rewrites_and_passes_gate(monkeypatch):
    coll = _FakeColl(
        [
            {"image_id": "a", "product_id": "P1", "url": "/uploads/a.png"},
            {"image_id": "c", "product_id": "P2", "url": "https://uniparallel.com/uploads/c.png"},
            {"image_id": "b", "product_id": "P1", "url": "https://cdn.shopify.com/b.png"},
        ]
    )
    out = _run(coll, dry_run=False, monkeypatch=monkeypatch)
    assert out["dry_run"] is False
    assert out["rehosted"] == 2
    assert out["failed"] == 0
    assert out["non_durable_remaining"] == 0   # GATE PASS
    rows = {r["image_id"]: r for r in coll._rows}
    assert rows["a"]["url"].startswith("https://img.bettervision-cdn.com/bvi-rehost/P1/a")
    assert rows["a"]["original_url"] == "/uploads/a.png"
    assert rows["a"]["locally_modified"] is True
    # the durable shopify row untouched
    assert rows["b"]["url"] == "https://cdn.shopify.com/b.png"
    # exit code computed off the result -> 0
    assert decide_exit_code(dry_run=False, non_durable_remaining=out["non_durable_remaining"]) == 0


def test_run_commit_idempotent_second_run_is_noop(monkeypatch):
    coll = _FakeColl([{"image_id": "a", "product_id": "P1", "url": "/uploads/a.png"}])
    first = _run(coll, dry_run=False, monkeypatch=monkeypatch)
    assert first["rehosted"] == 1
    # second run: the row is now durable -> nothing to do.
    second = _run(coll, dry_run=False, monkeypatch=monkeypatch)
    assert second["rehosted"] == 0
    assert second["candidates"] == 0
    assert second["non_durable_remaining"] == 0


def test_run_commit_ssrf_skips_unknown_host(monkeypatch):
    """A url on an unknown host is NEVER fetched (SSRF) and keeps the gate red."""
    fetched = []
    monkeypatch.setattr(rh, "_fetch_bytes", lambda url, **kw: (fetched.append(url), b"X")[1])
    coll = _FakeColl(
        [{"image_id": "evil", "product_id": "P9", "url": "https://evil.example.com/x.png"}]
    )
    out = run_rehost(
        _FakeDb({"product_images": coll, "audit_logs": _FakeColl([])}),
        dry_run=False,
        durable_hosts=DURABLE_HOSTS,
        bvi_hosts=BVI_HOSTS,
        bvi_public_base="https://uniparallel.com",
        storage=_FakeDurableStorage(),
        storage_durable=True,
    )
    assert out["skipped_unknown"] == 1
    assert out["rehosted"] == 0
    assert out["non_durable_remaining"] == 1   # GATE NOT MET
    assert fetched == []                        # never fetched the evil host
    # url left untouched
    assert coll._rows[0]["url"] == "https://evil.example.com/x.png"


def test_run_commit_no_durable_storage_fails_each(monkeypatch):
    """storage_durable=False -> nothing rehosted, all candidates stay non-durable."""
    monkeypatch.setattr(rh, "_fetch_bytes", lambda url, **kw: b"X")
    coll = _FakeColl([{"image_id": "a", "product_id": "P1", "url": "/uploads/a.png"}])
    out = run_rehost(
        _FakeDb({"product_images": coll, "audit_logs": _FakeColl([])}),
        dry_run=False,
        durable_hosts=DURABLE_HOSTS,
        bvi_hosts=BVI_HOSTS,
        bvi_public_base="https://uniparallel.com",
        storage=None,
        storage_durable=False,
    )
    assert out["rehosted"] == 0
    assert out["failed"] == 1
    assert out["non_durable_remaining"] == 1


def test_run_commit_local_uploads_without_base_fails(monkeypatch):
    """A /uploads row with NO BVI base url cannot be fetched -> stays non-durable."""
    monkeypatch.setattr(rh, "_fetch_bytes", lambda url, **kw: b"X")
    coll = _FakeColl([{"image_id": "a", "product_id": "P1", "url": "/uploads/a.png"}])
    out = run_rehost(
        _FakeDb({"product_images": coll, "audit_logs": _FakeColl([])}),
        dry_run=False,
        durable_hosts=DURABLE_HOSTS,
        bvi_hosts=BVI_HOSTS,
        bvi_public_base=None,        # <-- no base
        storage=_FakeDurableStorage(),
        storage_durable=True,
    )
    assert out["rehosted"] == 0
    assert out["failed"] == 1
    assert out["non_durable_remaining"] == 1


def test_run_commit_fetch_failure_is_failsoft(monkeypatch):
    """A fetch that raises is recorded as failed; the batch continues + gate red."""
    def _boom(url, **kw):
        raise RuntimeError("HTTP 404")

    monkeypatch.setattr(rh, "_fetch_bytes", _boom)
    coll = _FakeColl(
        [
            {"image_id": "a", "product_id": "P1", "url": "/uploads/a.png"},
            {"image_id": "b", "product_id": "P1", "url": "https://cdn.shopify.com/b.png"},
        ]
    )
    out = run_rehost(
        _FakeDb({"product_images": coll, "audit_logs": _FakeColl([])}),
        dry_run=False,
        durable_hosts=DURABLE_HOSTS,
        bvi_hosts=BVI_HOSTS,
        bvi_public_base="https://uniparallel.com",
        storage=_FakeDurableStorage(),
        storage_durable=True,
    )
    assert out["failed"] == 1
    assert out["rehosted"] == 0
    assert out["non_durable_remaining"] == 1   # the durable shopify row is fine
