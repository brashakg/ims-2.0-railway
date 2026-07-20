"""
Marketing Funnel Phase 0 — consent + identity spine + DARK audience export.

Covers:
  * match_keys.py     — SHA-256 hashing / normalisation of phone + email.
  * customers.py      — the new AD_AUDIENCE DPDP purpose (valid, ledgerable, but
                        NEVER a default grant) + its 0-day retention window.
  * audience_export.py — consent-gated ADD/REMOVE bucketing, email-only handling,
                        provider field naming, CSV, and the DARK contract.

Run:
  JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest \
      backend/tests/test_marketing_funnel_p0.py -q
"""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import match_keys as mk  # noqa: E402
from api.services import audience_export as ae  # noqa: E402


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# match_keys — email
# ---------------------------------------------------------------------------


def test_normalize_email_trims_and_lowercases():
    assert mk.normalize_email("  Test@Example.COM ") == "test@example.com"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_normalize_email_blank_is_none(blank):
    assert mk.normalize_email(blank) is None


def test_hash_email_matches_normalised_sha256():
    # Different surface forms of the same email hash identically.
    assert mk.hash_email("Test@Example.com ") == _sha("test@example.com")
    assert mk.hash_email("test@example.com") == mk.hash_email("TEST@EXAMPLE.COM")


def test_hash_email_none_when_absent():
    assert mk.hash_email(None) is None
    assert mk.hash_email("") is None


# ---------------------------------------------------------------------------
# match_keys — phone (E.164 +91)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["9876543210", "09876543210", "+91 98765 43210", "91-9876543210", "(+91)9876543210"],
)
def test_to_e164_india_collapses_surface_forms(raw):
    assert mk.to_e164_india(raw) == "+919876543210"


@pytest.mark.parametrize("bad", [None, "", "12345", "5555555555", "abcd"])
def test_to_e164_india_failsoft_none(bad):
    # Invalid / blank / foreign numbers must fail SOFT (None), never raise.
    assert mk.to_e164_india(bad) is None


def test_hash_phone_matches_e164_sha256():
    assert mk.hash_phone_e164("9876543210") == _sha("+919876543210")
    assert mk.hash_phone_e164("bad-number") is None


# ---------------------------------------------------------------------------
# match_keys — coalesce + build + tier
# ---------------------------------------------------------------------------


def test_coalesce_prefers_mobile_over_phone():
    assert mk.coalesce_mobile({"mobile": "9876543210", "phone": "9000000000"}) == "9876543210"
    assert mk.coalesce_mobile({"phone": "9000000000"}) == "9000000000"
    assert mk.coalesce_mobile({}) is None


def test_build_match_keys_both():
    keys = mk.build_match_keys({"mobile": "9876543210", "email": "a@b.com"})
    assert keys == {"phone_sha256": _sha("+919876543210"), "email_sha256": _sha("a@b.com")}
    assert mk.contact_tier(keys) == "PHONE_AND_EMAIL"


def test_build_match_keys_email_only_is_valid():
    keys = mk.build_match_keys({"email": "solo@b.com"})
    assert set(keys) == {"email_sha256"}
    assert mk.contact_tier(keys) == "EMAIL_ONLY"


def test_build_match_keys_phone_only():
    keys = mk.build_match_keys({"phone": "9876543210"})
    assert set(keys) == {"phone_sha256"}
    assert mk.contact_tier(keys) == "PHONE_ONLY"


def test_build_match_keys_none_when_no_contact():
    keys = mk.build_match_keys({"name": "No Contact"})
    assert keys == {}
    assert mk.contact_tier(keys) == "NONE"


# ---------------------------------------------------------------------------
# customers.py — AD_AUDIENCE purpose
# ---------------------------------------------------------------------------


