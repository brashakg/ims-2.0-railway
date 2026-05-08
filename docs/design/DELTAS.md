# DELTAS — what shipped vs what was designed

Audit of the live build at `ims-2-0-railway.vercel.app` against this design bundle. Organized as a punch-list — fix top-down.

## 🔴 Critical (design-system rules being broken)

### 1. Numbers rendered in Instrument Serif everywhere
**Where:** POS totals (₹8,915, ₹8,490), Reports KPIs (₹0, 0), Jarvis stats (₹1.45 L, 28, 12, 23, 42/45), Inventory alert cards.
**Fix:** Apply the `.figure` class — `font-family: var(--font-sans)` (Inter), `font-weight: 600`, `font-variant-numeric: tabular-nums`, `letter-spacing: -0.02em`. Serif is reserved for editorial display headings only (page titles, hero H1).

### 2. BV-red used as a primary CTA color
**Where:** "Search" button (Returns), "Quick Sale" giant filled icon block (POS Step 1), bright-blue Refresh button (Inventory).
**Rule:** BV-red is a *rare accent*. Primary CTA = `var(--ink)` (near-black). BV-red appears only on: the rail active indicator, "MOST USED" badge, the running total figure, and at most one hero CTA per screen. No more.

### 3. Emoji used as category icons
**Where:** Inventory category chips (👓 🕶 📖).
**Fix:** Use the line-icon set in `shell/shell.jsx → Icon` or the matching Lucide icons. The brand has no emoji.

### 4. Selected-state inconsistency (4+ different patterns)
**The correct pattern:** `border-color: var(--bv); background: var(--bv-50)` — the "Exchange" card on the Returns page is the reference. Apply to every selected card across POS step picker, Inventory filters, Setup nav, etc.

### 5. POS step counter off-by-one ("Step 0/4")
First step is **1**, not 0. Display as `Step {idx + 1} / {total}`.

---

## 🟡 Per-screen issues

### Hub
- ✅ Hero serif H1 with italic emphasis — correct
- ⚠️ Module cards have black backgrounds; design uses light cards on `var(--surface)`. Either re-skin to light or commit to dark for the whole app.
- 🔴 **Mobile is broken** — hero text wraps one word per line because rail stays 64px and topbar items stack. Fixed by `shell/mobile.css` (already in this bundle, just needs to be linked).

### POS
- 🔴 Step counter off-by-one
- 🔴 Numbers in serif
- ⚠️ "Quick Sale" giant red icon block — selected state should be subtle (BV-50 + ink border)
- ⚠️ Left rail crammed with: 4-step stepper + Hold/Recall/New-sale buttons + keyboard hints. Three different things in one column.
  - Move steps to a horizontal stepper at top of main column
  - Move Hold/Recall/New-sale above the cart
  - Keyboard hints into a `?` overlay or thin footer strip
- ⚠️ Step 3 "Complete order →" button is coral/peach — looks like a stuck disabled state. Use solid `var(--ink)` enabled or `opacity: 0.5` muted.
- ⚠️ Category chips mix `All` (sentence case) with `FRAMES SUNGLASSES` (uppercase) — pick one.

### Clinical
- ⚠️ Empty-state KPI counters render `0` in OK-green. Neutral state should be ink. Save green for actual completed positives.
- 🔴 **"Add New Customer" modal is a generic CRM form** (Email / DOB / Anniversary / Address / Pincode / State…). The clinical flow needs **token-first patient intake** with Rx capture (OD/OS × SPH/CYL/AXIS/ADD/PD), not a generic customer record. Patient (clinical) and Customer (POS) are two separate entities.
- ⚠️ Vertical scrollbar element on right edge of empty state looks like leaked overflow.

