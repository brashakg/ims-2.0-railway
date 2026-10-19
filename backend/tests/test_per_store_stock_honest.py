"""
Per-store website stock says only what IMS knows (rebuild of parked #1172).
=========================================================================
Four rules, each driven through the real writer with a MOCKED Shopify
transcript (no network, no Mongo):

  1. ONE RULE FOR EVERY PRESS (owner, round 4): a listing is put or kept
     ACTIVE on the website only when Shopify's own answer confirms, for EVERY
     size, tracked=true + the listing's policy (`tracking_confirmed`).
     Silence, tracked=false or another policy confirms nothing, whichever
     call it answered. A size added to a LIVE listing is created tracked and
     "stop selling at 0" IN THE SAME productVariantsBulkCreate call; not
     confirmed on a live listing -> taken off the website (Draft); not on the
     website -> it stays Draft (never ACTIVE before the answer) and the press
     says so. Sizes are named by their IMS SKU, never a Shopify id.
  2. "live and SOLD OUT" (the writer's `sold_out` stamp) only when every
     listed SKU was accepted as 0 at EVERY mapped shop and nothing is
     unknown: a refused write, an unreadable shelf, an unmapped holder or a
     stray size each clears it.
  3. A read that died is UNKNOWN and named (STOCK_ONHAND_UNKNOWN), never a
     green no-op or "0 of 0": the sweep's catalogue read, its size-row read,
     the stray-size reads, the sale's online-status read.
  4. A shop whose write failed keeps its last-sent number, so removing its
     location later still zeroes what Shopify shows there.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_per_store_stock_honest.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from strict_fakes import StrictCollection  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services import online_stock_writeback as wb  # noqa: E402
from test_shopify_online_stock import (  # noqa: E402,F401 -- _reset is the autouse fixture
    INV_GID,
    INV_TWO,
    LOC_A,
    LOC_B,
    LOC_C,
    VARIANT_GID,
    _ThrottledTracking,
    _Spy,
    _baseline,
    _break_shop,
    _catalog_row,
    _db,
    _listed,
    _live,
    _loc,
    _locations,
    _ok_body,
    _product_body,
    _reset,
    _responses,
    _run,
    _set_error,
)

# The push's other mechanics, for a brand that IS for the website (owner D6;
# the brand rule has its own test in test_add_product_owner_rulings.py).
pytestmark = pytest.mark.usefixtures("brand_is_for_the_website")

MINTED = "gid://shopify/ProductVariant/77"
INV_L = "gid://shopify/InventoryItem/777"


class _DeadFind(StrictCollection):
    """A collection whose find / find_one raise for the filters `hit` picks;
    every other read answers."""

    def __init__(self, base, hit, message="cursor died"):
        super().__init__(base.name, base.docs)
        self._hit = hit
        self._message = message

    def find(self, filter=None, *a, **k):
        if self._hit(filter or {}):
            raise RuntimeError(self._message)
        return super().find(filter, *a, **k)

    def find_one(self, filter=None, *a, **k):
        if self._hit(filter or {}):
            raise RuntimeError(self._message)
        return super().find_one(filter, *a, **k)


def _kill(db, name, hit, message="cursor died"):
    db._collections[name] = _DeadFind(db.get_collection(name), hit, message)


def _cat(db, pid="cat-1"):
    return db.get_collection("catalog_products").find_one({"id": pid})


# ---------------------------------------------------------------------------
# 1. a new size on a live listing
# ---------------------------------------------------------------------------


def _live_listing_gets_size_l(status="PUBLISHED"):
    """cat-1 is LIVE (PUBLISHED, tracked) with sizes M and S on Shopify; IMS
    has just added size L (no gid yet), one unit of it at BV-A. `status`
    DRAFT: the same listing on Shopify but never published."""
    db = _db(a=2, b=1, c=0)
    db.seed("products", [{"product_id": "spine-S", "sku": "SP-1-S"}, {"product_id": "spine-L", "sku": "SP-1-L"}])
    db.get_collection("stock_units").insert_one(
        {"stock_id": "L1", "product_id": "spine-L", "store_id": "BV-A", "status": "AVAILABLE"}
    )
    sent = {"quantities": {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}, "SP-1-S": {"BV-A": 0, "BV-B": 0, "BV-C": 0}},
            "tracked": True, "policy": "DENY"}
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True, status=status,
                                              locally_modified=True, online_stock=sent)])
    variants = [
        {"sku": "SP-1", "parent_product_id": "cat-1", "price": 1500, "option_size": "M",
         "shopify_variant_id": VARIANT_GID, "shopify_inventory_item_id": INV_GID},
        {"sku": "SP-1-S", "parent_product_id": "cat-1", "price": 1500, "option_size": "S",
         "shopify_variant_id": "gid://shopify/ProductVariant/6", "shopify_inventory_item_id": INV_TWO},
        {"sku": "SP-1-L", "parent_product_id": "cat-1", "price": 1500, "option_size": "L"},
    ]
    db.seed("catalog_variants", [dict(v) for v in variants])
    upd = _product_body("productUpdate")
    upd["data"]["productUpdate"]["product"]["variants"]["nodes"] = [
        {"id": VARIANT_GID, "selectedOptions": [{"name": "Size", "value": "M"}], "inventoryItem": {"id": INV_GID}},
        {"id": "gid://shopify/ProductVariant/6", "selectedOptions": [{"name": "Size", "value": "S"}],
         "inventoryItem": {"id": INV_TWO}},
    ]
    return db, variants, upd


def _created(*, tracked=True, policy="DENY"):
    """Shopify's productVariantsBulkCreate answer for size L. None leaves the
    field out of the answer (nothing confirms it)."""
    inv = {"id": INV_L}
    if tracked is not None:
        inv["tracked"] = tracked
    node = {"id": MINTED, "selectedOptions": [{"name": "Size", "value": "L"}], "inventoryItem": inv}
    if policy is not None:
        node["inventoryPolicy"] = policy
    return _ok_body("productVariantsBulkCreate", productVariants=[node])


def _drafts(spy):
    """The take-down writes: productUpdate {id, status: DRAFT} and nothing else."""
    return [
        i for i, c in enumerate(spy.calls)
        if "productUpdate(" in c["query"] and set((c["variables"] or {}).get("input") or {}) == {"id", "status"}
        and c["variables"]["input"]["status"] == "DRAFT"
    ]


def _index(spy, marker):
    return [i for i, c in enumerate(spy.calls) if marker in c["query"]]


def test_1_a_new_size_on_a_live_listing_is_created_tracked_and_stop_at_zero_in_one_call(monkeypatch):
    """The create call itself carries tracked=true + DENY, Shopify's answer
    confirms both, and the stock pass's re-send answer confirms every size:
    the listing stays on the website, green. Send the create row without
    `tracked` / `inventoryPolicy` -> the first half fails; treat a confirmed
    size as unconfirmed -> a Draft -> fails."""
    db, variants, upd = _live_listing_gets_size_l()
    spy = _Spy(_responses(**{"productUpdate(": upd, "productVariantsBulkCreate": _created()}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    (create,) = spy.calls_for("productVariantsBulkCreate")
    (row,) = create["variables"]["variants"]
    assert row["inventoryItem"] == {"sku": "SP-1-L", "tracked": True}, row
    assert row["inventoryPolicy"] == "DENY", row
    sel = shopify_push._VARIANTS_BULK_CREATE
    assert "inventoryPolicy" in sel and "tracked" in sel, "the answer must carry what it confirms"
    assert _drafts(spy) == [], "confirmed by Shopify -- the listing stays on the website"
    assert res.ok is True and res.code is None, res
    assert _cat(db)["ecom"]["status"] == "PUBLISHED"
    assert _baseline(db)["tracked"] is True

@pytest.mark.parametrize(
    "answer",
    [_created(tracked=None, policy=None), _created(tracked=False), _created(policy="CONTINUE")],
    ids=["answer-silent", "untracked", "keeps-selling-at-0"],
)
def test_1_an_unconfirmed_new_size_on_a_live_listing_takes_the_listing_off_the_website(monkeypatch, answer):
    """Shopify created size L but its answer does not confirm tracked + stop
    selling at 0: L may sell without limit on a listing that is live. The
    listing goes to Draft right after the create (before any stock write or
    publish), IMS records Draft, the publish is skipped and the press says so,
    naming the size. Drop the take-down -> no Draft call, ok True -> fails."""
    db, variants, upd = _live_listing_gets_size_l()
    spy = _Spy(_responses(**{"productUpdate(": upd, "productVariantsBulkCreate": answer}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    drafts = _drafts(spy)
    assert len(drafts) == 1, [c["query"][:40] for c in spy.calls]
    assert _index(spy, "productVariantsBulkCreate")[0] < drafts[0] < _index(spy, "inventorySetQuantities")[0]
    assert spy.calls_for("publishablePublish") == [], "a listing taken down is not published again"
    ecom = _cat(db)["ecom"]
    assert ecom["status"] == "DRAFT" and ecom.get("taken_down_at"), ecom
    assert res.ok is False and res.reason == "publish_withheld", res
    assert res.code == shopify_push.STOCK_TRACKING_FAILED
    assert "SP-1-L" in res.error and "taken OFF the website (Draft)" in res.error, res.error


class _DraftRefused(_Spy):
    """Shopify refuses the take-down's {id, status: DRAFT} productUpdate."""

    async def __call__(self, db, query, variables):  # noqa: ARG002
        inp = (variables or {}).get("input") or {}
        if "productUpdate(" in query and set(inp) == {"id", "status"}:
            self.calls.append({"query": query, "variables": variables})
            return {"data": {"productUpdate": {"product": None, "userErrors": [
                {"field": ["status"], "message": "Throttled"}]}}}
        return await super().__call__(db, query, variables)


