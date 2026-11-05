"""
L1-F23: campaigns + promo templates follow ONE store-scope rule on every door.

  - HQ (SUPERADMIN / ADMIN) reach every shop and the org-wide (no-store) record.
  - Everyone else (AREA_MANAGER, STORE_MANAGER) only their own shops: lists
    show only their shop's records (a ?store_id is validated, never trusted),
    an org-wide or other-shop record is refused on read AND write, a create
    with a missing/blank store lands in their own shop (never org-wide), and
    an update can never move a record into a forbidden shop or org-wide.

Fake in-memory DB from test_campaigns; no send endpoint, no provider.
"""

from __future__ import annotations

import pytest

from tests.test_campaigns import _FakeColl, _FakeDB, _create_payload, _hdr, _mk_client

A, B = "STORE-A", "STORE-B"
SM = ("STORE_MANAGER",)


def _seed():
    camps = _FakeColl(
        [
            {"campaign_id": "C-A", "name": "a", "status": "DRAFT", "store_id": A, "segment_key": "by_store"},
            {"campaign_id": "C-B", "name": "b", "status": "DRAFT", "store_id": B, "segment_key": "by_store"},
            {"campaign_id": "C-ORG", "name": "o", "status": "DRAFT", "store_id": None, "segment_key": "by_store"},
            {"campaign_id": "C-BLANK", "name": "x", "status": "DRAFT", "store_id": "", "segment_key": "by_store"},
        ]
    )
    tpls = _FakeColl(
        [
            {"template_id": "T-A", "name": "a", "type": "BOGO", "active": True, "store_id": A},
            {"template_id": "T-B", "name": "b", "type": "BOGO", "active": True, "store_id": B},
            {"template_id": "T-ORG", "name": "o", "type": "BOGO", "active": True, "store_id": None},
        ]
    )
    return _FakeDB({"campaigns": camps, "promo_templates": tpls, "customers": _FakeColl(),
                    "prescriptions": _FakeColl(), "orders": _FakeColl()})


@pytest.fixture
def client(monkeypatch):
    return _mk_client(_seed(), monkeypatch=monkeypatch)


def _ids(body, key, idk):
    return {d[idk] for d in body[key]}


# ---------------------------------------------------------------- lists

@pytest.mark.parametrize("q", ["", "?store_id=", f"?store_id={A}"])
def test_manager_campaign_list_is_own_shop_only(client, q):
    r = client.get(f"/api/v1/marketing/campaigns{q}", headers=_hdr(SM, A))
    assert r.status_code == 200, r.text
    assert _ids(r.json(), "campaigns", "campaign_id") == {"C-A"}


@pytest.mark.parametrize("q", ["", "?store_id=", f"?store_id={A}"])
def test_manager_template_list_is_own_shop_only(client, q):
    r = client.get(f"/api/v1/marketing/promo-templates{q}", headers=_hdr(SM, A))
    assert r.status_code == 200, r.text
    assert _ids(r.json(), "templates", "template_id") == {"T-A"}


@pytest.mark.parametrize("path", ["campaigns", "promo-templates"])
def test_manager_other_shop_filter_refused(client, path):
    r = client.get(f"/api/v1/marketing/{path}?store_id={B}", headers=_hdr(SM, A))
    assert r.status_code == 403


def test_admin_lists_every_shop(client):
    r = client.get("/api/v1/marketing/campaigns", headers=_hdr(("ADMIN",), A))
    assert _ids(r.json(), "campaigns", "campaign_id") == {"C-A", "C-B", "C-ORG", "C-BLANK"}
    r = client.get("/api/v1/marketing/promo-templates", headers=_hdr(("ADMIN",), A))
    assert _ids(r.json(), "templates", "template_id") == {"T-A", "T-B", "T-ORG"}
    r = client.get(f"/api/v1/marketing/campaigns?store_id={B}", headers=_hdr(("ADMIN",), A))
    assert _ids(r.json(), "campaigns", "campaign_id") == {"C-B"}


# ---------------------------------------------------------------- objects

@pytest.mark.parametrize("cid", ["C-B", "C-ORG", "C-BLANK"])
@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("get", "", None),
        ("put", "", {"name": "hijack"}),
        ("delete", "", None),
        ("post", "/duplicate", None),
        ("post", "/pause", None),
        ("post", "/resume", None),
        ("post", "/schedule", {"kind": "TRIGGERED", "trigger_event": "rx_expiry"}),
        ("get", "/analytics", None),
    ],
)
def test_manager_refused_on_other_or_org_campaign(client, cid, method, suffix, body):
    kw = {"json": body} if body is not None else {}
    r = getattr(client, method)(f"/api/v1/marketing/campaigns/{cid}{suffix}", headers=_hdr(SM, A), **kw)
    assert r.status_code == 403, (cid, method, suffix, r.text)


