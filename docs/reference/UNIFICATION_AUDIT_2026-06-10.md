# IMS 2.0 — Unification Synthesis Report (Products, Customers, Online Data, Collections)

**Date:** 2026-06-10 · **Chair:** synthesis over 4 grounded audit maps, with 6 load-bearing claims re-verified directly against the repo (all confirmed: `backend/api/routers/customers.py:277-311` lone validator; `backend/api/services/shopify_ingest.py:511` dead index helper; zero `catalog_products` indexes in `backend/database/connection.py`; `pm.mirror_enabled` default False at `backend/api/services/policy_registry.py:229`; `{field,relation,value}`-only rule reader at `backend/api/services/ecom_smart_rules.py:128-133`; ONDC `total_amount` vs `backend/api/routers/finance.py:53` revenue expression).

---

## The one-paragraph picture

Your instinct is right on all four counts. Products can be created through **seven** doors into **five** different stores, with **three contradictory rulebooks** about which fields are required — and the "Unified Product Master" you already paid to build is the right answer but is currently just one more door (its mirror is switched off by default). Customers can be created through **four** doors that disagree on phone format, duplicate detection, store assignment, and even what a customer ID looks like — and the **edit** endpoint skips every validation the create endpoint enforces. Online buyers DO land in the same customer list as your stores (good), but as stripped-down records, and ONDC buyers never become customers at all. A genuine Shopify-style collections engine exists, but the BVI collections you just migrated are dead data inside it for three independent reasons. The fix is not a rewrite: it is closing the side doors onto two canonical services that already half-exist, plus a small collections materialiser — sequenced below as 13 PR-sized steps.

---

## 1. Product entry — divergence matrix

| Entry path (target store) | Dedup key | Category model | Required fields per category | MRP>=offer | GST/HSN | Discount tier | Barcode | Cost |
|---|---|---|---|---|---|---|---|---|
| POST /products + bulk → `products` | sku (409 + sparse idx) | long-form enum | **NONE** (frame w/o colour passes) | YES | derived | optional | EAN-13 on edit only | absent |
| POST /products/master (PM) → `products` | minted sku + 409 | alias-tolerant | **FULL registry** | YES | derived | forced for HA/SERVICES | — | absent |
| POST /catalog/products → `catalog_products` | **NONE (no DB index)** | short codes, no SERVICES | **3rd table, contradicts PM** | YES | derived | **defaults MASS 15%** | none | yes |
| TechCherry import → `products` (the collection POS sells from) | barcode+store, else blind | free text | NONE | forced equal | **absent** | absent | none | yes |
| POST /lens-catalog → `lens_catalog` | slug (**index never built**) | lens enums | enums | n/a | **hardcoded 5% / HSN '9001'** | absent | n/a | yes |
| Mirror/BVI script → `catalog_variants` | sku upsert | none | none | **unchecked** | none | none | unvalidated | none |
| BVI Postgres + Shopify webhook | sku (Prisma) | BVI's own | loose, **mrp 0 ok** | own model | **no GST** | own model | — | — |
| POS 'custom-' lines → orders.items | none | none | none | **client-trusted** | n/a | n/a | n/a | n/a |

**Worst concrete examples:** the same hearing aid is 0%-discount-capped if created via PM but 15%-capped via /catalog; a contact lens needs an expiry date on one door and a 'power' value on another; 10,805 TechCherry rows sit in the POS-sellable collection with no product_id, no GST and free-text categories — they sell at POS but are invisible to stock-add and bulk pricing.

## 2. Customer entry — divergence matrix

| Entry path | Creates? | Mobile rule | Dedup | Identity | Store stamp | Skeleton | GSTIN/B2B |
|---|---|---|---|---|---|---|---|
| POST /customers (POS+CRM+Clinic) | yes | full normalize + 6-9 check | mobile/phone + email → 409 | uuid | home+preferred | FULL (consent, loyalty, Self patient) | format + B2B-needs-GSTIN |
| PUT /customers/{id} | edits | **none** | **none** (dup = 500) | — | **any role, any customer** | **cashier can set khata credit_limit** | **none** |
| POST .../patients | patient | normalized | **no dedup** | uuid | — | relation 'Family' | — |
| Walkout | yes | local regex (rejects +91, accepts 0-leading) | mobile/phone | 'cust-'+hex8 | **wrong keys → invisible in store lists** | missing consent/is_active | — |
| Shopify mapper | yes | normalize, **raw fallback** | phone→email, chain-wide | uuid | online bucket | **missing consent/loyalty/patients** | B2C forced |
| ONDC | **NO doc at all** | verbatim | n/a | none | env default | n/a | n/a |
| TechCherry | yes | digits[:15], no check | phone-field only | **phone-as-customer_id; re-import REWRITES identity** | preferred only | missing all | raw |

