# IMS 2.0 - RBAC Access Matrix (FOUNDATION / non-enforcing)

> Source of truth: derived from the **current** router enforcement and the live
> `api.main.app.routes` surface. Companion module:
> [`backend/api/services/rbac_policy.py`](../../backend/api/services/rbac_policy.py)
> (the declarative `POLICY` registry + `policy_for` / `check_access` /
> `uncatalogued_routes`). Coverage is locked by
> [`backend/tests/test_rbac_policy.py`](../../backend/tests/test_rbac_policy.py).

**This is a survey + registry, not a change.** No router gate was modified. The
registry mirrors today's behavior exactly so it can later BECOME the single
enforcer with zero behavior change (hardening dim 4).

## How access is enforced today (4 mechanisms)

1. **Per-endpoint dependency** - `Depends(require_roles(...))`, `require_admin`,
   `require_manager`, `require_superadmin`.
2. **Router-level dependency** - `APIRouter(dependencies=[Depends(_require_admin_role)])`
   on `admin` / `admin_catalog` / `admin_extras` (-> ADMIN/SUPERADMIN), and
   `include_router(..., dependencies=[Depends(require_roles(*_FINANCE_ROLES))])` on
   `finance` / `hr` / `payroll` in `api/main.py` (-> ACCOUNTANT/ADMIN/AREA_MANAGER/STORE_MANAGER).
3. **Inline check in the handler body** - e.g. `POS_WRITE_ROLES` on `POST /orders`,
   `_require_superadmin(current_user)` on `GET /audit/verify`.
4. **Implicit** - only `Depends(get_current_user)` (any logged-in user), or nothing
   at all (PUBLIC).

> NOTE: the finance/hr/payroll router-level gate does NOT flatten into a route's
> `dependant`, so it was applied by prefix during the survey. The admin router-level
> gate DOES flatten and was read from the dependency tree.

## Role model

`SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT, CATALOG_MANAGER,
OPTOMETRIST, SALES_CASHIER, SALES_STAFF, CASHIER, WORKSHOP_STAFF` + read-only
**INVESTOR**.

- `SUPERADMIN` passes every role-gated route (mirrors `require_roles`); it is listed
  explicitly in each allow-set so the table is self-contained.
- **INVESTOR** is read-only **app-wide via the `block_investor_writes` middleware**
  in `api/main.py` (403 on every non-safe method, carve-outs for login/logout/refresh/
  change-password). It is therefore NOT a member of any allow-set in this table.
- `store_scoped` (S) = the handler additionally restricts results to the caller's
  store(s) via `validate_store_access` (HQ roles bypass). Orthogonal to the role gate.

## Gate-type summary

| Gate type | Count |
|---|---|
| PUBLIC (no IMS auth) | 31 |
| AUTHENTICATED only (any logged-in user) | 367 |
| Role-gated (one or more roles) | 431 |
| **Total `/api/v1` (method x path)** | **829** |
| ...of which `store_scoped` | 58 |

---

## REVIEW - candidate privilege gaps (AUTHENTICATED-only, sensitivity warrants a gate)

These endpoints are reachable by **any logged-in user** today but mutate financial
value, security tokens, store/storefront config, or expose cross-store data. **No
gate was changed** -- these are flagged for your review. Each was confirmed ungated
by reading the handler (only 404/500/503 guards, no role check).