class _DraftAndTrackingRefused(_DraftRefused, _ThrottledTracking):
    """The take-down refused AND the stock pass's tracking re-send refused."""


def _tracking_sent(spy):
    """Every variant gid a tracking + policy re-send (productVariantsBulkUpdate
    rows carrying inventoryPolicy) went out for."""
    return {
        r["id"] for c in spy.calls_for("productVariantsBulkUpdate")
        for r in (c["variables"] or {}).get("variants") or [] if "inventoryPolicy" in r
    }


def test_1_a_refused_take_down_says_the_listing_is_still_live(monkeypatch):
    """The take-down itself refused and the tracking re-send refused too: the
    listing is STILL on the website with a size that may sell without limit.
    Never "withheld / not made visible"; the press fails and tells the owner
    to set it to Draft in Shopify admin. IMS keeps PUBLISHED (that is what
    the website shows)."""
    db, variants, upd = _live_listing_gets_size_l()
    spy = _DraftAndTrackingRefused(
        _responses(**{"productUpdate(": upd, "productVariantsBulkCreate": _created(tracked=None)}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    assert len(_drafts(spy)) == 1
    assert res.ok is False and res.reason != "publish_withheld", res
    assert "STILL LIVE" in res.error and "Shopify admin" in res.error and "SP-1-L" in res.error, res.error
    assert _cat(db)["ecom"]["status"] == "PUBLISHED"
    assert spy.calls_for("publishablePublish") == []


class _DraftRefusedTrackingEchoes(_DraftRefused):
    """The take-down refused; the tracking re-send (rows carrying
    inventoryPolicy) is ACCEPTED -- no userErrors -- and its answer echoes
    `echo(gid)` for each row sent (None leaves that row out of the answer)."""

    def __init__(self, responses, echo):
        super().__init__(responses)
        self._echo = echo

    async def __call__(self, db, query, variables):  # noqa: ARG002
        rows = (variables or {}).get("variants") or []
        if "productVariantsBulkUpdate" in query and any("inventoryPolicy" in r for r in rows):
            self.calls.append({"query": query, "variables": variables})
            nodes = [n for n in (self._echo(r["id"]) for r in rows) if n]
            return _ok_body("productVariantsBulkUpdate", productVariants=nodes)
        return await super().__call__(db, query, variables)


def _echo(gid, *, tracked=True, policy="DENY"):
    return {"id": gid, "inventoryPolicy": policy, "inventoryItem": {"id": "inv-" + gid, "tracked": tracked}}


def test_1_a_refused_take_down_whose_tracking_re_send_shopify_confirmed_is_the_normal_press(monkeypatch):
    """The take-down refused, but the same press's stock pass re-sent tracked
    + DENY to every size, and Shopify's ANSWER echoes L back tracked + DENY:
    the create's own rule now confirms it, the listing is live and tracked,
    so the press is the normal live press, never 'set it to Draft in Shopify
    admin'. Drop the 'confirmed after all' step -> STILL LIVE -> fails."""
    db, variants, upd = _live_listing_gets_size_l()
    spy = _DraftRefusedTrackingEchoes(
        _responses(**{"productUpdate(": upd, "productVariantsBulkCreate": _created(tracked=None)}), _echo)
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    assert len(_drafts(spy)) == 1
    assert MINTED in _tracking_sent(spy) and res.stock["errors"] == []
    assert _baseline(db)["tracked"] is True
    assert res.ok is True and res.code is None, res
    assert "STILL LIVE" not in (res.error or "") and "Shopify admin" not in (res.error or ""), res.error
    assert _cat(db)["ecom"]["status"] == "PUBLISHED"


@pytest.mark.parametrize(
    "echo",
    [
        lambda gid: None,  # accepted, the size left out of the answer
        lambda gid: _echo(gid, tracked=False, policy="CONTINUE") if gid == MINTED else _echo(gid),
    ],
    ids=["answer-silent", "echoes-untracked-and-continue"],
)
def test_1_a_refused_take_down_whose_re_send_answer_confirms_nothing_is_still_live(monkeypatch, echo):
    """Round-3 finding: the take-down refused and the tracking re-send came
    back with no userErrors -- but its answer does not confirm L (silent, or
    Shopify itself says tracked=false / CONTINUE). That is the same fact the
    create's answer-silent case counts as UNCONFIRMED, so it gets the same
    verdict: STILL LIVE, set it to Draft in Shopify admin, never published,
    never ok, and the last-sent record says untracked. Count 'no userErrors'
    as confirmed (the round-2 code) -> publishablePublish + ok=True -> fails;
    record an accepted re-send as tracked -> the sweep never re-sends -> fails."""
    db, variants, upd = _live_listing_gets_size_l()
    spy = _DraftRefusedTrackingEchoes(
        _responses(**{"productUpdate(": upd, "productVariantsBulkCreate": _created(tracked=False, policy="CONTINUE")}),
        echo,
    )
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    assert len(_drafts(spy)) == 1 and MINTED in _tracking_sent(spy)
    assert spy.calls_for("publishablePublish") == []
    assert res.ok is False and res.reason != "publish_withheld", res
    assert res.code == shopify_push.STOCK_TRACKING_FAILED
    assert "STILL LIVE" in res.error and "Shopify admin" in res.error and "SP-1-L" in res.error, res.error
    assert _cat(db)["ecom"]["status"] == "PUBLISHED"
    # Shopify did not confirm L: the last-sent record says untracked, so the
    # next sweep re-sends tracking (it re-sends only on tracked falsy).
    assert _baseline(db)["tracked"] is False


def test_1_a_staged_published_listing_gaining_a_size_is_not_live_and_never_taken_down(monkeypatch):
    """Round-3 finding (the staged axis of the ONE listing reader). IMS holds
    the listing PUBLISHED, but its baseline says tracking was never set and
    no quantity was ever accepted -- the staged-PUBLISHED draft, which
    `listing_visible` counts as NOT on the website (this is its first
    publish). A new size whose create answer is silent must therefore send
    NO Draft and write no taken_down_at: there is nothing on the website to
    take off. Swap `listing_visible` for a bare `status == PUBLISHED` check
    -> a Draft + 'taken OFF the website' about a listing never on it -> fails."""
    db, variants, upd = _live_listing_gets_size_l()
    db.get_collection("catalog_products").update_one(
        {"id": "cat-1"}, {"$set": {"ecom.online_stock": {"tracked": False}}}
    )
    spy = _Spy(_responses(**{"productUpdate(": upd,
                             "productVariantsBulkCreate": _created(tracked=None, policy=None)}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    assert _drafts(spy) == [], "a staged listing is not on the website -- nothing to take down"
    assert not _cat(db)["ecom"].get("taken_down_at")
    assert "taken OFF" not in (res.error or ""), res.error
    assert res.ok is True, res


class _CredsGoSoft(_Spy):
    """The creds resolve True until the size is created, then False (an OAuth
    re-mint failing after the token cache expired): the gate reads DARK, and
    every later call dies the way the transport dies with no creds."""

    def __init__(self, responses):
        super().__init__(responses)
        self.creds = True

    async def __call__(self, db, query, variables):  # noqa: ARG002
        if not self.creds:
            self.calls.append({"query": query, "variables": variables})
            raise ValueError("shopify creds missing at GraphQL call time")
        out = await super().__call__(db, query, variables)
        if "productVariantsBulkCreate" in query:
            self.creds = False
        return out


def test_1_a_take_down_that_ran_dark_mid_press_is_still_live_never_taken_off(monkeypatch):
    """Probe G. The creds go soft between the size's create and the
    take-down: push_product_delist answers a SIMULATED ok with nothing sent.
    No Draft went out, IMS keeps PUBLISHED with no taken_down_at, the listing
    is on the website with an unconfirmed size -- so the press says STILL
    LIVE, never 'taken OFF the website'. Accept a non-LIVE take-down's ok ->
    'taken OFF' + publish_withheld -> fails."""
    db, variants, upd = _live_listing_gets_size_l()
    spy = _CredsGoSoft(_responses(**{"productUpdate(": upd,
                                     "productVariantsBulkCreate": _created(tracked=None, policy=None)}))
    _live(monkeypatch, spy)
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": spy.creds)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    assert _drafts(spy) == [] and spy.calls_for("publishablePublish") == []
    ecom = _cat(db)["ecom"]
    assert ecom["status"] == "PUBLISHED" and not ecom.get("taken_down_at"), ecom
    assert res.ok is False and res.reason != "publish_withheld", res
    assert "STILL LIVE" in res.error and "taken OFF" not in res.error and "creds" in res.error, res.error


def test_1_a_draft_listing_with_an_unconfirmed_size_is_never_taken_down_and_every_new_size_is_tracked(monkeypatch):
    """Probe H. A first publish of a listing that is on Shopify as a DRAFT
    (never on the website). Shopify creates a size whose answer matches no
    IMS row (Size=Large) and says tracked=false. Not live, so nothing to take
    down: no Draft write, no taken_down_at, no 'taken OFF' line. The stock
    pass re-sends tracked + DENY to EVERY size this press created -- the
    unmatched one included -- before the publish. Count every gid as live
    (drop `listing_visible`) -> a Draft + 'taken OFF' -> fails; drop the
    unconfirmed sizes from the re-send -> /77 untracked on sale -> fails."""
    db, variants, upd = _live_listing_gets_size_l(status="DRAFT")
    node = {"id": MINTED, "selectedOptions": [{"name": "Size", "value": "Large"}],
            "inventoryItem": {"id": INV_L, "tracked": False}, "inventoryPolicy": "DENY"}
    spy = _Spy(_responses(**{"productUpdate(": upd,
                             "productVariantsBulkCreate": _ok_body("productVariantsBulkCreate", productVariants=[node])}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    assert _drafts(spy) == [], "a listing that is not on the website is never 'taken down'"
    assert not _cat(db)["ecom"].get("taken_down_at")
    assert "taken OFF" not in (res.error or ""), res.error
    sent = _tracking_sent(spy)
    assert {VARIANT_GID, "gid://shopify/ProductVariant/6", MINTED} <= sent, sent
    (pub,) = _index(spy, "publishablePublish")
    # The tracking re-send lands before the publish. (Since G2 a repair
    # press also re-sends today's PRICE to the sizes already on the listing
    # -- imsVariantPricesUpdate, after the publish like every non-seeding
    # press's price step -- so the marker is the tracking mutation's own.)
    assert max(_index(spy, "imsVariantInventoryUpdate")) < pub
    assert res.ok is True, res


def _tracking_echoes(echo):
    """A Spy body for productVariantsBulkUpdate: the tracking re-send answered
    with `echo(gid)` per row (None leaves the row out), seeding answered ok."""
    def body(query, variables):
        if "imsVariantInventoryUpdate" not in query:
            return _ok_body("productVariantsBulkUpdate", productVariants=[])
        rows = variables.get("variants") or []
        return _ok_body("productVariantsBulkUpdate", productVariants=[n for n in (echo(r["id"]) for r in rows) if n])
    return body


def _l_untracked(gid):
    return _echo(gid, tracked=False, policy="CONTINUE") if gid == MINTED else _echo(gid)


def _taken_down_with_size_l(monkeypatch):
    """Press 1: the live listing gains L, Shopify's create answer AND the
    tracking re-send's answer both leave L untracked (L was never confirmed),
    the listing is taken down (Draft, taken_down_at). Returns the db and the
    size rows as IMS now holds them (L carries its gid)."""
    db, variants, upd = _live_listing_gets_size_l()
    _live(monkeypatch, _Spy(_responses(**{"productUpdate(": upd,
                                           "productVariantsBulkCreate": _created(tracked=False),
                                           "productVariantsBulkUpdate": _tracking_echoes(_l_untracked)})))
    first = _run(shopify_push.push_product(db, _cat(db), variants))
    ecom = _cat(db)["ecom"]
    assert first.ok is False and ecom["status"] == "DRAFT" and ecom.get("taken_down_at"), (first, ecom)
    rows = list(db.get_collection("catalog_variants").find({"parent_product_id": "cat-1"}))
    assert {r["sku"]: r.get("shopify_variant_id") for r in rows}["SP-1-L"] == MINTED
    upd2 = _product_body("productUpdate")
    upd2["data"]["productUpdate"]["product"]["variants"]["nodes"] = upd["data"]["productUpdate"]["product"]["variants"]["nodes"] + [
        {"id": MINTED, "selectedOptions": [{"name": "Size", "value": "L"}], "inventoryItem": {"id": INV_L}}]
    return db, rows, upd2


@pytest.mark.parametrize(
    "spy_for",
    [
        lambda r: _Spy({**r, "productVariantsBulkUpdate": _tracking_echoes(_l_untracked)}),
        _ThrottledTracking,
    ],
    ids=["re-send-echoes-L-untracked-and-continue", "re-send-throttled"],
)
def test_1_a_re_press_after_a_take_down_stays_draft_until_shopify_confirms_every_size(monkeypatch, spy_for):
    """Round-3 finding (oversell panel P1/P2). After the take-down the owner
    presses Send to website again. The listing is NOT on the website, so its
    first write goes out Draft -- ACTIVE used to go out at call 0, before the
    tracking re-send was even answered, putting it back on sale. Shopify's
    re-send answer then confirms nothing (L echoed untracked + CONTINUE, or
    the call THROTTLED over an L Shopify never confirmed -- a throttle keeps
    only a CONFIRMED size's state): the listing stays Draft on Shopify AND in IMS, no
    publish, the press is not green and says it stays off the website.
    Send the payload's ACTIVE in the first write -> fails; count the re-send
    as confirmed -> publish + ACTIVE -> fails."""
    db, rows, upd2 = _taken_down_with_size_l(monkeypatch)
    spy = spy_for(_responses(**{"productUpdate(": upd2}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), rows))
    updates = spy.calls_for("productUpdate(")
    assert updates[0]["variables"]["input"]["status"] == "DRAFT", updates[0]["variables"]
    assert [c for c in updates if c["variables"]["input"].get("status") == "ACTIVE"] == [], "never back on sale"
    assert MINTED in _tracking_sent(spy) and spy.calls_for("publishablePublish") == []
    assert res.ok is False and res.reason == "publish_withheld" and res.code == shopify_push.STOCK_TRACKING_FAILED, res
    assert "stays OFF the website (Draft)" in res.error, res.error
    ecom = _cat(db)["ecom"]
    assert ecom["status"] == "DRAFT" and ecom.get("taken_down_at"), ecom
    assert _baseline(db)["tracked"] is False


def test_1_a_re_press_after_a_take_down_that_shopify_confirms_goes_back_on_the_website(monkeypatch):
    """The control: the same re-press, Shopify's re-send answer confirming
    every size tracked + DENY. Draft first, the re-send, the publish, THEN
    ACTIVE -- and IMS PUBLISHED with the take-down marker cleared. Skip the
    held-back ACTIVE -> the listing stays Draft on Shopify while IMS says
    PUBLISHED -> fails."""
    db, rows, upd2 = _taken_down_with_size_l(monkeypatch)
    spy = _Spy(_responses(**{"productUpdate(": upd2}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), rows))
    assert res.ok is True and res.code is None, res
    updates = [i for i, c in enumerate(spy.calls) if "productUpdate(" in c["query"]]
    assert spy.calls[updates[0]]["variables"]["input"]["status"] == "DRAFT"
    (pub,) = _index(spy, "publishablePublish")
    assert max(_index(spy, "imsVariantInventoryUpdate")) < pub < updates[-1]
    assert spy.calls[updates[-1]]["variables"]["input"] == {"id": "gid://shopify/Product/111", "status": "ACTIVE"}
    ecom = _cat(db)["ecom"]
    assert ecom["status"] == "PUBLISHED" and not ecom.get("taken_down_at"), ecom
    assert _baseline(db)["tracked"] is True


def _active_sent(spy):
    return [c for c in spy.calls_for("productUpdate(") if c["variables"]["input"].get("status") == "ACTIVE"]


def _confirmed_live_sizes(monkeypatch):
    db, rows, upd = _live_listing_gets_size_l()
    spy = _Spy(_responses(**{"productUpdate(": upd, "productVariantsBulkCreate": _created()}))
    _live(monkeypatch, spy)
    assert _run(shopify_push.push_product(db, _cat(db), rows)).ok
    rows = list(db.get_collection("catalog_variants").find({"parent_product_id": "cat-1"}))
    upd["data"]["productUpdate"]["product"]["variants"]["nodes"].append(
        {"id": MINTED, "selectedOptions": [{"name": "Size", "value": "L"}], "inventoryItem": {"id": INV_L}}
    )
    return db, rows, upd


def test_1_confirmed_sizes_survive_a_throttle_and_are_retried(monkeypatch):
    db, rows, upd = _confirmed_live_sizes(monkeypatch)
    assert _cat(db)["ecom"]["confirmed_sizes"][MINTED] == "DENY"
    spy = _ThrottledTracking(_responses(**{"productUpdate(": upd}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), rows))
    assert res.ok and res.stock["tracking_confirmed"], res
    assert not res.stock["ok"] and not res.stock["sold_out"]
    assert _drafts(spy) == [] and _cat(db)["ecom"]["status"] == "PUBLISHED"
    assert _baseline(db)["tracked"] is False, "the next stock pass must retry the unanswered tracking write"
    assert "stays on the website" in res.stock["error"]


def test_1_a_negative_size_answer_clears_only_that_confirmation(monkeypatch):
    db, rows, upd = _confirmed_live_sizes(monkeypatch)
    spy = _Spy(_responses(**{"productUpdate(": upd, "productVariantsBulkUpdate": _tracking_echoes(_l_untracked)}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), rows))
    record = _cat(db)["ecom"]["confirmed_sizes"]
    assert MINTED not in record and record[VARIANT_GID] == "DENY"
    assert not res.ok and len(_drafts(spy)) == 1
    retry = _ThrottledTracking(_responses(**{"productUpdate(": upd}))
    _live(monkeypatch, retry)
    res = _run(shopify_push.push_product(db, _cat(db), rows))
    assert not res.ok and not res.stock["tracking_confirmed"]
    assert _active_sent(retry) == [] and _cat(db)["ecom"]["status"] == "DRAFT"


def test_1_late_take_down_that_runs_simulated_never_claims_the_listing_is_off(monkeypatch):
    db, rows, upd = _confirmed_live_sizes(monkeypatch)
    spy = _Spy(_responses(**{"productUpdate(": upd, "productVariantsBulkUpdate": _tracking_echoes(_l_untracked)}))
    _live(monkeypatch, spy)

    async def simulated_delist(*_args, **_kwargs):
        return shopify_push.PushResult(mode=shopify_push.MODE_SIMULATED, entity="product", action="delist",
                                       ok=True, reason="creds unavailable")

    monkeypatch.setattr(shopify_push, "push_product_delist", simulated_delist)
    res = _run(shopify_push.push_product(db, _cat(db), rows))
    assert not res.ok and res.reason != "publish_withheld", res
    assert "STILL LIVE" in res.error and "taken OFF" not in res.error
    assert _cat(db)["ecom"]["status"] == "PUBLISHED" and not _cat(db)["ecom"].get("taken_down_at")
    assert spy.calls_for("productVariantsBulkCreate") == [], "this pins the late take-down, with no new size"


def test_1_a_deleted_shopify_size_is_cleared_and_recreated_on_the_next_press(monkeypatch):
    db, rows, upd = _confirmed_live_sizes(monkeypatch)

    def missing_size(query, variables):
        if "imsVariantInventoryUpdate" not in query:
            return _ok_body("productVariantsBulkUpdate", productVariants=[])
        missing = next(i for i, row in enumerate(variables["variants"]) if row["id"] == MINTED)
        return {"data": {"productVariantsBulkUpdate": {"productVariants": [], "userErrors": [
            {"field": ["variants", str(missing), "id"], "message": "Product variant does not exist"}
        ]}}}

    spy = _Spy(_responses(**{"productUpdate(": upd, "productVariantsBulkUpdate": missing_size}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), rows))
    stored = db.get_collection("catalog_variants").find_one({"sku": "SP-1-L"})
    assert not stored.get("shopify_variant_id") and not stored.get("shopify_inventory_item_id")
    assert "SP-1-L" in res.stock["error"] and "re-created on the next" in res.stock["error"]
    assert _drafts(spy) == [], "deleted sizes cannot oversell; remembered remaining sizes stay live"
    upd["data"]["productUpdate"]["product"]["variants"]["nodes"] = [
        n for n in upd["data"]["productUpdate"]["product"]["variants"]["nodes"] if n["id"] != MINTED
    ]
    retry = _Spy(_responses(**{"productUpdate(": upd, "productVariantsBulkCreate": _created()}))
    _live(monkeypatch, retry)
    rows = list(db.get_collection("catalog_variants").find({"parent_product_id": "cat-1"}))
    res = _run(shopify_push.push_product(db, _cat(db), rows))
    assert res.ok, res
    (created,) = retry.calls_for("productVariantsBulkCreate")
    assert created["variables"]["variants"][0]["inventoryItem"] == {"sku": "SP-1-L", "tracked": True}
    assert db.get_collection("catalog_variants").find_one({"sku": "SP-1-L"})["shopify_variant_id"] == MINTED


_THROTTLED = {"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]}


def test_1_a_re_press_after_a_take_down_whose_publish_is_refused_never_sends_active(monkeypatch):
    """Panel finding (mutant P). The same confirmed re-press, but Shopify
    refuses publishablePublish (THROTTLED). The held-back ACTIVE must never
    go out: the listing keeps its Online Store publication, so ACTIVE alone
    puts it back on sale while the press says withheld and IMS says DRAFT.
    Send the ACTIVE whatever the publish answered -> fails."""
    db, rows, upd2 = _taken_down_with_size_l(monkeypatch)
    spy = _Spy(_responses(**{"productUpdate(": upd2, "publishablePublish": _THROTTLED}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), rows))
    assert len(spy.calls_for("publishablePublish")) == 1
    assert _active_sent(spy) == [], "never back on sale on a refused publish"
    assert res.ok is False and res.reason == "publish_withheld", res
    ecom = _cat(db)["ecom"]
    assert ecom["status"] == "DRAFT" and ecom.get("taken_down_at"), ecom


@pytest.mark.parametrize(
    "refuse",
    [
        lambda: {"data": {"productUpdate": {"product": None, "userErrors": [
            {"field": ["status"], "message": "cannot activate"}]}}},
        lambda: _THROTTLED,
    ],
    ids=["active-user-error", "active-throttled"],
)
def test_1_a_re_press_after_a_take_down_whose_active_is_refused_stays_draft(monkeypatch, refuse):
    """Panel finding (`_set_active`). The confirmed re-press, the publish
    succeeds, then Shopify refuses the held-back productUpdate ACTIVE: the
    listing is still Draft on Shopify, so IMS must keep DRAFT and the
    take-down marker (listing_visible stays honest, the row stays queued) and
    the press is withheld, never ok. Count a refused ACTIVE as published ->
    IMS writes PUBLISHED over a Draft listing -> fails."""
    db, rows, upd2 = _taken_down_with_size_l(monkeypatch)

    def update(query, variables):  # noqa: ARG001
        return refuse() if variables["input"] == {"id": "gid://shopify/Product/111", "status": "ACTIVE"} else upd2

    spy = _Spy(_responses(**{"productUpdate(": update}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), rows))
    assert len(spy.calls_for("publishablePublish")) == 1 and len(_active_sent(spy)) == 1
    assert res.ok is False and res.reason == "publish_withheld", res
    assert "setting the listing Active failed" in res.error and "OFF the website (Draft)" in res.error, res.error
    ecom = _cat(db)["ecom"]
    assert ecom["status"] == "DRAFT" and ecom.get("taken_down_at"), ecom
    assert ecom.get("locally_modified") is True, "stays queued for the retry"


