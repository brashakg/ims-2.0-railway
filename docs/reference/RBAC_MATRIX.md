# IMS 2.0 - RBAC Access Matrix

The generated matrix documents every entry in the request-time
[RBAC policy registry](../../backend/api/services/rbac_policy/__init__.py).
The [RBAC middleware](../../backend/api/middleware/rbac_enforcement.py) consumes
this policy on requests. Route dependencies and handler checks remain in place;
the generated table is the registry's role gate, not a guarantee that every request or
resource will be accessible.

## How to maintain this document

**The full method x path table is no longer committed.** It changed on every
PR that added a route, so open PRs kept conflicting on it. To see it, run
`python scripts/generate_rbac_matrix.py`; it writes
`docs/reference/RBAC_MATRIX.generated.md` (gitignored) from POLICY. The
generator imports the policy only; it does not start the API or connect to a
database. A new route needs a POLICY row only - there is no document to
regenerate.

What CI still guarantees (ordinary backend suite):
[route-coverage tests](../../backend/tests/test_rbac_policy.py) fail when a
live `/api/v1` route has no POLICY row, or a POLICY row has no route.
[The matrix test](../../backend/tests/test_rbac_matrix_document.py) parses the
rendered table independently and compares every method, path, role list and
both flags with POLICY, plus totals and duplicate rows.

## How to interpret the policy

- `PUBLIC` means the registry has no IMS role gate. A route may still require
  its own credential or proof, such as a webhook signature or a portal token.
- `AUTH` abbreviates `AUTHENTICATED`: the registry admits an authenticated
  caller with a nonempty role set. Ownership, shop and other handler checks
  still apply.
- Role lists reproduce the policy entry as stored. The registry's
  `check_access` also admits `SUPERADMIN` on role-list entries, including those
  whose list omits it. This does not remove independent handler conditions.
- `S` records the policy's `store_scoped` flag. Store and resource access are
  checked by handlers; the flag itself does not enforce shop scope.
- `E` records `self_enforced`: on a role denial, the middleware defers to that
  route's own response, for example existence-hiding 404s or a specific 403.
- Investor write restrictions and per-user permission/module overrides are
  additional request-time rules. Current roles and shop assignments are also
  resolved at request time. This static table does not describe an individual
  user's effective access after those rules and resource checks.
- Dependencies may gate an individual endpoint or an entire router. Handler
  bodies may add inline checks, including ownership, shop scope and financial
  conditions. The generated rows do not replace those checks.

## Business explanations beyond role lists

ACCOUNTANT is on the inbox and on approve / reject for `rtv` (supplier credit)
requests only -- owner ruling 2026-10-12; the engine refuses an ACCOUNTANT any
other action type, and an `rtv` request is the accounts roles' alone (a
manager gets 404).

> Receiving goods (log a delivery, accept it, express receive, void, escalate,
> upload the receipt document, the receiving cockpit) is **managers only** --
> store manager, area manager, admin, superadmin; NOT the accountant (owner
> ruling 2026-09-28). Raising, sending, editing and cancelling a purchase
> order stay with the four order roles -- not the catalogue manager (owner
> 2026-10-08).

- `GET /api/v1/incentive/points/my-day`: Self-only by construction: keys on the JWT user_id, no staff parameter (POS "My day" tile).


## Historical foundation review notes

The following manual notes are retained to preserve the original business
reasoning. They describe the foundation survey, **not current permissions or a
current list of unresolved defects**. Several listed gates and routes have
changed since that survey. Use the generated matrix (see above) for the current
registry; inspect the handler before acting on any historical concern.

<details>
<summary>Original foundation survey and protection notes (historical)</summary>

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

</details>