**Good news verified:** clinic, POS and CRM frontends all already funnel creation through the one strong endpoint (POST /customers). The divergence concentrates in the three non-canonical creators (walkout / Shopify / TechCherry) and the validation-free PUT — so ask #3 ("clinic + POS + online the same") is mostly a back-end consolidation, not a UI rebuild.

## 3. Where online customer data lives (plain English)

1. **Same list, not separate.** A Shopify buyer becomes a record in the very same customer list your stores use — there is no online-customers database. They're tagged source='shopify', channel='ONLINE', with their Shopify ID, homed to the online bucket (BV-ONLINE-01).
2. **Matching is mobile-first, then email, chain-wide.** Someone who shopped in-store and then buys online with the same mobile is recognised as ONE person. It fails for odd phone formats (raw string stored), email-only buyers (a bug creates a "phantom" profile ID that was never saved), and ONDC.
3. **ONDC buyers never become customers.** Name/phone live only on the order. The channel is dark today — and its orders also wouldn't count in P&L revenue (different total field) or get GST invoice serials. Both must be fixed before it is ever enabled.
4. **Online profiles are second-class.** Missing consent flags (and the marketing engine treats missing as consented — a DPDP exposure), missing loyalty/store-credit/patient records, and online purchases never earn loyalty points.
5. **Orders segregate by a channel tag** in the one orders collection ('ONLINE' / 'ONDC'; POS orders carry no tag). Risk: if the online store bucket isn't configured, online sales silently bill under your FIRST physical store — wrong GSTIN, wrong branch books.

## 4. Collections — what exists vs what's missing

**Exists:** full CRUD + manual membership with drag-ordering, a real unit-tested smart-rule engine (AND/OR, equals/contains over brand/category/tag/title/sku/shape/gender/material/colour), a Shopify push for title/SEO/rules, an admin editor.

**Why it's dead today:** (1) migrated BVI rules are in Shopify's {column,relation,condition} shape — the engine reads {field,relation,value}, so every migrated smart collection resolves EMPTY and renders blank in the editor; (2) products have **no tags anywhere** and no write path for them, yet tag rules are the backbone of BVI's generated collections; (3) rules only run in a preview endpoint — membership is never stored, refreshed or counted; (4) rules scan only catalog_products, but the migration brought only VARIANTS — the online catalogue largely isn't in the haystack; in-store catalogue/POS has zero collection awareness; (5) the editor offers 7 operators + price, the engine supports 2 — "price greater than 5000" silently evaluates as "equals 5000"; (6) manual membership/ordering never pushes to Shopify.

## 5. Sequenced unification plan (13 PR-sized steps, smallest risk first)

| # | Step | Risk / gate |
|---|---|---|
| 1 | Index + dedupe backstops (wire dead shopify-order unique index; catalog_products + lens_catalog unique indexes; handle-race 409) | LOW · pure refactor |
| 2 | One phone normalizer everywhere (walkout, TechCherry, Shopify raw→raw_phone) | LOW · pure refactor |
| 3 | Close the unvalidated PUT /customers door + gate credit_limit + patient dedup | LOW-MED · heads-up: khata limits become manager+ |
| 4 | Online-customer parity + phantom-profile fix + explicit consent | MED · **OWNER SIGN-OFF**: online default = not marketable until consent; loyalty on online orders? |
| 5 | Canonical ensure_customer() service consumed by all creators (ask #3 delivered) | LOW-MED · mostly behaviour-preserving |
| 6 | ONDC canonical order + customer (revenue, invoice serial, GST split) before enable | LOW now (dark) · accountant review |
| 7 | TechCherry repair + 10,805-row backfill + dup-customer merge | HIGH · **OWNER SIGN-OFF** + backup + dry-run |
| 8 | One category registry (PM's) for all product writers | MED · **OWNER SIGN-OFF** on required-field list |
| 9 | Canonical product-create service: all three doors delegate to PM; mirror ON (ask #1 delivered) | MED · **OWNER SIGN-OFF** (SKU format, stricter entry) |
| 10 | POS sells from one spine; retire parallel catalog inventory; cap custom- lines; GRN FK check | HIGH · **OWNER SIGN-OFF — touches POS** |
| 11 | Revive migrated smart collections (rule-shape normaliser + brand_name path) | LOW · pure fix |
| 12 | Product tags (governed) + honest rule vocabulary (numeric/price operators) | LOW-MED · owner picks operator set |
| 13 | Materialised collections + auto-refresh + collection browse + Shopify membership push + BVI parent-product migration (ask #2 completed) | MED · live push + migration **OWNER-GATED** |

**Owner decisions needed before work starts:** step 4 (consent default + online loyalty), step 7 (data migration go), step 8 (required fields per category), step 9 (SKU format), step 10 (POS change), step 13 (live Shopify push + BVI product migration). Everything else is pure engineering and can start immediately in order 1→6, 11.

---

*Method note: synthesis over four read-only audit maps (product entry, customer entry, online storage, collections); every claim above is file:line-grounded in the underlying audits, and the six most consequential claims were independently re-verified against the repo before this report was written. No files were modified.*