def test_1_no_variant_gid_known_is_never_confirmed_tracking(monkeypatch):
    """Panel finding (mutant L). A listing whose gid IMS holds but no size
    gid at all -- none stored, none in Shopify's answer, seeding minting
    none. No tracking re-send is ever made, so nothing is confirmed: no
    publish, the tracking code, recorded untracked. Count 'no size known' as
    confirmed -> the listing is published with tracking never sent -> fails."""
    db = _db(a=2, b=1, c=0)
    row = _catalog_row("cat-1", "SP-1", gid=True, locally_modified=True)
    row["ecom"].update(shopify_variant_id=None, shopify_inventory_item_id=None)
    db.seed("catalog_products", [row])
    upd = _product_body("productUpdate")
    upd["data"]["productUpdate"]["product"]["variants"]["nodes"] = []
    spy = _Spy(_responses(**{"productUpdate(": upd}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), []))
    assert _tracking_sent(spy) == set() and spy.calls_for("publishablePublish") == []
    assert res.stock["tracking_confirmed"] is False, res.stock
    assert "no variant gid known -- tracking not set" in res.stock["errors"], res.stock
    assert res.ok is False and res.code == shopify_push.STOCK_TRACKING_FAILED, res
    assert _cat(db)["ecom"]["status"] == "DRAFT"
    assert (_baseline(db) or {}).get("tracked") is not True


