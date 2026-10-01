"""D7b (owner ruling 2026-09-29): the counter's READ-ONLY stock lookup.

Counter staff search a frame by model, name, brand, SKU or barcode and see how
many are sellable at THIS shop, at every other shop, and on their way to each
shop, with the model's other colours and eye sizes. Nothing here writes, and
nothing but MRP / selling price leaves it: the response is built from an
allow-list, never from the product or stock document.

Every rule is reused, not re-typed:
  * search      -- BaseRepository.search over ProductRepository.SEARCH_FIELDS
                   (+ attributes.gtin, + any word of the minted name), plus the
                   IMS unit barcode via StockRepository.find_by_barcode;
  * sellable    -- inventory_balancing._on_hand_by_product_store (item_events'
                   one on-hand rule, probed by test_on_hand_is_one_rule.py);
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
from database.repositories.product_repository import ProductRepository
from ...services.inventory_balancing import _on_hand_by_product_store
from ...services.item_events import status_match
from ...services.stores_util import physical_stores

# The counter roles of the ruling plus the manager ladder. SUPERADMIN passes
# require_roles on its own. Mirrors the rbac_policy row for /inventory/lookup.
STOCK_LOOKUP_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "SALES_STAFF",
    "SALES_CASHIER",
    "CASHIER",
    "OPTOMETRIST",
)

# The ONLY product fields a counter sees. An allow-list, so a cost, supplier or
# bill field added to the product doc later can never reach this screen.
_PRODUCT_FIELDS = ("sku", "name", "brand", "model", "color", "size", "mrp", "offer_price")
_SEARCH_FIELDS = [*ProductRepository.SEARCH_FIELDS, "attributes.gtin"]
_HITS = 50


def _qty(row) -> int:
    q = row.get("quantity")
    try:
        return 1 if q is None else int(q)
    except (TypeError, ValueError):
        return 1


def _in_transit_by_product_store(db, pids, shop_ids):
    """{(product_id, destination shop): units shipped to it, not yet received}."""
    out = {}
    rows = db.get_collection("stock_units").find(
        {
            "product_id": {"$in": pids},
            "transfer_to_store_id": {"$in": shop_ids},
            **status_match(StockState.TRANSFERRED),
        },
        {"product_id": 1, "transfer_to_store_id": 1, "quantity": 1},
    )
    for row in rows:
        key = (str(row["product_id"]), str(row["transfer_to_store_id"]))
        out[key] = out.get(key, 0) + _qty(row)
    return out


def _find(product_repo, stock_repo, q):
    """Search hits plus their model family (other colours = same brand+model,
    other eye sizes = variant_of), so one scan answers "in any colour?"."""
    hits = product_repo.search(q, _SEARCH_FIELDS, limit=_HITS, word_fields=("name",))
    unit = stock_repo.find_by_barcode(q) if stock_repo is not None else None
    if unit and unit.get("product_id"):
        hits += product_repo.find_many({"product_id": unit["product_id"]}, limit=1)
    if not hits:
        return []
    ids = [p["product_id"] for p in hits if p.get("product_id")]
    parents = [p["variant_of"] for p in hits if p.get("variant_of")]
    family = [{"product_id": {"$in": ids + parents}}, {"variant_of": {"$in": ids + parents}}]
    family += [
        {"brand": b, "model": m}
        for b, m in {(p.get("brand"), p.get("model")) for p in hits}
        if b and m
    ]
    return product_repo.find_many({"$or": family}, limit=4 * _HITS)


@router.get("/lookup")
async def stock_lookup(
    q: str = Query("", max_length=100, description="Model, name, brand, SKU or barcode"),
    current_user: dict = Depends(require_roles(*STOCK_LOOKUP_ROLES)),
):
    """Read-only: per physical shop, how many of each matching product are
    sellable now and how many are on their way there. MRP and selling price
    only -- no cost, supplier or bill."""
    here = current_user.get("active_store_id")
    q = q.strip()
    db = _get_db()
    product_repo = get_product_repository()
    # No usable handle -- None, or a dev box's MockDatabase, which has no
    # get_collection -- is an empty answer, never a 500.
    if not q or not hasattr(db, "get_collection") or product_repo is None:
        return {"store_id": here, "items": []}

    products = _find(product_repo, get_stock_repository(), q)
    pids = sorted({str(p["product_id"]) for p in products if p.get("product_id")})
    if not pids:
        return {"store_id": here, "items": []}
    shops = physical_stores(db)
    shop_ids = [str(s["store_id"]) for s in shops]
    available = _on_hand_by_product_store(db, pids)
    in_transit = _in_transit_by_product_store(db, pids, shop_ids)

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