def test_ad_audience_is_valid_but_not_default():
    from api.routers import customers as c

    assert "AD_AUDIENCE" in c._ALL_PURPOSES
    # Third-party sharing must NOT ride the default "grant all".
    assert "AD_AUDIENCE" not in c._DEFAULT_GRANT_PURPOSES
    assert c._DEFAULT_GRANT_PURPOSES == frozenset(
        {"SERVICE_DELIVERY", "MARKETING", "RX_HISTORY", "ANALYTICS"}
    )
    # Withdrawn immediately -> 0-day retention.
    assert c._PURPOSE_RETENTION_DAYS["AD_AUDIENCE"] == 0


def test_consent_grant_default_excludes_ad_audience():
    from api.routers.customers import ConsentGrantRequest

    default = ConsentGrantRequest()
    assert "AD_AUDIENCE" not in default.purposes
    assert set(default.purposes) == {"SERVICE_DELIVERY", "MARKETING", "RX_HISTORY", "ANALYTICS"}


def test_consent_grant_accepts_explicit_ad_audience():
    from api.routers.customers import ConsentGrantRequest

    req = ConsentGrantRequest(purposes=["AD_AUDIENCE"])
    assert req.purposes == ["AD_AUDIENCE"]


def test_consent_grant_rejects_unknown_purpose():
    from api.routers.customers import ConsentGrantRequest

    with pytest.raises(Exception):
        ConsentGrantRequest(purposes=["NONSENSE"])


# ---------------------------------------------------------------------------
# audience_export — fake Mongo
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def limit(self, n):
        return _FakeCursor(self._docs[:n])

    def sort(self, key, direction=-1):
        return _FakeCursor(
            sorted(self._docs, key=lambda d: d.get(key) or "", reverse=direction == -1)
        )

    def __iter__(self):
        return iter(self._docs)


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs

    def _match(self, doc, query):
        for k, v in query.items():
            if k == "$or":
                if not any(self._match(doc, sub) for sub in v):
                    return False
            elif doc.get(k) != v:
                return False
        return True

    def find(self, query=None, projection=None):
        return _FakeCursor([d for d in self._docs if self._match(d, query or {})])

    def find_one(self, query=None, projection=None, sort=None):
        docs = [d for d in self._docs if self._match(d, query or {})]
        if sort:
            key, direction = sort[0]
            docs = sorted(docs, key=lambda d: d.get(key) or "", reverse=direction == -1)
        return docs[0] if docs else None


class _FakeDB:
    def __init__(self, collections):
        self._c = {name: _FakeCollection(docs) for name, docs in collections.items()}

    def get_collection(self, name):
        return self._c.setdefault(name, _FakeCollection([]))


def _ledger_row(cid, event, purposes, ts):
    return {"customer_id": cid, "event_type": event, "purposes": purposes, "created_at": ts}


@pytest.fixture
def fake_db(monkeypatch):
    """A fake Mongo with five customers exercising every consent bucket."""
    customers = [
        # Opted in to AD_AUDIENCE, both identifiers -> AUDIENCE (ADD).
        {"customer_id": "C1", "mobile": "9876543210", "email": "c1@x.com", "marketing_consent": True},
        # Opted in, EMAIL-ONLY -> AUDIENCE + email_only.
        {"customer_id": "C2", "email": "c2@x.com", "marketing_consent": True},
        # Opted in to AD_AUDIENCE but globally opted out -> SUPPRESSION (REMOVE).
        {"customer_id": "C3", "mobile": "9800000003", "marketing_consent": False},
        # Granted then WITHDREW AD_AUDIENCE, still marketing-consented -> SUPPRESSION.
        {"customer_id": "C4", "mobile": "9800000004", "marketing_consent": True},
        # Never opted in to AD_AUDIENCE -> skipped_no_ad_consent.
        {"customer_id": "C5", "mobile": "9800000005", "marketing_consent": True},
        # No contact identifier at all -> skipped_no_contact.
        {"customer_id": "C6", "name": "Ghost", "marketing_consent": True},
    ]
    dpdp = [
        _ledger_row("C1", "GRANTED", ["MARKETING", "AD_AUDIENCE"], "2026-01-01T00:00:00"),
        _ledger_row("C2", "GRANTED", ["AD_AUDIENCE"], "2026-01-01T00:00:00"),
        _ledger_row("C3", "GRANTED", ["AD_AUDIENCE"], "2026-01-01T00:00:00"),
        _ledger_row("C4", "GRANTED", ["AD_AUDIENCE"], "2026-01-01T00:00:00"),
        _ledger_row("C4", "WITHDRAWN", ["AD_AUDIENCE"], "2026-02-01T00:00:00"),
        _ledger_row("C5", "GRANTED", ["MARKETING"], "2026-01-01T00:00:00"),
    ]
    db = _FakeDB({"customers": customers, "dpdp_consent_ledger": dpdp, "whatsapp_consent_ledger": []})
    # _active_purposes_from_ledger reads the ledger via the global get_db().
    monkeypatch.setattr("api.dependencies.get_db", lambda: db)
    return db