def test_2_still_live_then_press_again_on_the_same_answer_is_still_live(monkeypatch):
    """Round-3 finding (honest-screens probe A2). Press 1: L created with an
    answer saying untracked + CONTINUE, the take-down refused, the re-send
    echoing L untracked -> STILL LIVE, recorded untracked. Press 2 (the
    scheduled run re-presses it -- no taken_down_at): L now has its gid, and
    Shopify gives the identical answer. Same facts, same verdict: STILL LIVE,
    a take-down tried, never ok, never SOLD OUT, no publish, still recorded
    untracked. Check only newly minted sizes -> press 2 green -> fails."""
    db, variants, upd = _live_listing_gets_size_l()
    first = _DraftRefusedTrackingEchoes(
        _responses(**{"productUpdate(": upd, "productVariantsBulkCreate": _created(tracked=False, policy="CONTINUE")}),
        _l_untracked,
    )
    _live(monkeypatch, first)
    res1 = _run(shopify_push.push_product(db, _cat(db), variants))
    rows = list(db.get_collection("catalog_variants").find({"parent_product_id": "cat-1"}))
    upd2 = _product_body("productUpdate")
    upd2["data"]["productUpdate"]["product"]["variants"]["nodes"] = upd["data"]["productUpdate"]["product"]["variants"]["nodes"] + [
        {"id": MINTED, "selectedOptions": [{"name": "Size", "value": "L"}], "inventoryItem": {"id": INV_L}}]
    second = _DraftRefusedTrackingEchoes(_responses(**{"productUpdate(": upd2}), _l_untracked)
    _live(monkeypatch, second)
    res2 = _run(shopify_push.push_product(db, _cat(db), rows))
    for res, spy in ((res1, first), (res2, second)):
        assert len(_drafts(spy)) == 1 and spy.calls_for("publishablePublish") == []
        assert res.ok is False and res.reason != "publish_withheld", res
        assert res.code == shopify_push.STOCK_TRACKING_FAILED and res.stock["sold_out"] is False
        assert "STILL LIVE" in res.error and "SP-1-L" in res.error, res.error
    assert spy.calls_for("productVariantsBulkCreate") == [], "press 2 created nothing"
    assert _cat(db)["ecom"]["status"] == "PUBLISHED"
    assert _baseline(db)["tracked"] is False