@pytest.mark.parametrize("tid", ["T-B", "T-ORG"])
@pytest.mark.parametrize("method,body", [("get", None), ("put", {"name": "hijack"}), ("delete", None)])
def test_manager_refused_on_other_or_org_template(client, tid, method, body):
    kw = {"json": body} if body is not None else {}
    r = getattr(client, method)(f"/api/v1/marketing/promo-templates/{tid}", headers=_hdr(SM, A), **kw)
    assert r.status_code == 403, (tid, method, r.text)


def test_manager_same_shop_doors_work(client):
    h = _hdr(SM, A)
    assert client.get("/api/v1/marketing/campaigns/C-A", headers=h).status_code == 200
    assert client.put("/api/v1/marketing/campaigns/C-A", json={"name": "renamed"}, headers=h).status_code == 200
    assert client.get("/api/v1/marketing/promo-templates/T-A", headers=h).status_code == 200
    assert client.put("/api/v1/marketing/promo-templates/T-A", json={"name": "renamed"}, headers=h).status_code == 200


def test_admin_reaches_org_and_other_shop(client):
    h = _hdr(("ADMIN",), A)
    for cid in ("C-B", "C-ORG"):
        assert client.get(f"/api/v1/marketing/campaigns/{cid}", headers=h).status_code == 200
    for tid in ("T-B", "T-ORG"):
        assert client.get(f"/api/v1/marketing/promo-templates/{tid}", headers=h).status_code == 200


# ---------------------------------------------------------------- create

@pytest.mark.parametrize("store", [None, ""])
def test_manager_create_without_store_lands_in_own_shop(client, store):
    body = _create_payload(segment_key="by_store", type="custom", template_id="BIRTHDAY_WISH")
    if store is not None:
        body["store_id"] = store
    r = client.post("/api/v1/marketing/campaigns", json=body, headers=_hdr(SM, A))
    assert r.status_code == 200, r.text
    assert r.json()["campaign"]["store_id"] == A

    tpl = {"name": "bogo", "type": "BOGO", "buy_quantity": 1, "get_quantity": 1}
    if store is not None:
        tpl["store_id"] = store
    r = client.post("/api/v1/marketing/promo-templates", json=tpl, headers=_hdr(SM, A))
    assert r.status_code == 200, r.text
    assert r.json()["template"]["store_id"] == A


def test_admin_create_without_store_stays_org_wide(client):
    body = _create_payload(segment_key="by_store", type="custom", template_id="BIRTHDAY_WISH")
    r = client.post("/api/v1/marketing/campaigns", json=body, headers=_hdr(("ADMIN",), A))
    assert r.json()["campaign"]["store_id"] is None


# ---------------------------------------------------------------- update moves

# A forbidden shop is refused; a blank store resolves exactly as on create (the
# manager's own shop) -- never org-wide. Either way the record stays in reach.
@pytest.mark.parametrize("new_store,status", [(B, 403), ("", 200)])
def test_manager_cannot_move_campaign_out_of_reach(client, new_store, status):
    r = client.put("/api/v1/marketing/campaigns/C-A", json={"store_id": new_store}, headers=_hdr(SM, A))
    assert r.status_code == status, r.text
    assert client.get("/api/v1/marketing/campaigns/C-A", headers=_hdr(SM, A)).json()["store_id"] == A


@pytest.mark.parametrize("new_store,status", [(B, 403), ("", 200)])
def test_manager_cannot_move_template_out_of_reach(client, new_store, status):
    r = client.put("/api/v1/marketing/promo-templates/T-A", json={"store_id": new_store}, headers=_hdr(SM, A))
    assert r.status_code == status, r.text
    assert client.get("/api/v1/marketing/promo-templates/T-A", headers=_hdr(SM, A)).json()["store_id"] == A


# ---------------------------------------------------------------- send (stub provider)

def test_send_audience_scope_with_stub_provider(monkeypatch):
    from tests.test_campaigns import _SendRecorder

    db = _seed()
    db.get_collection("customers").docs.extend(
        [
            {"customer_id": "CA", "name": "A1", "mobile": "9000000011", "home_store_id": A},
            {"customer_id": "CB", "name": "B1", "mobile": "9000000012", "home_store_id": B},
        ]
    )
    rec = _SendRecorder(db)
    client = _mk_client(db, monkeypatch=monkeypatch, recorder=rec)
    h = _hdr(SM, A)
    # An org-wide campaign (whole-chain audience) is HQ-only: refused, nothing sent.
    assert client.post("/api/v1/marketing/campaigns/C-ORG/send", headers=h).status_code == 403
    assert rec.calls == []
    # The manager's own campaign reaches only its own shop's customers.
    for c in db.get_collection("campaigns").docs:
        c.update({"template_id": "BIRTHDAY_WISH", "channels": ["WHATSAPP"]})
    r = client.post("/api/v1/marketing/campaigns/C-A/send", headers=h)
    assert r.status_code == 200, r.text
    assert {c["customer_id"] for c in rec.calls} == {"CA"}


def test_admin_can_move_template_between_shops(client):
    r = client.put("/api/v1/marketing/promo-templates/T-A", json={"store_id": B}, headers=_hdr(("ADMIN",), A))
    assert r.status_code == 200
    assert r.json()["template"]["store_id"] == B