| Method | Path | Why it's a candidate gap |
|---|---|---|
| `POST` | `/api/v1/customers/{customer_id}/store-credit/add` | Adds store-credit balance (financial liability) with NO role gate. Note the sibling `/store-credit/issue` + `/redeem` ARE gated to ACCOUNTANT/ADMIN/AREA_MANAGER/STORE_MANAGER -- `add` looks like a legacy/duplicate write that escaped the gate. |
| `POST` | `/api/v1/customers/{customer_id}/loyalty/add` | Credits loyalty points (redeemable value) with no role gate. |
| `POST` | `/api/v1/crm/customers/{customer_id}/loyalty-points` | Second loyalty-credit path (crm router), also ungated. |
| `POST` | `/api/v1/loyalty/earn` | Loyalty engine EARN -- mutates a customer's points balance; any authenticated user. Compare `/loyalty/adjust` (ADMIN) and `/loyalty/settings` (SUPERADMIN), which ARE gated. |
| `POST` | `/api/v1/loyalty/redeem` | Loyalty engine REDEEM -- spends points (monetary value) ungated. |
| `POST` | `/api/v1/analytics-v2/loyalty/earn` | Duplicate loyalty EARN under analytics-v2, ungated. |
| `POST` | `/api/v1/analytics-v2/loyalty/redeem` | Duplicate loyalty REDEEM under analytics-v2, ungated. |
| `POST` | `/api/v1/catalog/online-status` | Toggles product online/e-commerce visibility (storefront-facing pricing/availability) with no role gate, while the rest of catalog WRITE is CATALOG_MANAGER/ADMIN. |
| `POST` | `/api/v1/stores/{store_id}/categories/{category}` | Mutates a store's enabled product categories (store config) with no role gate and no store-scope check -- any authenticated user can enable a category on any store. |
| `DELETE` | `/api/v1/stores/{store_id}/categories/{category}` | Disables a store category -- same ungated/unscoped store-config mutation. |
| `POST` | `/api/v1/marketing/notifications/send` | Triggers outbound customer messaging (WhatsApp/SMS) ungated -- a spend + reputational surface that should at least be manager-gated. |
| `GET` | `/api/v1/admin/escalations` | Lives in dashboard_widgets (mounted at bare /api/v1), so it does NOT inherit the admin router's ADMIN/SUPERADMIN gate that every other /api/v1/admin/* row has. Returns up to 20 escalated tasks across ALL stores to any authenticated user. Inconsistent with the admin namespace; candidate for the admin gate. |
| `GET` | `/api/v1/admin/system-health` | Also in dashboard_widgets, ungated -- exposes DB connection status under the /admin namespace to any authenticated user. |
| `POST` | `/api/v1/expenses/advances` | Self-service advance request (uses caller's own user_id, tracks own outstanding). Likely intentional, but there is no amount cap / approval at creation -- flag for review of whether any-amount self-request is acceptable. (Approval/disburse ARE manager-gated.) |
| `GET` | `/api/v1/auth/ecommerce-sso` | Mints an online-store SSO token. 403s only when the caller's role maps to no BVI role (role-derived, not an allow-list). Effectively gated, but the gate lives in `mapped_bvi_role` rather than the RBAC layer -- worth centralizing. |

### Intentionally broad (documented, NOT gaps)

For contrast, these AUTHENTICATED-only routes are intentionally open and should
stay that way:

- **POST /api/v1/orders/{order_id}/items + DELETE .../items/{item_id}** - Any POS user edits a DRAFT order's lines; over-cap discounts are 403'd by the discount-cap check, not a role gate.
- **Most /api/v1/tasks, /workshop/jobs, /follow-ups, /walkouts, /handoffs writes** - Floor-staff operational actions -- intentionally open to any authenticated store user; several add store-scope or ownership checks inside the handler.
- **/api/v1/customers POST/PUT, /customers/{id}/patients** - Customer capture is a front-desk action for any logged-in store user.
- **/api/v1/handoffs/{id} GET/DELETE/respond/reshare/dismiss** - Gated by RESOURCE OWNERSHIP (recipient/uploader) + SUPERADMIN override, not by role class -- correctly catalogued AUTHENTICATED.

---

## Intentional PUBLIC set (no IMS auth -- protected by their own mechanism)

| Method | Path | Protection |
|---|---|---|
| `GET` | `/api/v1/analytics` | static module-info stub (no DB access) |
| `GET` | `/api/v1/analytics/` | static module-info stub (no DB access) |
| `GET` | `/api/v1/catalog` | static module-info stub (no DB access) |
| `GET` | `/api/v1/catalog/` | static module-info stub (no DB access) |
| `GET` | `/api/v1/clinical` | static module-info stub (no DB access) |
| `GET` | `/api/v1/clinical/` | static module-info stub (no DB access) |
| `GET` | `/api/v1/crm` | static module-info stub (no DB access) |
| `GET` | `/api/v1/crm/` | static module-info stub (no DB access) |
| `GET` | `/api/v1/health` | health probe (no data) |
| `GET` | `/api/v1/inventory` | static module-info stub (no DB access) |
| `GET` | `/api/v1/inventory/` | static module-info stub (no DB access) |
| `GET` | `/api/v1/portal/rx` | OTP-minted view-token |
| `GET` | `/api/v1/portal/track/{token}` | tokenized order-tracking link |
| `GET` | `/api/v1/reports` | static module-info stub (no DB access) |
| `GET` | `/api/v1/reports/` | static module-info stub (no DB access) |
| `GET` | `/api/v1/settings` | static module-info stub (no DB access) |
| `GET` | `/api/v1/settings/` | static module-info stub (no DB access) |
| `GET` | `/api/v1/vendor-portal/{token_id}/jobs` | vendor-portal path token |
| `GET` | `/api/v1/vendor-portal/{token_id}/jobs/{job_id}` | vendor-portal path token |
| `GET` | `/api/v1/webhooks/health` | health probe (no data) |
| `GET` | `/api/v1/workshop` | static module-info stub (no DB access) |
| `GET` | `/api/v1/workshop/` | static module-info stub (no DB access) |
| `POST` | `/api/v1/admin/seed-database` | `SEED_SECRET` hmac compare (disabled if unset) |
| `POST` | `/api/v1/auth/login` | username/password + brute-force rate-limit |
| `POST` | `/api/v1/auth/refresh` | valid existing JWT in body |
| `POST` | `/api/v1/portal/rx/request-otp` | issues OTP + rate-limited |
| `POST` | `/api/v1/portal/rx/verify-otp` | OTP verification |
| `POST` | `/api/v1/vendor-portal/{token_id}/jobs/{job_id}/status` | vendor-portal path token |
| `POST` | `/api/v1/webhooks/razorpay` | HMAC signature verify |
| `POST` | `/api/v1/webhooks/shiprocket` | HMAC signature verify |
| `POST` | `/api/v1/webhooks/shopify` | HMAC signature verify |

---

## Full matrix (method x path -> allowed roles)

`PUBLIC` = no auth - `AUTH` = any logged-in user - `S` = store-scoped. Role lists
are the exact current gate (SUPERADMIN always implied).

### `/api/v1/admin`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/admin` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/brands` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/brands` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/admin/brands/{brand_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/brands/{brand_id}` | SUPERADMIN, ADMIN |  |
| `PUT` | `/api/v1/admin/brands/{brand_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/brands/{brand_id}/subbrands` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/brands/{brand_id}/subbrands` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/admin/brands/{brand_id}/subbrands/{subbrand_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/categories` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/categories` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/admin/categories/{category_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/categories/{category_id}` | SUPERADMIN, ADMIN |  |
| `PUT` | `/api/v1/admin/categories/{category_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/discounts/promo-codes` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/discounts/promo-codes` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/admin/discounts/promo-codes/{code_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/discounts/role-caps` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/discounts/role-caps` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/discounts/rules` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/discounts/tier-discounts` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/discounts/tier-discounts` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/escalations` | AUTH |  |
| `GET` | `/api/v1/admin/hsn` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/hsn` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/admin/hsn/{hsn_id}` | SUPERADMIN, ADMIN |  |
| `PUT` | `/api/v1/admin/hsn/{hsn_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/integrations` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/integrations/razorpay` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/razorpay` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/razorpay/test` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/integrations/shiprocket` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/shiprocket` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/shiprocket/create-shipment` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/integrations/shiprocket/rates` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/shiprocket/test` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/integrations/shiprocket/track/{awb}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/integrations/shopify` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/shopify` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/shopify/sync-inventory` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/shopify/sync-orders` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/shopify/test` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/integrations/sms` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/sms` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/integrations/tally` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/tally` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/integrations/tally/exports` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/tally/regenerate` | SUPERADMIN |  |
| `POST` | `/api/v1/admin/integrations/tally/test` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/integrations/tally/voucher.xml` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/integrations/whatsapp` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/whatsapp` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/integrations/whatsapp/test` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/lens/addons` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/lens/addons` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/admin/lens/addons/{addon_id}` | SUPERADMIN, ADMIN |  |
| `PUT` | `/api/v1/admin/lens/addons/{addon_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/lens/brands` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/lens/brands` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/admin/lens/brands/{brand_id}` | SUPERADMIN, ADMIN |  |
| `PUT` | `/api/v1/admin/lens/brands/{brand_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/lens/coatings` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/lens/coatings` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/admin/lens/coatings/{coating_id}` | SUPERADMIN, ADMIN |  |
| `PUT` | `/api/v1/admin/lens/coatings/{coating_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/lens/indices` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/lens/indices` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/admin/lens/indices/{index_id}` | SUPERADMIN, ADMIN |  |
| `PUT` | `/api/v1/admin/lens/indices/{index_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/lens/pricing` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/lens/pricing` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/lens/pricing-ranges` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/lens/pricing-ranges` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/lens/pricing-ranges/bulk` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/lens/pricing-ranges/quote` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/admin/lens/pricing-ranges/{range_id}` | SUPERADMIN, ADMIN |  |
| `PUT` | `/api/v1/admin/lens/pricing-ranges/{range_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/products` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/products/bulk-import` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/products/bulk-import/{job_id}/file` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/products/generate-sku` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/products/{product_id}` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/seed-database` | PUBLIC |  |
| `GET` | `/api/v1/admin/system-health` | AUTH |  |
| `GET` | `/api/v1/admin/system/audit-logs` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/system/backups` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/system/backups` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/system/backups/{backup_id}/restore` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/system/export/{export_type}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/system/settings` | SUPERADMIN, ADMIN |  |
| `PUT` | `/api/v1/admin/system/settings` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/admin/system/status` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/admin/techcherry/import` | SUPERADMIN |  |
| `GET` | `/api/v1/admin/techcherry/status` | SUPERADMIN |  |

### `/api/v1/analytics`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/analytics` | PUBLIC |  |
| `GET` | `/api/v1/analytics/` | PUBLIC |  |
| `GET` | `/api/v1/analytics/customer-insights` | AUTH |  |
| `GET` | `/api/v1/analytics/dashboard-summary` | AUTH |  |
| `GET` | `/api/v1/analytics/enterprise-kpis` | AUTH |  |
| `GET` | `/api/v1/analytics/inventory-intelligence` | AUTH |  |
| `GET` | `/api/v1/analytics/revenue-trends` | AUTH |  |
| `GET` | `/api/v1/analytics/store-performance` | AUTH |  |
| `GET` | `/api/v1/analytics/store-target-today` | AUTH |  |

### `/api/v1/analytics-v2`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/analytics-v2/anomaly-detection` | SUPERADMIN |  |
| `GET` | `/api/v1/analytics-v2/churn-prediction` | SUPERADMIN |  |
| `POST` | `/api/v1/analytics-v2/cl-subscription/reminder/{customer_id}` | AUTH |  |
| `GET` | `/api/v1/analytics-v2/cl-subscriptions` | AUTH |  |
| `GET` | `/api/v1/analytics-v2/dead-stock` | AUTH |  |
| `GET` | `/api/v1/analytics-v2/demand-forecast` | SUPERADMIN |  |
| `GET` | `/api/v1/analytics-v2/discount-analysis` | AUTH |  |
| `GET` | `/api/v1/analytics-v2/eye-camps` | AUTH |  |
| `POST` | `/api/v1/analytics-v2/eye-camps` | AUTH |  |
| `PATCH` | `/api/v1/analytics-v2/eye-camps/{camp_id}` | AUTH |  |
| `GET` | `/api/v1/analytics-v2/family-deals` | AUTH |  |
| `POST` | `/api/v1/analytics-v2/loyalty/earn` | AUTH |  |
| `POST` | `/api/v1/analytics-v2/loyalty/redeem` | AUTH |  |
| `GET` | `/api/v1/analytics-v2/loyalty/tiers` | AUTH |  |
| `GET` | `/api/v1/analytics-v2/staff-leaderboard` | AUTH |  |
| `GET` | `/api/v1/analytics-v2/vendor-margins` | SUPERADMIN |  |

### `/api/v1/audit`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/audit/verify` | SUPERADMIN |  |

### `/api/v1/auth`

| Method | Path | Allowed | S |
|---|---|---|---|
| `POST` | `/api/v1/auth/change-password` | AUTH |  |
| `GET` | `/api/v1/auth/ecommerce-sso` | AUTH |  |
| `POST` | `/api/v1/auth/login` | PUBLIC |  |
| `POST` | `/api/v1/auth/logout` | AUTH |  |
| `GET` | `/api/v1/auth/me` | AUTH |  |
| `POST` | `/api/v1/auth/refresh` | PUBLIC |  |
| `POST` | `/api/v1/auth/switch-store/{store_id}` | AUTH |  |

### `/api/v1/catalog`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/catalog` | PUBLIC |  |
| `GET` | `/api/v1/catalog/` | PUBLIC |  |
| `GET` | `/api/v1/catalog/brands` | AUTH |  |
| `GET` | `/api/v1/catalog/categories` | AUTH |  |
| `GET` | `/api/v1/catalog/categories/{category}/fields` | AUTH |  |
| `GET` | `/api/v1/catalog/online-status` | AUTH |  |
| `POST` | `/api/v1/catalog/online-status` | AUTH |  |
| `GET` | `/api/v1/catalog/online-stock-reconcile` | AUTH |  |
| `GET` | `/api/v1/catalog/online-summary` | AUTH |  |
| `GET` | `/api/v1/catalog/price-change-requests` | AUTH |  |
| `GET` | `/api/v1/catalog/products` | AUTH |  |
| `POST` | `/api/v1/catalog/products` | SUPERADMIN, ADMIN, CATALOG_MANAGER |  |
| `POST` | `/api/v1/catalog/products/bulk-sync-shopify` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/catalog/products/export` | SUPERADMIN, ADMIN, CATALOG_MANAGER |  |
| `POST` | `/api/v1/catalog/products/import` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/catalog/products/{product_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/catalog/products/{product_id}` | AUTH |  |
| `PUT` | `/api/v1/catalog/products/{product_id}` | SUPERADMIN, ADMIN, CATALOG_MANAGER |  |
| `GET` | `/api/v1/catalog/products/{product_id}/inventory` | AUTH |  |
| `POST` | `/api/v1/catalog/products/{product_id}/inventory/adjust` | SUPERADMIN, ADMIN, STORE_MANAGER, WORKSHOP_STAFF |  |
| `POST` | `/api/v1/catalog/products/{product_id}/sync-shopify` | SUPERADMIN, ADMIN, CATALOG_MANAGER |  |
| `GET` | `/api/v1/catalog/recent-activity` | AUTH |  |
| `POST` | `/api/v1/catalog/reconcile-store-barcodes` | SUPERADMIN |  |
| `GET` | `/api/v1/catalog/sku-counts` | AUTH |  |

### `/api/v1/catalog-autopilot`

| Method | Path | Allowed | S |
|---|---|---|---|
| `POST` | `/api/v1/catalog-autopilot/candidates/{candidate_id}/decision` | SUPERADMIN, ADMIN, CATALOG_MANAGER |  |
| `GET` | `/api/v1/catalog-autopilot/jobs` | SUPERADMIN, ADMIN, CATALOG_MANAGER |  |
| `POST` | `/api/v1/catalog-autopilot/jobs` | SUPERADMIN, ADMIN, CATALOG_MANAGER |  |
| `GET` | `/api/v1/catalog-autopilot/jobs/{job_id}` | SUPERADMIN, ADMIN, CATALOG_MANAGER |  |
| `GET` | `/api/v1/catalog-autopilot/sources` | SUPERADMIN, ADMIN, CATALOG_MANAGER |  |

### `/api/v1/clinical`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/clinical` | PUBLIC |  |
| `GET` | `/api/v1/clinical/` | PUBLIC |  |
| `GET` | `/api/v1/clinical/abuse-detection` | ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `GET` | `/api/v1/clinical/eye-tests` | AUTH |  |
| `GET` | `/api/v1/clinical/optometrist/{optometrist_id}/stats` | AUTH |  |
| `GET` | `/api/v1/clinical/patient-queue` | AUTH |  |
| `GET` | `/api/v1/clinical/prescription-redo-rate` | AUTH |  |
| `GET` | `/api/v1/clinical/prescriptions/{prescription_id}/print` | AUTH |  |
| `POST` | `/api/v1/clinical/prescriptions/{prescription_id}/redo` | ADMIN, AREA_MANAGER, STORE_MANAGER, OPTOMETRIST |  |
| `GET` | `/api/v1/clinical/prescriptions/{prescription_id}/redos` | AUTH |  |
| `GET` | `/api/v1/clinical/queue` | AUTH |  |
| `POST` | `/api/v1/clinical/queue` | ADMIN, STORE_MANAGER, OPTOMETRIST |  |
| `GET` | `/api/v1/clinical/queue/stats` | AUTH |  |
| `DELETE` | `/api/v1/clinical/queue/{queue_id}` | ADMIN, STORE_MANAGER, OPTOMETRIST |  |
| `POST` | `/api/v1/clinical/queue/{queue_id}/start-test` | ADMIN, STORE_MANAGER, OPTOMETRIST |  |
| `PATCH` | `/api/v1/clinical/queue/{queue_id}/status` | ADMIN, STORE_MANAGER, OPTOMETRIST |  |
| `GET` | `/api/v1/clinical/tests` | AUTH |  |
| `GET` | `/api/v1/clinical/tests/customer/{customer_id}` | AUTH |  |
| `GET` | `/api/v1/clinical/tests/patient/{customer_phone}` | AUTH |  |
| `GET` | `/api/v1/clinical/tests/{test_id}` | AUTH |  |
| `POST` | `/api/v1/clinical/tests/{test_id}/complete` | ADMIN, STORE_MANAGER, OPTOMETRIST |  |

### `/api/v1/crm`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/crm` | PUBLIC |  |
| `GET` | `/api/v1/crm/` | PUBLIC |  |
| `GET` | `/api/v1/crm/customers/360/{customer_id}` | AUTH |  |
| `GET` | `/api/v1/crm/customers/churn-risk/list` | AUTH |  |
| `GET` | `/api/v1/crm/customers/segment/rfm` | AUTH |  |
| `GET` | `/api/v1/crm/customers/{customer_id}/interactions` | AUTH |  |
| `POST` | `/api/v1/crm/customers/{customer_id}/interactions` | AUTH |  |
| `GET` | `/api/v1/crm/customers/{customer_id}/lifecycle` | AUTH |  |
| `POST` | `/api/v1/crm/customers/{customer_id}/loyalty-points` | AUTH |  |
| `GET` | `/api/v1/crm/customers/{customer_id}/prescriptions` | AUTH |  |

### `/api/v1/customers`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/customers` | AUTH |  |
| `POST` | `/api/v1/customers` | AUTH |  |
| `GET` | `/api/v1/customers/mobile/{mobile}` | AUTH |  |
| `GET` | `/api/v1/customers/search` | AUTH |  |
| `GET` | `/api/v1/customers/search/phone` | AUTH |  |
| `GET` | `/api/v1/customers/{customer_id}` | AUTH |  |
| `PUT` | `/api/v1/customers/{customer_id}` | AUTH |  |
| `POST` | `/api/v1/customers/{customer_id}/loyalty/add` | AUTH |  |
| `GET` | `/api/v1/customers/{customer_id}/orders` | AUTH |  |
| `POST` | `/api/v1/customers/{customer_id}/patients` | AUTH |  |
| `GET` | `/api/v1/customers/{customer_id}/prescriptions` | AUTH |  |
| `POST` | `/api/v1/customers/{customer_id}/store-credit/add` | AUTH |  |
| `POST` | `/api/v1/customers/{customer_id}/store-credit/issue` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/customers/{customer_id}/store-credit/ledger` | AUTH |  |
| `POST` | `/api/v1/customers/{customer_id}/store-credit/redeem` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |

### `/api/v1/display-fixtures`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/display-fixtures` | AUTH | S |
| `POST` | `/api/v1/display-fixtures` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |
| `GET` | `/api/v1/display-fixtures/` | AUTH | S |
| `POST` | `/api/v1/display-fixtures/` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |
| `GET` | `/api/v1/display-fixtures/meta/options` | AUTH |  |
| `DELETE` | `/api/v1/display-fixtures/{fixture_id}` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |
| `GET` | `/api/v1/display-fixtures/{fixture_id}` | AUTH | S |
| `PATCH` | `/api/v1/display-fixtures/{fixture_id}` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |

### `/api/v1/display-placements`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/display-placements` | AUTH | S |
| `POST` | `/api/v1/display-placements` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |
| `GET` | `/api/v1/display-placements/` | AUTH | S |
| `POST` | `/api/v1/display-placements/` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |
| `POST` | `/api/v1/display-placements/move` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |
| `DELETE` | `/api/v1/display-placements/{placement_id}` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |
| `GET` | `/api/v1/display-placements/{placement_id}` | AUTH | S |
| `PATCH` | `/api/v1/display-placements/{placement_id}` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |

### `/api/v1/entities`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/entities` | AUTH |  |
| `POST` | `/api/v1/entities` | ADMIN |  |
| `GET` | `/api/v1/entities/` | AUTH |  |
| `POST` | `/api/v1/entities/` | ADMIN |  |
| `GET` | `/api/v1/entities/meta/options` | AUTH |  |
| `GET` | `/api/v1/entities/{entity_id}` | AUTH |  |
| `PUT` | `/api/v1/entities/{entity_id}` | ADMIN |  |
| `GET` | `/api/v1/entities/{entity_id}/stores` | AUTH |  |
| `DELETE` | `/api/v1/entities/{entity_id}/stores/{store_id}` | ADMIN |  |
| `POST` | `/api/v1/entities/{entity_id}/stores/{store_id}` | ADMIN |  |

### `/api/v1/expenses`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/expenses` | AUTH |  |
| `POST` | `/api/v1/expenses` | AUTH |  |
| `GET` | `/api/v1/expenses/` | AUTH |  |
| `POST` | `/api/v1/expenses/` | AUTH |  |
| `GET` | `/api/v1/expenses/advances` | AUTH |  |
| `POST` | `/api/v1/expenses/advances` | AUTH |  |
| `POST` | `/api/v1/expenses/advances/{advance_id}/approve` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/expenses/advances/{advance_id}/disburse` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/expenses/advances/{advance_id}/settle` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/expenses/aging` | ADMIN, ACCOUNTANT |  |
| `GET` | `/api/v1/expenses/caps` | AUTH |  |
| `PUT` | `/api/v1/expenses/caps` | ADMIN |  |
| `GET` | `/api/v1/expenses/duplicate-bills` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/expenses/pending-approval` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/expenses/to-enter` | ADMIN, ACCOUNTANT |  |
| `POST` | `/api/v1/expenses/{expense_id}/approve` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/expenses/{expense_id}/bill` | AUTH |  |
| `POST` | `/api/v1/expenses/{expense_id}/mark-entered` | ADMIN, ACCOUNTANT |  |
| `POST` | `/api/v1/expenses/{expense_id}/reject` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/expenses/{expense_id}/send-to-accountant` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/expenses/{expense_id}/submit` | AUTH |  |
| `POST` | `/api/v1/expenses/{expense_id}/upload-bill` | AUTH |  |

### `/api/v1/finance`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/finance/budget` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/cash-flow` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/cash-flow-forecast` | SUPERADMIN, ADMIN, ACCOUNTANT |  |
| `POST` | `/api/v1/finance/cash-register/close` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |
| `POST` | `/api/v1/finance/cash-register/open` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |
| `GET` | `/api/v1/finance/cash-register/sessions` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |
| `GET` | `/api/v1/finance/gst-status` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/gst/reconciliation` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/gst/summary` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/finance/gstr2b-reconcile` | SUPERADMIN, ADMIN, ACCOUNTANT |  |
| `POST` | `/api/v1/finance/itc-export` | SUPERADMIN, ADMIN, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/itc-register` | SUPERADMIN, ADMIN, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/outstanding` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/owner-dashboard` | SUPERADMIN, ADMIN, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/pending-reconciliations` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/finance/period-lock` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/period-locks` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/period-status` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/pnl` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/pnl/by-category` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/pnl/by-store` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/reconciliation` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/revenue` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/summary-month` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/tally/sales-jv` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/finance/vendor-payments` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |

### `/api/v1/follow-ups`

| Method | Path | Allowed | S |
|---|---|---|---|
| `POST` | `/api/v1/follow-ups` | AUTH |  |
| `GET` | `/api/v1/follow-ups/` | AUTH |  |
| `POST` | `/api/v1/follow-ups/` | AUTH |  |
| `POST` | `/api/v1/follow-ups/auto-generate` | AUTH |  |
| `GET` | `/api/v1/follow-ups/due-today` | AUTH |  |
| `GET` | `/api/v1/follow-ups/summary` | AUTH |  |
| `PATCH` | `/api/v1/follow-ups/{follow_up_id}/complete` | AUTH |  |

### `/api/v1/handoffs`

| Method | Path | Allowed | S |
|---|---|---|---|
| `POST` | `/api/v1/handoffs` | AUTH |  |
| `POST` | `/api/v1/handoffs/` | AUTH |  |
| `GET` | `/api/v1/handoffs/eligible-recipients/list` | AUTH |  |
| `GET` | `/api/v1/handoffs/inbox` | AUTH |  |
| `GET` | `/api/v1/handoffs/sent` | AUTH |  |
| `DELETE` | `/api/v1/handoffs/{handoff_id}` | AUTH |  |
| `GET` | `/api/v1/handoffs/{handoff_id}` | AUTH |  |
| `POST` | `/api/v1/handoffs/{handoff_id}/dismiss` | AUTH |  |
| `GET` | `/api/v1/handoffs/{handoff_id}/file` | AUTH |  |
| `POST` | `/api/v1/handoffs/{handoff_id}/reshare` | AUTH |  |
| `POST` | `/api/v1/handoffs/{handoff_id}/respond` | AUTH |  |

### `/api/v1/health`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/health` | PUBLIC |  |

### `/api/v1/hr`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/hr` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/hr/` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/hr/attendance` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/hr/attendance-compliance` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/hr/attendance/check-in` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/hr/attendance/check-out` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/hr/attendance/grid` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |
| `GET` | `/api/v1/hr/attendance/late-marks` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |
| `POST` | `/api/v1/hr/attendance/mark` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/hr/attendance/{attendance_id}/check-out` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/hr/employee/{employee_id}/salary-slip` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/hr/leaves` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/hr/leaves` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/hr/leaves/balance/{employee_id}` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/hr/leaves/{leave_id}/approve` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/hr/leaves/{leave_id}/reject` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/hr/payroll` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/hr/payroll/generate` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/hr/payroll/{payroll_id}/approve` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/hr/reports/lwp` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |
| `GET` | `/api/v1/hr/shifts` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |
| `POST` | `/api/v1/hr/shifts` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |
| `POST` | `/api/v1/hr/shifts/assign` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |
| `GET` | `/api/v1/hr/summary-today` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/hr/weekoff-swaps` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/hr/weekoff-swaps` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/hr/weekoff-swaps/{swap_id}/approve` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |
| `POST` | `/api/v1/hr/weekoff-swaps/{swap_id}/reject` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |

### `/api/v1/incentive`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/incentive/points/daily` | AUTH |  |
| `POST` | `/api/v1/incentive/points/daily` | AUTH |  |
| `GET` | `/api/v1/incentive/points/daily/` | AUTH |  |
| `POST` | `/api/v1/incentive/points/daily/` | AUTH |  |
| `POST` | `/api/v1/incentive/points/daily/bulk` | AUTH |  |
| `DELETE` | `/api/v1/incentive/points/daily/{log_id}` | SUPERADMIN, STORE_MANAGER |  |
| `POST` | `/api/v1/incentive/points/inputs/last-year-sale` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/incentive/points/leaderboard` | AUTH |  |
| `GET` | `/api/v1/incentive/points/mtd` | AUTH |  |
| `GET` | `/api/v1/incentive/points/settings/eligibility` | AUTH |  |
| `PATCH` | `/api/v1/incentive/points/settings/eligibility` | SUPERADMIN |  |
| `PATCH` | `/api/v1/incentive/points/settings/payout` | SUPERADMIN |  |
| `PATCH` | `/api/v1/incentive/points/settings/visufit-gate` | SUPERADMIN |  |
| `GET` | `/api/v1/incentive/points/staff/{staff_id}/history` | AUTH |  |

### `/api/v1/inventory`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/inventory` | PUBLIC |  |
| `GET` | `/api/v1/inventory/` | PUBLIC |  |
| `GET` | `/api/v1/inventory/accountability` | ADMIN, AREA_MANAGER, STORE_MANAGER, CATALOG_MANAGER, WORKSHOP_STAFF |  |
| `POST` | `/api/v1/inventory/accountability` | ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `GET` | `/api/v1/inventory/accountability/shrinkage` | ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `GET` | `/api/v1/inventory/aging` | AUTH | S |
| `GET` | `/api/v1/inventory/alerts` | AUTH | S |
| `GET` | `/api/v1/inventory/barcode/{barcode}` | AUTH |  |
| `GET` | `/api/v1/inventory/contact-lenses` | AUTH |  |
| `GET` | `/api/v1/inventory/contact-lenses/expiry-status` | AUTH |  |
| `GET` | `/api/v1/inventory/contact-lenses/power-grid` | AUTH |  |
| `GET` | `/api/v1/inventory/expiring` | AUTH |  |
| `GET` | `/api/v1/inventory/lenses/power-grid` | AUTH |  |
| `GET` | `/api/v1/inventory/low-stock` | AUTH | S |
| `GET` | `/api/v1/inventory/non-moving` | AUTH |  |
| `GET` | `/api/v1/inventory/overstock-analysis` | AUTH |  |
| `GET` | `/api/v1/inventory/sell-through-analysis` | AUTH |  |
| `GET` | `/api/v1/inventory/serials` | AUTH | S |
| `POST` | `/api/v1/inventory/serials` | ADMIN, AREA_MANAGER, STORE_MANAGER, CATALOG_MANAGER, WORKSHOP_STAFF | S |
| `PATCH` | `/api/v1/inventory/serials/{serial_id}` | ADMIN, AREA_MANAGER, STORE_MANAGER, CATALOG_MANAGER, WORKSHOP_STAFF |  |
| `GET` | `/api/v1/inventory/stock` | AUTH | S |
| `GET` | `/api/v1/inventory/stock-count` | AUTH | S |
| `POST` | `/api/v1/inventory/stock-count-scan` | ADMIN, AREA_MANAGER, STORE_MANAGER, CATALOG_MANAGER, WORKSHOP_STAFF |  |
| `GET` | `/api/v1/inventory/stock-count-status` | AUTH |  |
| `POST` | `/api/v1/inventory/stock-count/start` | ADMIN, AREA_MANAGER, STORE_MANAGER, CATALOG_MANAGER, WORKSHOP_STAFF | S |
| `GET` | `/api/v1/inventory/stock-count/{count_id}` | AUTH |  |
| `POST` | `/api/v1/inventory/stock-count/{count_id}/complete` | ADMIN, AREA_MANAGER, STORE_MANAGER, CATALOG_MANAGER, WORKSHOP_STAFF |  |
| `POST` | `/api/v1/inventory/stock-count/{count_id}/items` | ADMIN, AREA_MANAGER, STORE_MANAGER, CATALOG_MANAGER, WORKSHOP_STAFF |  |
| `POST` | `/api/v1/inventory/stock/add` | ADMIN, AREA_MANAGER, STORE_MANAGER, CATALOG_MANAGER, WORKSHOP_STAFF |  |
| `GET` | `/api/v1/inventory/stock/barcode/{barcode}` | AUTH |  |
| `GET` | `/api/v1/inventory/transfer-recommendations` | ADMIN, AREA_MANAGER, STORE_MANAGER, CATALOG_MANAGER, WORKSHOP_STAFF |  |
| `GET` | `/api/v1/inventory/transfers` | AUTH |  |
| `POST` | `/api/v1/inventory/transfers` | ADMIN, AREA_MANAGER, STORE_MANAGER, CATALOG_MANAGER, WORKSHOP_STAFF |  |
| `POST` | `/api/v1/inventory/transfers/{transfer_id}/receive` | ADMIN, AREA_MANAGER, STORE_MANAGER, CATALOG_MANAGER, WORKSHOP_STAFF |  |
| `POST` | `/api/v1/inventory/transfers/{transfer_id}/send` | ADMIN, AREA_MANAGER, STORE_MANAGER, CATALOG_MANAGER, WORKSHOP_STAFF |  |

### `/api/v1/jarvis`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/jarvis` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/agents` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/agents/activity` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/agents/diagnostic` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/agents/health-history` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/agents/pixel/audits` | SUPERADMIN |  |
| `POST` | `/api/v1/jarvis/agents/reseed` | SUPERADMIN |  |
| `POST` | `/api/v1/jarvis/agents/run-all` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/agents/sentinel/health` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/agents/timeline` | SUPERADMIN |  |
| `PATCH` | `/api/v1/jarvis/agents/{agent_id}/config` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/agents/{agent_id}/logs` | SUPERADMIN |  |
| `POST` | `/api/v1/jarvis/agents/{agent_id}/run` | SUPERADMIN |  |
| `POST` | `/api/v1/jarvis/agents/{agent_id}/run-now` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/agents/{agent_id}/status` | SUPERADMIN |  |
| `PATCH` | `/api/v1/jarvis/agents/{agent_id}/toggle` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/alerts` | SUPERADMIN |  |
| `POST` | `/api/v1/jarvis/analyze` | SUPERADMIN |  |
| `POST` | `/api/v1/jarvis/command` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/dashboard` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/data/collections` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/data/{collection}` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/integrations/status` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/models` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/proposals` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/proposals/{proposal_id}` | SUPERADMIN |  |
| `POST` | `/api/v1/jarvis/proposals/{proposal_id}/approve` | SUPERADMIN |  |
| `POST` | `/api/v1/jarvis/proposals/{proposal_id}/reject` | SUPERADMIN |  |
| `POST` | `/api/v1/jarvis/query` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/quick-insights` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/recommendations` | SUPERADMIN |  |
| `GET` | `/api/v1/jarvis/status` | SUPERADMIN |  |

### `/api/v1/lens-catalog`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/lens-catalog` | AUTH |  |
| `POST` | `/api/v1/lens-catalog` | ADMIN, CATALOG_MANAGER |  |
| `GET` | `/api/v1/lens-catalog/` | AUTH |  |
| `POST` | `/api/v1/lens-catalog/` | ADMIN, CATALOG_MANAGER |  |
| `GET` | `/api/v1/lens-catalog/meta/options` | AUTH |  |
| `DELETE` | `/api/v1/lens-catalog/{lens_line_id}` | ADMIN, CATALOG_MANAGER |  |
| `GET` | `/api/v1/lens-catalog/{lens_line_id}` | AUTH |  |
| `PATCH` | `/api/v1/lens-catalog/{lens_line_id}` | ADMIN, CATALOG_MANAGER |  |

### `/api/v1/lens-enums`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/lens-enums` | AUTH |  |
| `GET` | `/api/v1/lens-enums/` | AUTH |  |
| `GET` | `/api/v1/lens-enums/{enum_type}` | AUTH |  |
| `PATCH` | `/api/v1/lens-enums/{enum_type}` | ADMIN, CATALOG_MANAGER |  |
| `POST` | `/api/v1/lens-enums/{enum_type}/items` | ADMIN, CATALOG_MANAGER |  |
| `DELETE` | `/api/v1/lens-enums/{enum_type}/items/{item}` | ADMIN, CATALOG_MANAGER |  |
| `POST` | `/api/v1/lens-enums/{enum_type}/rename` | ADMIN, CATALOG_MANAGER |  |

### `/api/v1/lens-stock`

| Method | Path | Allowed | S |
|---|---|---|---|
| `POST` | `/api/v1/lens-stock` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |
| `POST` | `/api/v1/lens-stock/` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |
| `GET` | `/api/v1/lens-stock/audit/{line_stock_id}` | AUTH |  |
| `GET` | `/api/v1/lens-stock/cell/{line_stock_id}` | AUTH | S |
| `GET` | `/api/v1/lens-stock/gap-planner` | AUTH | S |
| `GET` | `/api/v1/lens-stock/{lens_line_id}` | AUTH | S |
| `POST` | `/api/v1/lens-stock/{lens_line_id}/bulk-import` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |
| `POST` | `/api/v1/lens-stock/{lens_line_id}/commit` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |
| `POST` | `/api/v1/lens-stock/{lens_line_id}/release` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |
| `POST` | `/api/v1/lens-stock/{lens_line_id}/reserve` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |
| `PATCH` | `/api/v1/lens-stock/{line_stock_id}` | ADMIN, STORE_MANAGER, CATALOG_MANAGER | S |

### `/api/v1/loyalty`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/loyalty/account/{customer_id}` | AUTH |  |
| `GET` | `/api/v1/loyalty/account/{customer_id}/ledger` | AUTH |  |
| `POST` | `/api/v1/loyalty/adjust` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/loyalty/earn` | AUTH |  |
| `POST` | `/api/v1/loyalty/expire` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/loyalty/program-stats` | AUTH |  |
| `POST` | `/api/v1/loyalty/redeem` | AUTH |  |
| `GET` | `/api/v1/loyalty/settings` | AUTH |  |
| `PUT` | `/api/v1/loyalty/settings` | SUPERADMIN |  |

### `/api/v1/marketing`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/marketing/notifications/logs` | AUTH |  |
| `POST` | `/api/v1/marketing/notifications/send` | AUTH |  |
| `POST` | `/api/v1/marketing/notifications/send-bulk` | ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `GET` | `/api/v1/marketing/nps-dashboard` | AUTH |  |
| `POST` | `/api/v1/marketing/nps-response` | AUTH |  |
| `POST` | `/api/v1/marketing/nps-survey/{order_id}` | AUTH |  |
| `POST` | `/api/v1/marketing/referral-invite/{customer_id}` | AUTH |  |
| `GET` | `/api/v1/marketing/referrals` | AUTH |  |
| `POST` | `/api/v1/marketing/referrals/{referral_id}/redeem` | AUTH |  |
| `POST` | `/api/v1/marketing/review-request/{order_id}` | AUTH |  |
| `GET` | `/api/v1/marketing/rx-expiry-alerts` | AUTH |  |
| `POST` | `/api/v1/marketing/rx-reminder/{customer_id}` | AUTH |  |
| `POST` | `/api/v1/marketing/rx-snooze/{customer_id}` | AUTH |  |
| `POST` | `/api/v1/marketing/walkin` | AUTH |  |
| `GET` | `/api/v1/marketing/walkins` | AUTH |  |
| `GET` | `/api/v1/marketing/walkout-recoveries` | AUTH |  |
| `POST` | `/api/v1/marketing/walkout/{customer_id}` | AUTH |  |

### `/api/v1/notifications`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/notifications` | AUTH |  |
| `GET` | `/api/v1/notifications/` | AUTH |  |
| `POST` | `/api/v1/notifications/mark-all-read` | AUTH |  |
| `GET` | `/api/v1/notifications/unread-count` | AUTH |  |
| `PATCH` | `/api/v1/notifications/{notification_id}/read` | AUTH |  |
| `POST` | `/api/v1/notifications/{notification_id}/snooze` | AUTH |  |

### `/api/v1/orders`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/orders` | AUTH | S |
| `POST` | `/api/v1/orders` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, SALES_CASHIER, SALES_STAFF |  |
| `GET` | `/api/v1/orders/overdue/list` | AUTH | S |
| `GET` | `/api/v1/orders/pending/delivery` | AUTH | S |
| `GET` | `/api/v1/orders/sales/summary` | AUTH | S |
| `GET` | `/api/v1/orders/search` | AUTH | S |
| `GET` | `/api/v1/orders/status/counts` | AUTH | S |
| `GET` | `/api/v1/orders/unpaid/list` | AUTH | S |
| `GET` | `/api/v1/orders/{order_id}` | AUTH | S |
| `PUT` | `/api/v1/orders/{order_id}` | AUTH |  |
| `POST` | `/api/v1/orders/{order_id}/cancel` | AUTH |  |
| `POST` | `/api/v1/orders/{order_id}/confirm` | AUTH |  |
| `POST` | `/api/v1/orders/{order_id}/deliver` | AUTH |  |
| `GET` | `/api/v1/orders/{order_id}/invoice` | AUTH |  |
| `POST` | `/api/v1/orders/{order_id}/items` | AUTH |  |
| `DELETE` | `/api/v1/orders/{order_id}/items/{item_id}` | AUTH |  |
| `POST` | `/api/v1/orders/{order_id}/payments` | AUTH |  |
| `POST` | `/api/v1/orders/{order_id}/ready` | AUTH |  |

### `/api/v1/payout`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/payout/export/{snapshot_id}.csv` | AUTH |  |
| `POST` | `/api/v1/payout/lock` | SUPERADMIN |  |
| `GET` | `/api/v1/payout/preview` | AUTH |  |
| `GET` | `/api/v1/payout/snapshot/{snapshot_id}` | AUTH |  |
| `PATCH` | `/api/v1/payout/snapshot/{snapshot_id}/mark-paid` | SUPERADMIN |  |
| `GET` | `/api/v1/payout/snapshots` | AUTH |  |

### `/api/v1/payroll`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/payroll` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/payroll/advances` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/payroll/advances/{advance_id}/settle` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/advances/{employee_id}` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/payroll/approve` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/config` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/payroll/config` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/payroll/config/bulk` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/config/{employee_id}` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `PUT` | `/api/v1/payroll/config/{employee_id}` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/incentive-summary/{employee_id}/{month}/{year}` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/payroll/lock` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/payslip/{employee_id}` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/payslip/{employee_id}/{month}/{year}` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/payslip/{employee_id}/{month}/{year}/print` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/pt-slabs` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/payroll/pt-slabs/seed` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/pt-slabs/{state_code}` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `PUT` | `/api/v1/payroll/pt-slabs/{state_code}` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/registers/pf-ecr` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/registers/summary` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/payroll/run` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/run/rows` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/salary-sheet` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/payroll/salary/calculate` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/salary/{employee_id}` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/payroll/tally/salary-jv` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |

### `/api/v1/portal`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/portal/rx` | PUBLIC |  |
| `POST` | `/api/v1/portal/rx/request-otp` | PUBLIC |  |
| `POST` | `/api/v1/portal/rx/verify-otp` | PUBLIC |  |
| `GET` | `/api/v1/portal/track/{token}` | PUBLIC |  |

### `/api/v1/prescriptions`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/prescriptions` | AUTH |  |
| `POST` | `/api/v1/prescriptions` | SUPERADMIN, ADMIN, STORE_MANAGER, OPTOMETRIST |  |
| `GET` | `/api/v1/prescriptions/` | AUTH |  |
| `POST` | `/api/v1/prescriptions/` | SUPERADMIN, ADMIN, STORE_MANAGER, OPTOMETRIST |  |
| `GET` | `/api/v1/prescriptions/customer/{customer_id}/progression` | AUTH |  |
| `GET` | `/api/v1/prescriptions/expiring` | AUTH |  |
| `GET` | `/api/v1/prescriptions/optometrist/{optometrist_id}/stats` | AUTH |  |
| `GET` | `/api/v1/prescriptions/patient/{patient_id}` | AUTH |  |
| `GET` | `/api/v1/prescriptions/patient/{patient_id}/latest` | AUTH |  |
| `GET` | `/api/v1/prescriptions/patient/{patient_id}/valid` | AUTH |  |
| `GET` | `/api/v1/prescriptions/{prescription_id}` | AUTH |  |
| `POST` | `/api/v1/prescriptions/{prescription_id}/finalize` | SUPERADMIN, ADMIN, OPTOMETRIST |  |
| `GET` | `/api/v1/prescriptions/{prescription_id}/print` | AUTH |  |
| `GET` | `/api/v1/prescriptions/{prescription_id}/validate` | AUTH |  |
| `PATCH` | `/api/v1/prescriptions/{prescription_id}/version/{version_name}` | AUTH |  |
| `GET` | `/api/v1/prescriptions/{prescription_id}/versions` | AUTH |  |

### `/api/v1/print`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/print/qz/cert` | AUTH |  |
| `POST` | `/api/v1/print/qz/sign` | AUTH |  |

### `/api/v1/print-overrides`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/print-overrides` | AUTH |  |
| `GET` | `/api/v1/print-overrides/_meta/templates` | AUTH |  |
| `DELETE` | `/api/v1/print-overrides/{entity_id}/{template_key}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/print-overrides/{entity_id}/{template_key}` | AUTH |  |
| `PUT` | `/api/v1/print-overrides/{entity_id}/{template_key}` | SUPERADMIN, ADMIN |  |

### `/api/v1/product-templates`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/product-templates` | ADMIN, CATALOG_MANAGER |  |
| `POST` | `/api/v1/product-templates` | ADMIN, CATALOG_MANAGER |  |
| `GET` | `/api/v1/product-templates/` | ADMIN, CATALOG_MANAGER |  |
| `POST` | `/api/v1/product-templates/` | ADMIN, CATALOG_MANAGER |  |
| `DELETE` | `/api/v1/product-templates/{template_id}` | ADMIN, CATALOG_MANAGER |  |

### `/api/v1/products`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/products` | AUTH |  |
| `POST` | `/api/v1/products` | ADMIN, CATALOG_MANAGER |  |
| `GET` | `/api/v1/products/brands/list` | AUTH |  |
| `POST` | `/api/v1/products/bulk-create` | ADMIN, CATALOG_MANAGER |  |
| `POST` | `/api/v1/products/bulk-offer` | ADMIN, CATALOG_MANAGER |  |
| `POST` | `/api/v1/products/bulk-price` | ADMIN, CATALOG_MANAGER |  |
| `GET` | `/api/v1/products/categories/list` | AUTH |  |
| `GET` | `/api/v1/products/gst-rates` | AUTH |  |
| `GET` | `/api/v1/products/sku/{sku}` | AUTH |  |
| `GET` | `/api/v1/products/{product_id}` | AUTH |  |
| `PUT` | `/api/v1/products/{product_id}` | ADMIN, CATALOG_MANAGER |  |

### `/api/v1/reports`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/reports` | PUBLIC |  |
| `GET` | `/api/v1/reports/` | PUBLIC |  |
| `GET` | `/api/v1/reports/blueprint` | AUTH |  |
| `GET` | `/api/v1/reports/clinical/eye-tests` | AUTH |  |
| `GET` | `/api/v1/reports/customers/acquisition` | AUTH |  |
| `GET` | `/api/v1/reports/dashboard` | AUTH |  |
| `GET` | `/api/v1/reports/discount/analysis` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/reports/finance/expense-vs-revenue` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/reports/finance/gst` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/reports/finance/outstanding` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/reports/gstr1` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/reports/gstr1/gstn-json` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/reports/gstr3b` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/reports/gstr3b/gstn-json` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/reports/hr/attendance` | AUTH |  |
| `GET` | `/api/v1/reports/inventory` | AUTH |  |
| `GET` | `/api/v1/reports/inventory/brand-sellthrough` | AUTH |  |
| `GET` | `/api/v1/reports/inventory/non-moving-stock` | AUTH |  |
| `GET` | `/api/v1/reports/inventory/summary` | AUTH |  |
| `GET` | `/api/v1/reports/inventory/valuation` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |
| `GET` | `/api/v1/reports/profit/by-category` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/reports/profit/by-store` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/reports/purchase/recommendations` | AUTH |  |
| `GET` | `/api/v1/reports/sales/by-category` | AUTH |  |
| `GET` | `/api/v1/reports/sales/by-salesperson` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |
| `GET` | `/api/v1/reports/sales/comparison` | AUTH |  |
| `GET` | `/api/v1/reports/sales/daily` | AUTH |  |
| `GET` | `/api/v1/reports/sales/growth` | AUTH |  |
| `GET` | `/api/v1/reports/sales/lens-deep-dive` | AUTH |  |
| `GET` | `/api/v1/reports/sales/price-bands` | AUTH |  |
| `GET` | `/api/v1/reports/sales/seasonality` | AUTH |  |
| `GET` | `/api/v1/reports/sales/summary` | AUTH |  |
| `GET` | `/api/v1/reports/staff/ranking` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT | S |
| `GET` | `/api/v1/reports/stock/count` | AUTH |  |
| `GET` | `/api/v1/reports/targets` | AUTH |  |
| `GET` | `/api/v1/reports/tasks/summary` | AUTH |  |
| `GET` | `/api/v1/reports/walkouts/footfall-audit` | AUTH |  |
| `GET` | `/api/v1/reports/workshop/pending-jobs` | AUTH |  |

### `/api/v1/returns`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/returns` | AUTH |  |
| `POST` | `/api/v1/returns` | ADMIN, STORE_MANAGER, SALES_CASHIER, CASHIER |  |
| `GET` | `/api/v1/returns/` | AUTH |  |
| `POST` | `/api/v1/returns/` | ADMIN, STORE_MANAGER, SALES_CASHIER, CASHIER |  |
| `GET` | `/api/v1/returns/{return_id}` | AUTH |  |
| `POST` | `/api/v1/returns/{return_id}/restock` | ADMIN, STORE_MANAGER, SALES_CASHIER, CASHIER |  |

### `/api/v1/settings`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/settings` | PUBLIC |  |
| `GET` | `/api/v1/settings/` | PUBLIC |  |
| `GET` | `/api/v1/settings/admin-controls` | SUPERADMIN |  |
| `PUT` | `/api/v1/settings/admin-controls` | SUPERADMIN |  |
| `GET` | `/api/v1/settings/approval-workflows` | SUPERADMIN, ADMIN |  |
| `PUT` | `/api/v1/settings/approval-workflows` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/settings/audit-logs` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/settings/audit-logs/summary` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/settings/business` | AUTH |  |
| `PUT` | `/api/v1/settings/business` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/settings/business/logo` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/settings/discount-rules` | AUTH |  |
| `POST` | `/api/v1/settings/discount-rules` | SUPERADMIN, ADMIN, AREA_MANAGER |  |
| `PUT` | `/api/v1/settings/discount-rules` | SUPERADMIN, ADMIN, AREA_MANAGER |  |
| `GET` | `/api/v1/settings/feature-toggles/{store_id}` | SUPERADMIN, STORE_MANAGER |  |
| `PATCH` | `/api/v1/settings/feature-toggles/{store_id}` | SUPERADMIN, STORE_MANAGER |  |
| `PUT` | `/api/v1/settings/feature-toggles/{store_id}` | SUPERADMIN, STORE_MANAGER |  |
| `GET` | `/api/v1/settings/integrations` | AUTH |  |
| `GET` | `/api/v1/settings/integrations/{integration_type}` | AUTH |  |
| `PUT` | `/api/v1/settings/integrations/{integration_type}` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/settings/integrations/{integration_type}/test` | AUTH |  |
| `GET` | `/api/v1/settings/invoice` | AUTH |  |
| `PUT` | `/api/v1/settings/invoice` | SUPERADMIN, ADMIN, ACCOUNTANT |  |
| `GET` | `/api/v1/settings/marketplace-channels` | AUTH |  |
| `PUT` | `/api/v1/settings/marketplace-channels` | ADMIN |  |
| `POST` | `/api/v1/settings/marketplace-channels/{channel}/sync` | ADMIN |  |
| `GET` | `/api/v1/settings/notifications/logs` | AUTH |  |
| `GET` | `/api/v1/settings/notifications/providers` | ADMIN |  |
| `PUT` | `/api/v1/settings/notifications/providers` | ADMIN |  |
| `GET` | `/api/v1/settings/notifications/templates` | AUTH |  |
| `POST` | `/api/v1/settings/notifications/templates` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/settings/notifications/templates/{template_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/settings/notifications/templates/{template_id}` | AUTH |  |
| `PUT` | `/api/v1/settings/notifications/templates/{template_id}` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/settings/notifications/test` | AUTH |  |
| `GET` | `/api/v1/settings/printers` | AUTH |  |
| `PUT` | `/api/v1/settings/printers` | AUTH |  |
| `GET` | `/api/v1/settings/printers/available` | AUTH |  |
| `GET` | `/api/v1/settings/profile` | AUTH |  |
| `PUT` | `/api/v1/settings/profile` | AUTH |  |
| `POST` | `/api/v1/settings/profile/change-password` | AUTH |  |
| `GET` | `/api/v1/settings/profile/preferences` | AUTH |  |
| `PUT` | `/api/v1/settings/profile/preferences` | AUTH |  |
| `GET` | `/api/v1/settings/system` | SUPERADMIN, ADMIN |  |
| `PUT` | `/api/v1/settings/system` | SUPERADMIN |  |
| `GET` | `/api/v1/settings/tax` | AUTH |  |
| `PUT` | `/api/v1/settings/tax` | SUPERADMIN, ADMIN, ACCOUNTANT |  |

### `/api/v1/shipping`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/shipping/shipments` | AUTH |  |
| `POST` | `/api/v1/shipping/shipments` | ADMIN, AREA_MANAGER, STORE_MANAGER, SALES_CASHIER, CASHIER |  |
| `GET` | `/api/v1/shipping/shipments/{shipment_id}/track` | AUTH |  |

### `/api/v1/stores`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/stores` | AUTH |  |
| `POST` | `/api/v1/stores` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/stores/summary` | AUTH |  |
| `DELETE` | `/api/v1/stores/{store_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/stores/{store_id}` | AUTH | S |
| `PUT` | `/api/v1/stores/{store_id}` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/stores/{store_id}/categories/{category}` | AUTH |  |
| `POST` | `/api/v1/stores/{store_id}/categories/{category}` | AUTH |  |
| `GET` | `/api/v1/stores/{store_id}/stats` | AUTH | S |
| `GET` | `/api/v1/stores/{store_id}/users` | AUTH | S |

### `/api/v1/tasks`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/tasks` | AUTH |  |
| `POST` | `/api/v1/tasks` | AUTH |  |
| `GET` | `/api/v1/tasks/` | AUTH |  |
| `POST` | `/api/v1/tasks/` | AUTH |  |
| `POST` | `/api/v1/tasks/auto-escalate-overdue` | AUTH |  |
| `POST` | `/api/v1/tasks/auto-generate` | AUTH |  |
| `GET` | `/api/v1/tasks/checklists` | AUTH |  |
| `POST` | `/api/v1/tasks/checklists/{checklist_type}/complete-item` | AUTH |  |
| `GET` | `/api/v1/tasks/completion-stats` | AUTH |  |
| `GET` | `/api/v1/tasks/escalations` | AUTH |  |
| `GET` | `/api/v1/tasks/integrity/fake-closures` | ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `GET` | `/api/v1/tasks/integrity/silent` | ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `GET` | `/api/v1/tasks/my-tasks` | AUTH |  |
| `GET` | `/api/v1/tasks/overdue` | AUTH |  |
| `POST` | `/api/v1/tasks/scan/payment-variance` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/tasks/sla-config` | AUTH |  |
| `PUT` | `/api/v1/tasks/sla-config` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/tasks/sop-checklist` | AUTH |  |
| `POST` | `/api/v1/tasks/sop-checklist/item` | AUTH |  |
| `GET` | `/api/v1/tasks/sop-templates` | AUTH |  |
| `POST` | `/api/v1/tasks/sop-templates` | SUPERADMIN, ADMIN, STORE_MANAGER |  |
| `POST` | `/api/v1/tasks/sop-templates/` | SUPERADMIN, ADMIN, STORE_MANAGER |  |
| `POST` | `/api/v1/tasks/sop-templates/seed-defaults` | SUPERADMIN, ADMIN, STORE_MANAGER |  |
| `DELETE` | `/api/v1/tasks/sop-templates/{template_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/tasks/sop-templates/{template_id}` | AUTH |  |
| `PATCH` | `/api/v1/tasks/sop-templates/{template_id}` | SUPERADMIN, ADMIN, STORE_MANAGER |  |
| `POST` | `/api/v1/tasks/sop-templates/{template_id}/assign` | SUPERADMIN, ADMIN, STORE_MANAGER |  |
| `GET` | `/api/v1/tasks/summary` | AUTH |  |
| `GET` | `/api/v1/tasks/{task_id}` | AUTH |  |
| `PATCH` | `/api/v1/tasks/{task_id}` | AUTH |  |
| `PUT` | `/api/v1/tasks/{task_id}` | AUTH |  |
| `POST` | `/api/v1/tasks/{task_id}/acknowledge` | AUTH |  |
| `PATCH` | `/api/v1/tasks/{task_id}/complete` | AUTH |  |
| `POST` | `/api/v1/tasks/{task_id}/escalate` | AUTH |  |
| `POST` | `/api/v1/tasks/{task_id}/reassign` | AUTH |  |
| `POST` | `/api/v1/tasks/{task_id}/start` | AUTH |  |

### `/api/v1/transfers`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/transfers` | AUTH |  |
| `POST` | `/api/v1/transfers` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `GET` | `/api/v1/transfers/` | AUTH |  |
| `POST` | `/api/v1/transfers/` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `GET` | `/api/v1/transfers/analytics/location/{location_id}` | AUTH |  |
| `GET` | `/api/v1/transfers/analytics/summary` | AUTH |  |
| `POST` | `/api/v1/transfers/bulk-approve` | SUPERADMIN, ADMIN, AREA_MANAGER |  |
| `GET` | `/api/v1/transfers/pending` | AUTH |  |
| `GET` | `/api/v1/transfers/{transfer_id}` | AUTH |  |
| `PUT` | `/api/v1/transfers/{transfer_id}` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `POST` | `/api/v1/transfers/{transfer_id}/approve` | SUPERADMIN, ADMIN, AREA_MANAGER |  |
| `POST` | `/api/v1/transfers/{transfer_id}/cancel` | SUPERADMIN, ADMIN, AREA_MANAGER |  |
| `POST` | `/api/v1/transfers/{transfer_id}/complete` | SUPERADMIN, ADMIN, STORE_MANAGER |  |
| `POST` | `/api/v1/transfers/{transfer_id}/complete-picking` | SUPERADMIN, ADMIN, STORE_MANAGER, WORKSHOP_STAFF |  |
| `POST` | `/api/v1/transfers/{transfer_id}/create-shiprocket-shipment` | SUPERADMIN, ADMIN, STORE_MANAGER |  |
| `POST` | `/api/v1/transfers/{transfer_id}/receive` | SUPERADMIN, ADMIN, STORE_MANAGER, WORKSHOP_STAFF |  |
| `POST` | `/api/v1/transfers/{transfer_id}/ship` | SUPERADMIN, ADMIN, STORE_MANAGER, WORKSHOP_STAFF |  |
| `POST` | `/api/v1/transfers/{transfer_id}/start-picking` | SUPERADMIN, ADMIN, STORE_MANAGER, WORKSHOP_STAFF |  |
| `GET` | `/api/v1/transfers/{transfer_id}/tracking` | AUTH |  |

### `/api/v1/users`

| Method | Path | Allowed | S |
|---|---|---|---|
| `POST` | `/api/v1/users` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/users/` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `POST` | `/api/v1/users/` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/users/role/{role}` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `GET` | `/api/v1/users/search` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `GET` | `/api/v1/users/store/{store_id}` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `GET` | `/api/v1/users/summary` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `DELETE` | `/api/v1/users/{user_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/users/{user_id}` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `PUT` | `/api/v1/users/{user_id}` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/users/{user_id}/assign-store` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/users/{user_id}/reset-password` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/users/{user_id}/roles/{role}` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/users/{user_id}/roles/{role}` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/users/{user_id}/stores/{store_id}` | SUPERADMIN, ADMIN |  |
| `POST` | `/api/v1/users/{user_id}/stores/{store_id}` | SUPERADMIN, ADMIN |  |

### `/api/v1/vendor-portal`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/vendor-portal/{token_id}/jobs` | PUBLIC |  |
| `GET` | `/api/v1/vendor-portal/{token_id}/jobs/{job_id}` | PUBLIC |  |
| `POST` | `/api/v1/vendor-portal/{token_id}/jobs/{job_id}/status` | PUBLIC |  |

### `/api/v1/vendor-returns`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/vendor-returns` | AUTH |  |
| `POST` | `/api/v1/vendor-returns` | AUTH |  |
| `GET` | `/api/v1/vendor-returns/` | AUTH |  |
| `POST` | `/api/v1/vendor-returns/` | AUTH |  |
| `GET` | `/api/v1/vendor-returns/{return_id}` | AUTH |  |
| `PATCH` | `/api/v1/vendor-returns/{return_id}/status` | AUTH |  |

### `/api/v1/vendors`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/vendors` | AUTH |  |
| `POST` | `/api/v1/vendors` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/vendors/` | AUTH |  |
| `POST` | `/api/v1/vendors/` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/vendors/ap-aging` | ADMIN, ACCOUNTANT |  |
| `GET` | `/api/v1/vendors/grn` | AUTH |  |
| `POST` | `/api/v1/vendors/grn` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/vendors/grn/{grn_id}` | AUTH |  |
| `POST` | `/api/v1/vendors/grn/{grn_id}/accept` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/vendors/grn/{grn_id}/escalate` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/vendors/purchase-orders` | AUTH |  |
| `POST` | `/api/v1/vendors/purchase-orders` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/vendors/purchase-orders/{po_id}` | AUTH |  |
| `POST` | `/api/v1/vendors/purchase-orders/{po_id}/cancel` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/vendors/purchase-orders/{po_id}/send` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/vendors/{vendor_id}` | AUTH |  |
| `PUT` | `/api/v1/vendors/{vendor_id}` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/vendors/{vendor_id}/bills` | AUTH |  |
| `POST` | `/api/v1/vendors/{vendor_id}/bills` | ADMIN, ACCOUNTANT |  |
| `GET` | `/api/v1/vendors/{vendor_id}/debit-notes` | AUTH |  |
| `POST` | `/api/v1/vendors/{vendor_id}/debit-notes` | ADMIN, ACCOUNTANT |  |
| `GET` | `/api/v1/vendors/{vendor_id}/ledger` | AUTH |  |
| `GET` | `/api/v1/vendors/{vendor_id}/payments` | AUTH |  |
| `POST` | `/api/v1/vendors/{vendor_id}/payments` | ADMIN, ACCOUNTANT |  |
| `POST` | `/api/v1/vendors/{vendor_id}/portal-token` | SUPERADMIN, ADMIN |  |
| `DELETE` | `/api/v1/vendors/{vendor_id}/portal-token/{token_id}` | SUPERADMIN, ADMIN |  |
| `GET` | `/api/v1/vendors/{vendor_id}/portal-tokens` | SUPERADMIN, ADMIN |  |

### `/api/v1/vouchers`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/vouchers` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/vouchers` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/vouchers/` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `POST` | `/api/v1/vouchers/` | ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/vouchers/{code}` | AUTH |  |
| `POST` | `/api/v1/vouchers/{code}/cancel` | ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `POST` | `/api/v1/vouchers/{code}/redeem` | ADMIN, AREA_MANAGER, STORE_MANAGER, SALES_CASHIER, SALES_STAFF, CASHIER |  |

### `/api/v1/walkouts`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/walkouts` | AUTH |  |
| `POST` | `/api/v1/walkouts` | AUTH |  |
| `GET` | `/api/v1/walkouts/` | AUTH |  |
| `POST` | `/api/v1/walkouts/` | AUTH |  |
| `GET` | `/api/v1/walkouts/conversion-feed` | AUTH |  |
| `GET` | `/api/v1/walkouts/dashboard/fu-status` | AUTH |  |
| `GET` | `/api/v1/walkouts/dashboard/per-staff` | AUTH |  |
| `GET` | `/api/v1/walkouts/dashboard/result-breakdown` | AUTH |  |
| `GET` | `/api/v1/walkouts/dashboard/top-reasons` | AUTH |  |
| `GET` | `/api/v1/walkouts/followups/due-today` | AUTH |  |
| `POST` | `/api/v1/walkouts/followups/escalate-overdue` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `POST` | `/api/v1/walkouts/walkins/manual-topup` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT |  |
| `GET` | `/api/v1/walkouts/walkins/mtd` | AUTH |  |
| `POST` | `/api/v1/walkouts/walkins/pos-increment` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT, OPTOMETRIST, SALES_CASHIER, SALES_STAFF, CASHIER |  |
| `GET` | `/api/v1/walkouts/walkins/today` | AUTH |  |
| `DELETE` | `/api/v1/walkouts/{walkout_id}` | AUTH |  |
| `GET` | `/api/v1/walkouts/{walkout_id}` | AUTH |  |
| `PATCH` | `/api/v1/walkouts/{walkout_id}` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `POST` | `/api/v1/walkouts/{walkout_id}/followups` | AUTH |  |
| `PATCH` | `/api/v1/walkouts/{walkout_id}/followups/{round_num}` | AUTH |  |
| `POST` | `/api/v1/walkouts/{walkout_id}/followups/{round_num}/approve` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER |  |
| `PATCH` | `/api/v1/walkouts/{walkout_id}/result` | AUTH |  |

### `/api/v1/webhooks`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/webhooks/health` | PUBLIC |  |
| `POST` | `/api/v1/webhooks/razorpay` | PUBLIC |  |
| `POST` | `/api/v1/webhooks/shiprocket` | PUBLIC |  |
| `POST` | `/api/v1/webhooks/shopify` | PUBLIC |  |

### `/api/v1/workshop`

| Method | Path | Allowed | S |
|---|---|---|---|
| `GET` | `/api/v1/workshop` | PUBLIC |  |
| `GET` | `/api/v1/workshop/` | PUBLIC |  |
| `GET` | `/api/v1/workshop/dashboard-kpis` | AUTH |  |
| `GET` | `/api/v1/workshop/jobs` | AUTH |  |
| `POST` | `/api/v1/workshop/jobs` | AUTH |  |
| `GET` | `/api/v1/workshop/jobs/by-vendor/{vendor_id}` | AUTH |  |
| `GET` | `/api/v1/workshop/jobs/{job_id}` | AUTH |  |
| `PUT` | `/api/v1/workshop/jobs/{job_id}` | AUTH |  |
| `POST` | `/api/v1/workshop/jobs/{job_id}/assign` | AUTH |  |
| `POST` | `/api/v1/workshop/jobs/{job_id}/complete` | AUTH |  |
| `PATCH` | `/api/v1/workshop/jobs/{job_id}/fitting-details` | AUTH |  |
| `GET` | `/api/v1/workshop/jobs/{job_id}/label` | AUTH |  |
| `POST` | `/api/v1/workshop/jobs/{job_id}/lens-status` | ADMIN, AREA_MANAGER, STORE_MANAGER, WORKSHOP_STAFF |  |
| `POST` | `/api/v1/workshop/jobs/{job_id}/notify-ready` | ADMIN, AREA_MANAGER, STORE_MANAGER, WORKSHOP_STAFF |  |
| `POST` | `/api/v1/workshop/jobs/{job_id}/qc` | AUTH |  |
| `POST` | `/api/v1/workshop/jobs/{job_id}/rework` | AUTH |  |
| `POST` | `/api/v1/workshop/jobs/{job_id}/scan-advance` | ADMIN, AREA_MANAGER, STORE_MANAGER, CASHIER, WORKSHOP_STAFF |  |
| `POST` | `/api/v1/workshop/jobs/{job_id}/start` | AUTH |  |
| `PATCH` | `/api/v1/workshop/jobs/{job_id}/status` | AUTH |  |
| `PATCH` | `/api/v1/workshop/jobs/{job_id}/vendor` | SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, WORKSHOP_STAFF |  |
| `POST` | `/api/v1/workshop/jobs/{job_id}/vendor-status` | AUTH |  |
| `GET` | `/api/v1/workshop/overdue` | AUTH |  |
| `GET` | `/api/v1/workshop/pending` | AUTH |  |
| `GET` | `/api/v1/workshop/product-label` | AUTH |  |
| `GET` | `/api/v1/workshop/ready` | AUTH |  |
| `GET` | `/api/v1/workshop/technician-workload` | AUTH |  |

---

_Generated from the live route surface (829 method x path entries). Re-derive after route changes and keep `rbac_policy.POLICY` + this doc in sync; `test_rbac_policy.py::test_no_uncatalogued_routes` enforces it._