def test_1_a_size_shopify_labels_differently_is_named_a_new_size_never_its_id(monkeypatch):
    """Round-3 finding (LOW). Shopify's create answer labels IMS size SP-1-L
    as Size=Large, so it matches no IMS row by option values, and says
    tracked=false. The press text names it 'a new size' -- never
    gid://shopify/ProductVariant/77, an id the owner cannot act on. Fall back
    to the gid -> fails."""
    db, variants, upd = _live_listing_gets_size_l()
    node = {"id": MINTED, "selectedOptions": [{"name": "Size", "value": "Large"}],
            "inventoryItem": {"id": INV_L, "tracked": False}, "inventoryPolicy": "DENY"}
    spy = _Spy(_responses(**{"productUpdate(": upd,
                             "productVariantsBulkCreate": _ok_body("productVariantsBulkCreate", productVariants=[node])}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    assert len(_drafts(spy)) == 1
    assert "a new size" in res.error and "gid://" not in res.error, res.error


def test_1_a_shopify_size_ims_has_no_row_for_is_named_a_new_size_never_its_id(monkeypatch):
    """The same rule for a size the re-send leaves unconfirmed that IMS holds
    no row for (Shopify lists /99 on the live listing; IMS never linked it):
    the take-down names it 'a new size', never its gid. Fall back to the gid
    in the press text -> fails."""
    hand = "gid://shopify/ProductVariant/99"
    db = _db(a=2, b=1, c=0)
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True, status="PUBLISHED", locally_modified=True)])
    upd = _product_body("productUpdate")
    upd["data"]["productUpdate"]["product"]["variants"]["nodes"].append(
        {"id": hand, "selectedOptions": [], "inventoryItem": {"id": "gid://shopify/InventoryItem/990"}})
    spy = _Spy(_responses(**{"productUpdate(": upd,
                             "productVariantsBulkUpdate": _tracking_echoes(lambda g: None if g == hand else _echo(g))}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), []))
    assert hand in _tracking_sent(spy) and len(_drafts(spy)) == 1
    assert "on a new size" in res.error and "gid://" not in res.error, res.error


