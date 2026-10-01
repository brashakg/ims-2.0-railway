import os, sys, json
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "backend")
os.environ.setdefault("ENVIRONMENT", "production")
import httpx
from pymongo import MongoClient

SECRET_KEYS = ("access_token", "token", "secret", "api_key", "api_secret", "webhook_secret", "password", "client_secret", "refresh_token")


def scrub(d):
    if isinstance(d, dict):
        return {k: ("<set>" if any(s in k.lower() for s in SECRET_KEYS) and v else scrub(v)) for k, v in d.items()}
    if isinstance(d, list):
        return [scrub(x) for x in d[:20]]
    return d if isinstance(d, (str, int, float, bool, type(None))) else str(d)


def p(title, obj):
    print("\n### " + title)
    print(json.dumps(scrub(obj), indent=1, default=str)[:6000])


uri = os.getenv("MONGO_PUBLIC_URL") or os.getenv("MONGO_URL")
print("mongo uri key present:", bool(uri))
db = MongoClient(uri, serverSelectionTimeoutMS=20000)["ims_2_0"]
now = datetime.now(timezone.utc)
d30 = now - timedelta(days=30)
d30_iso = d30.isoformat()

# --- 1. Shopify webhook subscriptions (GraphQL QUERY) ---
from api.services.shopify_auth import resolve_shopify_credentials
creds = resolve_shopify_credentials(None, "BV") or {}
shop_url = creds.get("shop_url")
tok = creds.get("access_token")
print("shop_url:", shop_url, "| token present:", bool(tok), "| api_version:", creds.get("api_version"))
from api.services.shopify_push import SHOPIFY_API_VERSION
ver = SHOPIFY_API_VERSION


def gql(q, variables=None):
    url = f"https://{shop_url}/admin/api/{ver}/graphql.json"
    h = {"X-Shopify-Access-Token": tok, "Content-Type": "application/json"}
    for attempt in range(2):
        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=20.0)) as c:
                r = c.post(url, headers=h, json={"query": q, "variables": variables or {}})
            return r.status_code, r.json()
        except httpx.ReadTimeout:
            if attempt == 1:
                raise


if shop_url and tok:
    st, body = gql("""{ webhookSubscriptions(first:50){ edges{ node{ id topic format apiVersion{handle} createdAt updatedAt endpoint{ __typename ... on WebhookHttpEndpoint{callbackUrl} ... on WebhookEventBridgeEndpoint{arn} ... on WebhookPubSubEndpoint{pubSubProject pubSubTopic} } } } } }""")
    p("SHOPIFY webhookSubscriptions status=%s" % st, body)
    st, body = gql("""{ orders(first:5, sortKey:CREATED_AT, reverse:true){ edges{ node{ id name createdAt displayFinancialStatus displayFulfillmentStatus totalPriceSet{shopMoney{amount}} } } } }""")
    p("SHOPIFY newest 5 orders status=%s" % st, body)
    st, body = gql("""{ ordersCount(query:"created_at:>%s"){count precision} }""" % d30.strftime("%Y-%m-%d"))
    p("SHOPIFY ordersCount last30d status=%s" % st, body)
    st, body = gql("""{ currentAppInstallation{ id app{ title handle } accessScopes{handle} } shop{ myshopifyDomain } }""")
    p("SHOPIFY currentAppInstallation status=%s" % st, body)

# --- 2. webhook_inbox ---
inbox = db["webhook_inbox"]
p("inbox total", {"all": inbox.count_documents({}), "shopify_all": inbox.count_documents({"vendor": "shopify"})})
pipe = [{"$match": {"received_at": {"$gte": d30}}},
        {"$group": {"_id": {"vendor": "$vendor", "topic": "$headers.x-shopify-topic", "processed": "$processed", "skipped": "$skipped_reason", "err": {"$ifNull": ["$handler_error", None]}}, "n": {"$sum": 1}, "newest": {"$max": "$received_at"}}},
        {"$sort": {"n": -1}}]
p("inbox last30d by vendor/topic/processed", list(inbox.aggregate(pipe)))
p("inbox last30d (string received_at) count", inbox.count_documents({"received_at": {"$gte": d30_iso, "$type": "string"}}))
newest = list(inbox.find({"vendor": "shopify"}, {"payload": 0}).sort("received_at", -1).limit(5))
p("inbox newest 5 shopify (no payload)", newest)
p("inbox shopify all-time by topic", list(inbox.aggregate([{"$match": {"vendor": "shopify"}}, {"$group": {"_id": {"topic": "$headers.x-shopify-topic", "processed": "$processed", "skipped": "$skipped_reason"}, "n": {"$sum": 1}, "newest": {"$max": "$received_at"}}}, {"$sort": {"n": -1}}])))

# --- 3. IMS orders under BV-ONLINE-01 ---
orders = db["orders"]
q = {"store_id": "BV-ONLINE-01"}
p("orders BV-ONLINE-01", {"count": orders.count_documents(q),
                          "channel_online": orders.count_documents({"channel": "ONLINE"}),
                          "with_shopify_order_id": orders.count_documents({"shopify_order_id": {"$exists": True, "$ne": None}}),
                          "historical_import": orders.count_documents({"imported_from": {"$exists": True}}),
                          "last30d_created": orders.count_documents({**q, "created_at": {"$gte": d30}}),
                          "last30d_created_str": orders.count_documents({**q, "created_at": {"$gte": d30_iso, "$type": "string"}})})
