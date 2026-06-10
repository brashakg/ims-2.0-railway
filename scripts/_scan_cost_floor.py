"""Read-only prod diagnostic: which active products would the Fcostfloor
default-ON rule deadlock (unsellable at sticker)? Formula per the adversarial
chair: ex-GST offer price < cost_price * 1.10. Prints counts + samples only --
no secrets. Run via railway run with MONGO_PUBLIC_URL injected."""
import os
import sys

from pymongo import MongoClient

RATE = {
    "FRAME": 5, "OPTICAL_LENS": 5, "LENS": 5, "CONTACT_LENS": 5, "CONTACTS": 5,
    "SUNGLASS": 18, "SUNGLASSES": 18, "WATCH": 18, "ACCESSORY": 18,
    "ACCESSORIES": 18, "SERVICE": 18,
}


def main() -> None:
    url = os.environ.get("MONGO_PUBLIC_URL") or os.environ.get("MONGODB_URL") or ""
    if not url:
        print("ERROR: no MONGO_PUBLIC_URL/MONGODB_URL in env")
        sys.exit(1)
    client = MongoClient(url, serverSelectionTimeoutMS=20000)
    db = client[os.environ.get("MONGO_DATABASE", "ims_2_0")]
    total = 0
    with_cost = 0
    hits = []
    cursor = db.products.find(
        {"is_active": {"$ne": False}},
        {"sku": 1, "product_id": 1, "category": 1, "cost_price": 1,
         "offer_price": 1, "mrp": 1, "gst_rate": 1},
    )
    for p in cursor:
        total += 1
        cost = p.get("cost_price")
        offer = p.get("offer_price") or p.get("mrp")
        try:
            cost = float(cost)
            offer = float(offer)
        except (TypeError, ValueError):
            continue
        if cost <= 0 or offer <= 0:
            continue
        with_cost += 1
        rate = p.get("gst_rate")
        try:
            rate = float(rate)
        except (TypeError, ValueError):
            rate = None
        if rate is None:
            rate = float(RATE.get(str(p.get("category") or "").upper(), 18))
        ex_gst = offer / (1.0 + rate / 100.0)
        if ex_gst < cost * 1.10:
            hits.append((str(p.get("sku") or p.get("product_id")),
                         str(p.get("category")), cost, offer, rate, round(ex_gst, 2)))
    print("SCAN: total_active=%d with_cost_and_price=%d FLOOR_DEADLOCK_HITS=%d"
          % (total, with_cost, len(hits)))
    for h in hits[:12]:
        print("  HIT sku=%s cat=%s cost=%.2f offer=%.2f gst=%.0f%% ex_gst=%.2f needs>=%.2f"
              % (h[0], h[1], h[2], h[3], h[4], h[5], h[2] * 1.10))
    if len(hits) > 12:
        print("  ...+%d more" % (len(hits) - 12))
    csv_path = os.environ.get("SCAN_CSV")
    if csv_path:
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("sku,category,cost_price,offer_price,gst_pct,ex_gst_price,needed_for_cost_plus_10\n")
            for h in sorted(hits, key=lambda x: (x[1], x[0])):
                f.write("%s,%s,%.2f,%.2f,%.0f,%.2f,%.2f\n"
                        % (h[0], h[1].replace(",", " "), h[2], h[3], h[4], h[5], h[2] * 1.10))
        print("CSV written: %s (%d rows)" % (csv_path, len(hits)))


if __name__ == "__main__":
    main()