def test_1_a_live_listing_recorded_untracked_gaining_an_unconfirmed_size_is_taken_down(monkeypatch):
    """Round-3 finding (test pin, M1). IMS holds the listing PUBLISHED with
    accepted quantities -- it IS on the website -- but its last pass recorded
    tracking as not set (what an unconfirmed size leaves behind). It gains L,
    whose create answer says tracked=false. The take-down asks the ONE
    listing reader (`listing_visible`), so the listing comes off the website
    at once. Ask 'visible AND recorded tracked' instead -> no take-down, the
    re-send's answer puts it back on sale green -> fails."""
    db, variants, upd = _live_listing_gets_size_l()
    db.get_collection("catalog_products").update_one({"id": "cat-1"}, {"$set": {"ecom.online_stock.tracked": False}})
    spy = _Spy(_responses(**{"productUpdate(": upd, "productVariantsBulkCreate": _created(tracked=False)}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    drafts = _drafts(spy)
    assert len(drafts) == 1 and drafts[0] < _index(spy, "inventorySetQuantities")[0], [c["query"][:40] for c in spy.calls]
    assert res.ok is False and "taken OFF the website (Draft)" in res.error and "SP-1-L" in res.error, res
    assert _cat(db)["ecom"]["status"] == "DRAFT"


def test_1_an_allow_oversell_listing_gaining_a_size_confirms_against_its_own_policy(monkeypatch):
    """Round-3 finding (test pin, M2 / M3). A made-to-order listing
    (ecom.allow_oversell -> policy CONTINUE) gains L; Shopify answers L
    tracked=true + CONTINUE. The create row asks for the listing's OWN policy
    and the answer is confirmed against it, so the listing stays live and
    green. Hardcode DENY on the create row -> the row assert fails; confirm
    against a literal DENY -> a Draft + 'did not confirm' -> fails."""
    db, variants, upd = _live_listing_gets_size_l()
    db.get_collection("catalog_products").update_one({"id": "cat-1"}, {"$set": {"ecom.allow_oversell": True}})
    spy = _Spy(_responses(**{"productUpdate(": upd, "productVariantsBulkCreate": _created(policy="CONTINUE")}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    (create,) = spy.calls_for("productVariantsBulkCreate")
    assert create["variables"]["variants"][0]["inventoryPolicy"] == "CONTINUE"
    assert _drafts(spy) == [], [c["query"][:40] for c in spy.calls]
    assert res.ok is True and res.code is None, res
    assert _cat(db)["ecom"]["status"] == "PUBLISHED" and _baseline(db)["tracked"] is True


# ---------------------------------------------------------------------------
# 2. SOLD OUT only when every mapped shop was written 0 and nothing is unknown
# ---------------------------------------------------------------------------


class _RefuseAt(_Spy):
    """inventorySetQuantities refused for any call carrying a row at `loc`."""

    def __init__(self, loc, responses):
        super().__init__(responses)
        self._loc = loc

    async def __call__(self, db, query, variables):  # noqa: ARG002
        if "inventorySetQuantities" in query:
            rows = variables["input"]["quantities"]
            if any(r["locationId"] == self._loc for r in rows):
                self.calls.append({"query": query, "variables": variables})
                return _set_error("INVALID_LOCATION", "location is not active")
        return await super().__call__(db, query, variables)


def _sold_out(db, spy, monkeypatch):
    _live(monkeypatch, spy)
    return _run(shopify_push.push_skus_stock(db, ["SP-1"], source="sale"))


def test_2_sold_out_when_every_mapped_shop_was_written_zero(monkeypatch):
    out = _sold_out(_listed(_db(a=0, b=0, c=0)), _Spy(_responses()), monkeypatch)
    assert out["ok"] is True and out["set"] == 3 and out["sold_out"] is True, out


def test_2_a_refused_shop_is_not_sold_out(monkeypatch):
    """BV-B's write refused: Shopify still shows BV-B's old number. A and C
    accepted 0, so 'every accepted number is 0' was true -- and false about
    the website. Stamp from the accepted rows only -> True -> fails."""
    out = _sold_out(_listed(_db(a=0, b=0, c=0)), _RefuseAt(LOC_B, _responses()), monkeypatch)
    assert out["set"] == 2 and out["code"] == shopify_push.STOCK_WRITE_FAILED, out
    assert out["sold_out"] is False


def test_2_an_unreadable_shelf_is_not_sold_out(monkeypatch):
    db = _listed(_db(a=0, b=0, c=0))
    _break_shop(db, "BV-B")
    out = _sold_out(db, _Spy(_responses()), monkeypatch)
    assert out["unknown_stores"] == ["BV-B"] and out["set"] == 2, out
    assert out["sold_out"] is False


def test_2_an_unmapped_holder_is_not_sold_out(monkeypatch):
    """BV-D has no location and holds the unit: the mapped shops at 0 are
    not the whole truth."""
    out = _sold_out(_listed(_db(a=0, b=0, c=0, d=1)), _Spy(_responses()), monkeypatch)
    assert [h["store_id"] for h in out["unmapped_stores"]] == ["BV-D"] and out["set"] == 3, out
    assert out["sold_out"] is False


def test_2_a_stray_size_is_not_sold_out(monkeypatch):
    """The baseline still shows SP-1-X (a size row deleted off the parent)
    at 2 on Shopify; IMS no longer writes it, so the listing is selling it."""
    sent = {"quantities": {"SP-1": {"BV-A": 1, "BV-B": 0, "BV-C": 0}, "SP-1-X": {"BV-A": 2}},
            "tracked": True, "policy": "DENY"}
    out = _sold_out(_listed(_db(a=0, b=0, c=0), online_stock=sent), _Spy(_responses()), monkeypatch)
    assert out["stray_skus"] == ["SP-1-X"] and out["set"] == 3, out
    assert out["sold_out"] is False


@pytest.mark.parametrize(
    "spy_for",
    [
        _ThrottledTracking,
        lambda r: _Spy({**r, "productVariantsBulkUpdate": _ok_body("productVariantsBulkUpdate", productVariants=[])}),
        lambda r: _Spy({**r, "productVariantsBulkUpdate": _ok_body(
            "productVariantsBulkUpdate", productVariants=[_echo(VARIANT_GID, tracked=False, policy="CONTINUE")])}),
    ],
    ids=["re-send-refused", "re-send-silent", "re-send-echoes-untracked"],
)
def test_2_unconfirmed_tracking_on_a_live_listing_is_not_sold_out_and_comes_off(monkeypatch, spy_for):
    """Round-3 finding (honest-screens probe A). A live listing whose last
    pass recorded tracking as NOT set, every shelf 0; the re-send is refused,
    or accepted with an answer that confirms nothing. The size has its gid
    (nothing new), and that is still unconfirmed: never ok, never SOLD OUT,
    recorded untracked, and the listing comes off the website. Check only the
    sizes this press minted -> ok + SOLD OUT -> fails; drop the sold_out
    narrowing -> SOLD OUT beside an unconfirmed size -> fails."""
    left = {"quantities": {"SP-1": {"BV-A": 1, "BV-B": 0, "BV-C": 0}}, "tracked": False, "policy": "DENY"}
    db = _db(a=0, b=0, c=0)
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True, status="PUBLISHED",
                                              locally_modified=True, online_stock=left)])
    spy = spy_for(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), []))
    assert res.stock["set"] == 3 and res.stock["ok"] is False and res.stock["sold_out"] is False, res.stock
    assert res.ok is False and res.code == shopify_push.STOCK_TRACKING_FAILED, res
    assert "taken OFF the website (Draft)" in res.error and "SP-1" in res.error, res.error
    assert len(_drafts(spy)) == 1 and spy.calls_for("publishablePublish") == []
    assert _baseline(db)["tracked"] is False

