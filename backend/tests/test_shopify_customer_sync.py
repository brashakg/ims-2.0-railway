"""
IMS 2.0 - Email-only online contact capture (contact_tier MARKETING / FULL)
===========================================================================
Owner-approved model (2026-07-20): mobile stays the PRIMARY retail identity, but
an EMAIL-ONLY online buyer must no longer be dropped. It is captured as a
customer record flagged contact_tier="MARKETING" (no loyalty / no WhatsApp /
excluded from POS pickers where a tier filter opts in) and AUTO-UPGRADES to
contact_tier="FULL" the moment a phone appears for it.

These tests pin every rule the model requires:

  CREATE
    * an email-only Shopify customer / order buyer creates a MARKETING-tier
      customer (source ONLINE, no mobile, email set) -- no longer skipped.
    * a phone-carrying buyer is UNCHANGED (goes through the canonical
      ensure_customer path; carries NO contact_tier tag -- absent == FULL).

  DEDUPE
    * an email-only re-delivery collapses onto the SAME MARKETING record
      (normalized lowercase email).
    * an email shared with a phone-carrying FULL record (families share emails)
      is NEVER merged into that FULL record -- a fresh MARKETING contact is made.

  AUTO-UPGRADE
    * when a phone later appears for a MARKETING contact (a customers/update
      webhook, or a later order), the record flips to FULL and gains the mobile,
      WITHOUT minting a duplicate.

  DOWNSTREAM
    * the DARK ad-audience export still treats a MARKETING record as a valid
      email audience member (it keys on match_keys, not contact_tier).
    * GET /customers gains an ADDITIVE ?exclude_marketing filter (default false
      keeps every existing list unchanged); when true it drops MARKETING rows.

Pure: an in-memory FakeDB + the real CustomerRepository over fake collections.
No network, no real Mongo.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")
os.environ.setdefault("ENVIRONMENT", "test")


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo emulator (subset the services touch; handles $ne/$or).
# ---------------------------------------------------------------------------


class _DuplicateKeyError(Exception):
    pass


def _match(doc, filter_) -> bool:
    if not filter_:
        return True
    for k, expected in filter_.items():
        if k == "$or":
            if not any(_match(doc, sub) for sub in expected):
                return False
            continue
        if k == "$nor":
            if any(_match(doc, sub) for sub in expected):
                return False
            continue
        if k == "$and":
            if not all(_match(doc, sub) for sub in expected):
                return False
            continue
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$ne":
                    if actual == op_val:
                        return False
                elif op == "$type":
                    if actual is None:
                        return False
                elif op == "$exists":
                    present = k in doc
                    if bool(op_val) != present:
                        return False
                elif op == "$nin":
                    if actual in op_val:
                        return False
                else:
                    return False
        else:
            if actual != expected:
                return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *args, **kwargs):
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter([dict(d) for d in self._docs])


class FakeCollection:
    def __init__(self, name, database):
        self._name = name
        self.database = database
        self.docs: list = []
        self._unique_fields: set = set()

    def create_index(self, keys, **kwargs):
        if kwargs.get("unique") and isinstance(keys, str):
            self._unique_fields.add(keys)
        return None

    def _violates_unique(self, doc) -> bool:
        for f in self._unique_fields:
            val = doc.get(f)
            if val is None:
                continue
            for d in self.docs:
                if d.get(f) == val:
                    return True
        if "_id" in doc:
            for d in self.docs:
                if d.get("_id") == doc.get("_id"):
                    return True
        return False

    def insert_one(self, doc):
        if self._violates_unique(doc):
            raise _DuplicateKeyError("E11000 duplicate key error")
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filter_=None, projection=None):
        for d in self.docs:
            if _match(d, filter_):
                return dict(d)
        return None

    def find(self, filter_=None, projection=None):
        return _Cursor([d for d in self.docs if _match(d, filter_)])

    def count_documents(self, filter_=None):
        return len([d for d in self.docs if _match(d, filter_)])

    def update_one(self, filter_, update, upsert=False):
        for d in self.docs:
            if _match(d, filter_):
                for k, v in (update.get("$set") or {}).items():
                    d[k] = v
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            doc = dict(filter_)
            for k, v in (update.get("$set") or {}).items():
                doc[k] = v
            self.docs.append(doc)
            return type("R", (), {"modified_count": 0, "upserted_id": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections: dict = {}

    def __getitem__(self, name):
        return self.get_collection(name)

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection(name, self)
        return self._collections[name]


@pytest.fixture
def wired(monkeypatch):
    db = FakeDB()
    from database.repositories.customer_repository import CustomerRepository

    customer_repo = CustomerRepository(db.get_collection("customers"))

    class _StoreRepo:
        def find_by_id(self, _sid):
            return {"gstin": "", "state_code": "20"}

        def find_active(self, filter=None):
            return [{"store_id": "BV-ONLINE-01", "state_code": "20"}]

    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(deps, "get_store_repository", lambda: _StoreRepo())

    return {
        "db": db,
        "customer_repo": customer_repo,
        "customers": db.get_collection("customers"),
    }


def _customer_payload(cid=555, phone="", email="ravi@example.com", first="Ravi", last="Kumar"):
    """A Shopify customers/create|update payload (the payload IS the customer)."""
    return {
        "id": cid,
        "first_name": first,
        "last_name": last,
        "email": email,
        "phone": phone,
    }


# ===========================================================================
# CREATE -- email-only -> MARKETING ; phone -> unchanged FULL
# ===========================================================================


def test_email_only_customer_creates_marketing_tier(wired):
    from api.services.shopify_customer_sync import upsert_shopify_customer

    res = upsert_shopify_customer(
        wired["db"], _customer_payload(email="solo@x.com", phone=""),
        topic="customers/create",
    )
    assert res["status"] == "created"
    cust = wired["customers"].find_one({"customer_id": res["customer_id"]})
    assert cust is not None
    assert cust["contact_tier"] == "MARKETING"
    assert cust["email"] == "solo@x.com"
    assert not (cust.get("mobile") or "")   # no phone captured
    assert cust["source"] == "ONLINE"


def test_email_only_order_buyer_creates_marketing_tier(wired):
    """The ORDER-path customer resolver (used by map_shopify_order) also captures
    an email-only buyer as a MARKETING contact instead of dropping it."""
    from api.services.online_order_mapper import _match_or_create_customer

    buyer = {"name": "Web Buyer", "phone": "", "email": "web@x.com", "shopify_customer_id": "42"}
    cid = _match_or_create_customer(wired["db"], buyer, "BV-ONLINE-01")
    assert cid
    cust = wired["customers"].find_one({"customer_id": cid})
    assert cust["contact_tier"] == "MARKETING"
    assert cust["email"] == "web@x.com"
    assert not (cust.get("mobile") or "")


def test_phone_customer_create_is_unchanged_full(wired):
    """A phone-carrying buyer goes through the canonical ensure_customer path and
    is NOT tagged MARKETING (absent tier == FULL) -- byte-identical to before."""
    from api.services.shopify_customer_sync import upsert_shopify_customer

    res = upsert_shopify_customer(
        wired["db"], _customer_payload(phone="+91 98765 43210", email="ravi@x.com"),
        topic="customers/create",
    )
    assert res["status"] == "created"
    cust = wired["customers"].find_one({"customer_id": res["customer_id"]})
    assert cust["mobile"] == "9876543210"          # normalized Indian mobile
    assert cust.get("contact_tier") != "MARKETING"  # untagged == FULL
    assert "contact_tier" not in cust               # phone create left byte-identical


# ===========================================================================
# DEDUPE
# ===========================================================================


def test_email_only_redelivery_dedupes_to_same_record(wired):
    from api.services.shopify_customer_sync import upsert_shopify_customer

    first = upsert_shopify_customer(
        wired["db"], _customer_payload(cid=1, email="dup@x.com", phone=""),
        topic="customers/create",
    )
    before = wired["customers"].count_documents({})
    # A second delivery for the same email (different surface case) -> same record.
    second = upsert_shopify_customer(
        wired["db"], _customer_payload(cid=1, email="DUP@x.com", phone=""),
        topic="customers/update",
    )
    assert second["customer_id"] == first["customer_id"]
    assert wired["customers"].count_documents({}) == before  # no duplicate minted


def test_email_never_merges_into_phone_carrying_full_record(wired):
    """Families share emails: an email-only contact whose email matches a FULL
    (phone-carrying) record must NOT merge into it -- a separate MARKETING record
    is created and the FULL record is untouched."""
    from api.services.shopify_customer_sync import upsert_shopify_customer

    wired["customer_repo"].create(
        {
            "customer_id": "CUST-FULL",
            "name": "Parent",
            "mobile": "9876500000",
            "phone": "9876500000",
            "email": "family@x.com",
        }
    )
    res = upsert_shopify_customer(
        wired["db"], _customer_payload(cid=777, email="family@x.com", phone=""),
        topic="customers/create",
    )
    assert res["status"] == "created"
    assert res["customer_id"] != "CUST-FULL"       # a NEW, separate record
    # The FULL record kept its phone and did not gain the shopify id.
    full = wired["customers"].find_one({"customer_id": "CUST-FULL"})
    assert full["mobile"] == "9876500000"
    assert not full.get("shopify_customer_id")
    # The new one is an email-only MARKETING contact.
    new = wired["customers"].find_one({"customer_id": res["customer_id"]})
    assert new["contact_tier"] == "MARKETING"
    assert new["email"] == "family@x.com"
    assert not (new.get("mobile") or "")


# ===========================================================================
# AUTO-UPGRADE (MARKETING -> FULL the moment a phone appears)
# ===========================================================================


def test_phone_later_upgrades_marketing_to_full_via_customer_webhook(wired):
    from api.services.shopify_customer_sync import upsert_shopify_customer

    # 1) email-only create -> MARKETING.
    first = upsert_shopify_customer(
        wired["db"], _customer_payload(cid=900, email="grow@x.com", phone=""),
        topic="customers/create",
    )
    assert wired["customers"].find_one({"customer_id": first["customer_id"]})["contact_tier"] == "MARKETING"
    before = wired["customers"].count_documents({})

    # 2) the same Shopify customer later adds a phone -> upgrade IN PLACE to FULL.
    second = upsert_shopify_customer(
        wired["db"],
        _customer_payload(cid=900, email="grow@x.com", phone="+91 98765 43210"),
        topic="customers/update",
    )
    assert second["customer_id"] == first["customer_id"]          # same record
    assert wired["customers"].count_documents({}) == before        # no duplicate
    cust = wired["customers"].find_one({"customer_id": first["customer_id"]})
    assert cust["contact_tier"] == "FULL"                          # tier flipped
    assert cust["mobile"] == "9876543210"                         # phone captured


def test_merge_fields_flips_marketing_tier_when_phone_added(wired):
    """The _merge_fields hook itself: adding a phone onto an email-only MARKETING
    record fills the mobile AND flips contact_tier -> FULL."""
    from api.services.shopify_customer_sync import _merge_fields

    wired["customer_repo"].create(
        {
            "customer_id": "MKT-1",
            "name": "Email Only",
            "email": "mkt1@x.com",
            "mobile": "",
            "phone": "",
            "contact_tier": "MARKETING",
        }
    )
    applied = _merge_fields(
        "MKT-1",
        {"name": "Email Only", "phone": "+91 98765 43210", "email": "mkt1@x.com"},
    )
    assert applied.get("contact_tier") == "FULL"
    assert applied.get("mobile") == "9876543210"
    cust = wired["customers"].find_one({"customer_id": "MKT-1"})
    assert cust["contact_tier"] == "FULL"
    assert cust["mobile"] == "9876543210"


def test_phone_later_upgrades_marketing_to_full_via_order(wired):
    """The auto-upgrade also fires when a later ORDER carries a phone for an
    existing email-only MARKETING contact (the order-path resolver)."""
    from api.services.online_order_mapper import _match_or_create_customer

    # email-only contact exists (MARKETING).
    m = _match_or_create_customer(
        wired["db"],
        {"name": "Ord Buyer", "phone": "", "email": "later@x.com", "shopify_customer_id": "5"},
        "BV-ONLINE-01",
    )
    assert wired["customers"].find_one({"customer_id": m})["contact_tier"] == "MARKETING"
    before = wired["customers"].count_documents({})

    # a later order for the same email now carries a phone.
    cid = _match_or_create_customer(
        wired["db"],
        {"name": "Ord Buyer", "phone": "+91 98765 43210", "email": "later@x.com", "shopify_customer_id": "5"},
        "BV-ONLINE-01",
    )
    assert cid == m                                                # upgraded, not duplicated
    assert wired["customers"].count_documents({}) == before
    cust = wired["customers"].find_one({"customer_id": m})
    assert cust["contact_tier"] == "FULL"
    assert cust["mobile"] == "9876543210"


def test_phone_first_dedupe_wins_over_email_upgrade(wired):
    """If a phone-keyed record already exists, a phone+email buyer dedupes to it
    (phone-first) and does NOT wrongly upgrade a different email-only contact."""
    from api.services.online_order_mapper import _match_or_create_customer

    wired["customer_repo"].create(
        {"customer_id": "CUST-PHONE", "name": "Ravi", "mobile": "9876543210", "phone": "9876543210"}
    )
    # An unrelated email-only MARKETING contact that happens to share the email.
    _match_or_create_customer(
        wired["db"],
        {"name": "Web", "phone": "", "email": "shared@x.com", "shopify_customer_id": "9"},
        "BV-ONLINE-01",
    )
    before = wired["customers"].count_documents({})
    cid = _match_or_create_customer(
        wired["db"],
        {"name": "Ravi", "phone": "+91 98765 43210", "email": "shared@x.com", "shopify_customer_id": "9"},
        "BV-ONLINE-01",
    )
    assert cid == "CUST-PHONE"                                     # phone-first match
    assert wired["customers"].count_documents({}) == before        # no new record
    # The email-only MARKETING contact was left untouched (NOT upgraded).
    marketing = wired["customers"].find_one({"email": "shared@x.com", "contact_tier": "MARKETING"})
    assert marketing is not None
    assert not (marketing.get("mobile") or "")


# ===========================================================================
# DOWNSTREAM -- audience export still counts MARKETING contacts (via match_keys)
# ===========================================================================


class _AeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def limit(self, n):
        return _AeCursor(self._docs[:n])

    def sort(self, key, direction=-1):
        return _AeCursor(sorted(self._docs, key=lambda d: d.get(key) or "", reverse=direction == -1))

    def __iter__(self):
        return iter(self._docs)


class _AeCollection:
    def __init__(self, docs):
        self._docs = docs

    def _m(self, doc, query):
        for k, v in query.items():
            if k == "$or":
                if not any(self._m(doc, sub) for sub in v):
                    return False
            elif doc.get(k) != v:
                return False
        return True

    def find(self, query=None, projection=None):
        return _AeCursor([d for d in self._docs if self._m(d, query or {})])

    def find_one(self, query=None, projection=None, sort=None):
        docs = [d for d in self._docs if self._m(d, query or {})]
        if sort:
            key, direction = sort[0]
            docs = sorted(docs, key=lambda d: d.get(key) or "", reverse=direction == -1)
        return docs[0] if docs else None


class _AeDB:
    def __init__(self, collections):
        self._c = {n: _AeCollection(d) for n, d in collections.items()}

    def get_collection(self, name):
        return self._c.setdefault(name, _AeCollection([]))


def test_audience_export_includes_marketing_email_contact(monkeypatch):
    """A MARKETING-tier (email-only) customer that opted in to AD_AUDIENCE is a
    valid email audience member -- the export keys on match_keys, not
    contact_tier, so the tag never excludes it."""
    from api.services import audience_export as ae

    customers = [
        {
            "customer_id": "M1",
            "email": "m1@x.com",
            "contact_tier": "MARKETING",
            "marketing_consent": True,
        }
    ]
    dpdp = [
        {
            "customer_id": "M1",
            "event_type": "GRANTED",
            "purposes": ["AD_AUDIENCE"],
            "created_at": "2026-01-01T00:00:00",
        }
    ]
    db = _AeDB({"customers": customers, "dpdp_consent_ledger": dpdp, "whatsapp_consent_ledger": []})
    monkeypatch.setattr("api.dependencies.get_db", lambda: db)

    res = ae.build_ad_audience_export(db, provider="generic")
    rows = {r.customer_id: r for r in res.audience}
    assert "M1" in rows
    assert rows["M1"].contact_tier == "EMAIL_ONLY"           # derived audience tier
    assert set(rows["M1"].keys) == {"email_sha256"}
    assert res.email_only_count == 1


# ===========================================================================
# DOWNSTREAM -- GET /customers additive ?exclude_marketing filter
# ===========================================================================


def _run(coro):
    return asyncio.run(coro)


def test_list_customers_exclude_marketing_is_additive(wired, monkeypatch):
    import api.routers.customers as customers_router

    monkeypatch.setattr(customers_router, "get_customer_repository", lambda: wired["customer_repo"])

    wired["customer_repo"].create(
        {"customer_id": "F1", "name": "Full One", "mobile": "9800000001"}
    )
    wired["customer_repo"].create(
        {"customer_id": "M1", "name": "Marketing One", "email": "m1@x.com", "contact_tier": "MARKETING"}
    )
    su = {"roles": ["SUPERADMIN"], "active_store_id": None}

    # All params passed explicitly: calling the endpoint fn directly bypasses
    # FastAPI's Query() default resolution.
    common = dict(
        search=None, customer_type=None, channel=None, store_id=None, skip=0, limit=50,
        current_user=su,
    )

    # Default (no filter) -> BOTH visible (existing lists unchanged).
    default = _run(customers_router.list_customers(exclude_marketing=False, **common))
    ids_default = {c["customer_id"] for c in default["customers"]}
    assert {"F1", "M1"} <= ids_default

    # exclude_marketing=True -> only the FULL customer.
    filtered = _run(customers_router.list_customers(exclude_marketing=True, **common))
    ids_filtered = {c["customer_id"] for c in filtered["customers"]}
    assert "F1" in ids_filtered
    assert "M1" not in ids_filtered
