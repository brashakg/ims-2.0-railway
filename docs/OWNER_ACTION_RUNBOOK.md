# Owner Action Runbook — 2026-05-27 EOD

Single-page, action-oriented. Read top-to-bottom, do the items you have time for. Each item is independent — you can do them in any order.

**Live app**: `https://ims-2-0-railway.vercel.app`
**Test login**: `admin` / `admin123`
**Railway project**: `b9ccf10c` (IMS 2.0)
**Backend URL**: `https://ims-20-railway-production.up.railway.app`

---

## 🟢 5-minute trust-check (do this first, EOD today)

Confirms the 13 PRs shipped today actually work on production. If any of these don't behave as described, tell me immediately.

### 1. Refresh preserves your store + role (~30 sec)
- Open `https://ims-2-0-railway.vercel.app/dashboard`
- Click the store dropdown (top right) — pick any non-default store (e.g. "Pune" if you have one, else any store other than the first)
- Press **F5** (or Cmd+R)
- ✅ Store pill should STILL show what you picked (was the bug: it used to reset to default)
- Also try switching active role (if you have multiple) — refresh — role should hold

### 2. ⌘K command palette works (~30 sec)
- On any page, press **⌘K** (Mac) or **Ctrl+K** (Win/Linux)
- ✅ A modal opens with sections (Customers / Orders / Products / Jump to page)
- Type `cashflow` — should suggest navigating to `/finance/cash-flow`
- Press Esc to close — no navigation happens

### 3. Integrations Configure actually accepts keys (~2 min)
- Go to `/settings` → **Integrations** tab
- Click **Configure** on any tile (Razorpay is a safe choice — paid via test mode)
- ✅ Modal opens with editable input fields (NOT read-only)
- ✅ Save button has a working `onClick` (you can click it; it tries to save)
- For now just click **Cancel** — we'll come back to actually saving keys in §3 below

If any of these fail, surface it. Otherwise you're good and the rest of the runbook below is optional / paced.

---

## 🔴 Today / this week — unlocks dormant features

### 1. Sign off PR #270 (Branch B' lens-catalog spec) — unlocks ~1 week of Power Grid rebuild

**Why**: Today's #267 stock-unification fixed the Power Grid's data source, but the schema can't express the way you actually stock lenses (anti-blue + green coat + index 1.6 + SV combos). Branch B' rebuilds the lens data model. Spec is written; I need your sign-off on 6 design questions before I spawn the implementation.

**Where**: https://github.com/brashakg/ims-2.0-railway/pull/270 → scroll to **§ 8 Open Design Questions** in the diff (or open `docs/LENS_CATALOG_REBUILD_SPEC.md`).

**What to do**: Tick the 6 checkboxes in §12 (or leave a comment with your picks):
- Q1 coating model (array vs single code)
- Q2 ADD axis for bifocals/progressives
- Q3 migration approach for existing products
- Q4 POS lens-sell hook point
- Q5 your coating vocabulary (any to add/remove)
- Q6 index + material enums

**Time**: ~15 min reading + 2 min ticking. The recommended answers (Option A on each) are sensible defaults — say "all Option A" if you don't want to think about it.

**After**: comment "go" on the PR and I spawn the B'1 backend agent (~2 days for the foundation PR).

---

### 2. Set SSO keys on Railway — activates IMS→BVI shared login

**Why**: PR #271 hardened the SSO exchange-token signing (jti single-use, alg/typ pin, 90s exp). It's dormant in prod until you put the RS256 keypair on Railway. Once set, you + any future CATALOG_MANAGER can click "Online Store" in the rail and land in the BVI admin already logged in (no second login).

**Where**: Railway dashboard → project IMS 2.0 → backend service → Variables tab.

**What to do** (5 min):
1. Generate an RS256 keypair on your machine:
   ```bash
   openssl genrsa -out ims_sso_private.pem 2048
   openssl rsa -in ims_sso_private.pem -pubout -out ims_sso_public.pem
   ```
2. On Railway backend service, add variable:
   - Name: `ECOMMERCE_SSO_PRIVATE_KEY`
   - Value: paste the entire content of `ims_sso_private.pem` (including the `-----BEGIN/END-----` lines)
3. On the BVI (ecommerce) service, add variable:
   - Name: `IMS_SSO_PUBLIC_KEY`
   - Value: paste the entire content of `ims_sso_public.pem`
4. Both services auto-redeploy on variable change.

