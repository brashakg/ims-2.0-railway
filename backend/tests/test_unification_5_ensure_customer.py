"""
IMS 2.0 -- Unification step-5: ONE canonical ensure_customer service
=====================================================================
Locks the canonical ``api.services.customer_service.ensure_customer`` and the two
doors that delegate to it:

  * DEDUP across doors -- the SAME person entered at two different doors (any phone
    surface form: +91 / 0-leading / spaced) resolves to ONE customer_id.
  * CONCURRENCY -- a racing duplicate-mobile insert never double-creates (the second
    create raises a DuplicateKey-like error; the service re-finds the winner).
  * SOURCE TAG persisted on a new record (POS|CLINIC|WALKOUT|ONLINE).
  * LENIENT create -- a mobile alone is a valid customer (owner: STRICT is products
    only, not customers).
  * STRICT-on-PROVIDED -- a bad email / GSTIN supplied alongside the mobile is still
    rejected (reuses the customers.py create validators); a future DOB too.
  * DELEGATION -- the walkout door (source=WALKOUT) and the online door
    (source=ONLINE) both resolve through the canonical service.

CI-robust: every DB accessor is monkeypatched + docs are seeded in-memory; no Mongo,
no HTTP, no whole-JSON substring assertions.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import customer_service as cs  # noqa: E402

# ---------------------------------------------------------------------------
# In-memory customer repo -- only the methods the service + doors touch. Mirrors
# the real CustomerRepository.find_by_mobile ($or phone/mobile) so normalized-key
# dedup is exercised exactly as in production.
# ---------------------------------------------------------------------------


class _FakeRepo:
    def __init__(self):
        self.docs = {}  # customer_id -> doc
        self.create_calls = 0

    def find_by_mobile(self, mobile):
        for d in self.docs.values():
            if d.get("mobile") == mobile or d.get("phone") == mobile:
                return dict(d)
        return None

    def find_by_email(self, email):
        for d in self.docs.values():
            if email and d.get("email") == email:
                return dict(d)
        return None

    def create(self, doc):
        self.create_calls += 1
        self.docs[doc["customer_id"]] = dict(doc)
        return doc

    def update(self, customer_id, data):
        if customer_id in self.docs:
            self.docs[customer_id].update(data)
            return True
        return False


def _patch_repo(monkeypatch, repo=None):
    """Point BOTH accessors the service/doors can reach at the SAME fake repo so the
    service resolves it whether a door passed a connected db handle or not."""
    repo = repo or _FakeRepo()
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_customer_repository", lambda: repo)
    return repo


# ---------------------------------------------------------------------------
# DEDUP -- same person, different doors / phone forms -> ONE record.
# ---------------------------------------------------------------------------


def test_same_person_two_doors_one_customer(monkeypatch):
    repo = _patch_repo(monkeypatch)
    # Door A (POS): bare form.
    cid_a, created_a = cs.ensure_customer(
        None, mobile="9876543210", name="Ravi", store_id="S1", source="POS"
    )
    # Door B (CLINIC): SAME person, +91-prefixed surface form.
    cid_b, created_b = cs.ensure_customer(
        None, mobile="+91 98765 43210", name="Ravi K", store_id="S2", source="CLINIC"
    )
    assert cid_a and cid_a == cid_b
    assert created_a is True and created_b is False
    assert repo.create_calls == 1  # the dedup short-circuited the second create


def test_plus91_and_zero_leading_dedup_to_same(monkeypatch):
    repo = _patch_repo(monkeypatch)
    cid1, _ = cs.ensure_customer(None, mobile="+919876543210", source="ONLINE")
    cid2, c2 = cs.ensure_customer(None, mobile="098765 43210", source="WALKOUT")
    cid3, c3 = cs.ensure_customer(None, mobile="98765-43210", source="POS")
    assert cid1 == cid2 == cid3
    assert c2 is False and c3 is False
    assert repo.create_calls == 1
    # All three resolved to the canonical bare 10-digit key.
    assert repo.docs[cid1]["mobile"] == "9876543210"


# ---------------------------------------------------------------------------
# CONCURRENCY -- a racing duplicate-mobile insert never double-creates.
# ---------------------------------------------------------------------------


def test_concurrent_create_single_record(monkeypatch):
    """Two creates race: find_by_mobile sees nothing for both, then the SECOND
    insert hits the unique-mobile guard (DuplicateKey). The service must re-find and
    return the winner -- never a second record."""
    repo = _FakeRepo()
    _patch_repo(monkeypatch, repo)

    # Writer #1 commits normally.
    cid1, c1 = cs.ensure_customer(None, mobile="9000000055", name="First", source="POS")
    assert c1 is True

    # Simulate a writer that raced: its pre-create find returned None (stale), but the
    # unique-mobile index now rejects the duplicate insert.
    real_find = repo.find_by_mobile
    state = {"first_find": True}

    def _stale_then_real(mobile):
        if state["first_find"]:
            state["first_find"] = False
            return None  # the racing reader saw an empty DB
        return real_find(mobile)

    def _dup_create(doc):
        raise RuntimeError("E11000 duplicate key error: mobile")

    monkeypatch.setattr(repo, "find_by_mobile", _stale_then_real)
    monkeypatch.setattr(repo, "create", _dup_create)

    cid2, c2 = cs.ensure_customer(
        None, mobile="9000000055", name="Second", source="ONLINE"
    )
    assert cid2 == cid1  # the racing loser resolves to the winner
    assert c2 is False
    assert len(repo.docs) == 1  # still exactly one record


# ---------------------------------------------------------------------------
# SOURCE TAG persisted.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["POS", "CLINIC", "WALKOUT", "ONLINE"])
def test_source_tag_persisted(monkeypatch, source):
    repo = _patch_repo(monkeypatch)
    cid, created = cs.ensure_customer(
        None, mobile="9111100000", name="Tag", source=source
    )
    assert created is True
    assert repo.docs[cid]["source"] == source
    # ONLINE is the only non-store channel.
    expected_channel = "ONLINE" if source == "ONLINE" else "STORE"
    assert repo.docs[cid]["channel"] == expected_channel


def test_unknown_source_rejected(monkeypatch):
    _patch_repo(monkeypatch)
    with pytest.raises(ValueError):
        cs.ensure_customer(None, mobile="9111100001", source="MAGIC")


# ---------------------------------------------------------------------------
# LENIENT create (mobile-only ok) + canonical skeleton shape.
# ---------------------------------------------------------------------------


def test_lenient_mobile_only_create(monkeypatch):
    repo = _patch_repo(monkeypatch)
    cid, created = cs.ensure_customer(None, mobile="9222200000", source="WALKOUT")
    assert created is True and cid
    doc = repo.docs[cid]
    # Canonical minimal skeleton -- no consent gate, lenient.
    assert doc["mobile"] == "9222200000"
    assert doc["phone"] == "9222200000"
    assert doc["customer_type"] == "B2C"
    assert doc["is_active"] is True
    assert doc["loyalty_points"] == 0
    assert doc["patients"] == []
    # No consent flag added (owner: missing == consented).
    assert "marketing_consent" not in doc
    assert "data_consent" not in doc


def test_store_id_written_to_every_list_key(monkeypatch):
    repo = _patch_repo(monkeypatch)
    cid, _ = cs.ensure_customer(
        None, mobile="9222200001", store_id="BV-ABC-01", source="POS"
    )
    doc = repo.docs[cid]
    # Visible in EVERY store-scoped customer list regardless of which key it filters.
    assert doc["home_store_id"] == "BV-ABC-01"
    assert doc["preferred_store_id"] == "BV-ABC-01"
    assert doc["primary_store_id"] == "BV-ABC-01"
    assert doc["store_ids"] == ["BV-ABC-01"]


def test_blank_mobile_returns_none_no_create(monkeypatch):
    repo = _patch_repo(monkeypatch)
    assert cs.ensure_customer(None, mobile="", source="POS") == (None, False)
    assert cs.ensure_customer(None, mobile=None, source="POS") == (None, False)
    assert repo.create_calls == 0


def test_unparseable_mobile_is_lenient_none_not_raise(monkeypatch):
    repo = _patch_repo(monkeypatch)
    # A junk number is NOT a hard error -- the door just gets a null link.
    assert cs.ensure_customer(None, mobile="not-a-number", source="WALKOUT") == (
        None,
        False,
    )
    assert repo.create_calls == 0


def test_repo_unavailable_returns_none(monkeypatch):
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_customer_repository", lambda: None)
    assert cs.ensure_customer(None, mobile="9333300000", source="POS") == (None, False)


# ---------------------------------------------------------------------------
# STRICT on PROVIDED extras -- bad email / GSTIN / future DOB still rejected.
# ---------------------------------------------------------------------------


def test_bad_email_rejected(monkeypatch):
    repo = _patch_repo(monkeypatch)
    with pytest.raises(ValueError):
        cs.ensure_customer(
            None, mobile="9444400000", source="POS", email="not-an-email"
        )
    assert repo.create_calls == 0  # nothing persisted on a validation failure


def test_bad_gstin_rejected(monkeypatch):
    repo = _patch_repo(monkeypatch)
    with pytest.raises(ValueError):
        cs.ensure_customer(None, mobile="9444400001", source="POS", gstin="BADGSTIN")
    assert repo.create_calls == 0


def test_future_dob_rejected(monkeypatch):
    _patch_repo(monkeypatch)
    future = date.today() + timedelta(days=10)
    with pytest.raises(ValueError):
        cs.ensure_customer(None, mobile="9444400002", source="POS", dob=future)


def test_valid_extras_persisted(monkeypatch):
    repo = _patch_repo(monkeypatch)
    cid, _ = cs.ensure_customer(
        None,
        mobile="9444400003",
        source="POS",
        email="ok@example.com",
        gstin="27AAPFU0939F1ZV",
    )
    assert repo.docs[cid]["email"] == "ok@example.com"
    assert repo.docs[cid]["gstin"] == "27AAPFU0939F1ZV"


# ---------------------------------------------------------------------------
# DELEGATION -- the two find-or-create doors resolve through the service.
# ---------------------------------------------------------------------------


def test_walkout_door_resolves_through_service(monkeypatch):
    """walkouts._ensure_customer is a thin wrapper -> a new record carries the
    canonical WALKOUT source + uuid id, and a second walkout with the same number
    dedups to the same id."""
    from api.routers import walkouts as wk

    repo = _FakeRepo()
    monkeypatch.setattr(wk, "get_db", lambda: None)  # force service's accessor path
    monkeypatch.setattr(wk, "get_audit_repository", lambda: None)
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_customer_repository", lambda: repo)

    user = {"user_id": "u1"}
    cid1 = wk._ensure_customer("+91 98765 11111", "Walk One", "BV-PUN-01", user)
    cid2 = wk._ensure_customer("098765 11111", "Walk One Again", "BV-PUN-01", user)
    assert cid1 and cid1 == cid2
    assert repo.docs[cid1]["source"] == "WALKOUT"
    # uuid id, not the legacy cust-+hex8 form.
    assert not cid1.startswith("cust-")
    assert repo.create_calls == 1


def test_walkout_door_no_mobile_returns_none(monkeypatch):
    from api.routers import walkouts as wk

    repo = _FakeRepo()
    monkeypatch.setattr(wk, "get_db", lambda: None)
    monkeypatch.setattr(wk, "get_audit_repository", lambda: None)
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_customer_repository", lambda: repo)

    assert (
        wk._ensure_customer(None, "No Mobile", "BV-PUN-01", {"user_id": "u1"}) is None
    )
    assert repo.create_calls == 0


def test_online_door_resolves_through_service(monkeypatch):
    """online_order_mapper._match_or_create_customer delegates its phone-keyed path
    to the service: a NEW online buyer gets channel=ONLINE + shopify_customer_id
    stamped, and dedups against an in-store customer with the same number."""
    from api.services import online_order_mapper as om

    repo = _FakeRepo()
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_customer_repository", lambda: repo)

    # An in-store WALKOUT customer already exists with this number.
    existing_id, _ = cs.ensure_customer(
        None,
        mobile="9876522222",
        name="InStore",
        store_id="BV-PUN-01",
        source="WALKOUT",
    )
    assert existing_id

    # The SAME person now buys online (different surface form).
    buyer = {
        "name": "Online Same",
        "phone": "+91 98765 22222",
        "email": "same@x.com",
        "shopify_customer_id": "555",
    }
    resolved = om._match_or_create_customer(object(), buyer, "BV-ONLINE-01")
    assert resolved == existing_id  # dedup across in-store + online
    assert repo.create_calls == 1  # online did NOT mint a second record


def test_online_door_new_buyer_gets_shopify_linkage(monkeypatch):
    from api.services import online_order_mapper as om

    repo = _FakeRepo()
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_customer_repository", lambda: repo)

    buyer = {
        "name": "Fresh Online",
        "phone": "+91 98765 33333",
        "email": "fresh@x.com",
        "shopify_customer_id": "999",
    }
    cid = om._match_or_create_customer(object(), buyer, "BV-ONLINE-01")
    assert cid
    doc = repo.docs[cid]
    assert doc["channel"] == "ONLINE"
    assert doc["mobile"] == "9876533333"
    # Shopify linkage stamped on the canonical record (drives the dashboard count).
    assert doc["shopify_customer_id"] == "999"
    assert doc["email"] == "fresh@x.com"