def test_summary_buckets(fake_db):
    r = ae.summarize_ad_audience(fake_db)
    assert r.scanned == 6
    assert r.audience_count == 2  # C1, C2
    assert r.suppression_count == 2  # C3 (opted out), C4 (withdrawn)
    assert r.email_only_count == 1  # C2
    assert r.phone_count == 1  # C1 (audience rows carrying a phone)
    assert r.skipped_no_contact == 1  # C6
    assert r.skipped_no_ad_consent == 1  # C5
    # Summary carries NO hashes.
    assert r.audience == [] and r.suppression == []


def test_export_rows_and_actions(fake_db):
    r = ae.build_ad_audience_export(fake_db, provider="generic")
    ids_add = {row.customer_id: row for row in r.audience}
    ids_rm = {row.customer_id for row in r.suppression}
    assert set(ids_add) == {"C1", "C2"}
    assert ids_rm == {"C3", "C4"}
    assert all(row.action == "ADD" for row in r.audience)
    assert all(row.action == "REMOVE" for row in r.suppression)
    # C1 carries both hashes, correctly computed.
    assert ids_add["C1"].keys == {
        "phone_sha256": _sha("+919876543210"),
        "email_sha256": _sha("c1@x.com"),
    }
    # C2 email-only.
    assert ids_add["C2"].contact_tier == "EMAIL_ONLY"
    assert set(ids_add["C2"].keys) == {"email_sha256"}


def test_provider_field_naming(fake_db):
    r = ae.build_ad_audience_export(fake_db, provider="meta")
    c1 = next(ae.format_row(row, "meta") for row in r.audience if row.customer_id == "C1")
    assert "EMAIL_SHA256" in c1 and "PHONE_SHA256" in c1
    google_c1 = next(
        ae.format_row(row, "google") for row in r.audience if row.customer_id == "C1"
    )
    assert "Email" in google_c1 and "Phone" in google_c1


def test_to_csv_has_header_and_all_rows(fake_db):
    r = ae.build_ad_audience_export(fake_db, provider="google")
    csv = ae.to_csv(r)
    lines = csv.strip().split("\n")
    assert lines[0] == "action,contact_tier,Phone,Email"
    # 1 header + 2 audience + 2 suppression.
    assert len(lines) == 5
    assert any(line.startswith("ADD,") for line in lines[1:])
    assert any(line.startswith("REMOVE,") for line in lines[1:])


def test_none_db_is_failsoft():
    r = ae.build_ad_audience_export(None)
    assert r.scanned == 0
    assert r.audience == [] and r.suppression == []
    assert "unavailable" in r.note.lower()


def test_unknown_provider_failsoft():
    # No DB scan happens; a clear note is returned instead of a crash.
    res = ae.build_ad_audience_export(_FakeDB({"customers": []}), provider="tiktok")
    assert res.audience == []
    assert "provider" in res.note.lower()