**Verify**:
- Hit `https://ims-20-railway-production.up.railway.app/api/v1/ecommerce-sso` as a logged-in SUPERADMIN — should return `{"url": ".../sso?token=<JWT>", "expires_in": 90}`
- Click the "Online Store" link in the IMS rail — should land you in `uniparallel.com/admin` already logged in (mapped to your email)

**Safety**: never paste the private key into chat / email / a Slack channel / a screenshot. Treat it like a password. If you suspect it leaked, generate a new pair and rotate both env vars.

---

### 3. Save your first real integration credential — proves the round-trip

**Why**: PR #274 fixed the Configure button. Owner-side action: actually paste one set of credentials and confirm it persists + reaches the providers.

**Where**: `/settings` → Integrations → pick a tile.

**What to do** (~10 min per integration):

Pick the integration you most want live RIGHT NOW. Recommended order:
1. **Shiprocket** (shipment booking) — already env-configurable, also collection-configurable now
2. **MSG91 WhatsApp + SMS** (Rx-expiry / birthday / follow-up nudges)
3. **Razorpay** (payment gateway)
4. **Tally** (export-only today; live push deferred)

For each:
- Open Configure on the tile
- Paste real credentials in the form fields (NEVER in chat with me)
- Set the relevant `DISPATCH_MODE`:
  - `off` (default) — providers simulate, no outbound calls
  - `test` — sends only to `TEST_PHONE` (set this env on Railway too)
  - `live` — fully live; only flip this after a successful test send
- Click **Save**
- Toast should say "saved"; the **IntegrationStatusCard** at the top of the page should re-fetch and show the integration as `Configured`

**Belt-and-suspenders verify**: click **Test Connection** on the tile. Successful response → ready.

**Where the live integration deep-dive lives**: docs/INTEGRATIONS_GO_LIVE_RUNBOOK.md (already in repo, has the per-provider Railway-env-var list).

---

## 🟡 Operational — when convenient

### 4. TASKMASTER cutover diagnostic — was the auto-reorder agent dead?

**Why**: PR #267 fix-receipt says TASKMASTER was reading the wrong (empty) collection since the stock_units cutover ~90 days ago. Either it was silently dead (no reorders for ~720 SKUs × 6 stores) OR it was firing zero-stock triggers (phantom POs). One Mongo query tells us which.

**Where**: Railway dashboard → MongoDB service → "Data" tab (or `mongosh` against the Railway URI).

**What to run**:
```javascript
// 1) How many stock.below_reorder events fired since cutover?
db.agent_events.find({
  event_type: "stock.below_reorder",
  created_at: { $gte: new Date("2026-02-27T00:00:00Z") }  // 90 days back; adjust to your real cutover date
}).count()
```
- **0 or single-digit** → TASKMASTER was silently dead. Branch B is now live; auto-reorder will resume on next tick. No phantom POs to back out. Done.
- **Triple-digit or more** → Phantom triggers fired. Check `purchase_orders` for any drafts you don't recognise:
  ```javascript
  db.purchase_orders.find({
    auto_drafted_by: "TASKMASTER",
    status: "DRAFT",
    created_at: { $gte: new Date("2026-02-27T00:00:00Z") }
  }, { _id: 0, po_id: 1, vendor_id: 1, total: 1, created_at: 1 }).sort({ created_at: -1 }).limit(20)
  ```
  Cancel any spurious DRAFT POs from the UI.

**Time**: ~5 min.

---

### 5. `db.stock` cleanup (the orphan collection)

**Why**: PR #267 fixed 9 read sites that were calling `get_collection("stock")` while writes go to `stock_units`. The `stock` collection auto-created itself from those bare reads — it should be either empty or contain stale residuals. Verify and drop.

**What to run**:
```javascript
db.stock.count_documents({})
```
- **Returns 0** → drop it cleanly:
  ```javascript
  db.stock.drop()
  ```
- **Returns > 0** → these are orphans (writes never went here; some old defensive code may have stamped a few). Merge them into the canonical collection first:
  ```javascript
  db.stock.aggregate([{ $merge: { into: "stock_units", on: "_id", whenMatched: "keepExisting", whenNotMatched: "insert" } }])
  db.stock.drop()
  ```

**Time**: ~2 min.

---

### 6. Accountant confirm 5% optical GST

