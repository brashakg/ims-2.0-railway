"""D7b (owner ruling 2026-09-29): the counter's READ-ONLY stock lookup.

Counter staff search a frame by brand, model, SKU or barcode and see how many
the till can sell at THIS shop, at every other shop, and how many are on their
way to each shop, with the model's other colours and eye sizes. Nothing here
writes, and nothing but MRP / selling price leaves it: the response is built
from an allow-list, never from the product or stock document.

Every rule is reused, not re-typed:
  * search      -- ProductRepository.search_products, the active-only search
                   GET /products?search= runs, plus the IMS unit barcode via
                   StockRepository.find_by_barcode (the till's scan lookup);
  * the model   -- product_master.find_similar_products (the identity_key
                   'brand|model|' rule) for colours, variant_of for sizes;
  * sellable    -- StockRepository.sellable_filter, the filter find_available
                   and the sale guard count (AVAILABLE and in date), one unit
                   per stock_units row -- so this shop's figure is the till's;
  * the shops   -- stores_util.physical_stores;
  * in transit  -- item_events.status_match(TRANSFERRED) + transfer_to_store_id
                   (what StockRepository.claim_for_transfer stamps).
"""

from ._shared import (
    Depends,
    Query,
    StockState,
    get_product_repository,
    get_stock_repository,
    require_roles,
    router,
)
from .helpers import _get_db
from ...services.item_events import status_match
from ...services.product_master import find_similar_products
from ...services.rbac_policy import policy_for
from ...services.stores_util import physical_stores

# The route gate IS the rbac_policy row (rows_items_jarvis.py): one list, so
# the row and the route can never disagree about a role.
STOCK_LOOKUP_ROLES = tuple(policy_for("GET", "/api/v1/inventory/lookup")["allowed"])

# The ONLY product fields a counter sees. An allow-list, so a cost, supplier or
# bill field added to the product doc later can never reach this screen.
_PRODUCT_FIELDS = ("sku", "name", "brand", "model", "color", "size", "mrp", "offer_price")
_HITS = 50


def _units_by_product_shop(stock_repo, match, shop_field="$store_id"):
    """{(product_id, shop): stock_units rows matching `match`} -- one unit per
    row, the way find_available counts them. Both columns go through here."""
    rows = stock_repo.aggregate(
        [
            {"$match": match},
            {"$group": {"_id": {"p": "$product_id", "s": shop_field}, "n": {"$sum": 1}}},
            # aggregate() stringifies a non-string _id, so lift the key out.
            {"$project": {"_id": 0, "p": "$_id.p", "s": "$_id.s", "n": 1}},
        ]
    )
    return {(str(r["p"]), str(r["s"])): int(r["n"]) for r in rows if r.get("p") and r.get("s")}


def sellable_by_product_shop(stock_repo, pids):
    """{(product_id, shop): units the till may sell there} for many products at
    every shop: find_available's own sellable_filter, so a shop's figure is the
    one its sale guard counts. Shared with GET /inventory/cross-store-stock."""
    return _units_by_product_shop(
        stock_repo, stock_repo.sellable_filter({"$in": list(pids)}, {"$ne": None})
    )


def _in_transit_by_product_shop(stock_repo, pids, shop_ids):
    """{(product_id, destination shop): units shipped to it, not yet received}."""
    match = {
        "product_id": {"$in": pids},
        "transfer_to_store_id": {"$in": shop_ids},
        **status_match(StockState.TRANSFERRED),
    }
    return _units_by_product_shop(stock_repo, match, "$transfer_to_store_id")


def _find(product_repo, stock_repo, q):
    """Active search hits plus their model family (other colours by the
    identity_key rule, other eye sizes by variant_of), so one scan answers "in
    any colour?". Inactive (soft-deleted / draft) products never come back."""
    hits = product_repo.search_products(q, limit=_HITS)
    unit = stock_repo.find_by_barcode(q)
    if unit and unit.get("product_id"):
        hits += product_repo.find_many({"product_id": unit["product_id"]}, limit=1)
    ids = [p["product_id"] for p in hits if p.get("product_id")]
    # ponytail: 3 indexed reads per distinct model among <= 51 hits; fold into
    # one identity_key $regex only if a broad brand search ever feels slow.
    for cat, brand, model in {(p.get("category"), p.get("brand"), p.get("model")) for p in hits}:
        similar = find_similar_products(
            product_repo.collection, category=cat, brand=brand, model=model, limit=4 * _HITS
        )
        ids += [
            s["product_id"]
            for s in similar["siblings"] + [similar["exact_match"] or {}]
            if s.get("product_id")
        ]
    if not ids:
        return []
    ids += [p["variant_of"] for p in hits if p.get("variant_of")]
    family = [{"product_id": {"$in": ids}}, {"variant_of": {"$in": ids}}]
    return product_repo.find_many({"$or": family, "is_active": True}, limit=4 * _HITS)


@router.get("/lookup")
async def stock_lookup(
    q: str = Query("", max_length=100, description="Brand, model, SKU or barcode"),
    current_user: dict = Depends(require_roles(*STOCK_LOOKUP_ROLES)),
):
    """Read-only: per physical shop, how many of each matching product the
    till can sell now and how many are on their way there. MRP and selling
    price only -- no cost, supplier or bill."""
    here = current_user.get("active_store_id")
    q = q.strip()
    db = _get_db()
    product_repo = get_product_repository()
    stock_repo = get_stock_repository()
    # No usable handle -- None, or a dev box's MockDatabase, which has no
    # get_collection -- is an empty answer, never a 500.
    if not q or not hasattr(db, "get_collection") or product_repo is None or stock_repo is None:
        return {"store_id": here, "items": []}

    products = _find(product_repo, stock_repo, q)
    pids = sorted({str(p["product_id"]) for p in products if p.get("product_id")})
    if not pids:
        return {"store_id": here, "items": []}
    # A stores doc with no store_id is no shop anyone can stock or sell from.
    shops = [s for s in physical_stores(db) if s.get("store_id")]
    shop_ids = [str(s["store_id"]) for s in shops]
    available = sellable_by_product_shop(stock_repo, pids)
    in_transit = _in_transit_by_product_shop(stock_repo, pids, shop_ids)

    items, seen = [], set()
    for p in products:
        pid = str(p.get("product_id") or "")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        item = {"product_id": pid, **{k: p.get(k) for k in _PRODUCT_FIELDS}}
        item["stores"] = [
            {
                "store_id": sid,
                "store_name": s.get("store_name") or s.get("store_code") or sid,
                "available": available.get((pid, sid), 0),
                "in_transit": in_transit.get((pid, sid), 0),
            }
            for sid, s in zip(shop_ids, shops)
        ]
        items.append(item)
    items.sort(key=lambda i: tuple(str(i.get(k) or "") for k in ("brand", "model", "color", "size")))
    return {"store_id": here, "items": items}