# ---------------------------------------------------------------------------
# 3. a read that died is unknown and named
# ---------------------------------------------------------------------------


def test_3_the_sweep_over_a_dead_catalogue_read_is_unknown_never_a_green_noop(monkeypatch):
    """catalog_products cannot be read: the sweep used to answer ok, noop,
    '0 of 0 listings changed'. Now ok=False, STOCK_ONHAND_UNKNOWN, the read is
    named, no count is reported (the screens print '-'), a LIVE press stays
    LIVE, and nothing is written."""
    db = _listed(_db(a=2, b=1, c=0))
    _kill(db, "catalog_products", lambda f: f == {}, "catalog read died")
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.STOCK_ONHAND_UNKNOWN, res
    assert "catalogue" in res.error and "catalog read died" in res.error and "nothing written" in res.error
    assert res.mode == "LIVE"
    for count in ("candidates", "changed", "synced", "failed"):
        assert (res.payload or {}).get(count) is None, (count, res.payload)
    assert spy.writes() == []


def test_3_the_sweep_over_a_dead_size_row_read_is_unknown_never_strays(monkeypatch):
    """catalog_variants cannot be read: every listing shrank to its own SKU,
    so every live size was named STOCK_BASELINE_STRAY ('delete it in Shopify
    admin') and only the parent SKU was written. Now the pass is unknown and
    writes nothing."""
    sent = {"quantities": {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}, "SP-1-L": {"BV-A": 1, "BV-B": 0, "BV-C": 0}},
            "tracked": True, "policy": "DENY"}
    db = _listed(_db(a=1, b=1, c=0), online_stock=sent)
    db.seed("catalog_variants", [{"sku": "SP-1-L", "parent_product_id": "cat-1", "shopify_inventory_item_id": INV_TWO}])
    _kill(db, "catalog_variants", lambda f: f == {}, "variant read died")
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.STOCK_ONHAND_UNKNOWN, res
    assert "variant read died" in res.error and "STOCK_BASELINE_STRAY" not in (res.error or "")
    assert spy.writes() == []


def _dead_size_rows(db):
    """variant_rows_for_product's own reads (by parent link) die; the key
    lookups ($or) and every other read answer."""
    _kill(db, "catalog_variants", lambda f: "parent_product_id" in f or "parent_sku" in f, "size rows died")


def test_3_a_dead_stray_check_on_the_writer_is_named_and_never_sold_out(monkeypatch):
    """The writer's stray question (listing_strays) could not be read. It
    used to answer 'no stray' -- green, and SOLD OUT on an all-0 pass. The
    numbers still go out; the pass is not ok, coded unknown, and names the
    check."""
    db = _listed(_db(a=0, b=0, c=0))
    _dead_size_rows(db)
    spy = _Spy(_responses())
    out = _sold_out(db, spy, monkeypatch)
    assert out["set"] == 3 and len(spy.rows()) == 3, "a report never blocks the write"
    assert out["ok"] is False and out["code"] == shopify_push.STOCK_ONHAND_UNKNOWN, out
    assert "stray" in out["error"] and "size rows died" in out["error"], out["error"]
    assert out["sold_out"] is False


def test_3_a_dead_listing_read_on_a_sale_is_named_never_sold_out(monkeypatch):
    """A sale of size SP-1-L: which listing it rides is read through its
    parent. That read dying answered 'no listing' -- no last-sent record, no
    stray question, and an all-0 pass read SOLD OUT. Now the numbers still go
    out and the pass names the dead read. Read the listing fail-soft again ->
    ok, SOLD OUT -> fails."""
    db = _db(a=0, b=0, c=0, sku="SP-1-L")
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True, status="PUBLISHED")])
    db.seed("catalog_variants", [{"sku": "SP-1-L", "parent_product_id": "cat-1", "shopify_inventory_item_id": INV_TWO}])
    _kill(db, "catalog_products", lambda f: any("id" in c for c in f.get("$or", [])), "parent read died")
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    out = _run(shopify_push.push_skus_stock(db, ["SP-1-L"], source="sale"))
    assert out["set"] == 3 and (INV_TWO, LOC_A, 0) in spy.rows(), out
    assert out["ok"] is False and out["code"] == shopify_push.STOCK_ONHAND_UNKNOWN, out
    assert "parent read died" in out["error"] and out["sold_out"] is False, out


PUNE = _loc("gid://shopify/Location/76684427513", "GANGADHAM- PUNE")


@pytest.mark.parametrize(
    "setup, code",
    [
        ("clean", shopify_push.STOCK_ONHAND_UNKNOWN),
        # The documented day-1 setup, how production runs: Gangadham Pune
        # fulfils online orders and is mapped to no shop, so EVERY run's code
        # is SHOPIFY_LOCATION_UNMAPPED -- the listing's top code too.
        ("pune-unmapped", shopify_push.SHOPIFY_LOCATION_UNMAPPED),
        # The run's own code is STOCK_ONHAND_UNKNOWN for another reason (one
        # shop's shelf unreadable) -- the listing's top code again.
        ("one-shop-unreadable", shopify_push.STOCK_ONHAND_UNKNOWN),
    ],
)
def test_3_a_dead_stray_check_inside_the_sweep_is_named_never_a_green_pass(monkeypatch, setup, code):
    """The sweep's own reads answer, but one listing's size-row re-read dies
    inside the loop (listing_strays, strict). Its numbers went out (written,
    not failed), and its pass says the stray line under its top rung. That
    used to reach the run with NO line and ok=True; then (round 3) with the
    line dropped whenever the listing's top code matched the run's -- which
    is every run under the day-1 location setup. Now the run is not ok and
    names the check in every setup. Cut a listing's line by CODE again ->
    'pune-unmapped' and 'one-shop-unreadable' lose the line -> fail; drop
    the per-listing note -> 'clean' fails."""
    db = _listed(_db(a=2, b=1, c=0))
    _dead_size_rows(db)
    over = {}
    if setup == "pune-unmapped":
        over["imsLocationList"] = _locations(_loc(LOC_A, "Bokaro"), _loc(LOC_B, "Dhanbad"), _loc(LOC_C, "Sector 4"), PUNE)
    if setup == "one-shop-unreadable":
        _break_shop(db, "BV-B")
    spy = _Spy(_responses(**over))
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert (res.payload["synced"], res.payload["failed"]) == (1, 0), res.payload
    assert (INV_GID, LOC_A, 2) in spy.rows()
    assert res.ok is False and res.code == code, res
    assert "cat-1: the stray-size check" in res.error and "size rows died" in res.error, res.error
    if setup == "pune-unmapped":
        assert "GANGADHAM- PUNE" in res.error and res.error.count("GANGADHAM- PUNE") == 1, res.error


