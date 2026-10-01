import os, sys, json
sys.path.insert(0, "backend")
import httpx
from pymongo import MongoClient
from api.services.shopify_auth import resolve_shopify_credentials

def env_state(k):
    v = os.getenv(k, "")
    return "SET" if v.strip() else "UNSET"
print("GATES:", {
    "IMS_SHOPIFY_WRITES": env_state("IMS_SHOPIFY_WRITES"),
    "IMS_SHOPIFY_WRITES_truthy": os.getenv("IMS_SHOPIFY_WRITES","").strip().lower() in ("1","true","on","yes"),
    "DISPATCH_MODE": env_state("DISPATCH_MODE"),
    "DISPATCH_MODE_is_live": os.getenv("DISPATCH_MODE","").strip().lower()=="live",
    "SHOPIFY_ONLINE_LOCATION_ID": env_state("SHOPIFY_ONLINE_LOCATION_ID"),
})
uri = os.getenv("MONGO_PUBLIC_URL") or os.getenv("MONGO_URL")
db = MongoClient(uri, serverSelectionTimeoutMS=20000)["ims_2_0"]
cfg = db["integrations"].find_one({"provider":"shopify"}) or db["integrations"].find_one({"name":"shopify"}) or {}
print("integrations.shopify keys:", sorted(k for k in cfg.keys() if k not in ("_id",)))
print("integrations.shopify online_location_id set:", bool(cfg.get("online_location_id") or (cfg.get("config") or {}).get("online_location_id")))
print("opening_stock_batches:", db["opening_stock_batches"].count_documents({}))
print("stock_units total:", db["stock_units"].count_documents({}))
print("sync_runs by kind:", list(db["sync_runs"].aggregate([{"$group":{"_id":"$kind","n":{"$sum":1},"last":{"$max":"$at"}}}])))
print("audit ONLINE_STORE_PUSH:", db["audit_logs"].count_documents({"action":"ONLINE_STORE_PUSH"}))
twins = list(db["catalog_products"].find({"ecom.shopify_product_id":{"$nin":[None,""]}},{"_id":0,"product_id":1,"sku":1,"name":1,"ecom.shopify_product_id":1,"ecom.shopify_inventory_item_id":1,"ecom.locally_modified":1}))
print("twins:", len(twins))
pids=[t.get("product_id") for t in twins]; skus=[t.get("sku") for t in twins if t.get("sku")]
vars_ = list(db["catalog_variants"].find({"product_id":{"$in":pids}},{"_id":0,"product_id":1,"sku":1,"shopify_inventory_item_id":1,"shopify_variant_id":1}))
print("catalog_variants for twins:", len(vars_))
# IMS pooled on-hand via the writeback's own helper
class _W:  # minimal shim exposing get_collection
    def get_collection(self, n): return db[n]
from api.services.online_stock_writeback import _on_hand_for_skus
# Shopify live availability
_creds = resolve_shopify_credentials(None, "BV") or {}; shop_url, token = _creds.get("shop_url") or _creds.get("shop") or "", _creds.get("access_token") or _creds.get("token") or ""; print("cred keys:", sorted(_creds.keys()))
six = [v for v in vars_ if str(v.get("shopify_inventory_item_id") or "").endswith(("51110108659961","51110110462201","51110108791033","51110108823801","51110108889337","51110108922105"))]
print("SIX IMS-pushed:", six)
skus=[v["sku"] for v in six]
prod_rows = list(db["products"].find({"sku":{"$in":skus}},{"_id":0,"product_id":1,"sku":1}))
print("spine rows:", prod_rows)
print("IMS pooled on-hand (writeback helper):", _on_hand_for_skus(_W(), skus, None))
print("stock_units any-status per pid:", {p["product_id"]: db["stock_units"].count_documents({"product_id":p["product_id"]}) for p in prod_rows})
print("catalog_products twin stock fields:", list(db["catalog_products"].find({"sku":{"$in":skus}},{"_id":0,"sku":1,"stock":1,"quantity":1,"stock_qty":1,"ecom.shopify_product_id":1,"ecom.pushed_at":1,"ecom.last_pushed_at":1})))
inv_ids=[v["shopify_inventory_item_id"] for v in six]
inv_ids = [i if str(i).startswith("gid://") else f"gid://shopify/InventoryItem/{i}" for i in inv_ids]
q = """query($ids:[ID!]!){ nodes(ids:$ids){ ... on InventoryItem { id sku tracked variant { product { id title status } } inventoryLevels(first:5){ edges{ node{ location{ id name } quantities(names:["available"]){ name quantity } } } } } } }"""
host = shop_url if shop_url.startswith("http") else f"https://{shop_url}"
def call():
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=20.0)) as c:
        r = c.post(f"{host}/admin/api/2025-01/graphql.json", headers={"X-Shopify-Access-Token":token,"Content-Type":"application/json"}, json={"query":q,"variables":{"ids":inv_ids}})
        return r.json()
try: body = call()
except httpx.ReadTimeout: body = call()
print("SHOPIFY inv nodes:", json.dumps(body.get("data") or body, indent=1)[:4000])