nw = list(orders.find(q, {"order_id": 1, "order_number": 1, "invoice_number": 1, "created_at": 1, "order_date": 1, "shopify_order_id": 1, "status": 1, "channel": 1, "imported_from": 1, "historical": 1, "source": 1, "customer_id": 1, "grand_total": 1, "interstate": 1, "place_of_supply": 1, "fulfillment_stores": 1, "_id": 0}).sort([("created_at", -1)]).limit(3))
p("orders BV-ONLINE-01 newest 3", nw)
p("orders BV-ONLINE-01 by source/imported_from", list(orders.aggregate([{"$match": q}, {"$group": {"_id": {"src": "$source", "imp": "$imported_from", "hist": "$historical"}, "n": {"$sum": 1}, "newest": {"$max": "$created_at"}}}])))

# --- 4. store doc + GSTIN ---
st = db["stores"].find_one({"store_id": "BV-ONLINE-01"}, {"_id": 0, "store_id": 1, "name": 1, "gstin": 1, "state": 1, "state_code": 1, "entity_id": 1, "legal_entity": 1, "is_active": 1, "status": 1, "store_type": 1, "is_online": 1, "channel": 1, "fulfillment_store_id": 1})
p("stores BV-ONLINE-01", st)

# --- 5. posture ---
from api.services.shopify_push import push_mode_status
p("push_mode_status(db)", push_mode_status(db))
for k in ["IMS_SHOPIFY_WRITES", "SHOPIFY_DISPATCH_MODE", "DISPATCH_MODE", "SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "SHOPIFY_REFUND_AUTO", "ONLINE_STORE_ID", "SHOPIFY_WEBHOOK_SECRET", "JARVIS_ENABLED", "AGENTS_ENABLED", "REDIS_URL"]:
    print("env", k, "set" if os.getenv(k) else "UNSET")

p("integrations shopify (scrubbed)", list(db["integrations"].find({"type": "shopify"}, {"_id": 0})))
p("integrations enabled types", list(db["integrations"].find({"enabled": True}, {"_id": 0, "type": 1, "storefront_id": 1})))

# --- 6. agent config / heartbeats / sync_runs ---
p("agent_config nexus", list(db["agent_config"].find({"agent_id": "nexus"}, {"_id": 0})))
for coll in ["agent_heartbeats", "agent_heartbeat", "agent_runs"]:
    try:
        rows = list(db[coll].find({"agent_id": "nexus"}, {"_id": 0}).sort([("_id", -1)]).limit(2))
        if rows:
            p(coll + " nexus latest", rows)
        else:
            print(coll, "nexus rows:", db[coll].count_documents({"agent_id": "nexus"}))
    except Exception as e:
        print(coll, "err", e)
p("sync_runs latest shopify", list(db["sync_runs"].find({"integration": "shopify"}, {"_id": 0}).sort("ran_at", -1).limit(3)))
p("sync_runs latest any", list(db["sync_runs"].find({}, {"_id": 0}).sort("ran_at", -1).limit(3)))
p("agent_errors nexus latest", list(db["agent_errors"].find({"agent_id": "nexus"}, {"_id": 0}).sort("_id", -1).limit(2)))

# --- refunds / stock / push audit ---
p("shopify_refund_review by status", list(db["shopify_refund_review"].aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}])))
p("credit_note_ledger ONLINE", db["credit_note_ledger"].count_documents({"channel": "ONLINE"}))
p("online_stock_miss", db["online_stock_miss"].count_documents({}))
names = db.list_collection_names()
p("collections matching writeback/sync/shopify/online/webhook", [c for c in names if any(s in c for s in ("writeback", "sync", "shopify", "online", "webhook"))])
for coll in [c for c in names if "writeback" in c]:
    p(coll + " latest", list(db[coll].find({}, {"_id": 0}).sort("_id", -1).limit(2)))
p("audit_logs ONLINE_STORE_PUSH latest", list(db["audit_logs"].find({"action": "ONLINE_STORE_PUSH"}, {"_id": 0, "timestamp": 1, "created_at": 1, "user_id": 1, "details.sku": 1, "details.result": 1, "details.status": 1, "details.shopify_product_id": 1, "details.mode": 1}).sort("_id", -1).limit(8)))
p("catalog_products with ecom.shopify_product_id", db["catalog_products"].count_documents({"ecom.shopify_product_id": {"$exists": True, "$ne": None}}))
p("catalog_products ecom.locally_modified true", db["catalog_products"].count_documents({"ecom.locally_modified": True}))
p("catalog_variants with shopify_variant_id", db["catalog_variants"].count_documents({"shopify_variant_id": {"$exists": True, "$ne": None}}))
p("catalog_variants with ecom.shopify_variant_id", db["catalog_variants"].count_documents({"ecom.shopify_variant_id": {"$exists": True, "$ne": None}}))
print("\nDONE")
