"""
Per-store website stock says only what IMS knows (rebuild of parked #1172).
=========================================================================
Four rules, each driven through the real writer with a MOCKED Shopify
transcript (no network, no Mongo):

  1. A size added to a listing that is already LIVE is created tracked and
     "stop selling at 0" IN THE SAME productVariantsBulkCreate call, and
     Shopify's answer must confirm both. Not confirmed on a live listing ->
     the listing is taken off the website (Draft) and the press says so.
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
    _ok_body,
    _product_body,
    _reset,
    _responses,
    _run,
    _set_error,
)

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
    """The create call itself carries tracked=true + DENY, and Shopify's answer
    confirming both is what makes the size safe: a refused re-send of tracking
    afterwards changes nothing on Shopify, so the press is the live-listing
    warning, never "WITHOUT LIMIT", and the listing stays on the website.
    Send the create row without `tracked` / `inventoryPolicy` -> the first half
    fails; treat a confirmed size as unconfirmed -> WITHOUT LIMIT -> fails."""
    db, variants, upd = _live_listing_gets_size_l()
    spy = _ThrottledTracking(_responses(**{"productUpdate(": upd, "productVariantsBulkCreate": _created()}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    (create,) = spy.calls_for("productVariantsBulkCreate")
    (row,) = create["variables"]["variants"]
    assert row["inventoryItem"] == {"sku": "SP-1-L", "tracked": True}, row
    assert row["inventoryPolicy"] == "DENY", row
    sel = shopify_push._VARIANTS_BULK_CREATE
    assert "inventoryPolicy" in sel and "tracked" in sel, "the answer must carry what it confirms"
    assert _drafts(spy) == [], "confirmed by Shopify -- the listing stays on the website"
    assert res.ok is True and res.code == shopify_push.STOCK_TRACKING_FAILED, res
    assert "re-confirmed" in res.error and "WITHOUT LIMIT" not in res.error, res.error
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


def test_1_a_refused_take_down_whose_tracking_re_send_was_accepted_is_one_answer(monkeypatch):
    """The take-down refused, but the same press's stock pass re-sent tracked
    + DENY to every size -- L included -- and Shopify accepted it. The listing
    is live and tracked (the last-sent record says so), so the press must not
    send the owner into Shopify admin to take it off sale: it is the normal
    live press. Drop the 'tracking confirmed after all' step -> STILL LIVE ->
    fails."""
    db, variants, upd = _live_listing_gets_size_l()
    spy = _DraftRefused(_responses(**{"productUpdate(": upd, "productVariantsBulkCreate": _created(tracked=None)}))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), variants))
    assert len(_drafts(spy)) == 1
    assert MINTED in _tracking_sent(spy) and res.stock["errors"] == []
    assert _baseline(db)["tracked"] is True
    assert res.ok is True and res.code is None, res
    assert "STILL LIVE" not in (res.error or "") and "Shopify admin" not in (res.error or ""), res.error
    assert _cat(db)["ecom"]["status"] == "PUBLISHED"


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
    assert max(_index(spy, "productVariantsBulkUpdate")) < pub
    assert res.ok is True, res


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


def test_2_unconfirmed_tracking_on_a_live_listing_is_not_sold_out(monkeypatch):
    """A live listing whose last pass recorded tracking as NOT set, every
    shelf 0, the tracking re-send refused: the press stays live with
    'sells WITHOUT LIMIT' -- and that is not SOLD OUT, whatever the numbers
    say. Drop the tracking override in sync_product_stock -> sold_out True
    beside WITHOUT LIMIT -> fails."""
    left = {"quantities": {"SP-1": {"BV-A": 1, "BV-B": 0, "BV-C": 0}}, "tracked": False, "policy": "DENY"}
    db = _db(a=0, b=0, c=0)
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True, status="PUBLISHED",
                                              locally_modified=True, online_stock=left)])
    spy = _ThrottledTracking(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, _cat(db), []))
    assert res.ok is True and res.code == shopify_push.STOCK_TRACKING_FAILED, res
    assert "WITHOUT LIMIT" in res.error and res.stock["set"] == 3, res
    assert res.stock["sold_out"] is False


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


def test_3_a_dead_stray_check_inside_the_sweep_is_named_never_a_green_pass(monkeypatch):
    """The sweep's own reads answer, but one listing's size-row re-read dies
    inside the loop (listing_strays, strict). Its numbers went out (written,
    not failed), and its pass says STOCK_ONHAND_UNKNOWN with the stray line.
    That code reached the run with NO line and ok=True: a green Push stock
    toast and 'Stock pass: ok ... STOCK_ONHAND_UNKNOWN'. Now the run is not
    ok and names the check. Drop the per-listing note -> ok, no line ->
    fails."""
    db = _listed(_db(a=2, b=1, c=0))
    _dead_size_rows(db)
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert (res.payload["synced"], res.payload["failed"]) == (1, 0), res.payload
    assert (INV_GID, LOC_A, 2) in spy.rows()
    assert res.ok is False and res.code == shopify_push.STOCK_ONHAND_UNKNOWN, res
    assert "cat-1" in res.error and "stray" in res.error and "size rows died" in res.error, res.error


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