### Inventory
- 🔴 12+ tabs with horizontal scroll. Collapse to 4–5 primary (Catalog / Low stock / Transfers / Movements) + a "More ⌄" overflow menu.
- ⚠️ KPI strip's last cell is "VIEW Alerts" — using a stat slot to indicate active tab is confusing. Drop it; the active tab indicator below is enough.
- 🔴 Emoji on category chips (see Critical #3)
- ⚠️ Alert summary cards in 4 different colored backgrounds. Use lighter tints + a consistent border treatment. Numbers in serif again.
- ⚠️ Bright blue Refresh button — use `.btn` neutral or `.btn.primary` ink.
- ⚠️ "Unknown Product" on every row — flagging missing seed data.

### Reports
- 🔴 KPI numbers in serif
- ⚠️ KPI cards too tall (~140px) — collapse to ~92px stat-strip pattern from `inventory.html`
- 🔴 **Forecast section is a different design language entirely.** Bright purple as the accent (chip, sub-tab, period selector), 4-color stat cards, separate icon style. Re-skin in BV palette: BV-red active states, neutral cards with subtle warn/err tints for "at risk".
- ⚠️ "Summer Season" purple pill — same off-palette
- ✅ Forecast table structure (Category / Stock / Days to Stockout / Reorder Qty / Trend / Confidence) is good — keep

### Tasks & SOPs
- 🔴 **Biggest miss.** Designed as a complex split-panel with priority strip, escalation ladder with countdowns, SOP attachments, owner avatars. Build is a basic single-task list with status/priority dropdowns.
- 🔴 No: priority strip (P0–P4 counts), escalation ladder, detail panel, SOP system, countdowns, owner avatars, `TSK-xxxx` IDs
- 🔴 "Pending" + "Medium" chips — should use the P0–P4 system (`pill-P0` through `pill-P4` in mono caps)
- 🔴 "Invalid Date" bug
- ⚠️ Hero subtitle ("P0–P4 priorities with countdown timers and auto-escalation tied to SOPs. 40-person ops coordination.") **describes** features that aren't in the UI — either implement or change the copy
- → **Recommendation:** rebuild this module from `tasks.html` in this bundle as a v2 sprint

### Print
**The strongest screen — closest to design intent.**
- ✅ Document list with PAPER/THERMAL pills, active state on "Tax invoice", serif heading on preview, structured inspector with meta + data bindings
- 🟡 Minor: "Go to /pos →" button — convention should be `Open POS →` not the URL path

### Jarvis
- 🔴 Status semantics inverted: toggles green (on) but pills say "stopped" (also green). Pick: green = running, gray = stopped. Don't color "stopped" green.
- ⚠️ "Good morning, Sir." — too on-the-nose. Replace with: `JARVIS · Online. Ready when you are.`
- 🔴 Numbers in serif (₹1.45 L, 28, 12, 23, 42/45)
- ✅ Activity filter pills, right LIVE INSIGHTS sidebar — good

### Returns *(extra screen the dev added — not in original design)*
- ✅ Three-card pattern (Refund / Exchange / Store Credit) — good
- ✅ "Exchange" selected state — **this is the correct selected pattern; reuse everywhere**
- 🔴 "Search" button in solid BV-red — should be `.btn.primary` (ink)
- ⚠️ Empty space below — preview recent orders here even before search

### Store Setup
- 🔴 17 config sections is too many. Group them: **Profile** (My Profile, Business) / **Stores & Users** (Store Mgmt, User Mgmt, Approvals) / **Catalog** (Category, Brand, Lens, Discount) / **Operations** (Tax, Notifications, Printers, Integrations) / **Advanced** (AI Agents, Feature Toggles, Audit Logs, System).
- ⚠️ "Add New Store" modal form OK in structure but very tall — split into 2 cols where possible
- ⚠️ "Add Store" header button has BV-red border + tint. Use `.btn.primary` (ink) like the modal-footer Create Store.

---

## 📐 Mobile responsiveness

This bundle now includes `shell/mobile.css` — a complete responsive layer that handles tablet (≤1023px) and phone (≤767px). The current build is desktop-only, so phones see the desktop layout squeezed into 360px. To enable:

1. Add `<link rel="stylesheet" href="shell/mobile.css" />` after `shell.css` in every page
2. Change `<meta name="viewport" content="width=1440">` to `<meta name="viewport" content="width=device-width, initial-scale=1">` in every page
3. Verify the rail collapses to a bottom tab bar on phones; modals become bottom sheets; tables scroll horizontally

The mobile CSS targets the actual class names used in this bundle (`.t-body`, `.pos-body`, `.cl-body`, `.s-body`, `.pr-body`, `.kpi-grid`, `.j-grid`, `.stat-strip`, `.modules`, `.hub-hero .meta-grid`, etc.). If your production CSS uses different class names, update mobile.css selectors accordingly.

---

## 🌐 Systemic patterns to fix once

| Issue | Fix |
|---|---|
| Big numbers in serif | `.figure` utility — Inter 600, tabular nums |
| BV-red as primary | Primary = `var(--ink)`. BV only on rail-active, badges, ≤1 hero CTA per screen |
| Selected card states | `border: var(--bv) + bg: var(--bv-50)` (Returns Exchange pattern) |
| Brand glyph oversized | 40×40, radius 10px (currently rendering ~56×60) |
| Empty whitespace below content | Stack secondary panels (recent activity, related items) or constrain page max-width |
| Topbar gap | Show full breadcrumb path (Hub › Module › Step), not just the module name |
| Left rail content drift | Rail = nav icons only. Steppers, action buttons, kbd hints belong in main column |
| Mixed casing on chips | Pick one (sentence case OR uppercase) — be consistent |
| Disabled buttons | `.btn:disabled { opacity: 0.5 }` from shell.css — don't invent new colors |
| Status color semantics | Green = active/running/positive. Don't paint "stopped" or "0" green. |

---

## ✅ Things the dev got right

- Print module — closest to design
- Hub hero serif H1 with italic emphasis
- Cmd-K topbar pattern
- Store-pill with green dot + mono code
- Jarvis activity filter pills
- Returns "Exchange" selected-card pattern (this is the reference for the rest of the app)
- Toggle switches (iOS-style green when on)
- KBD shortcut chips styling