@pytest.mark.parametrize("dead", ["last-sent stock", "size rows"])
def test_3_a_dead_stray_check_on_a_sale_with_no_target_is_named(monkeypatch, dead):
    """A sale of SP-9, a SKU with no Shopify target that cat-1's last-sent
    record still shows at 1, asks the stray question from the SKU side
    (stray_baseline_skus). A dead read there -- the listings' last-sent
    stock, or cat-1's size rows -- was 'no stray' (or every size a stray).
    Now a not-ok row names the dead read."""
    sent = {"quantities": {"SP-1": {"BV-A": 0, "BV-B": 0, "BV-C": 0}, "SP-9": {"BV-A": 1}},
            "tracked": True, "policy": "DENY"}
    db = _db(a=1, b=0, c=0, sku="SP-9")
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True, status="PUBLISHED", online_stock=sent)])
    if dead == "last-sent stock":
        _kill(db, "catalog_products", lambda f: "ecom.online_stock.quantities" in f, "read died")
    else:
        _kill(db, "catalog_variants", lambda f: "parent_product_id" in f or "parent_sku" in f, "read died")
    _live(monkeypatch, _Spy(_responses()))
    out = _run(wb.writeback_skus(db, ["SP-9"], "BV-A", source="sale"))
    assert out["code"] == shopify_push.STOCK_ONHAND_UNKNOWN, out
    assert "stray" in out["error"] and "read died" in out["error"], out["error"]
    (row,) = list(db.get_collection("sync_runs").find({}))
    assert row["ok"] is False and "read died" in row["error"]


def test_3_a_dead_online_status_read_names_that_read_and_claims_no_write(monkeypatch):
    """A sale of a mapped SKU plus an unmapped one; the online-status read for
    the unmapped one dies AFTER the mapped SKU was written. The line used to
    say 'the inventory-item mapping could not be read -- nothing written'
    (the wrong read, and a write had gone through)."""
    from api.services import online_catalog as oc

    db = _listed(_db(a=1, b=0, c=0))
    db.seed("products", [{"product_id": "spine-9", "sku": "SP-9"}])
    spy = _Spy(_responses())
    _live(monkeypatch, spy)

    def _dead(*a, **k):
        raise RuntimeError("status read died")

    monkeypatch.setattr(oc, "online_status_for_skus", _dead)
    out = _run(wb.writeback_skus(db, ["SP-1", "SP-9"], "BV-A", source="sale"))
    assert out["pushed"] == 1 and (INV_GID, LOC_A, 1) in spy.rows(), out
    assert out["code"] == shopify_push.STOCK_ONHAND_UNKNOWN
    assert "online" in out["error"] and "status read died" in out["error"], out["error"]
    assert "nothing written" not in out["error"] and "inventory-item mapping" not in out["error"], out["error"]


def test_3_the_scheduled_run_records_no_count_for_a_pass_that_counted_nothing(monkeypatch):
    """The live-sync run summary stamped 0 for a stock pass whose payload
    carried no count, so the Live sync card read '0 changed - 0 written'
    over a pass that never counted. Unknown stays None (the card prints '-')."""
    from api.services import shopify_live_sync as ls

    async def _aborted(db, *, dry_run=False):  # noqa: ARG001
        return shopify_push.PushResult(mode="LIVE", entity="stock", action="sync", ok=False,
                                       code=shopify_push.STOCK_ONHAND_UNKNOWN, error="the catalogue could not be read",
                                       payload={})

    monkeypatch.setattr(shopify_push, "sync_stock_levels", _aborted)
    monkeypatch.setattr(ls, "live_sync_config", lambda: {"enabled": True, "slots": ["01:00"], "max_products_per_run": 5})
    monkeypatch.setattr(ls, "write_push_audit", lambda *a, **k: None)
    run = _run(ls.sync_live_products(_listed(_db()), trigger="manual", actor="u"))
    assert run["stock"]["ok"] is False and run["stock"]["code"] == shopify_push.STOCK_ONHAND_UNKNOWN
    assert (run["stock"]["changed"], run["stock"]["synced"], run["stock"]["failed"]) == (None, None, None), run["stock"]


def test_3_online_status_for_skus_threads_strict_to_each_key_lookup():
    """The sale's online-status read (strict) has three key lookups; only the
    pair together was pinned -- drop `strict` from either one alone and every
    test stayed green. Each is pinned on its own here: the other lookups have
    nothing to read. Drop `strict=strict` from either call -> its half fails."""
    from api.services import online_catalog as oc

    db = _listed(_db())
    _kill(db, "catalog_products", lambda f: True, "products died")
    with pytest.raises(RuntimeError, match="products died"):
        oc.online_status_for_skus(db, ["SP-1"], strict=True)
    db2 = _db()
    db2.seed("catalog_products", [])
    db2.seed("catalog_variants", [{"sku": "SP-1", "parent_product_id": "cat-1"}])
    _kill(db2, "catalog_variants", lambda f: True, "variants died")
    with pytest.raises(RuntimeError, match="variants died"):
        oc.online_status_for_skus(db2, ["SP-1"], strict=True)


# ---------------------------------------------------------------------------
# 4. a shop whose write failed keeps its last-sent number
# ---------------------------------------------------------------------------


def test_4_a_failed_shop_keeps_its_last_sent_number_so_its_release_still_zeroes_it(monkeypatch):
    """A sale at BV-A (2 -> 1) while BV-B's location refuses the write:
    Shopify still shows BV-B's 1. The last-sent record used to REPLACE the
    SKU's row with the shops written, dropping BV-B -- so once BV-B's unit
    left the shelf, removing BV-B's location zeroed nothing and Shopify kept
    selling a unit nobody has. Replace instead of merge -> fails."""
    sent = {"quantities": {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0}}, "tracked": True, "policy": "DENY"}
    db = _listed(_db(a=1, b=1, c=0), online_stock=sent)
    out = _sold_out(db, _RefuseAt(LOC_B, _responses()), monkeypatch)
    assert out["code"] == shopify_push.STOCK_WRITE_FAILED and out["quantities"] == {"SP-1": {"BV-A": 1, "BV-C": 0}}
    assert _baseline(db)["quantities"] == {"SP-1": {"BV-A": 1, "BV-B": 1, "BV-C": 0}}
    db.get_collection("stock_units").update_one({"stock_id": "BV-B-u0"}, {"$set": {"status": "SOLD"}})
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    rel = _run(shopify_push.release_store_location(db, "BV-B", LOC_B))
    assert rel["ok"] is True and (INV_GID, LOC_B, 0) in spy.rows(), (rel, spy.rows())


def test_4_a_shop_that_is_no_longer_mapped_leaves_the_record(monkeypatch):
    """The merge keeps MAPPED shops only. BV-OLD (deactivated) is in the old
    record; kept, every pass would see the listing as changed for ever."""
    sent = {"quantities": {"SP-1": {"BV-A": 2, "BV-B": 1, "BV-C": 0, "BV-OLD": 4}}, "tracked": True, "policy": "DENY"}
    db = _listed(_db(a=1, b=1, c=0), online_stock=sent)
    out = _sold_out(db, _Spy(_responses()), monkeypatch)
    assert out["ok"] is True, out
    assert _baseline(db)["quantities"] == {"SP-1": {"BV-A": 1, "BV-B": 1, "BV-C": 0}}