**Why**: I shipped editable HSN/GST master (#255 from earlier session) with optical goods at 5%. Your CA should confirm this is correct for HSN 9001 (lenses) / 9003 (frames) under your specific business setup. The default value in the live `hsn_gst_master` is 5%.

**What to do**: send your CA this message:
> Per current GST law for HSN 9001 (corrective lenses including contact lenses) and HSN 9003 (frames + mountings) under our retail entities (Better Vision Pvt Ltd, WizOpt), our POS bills these at 5% GST (CGST 2.5% + SGST 2.5% intra-state; IGST 5% inter-state). Please confirm 5% is the correct rate, and flag if any sub-category should be 12% or 18% instead. Reference: IMS 2.0 hsn_gst_master collection.

**Where to update if rate is different**: `/settings` → Tax → HSN/GST master section → row for the HSN code → edit `gst_rate` → Save.

**Time**: ~5 min to send the message; CA response timing is theirs.

---

### 7. Test the walkout flow yourself end-to-end (validates today's PR #277)

**Why**: anti-fake-closure is a behavioural change. Test it with your eyes so you trust the audit trail.

**Steps** (~5 min):
1. Go to `/walkouts` → click **+ Log Walkout**
2. Fill required fields but LEAVE MOBILE BLANK
3. Click Save Walkout → ✅ in-modal warning appears ("Save without mobile number? You won't be able to schedule call/WhatsApp/SMS follow-ups, only in-person")
4. Click "Save anyway"
5. Open the walkout. Schedule Round 1 (CALL mode, today). Save.
6. Schedule Round 2 (WHATSAPP). Schedule Round 3 (IN-PERSON).
7. Mark Round 1 status = DONE
   - You're SUPERADMIN → should auto-APPROVE with your name stamped → ✅ green chip "Approved by Avinash · just now"
8. Switch to a non-manager role (topbar → role pill → pick SALES_CASHIER if available) — open the walkout — mark Round 2 status = DONE → ✅ amber chip "Awaiting manager approval"
9. Switch back to SUPERADMIN — open walkout — click **Approve** on Round 2 → ✅ chip turns green
10. Mark Round 3 status = NOT REACHABLE → ✅ no approval chip (non-DONE statuses skip approval)
11. Delete this test walkout (or leave it — tagged with whatever name you used)

If any step doesn't behave as described, surface it.

---

## 🔵 Live integration credentials (separate session)

A separate Claude session was spawned earlier for: Shiprocket / MSG91 / Razorpay / Tally / GST-portal / Anthropic / PageSpeed. That session is independent of this one. If it produced output, follow its instructions there. If not, item 3 above covers the Settings UI path now that the Configure button works.

---

## Today's PRs — quick reference

13 merged + 1 still open:

| # | PR | What | Time |
|---|---|---|---|
| 1 | #265 | returns→serialized-stock restock | 07:33 UTC |
| 2 | #266 | Power Grid empty-state | 10:04 |
| 3 | #267 | stock/stock_units split-brain (3 silent failures, 1 fix) | 10:21 |
| 4 | #268 | finance correctness bundle (ITC + IGST + AR-by-due + COGS freeze) | 10:24 |
| 5 | #269 | brand-token cleanup on 5 pages | 10:43 |
| 6 | #271 | SSO hardening (jti + alg-pin + 12 BVI tests) | 11:06 |
| 7 | #272 | design v2 refresh (Phases 1-12, 11 screens, 26 prints) | 11:29 |
| 8 | #273 | cmdk command palette | 11:30 |
| 9 | #274 | integrations config saves keys | 11:32 |
| 10 | #275 | display fixtures + placements backend (v2-2a) | 12:13 |
| 11 | #276 | salesperson picker storewise + role-filtered | 12:25 |
| 12 | #277 | walkouts: mobile optional + 3 rounds + manager approval | 12:58 |
| 13 | #278 | active store + role preserved across refresh | 14:12 |
| open | **#270** | **B' lens-catalog spec — awaiting your sign-off** | — |

## Three background agents currently building

| Agent | Lane | Expected output |
|---|---|---|
| Full functional QA sweep | Walking the live app as you, exercising writes + completing a checkout + the walkout flow | Punch-list of any bugs found |
| v2-2b FE | Display Layout tab + Zone column on Stock Ledger | Single PR |
| v2-3 | Statutory polish on 6 prints + per-entity content editor | Single PR |

---

## TL;DR — minimum viable owner action

If you only do 2 things today:
1. **Sign off PR #270** (15 min) — unlocks ~1 week of Power Grid rebuild
2. **Run the §4 + §5 Mongo queries** (~7 min total) — closes out the post-mortem on the split-brain fix

Everything else is "when you have time".
