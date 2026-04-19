# Handoff: Better Vision IMS 2.0 — Store Operations Platform

## Overview

IMS 2.0 is an operations platform for a multi-store optical retail chain (Better Vision) — the single surface a store team opens at 10 AM and closes at 9 PM. It replaces three disjoint tools: a legacy POS, a paper clinical workflow, and an Excel-based inventory process. The design spans nine modules:

1. **Hub** — dashboard / launcher / "who's this for" intro
2. **POS** — checkout (cart, tender, invoice)
3. **Clinical** — eye-exam flow (token, Rx capture, dispense)
4. **Inventory** — SKU browser, stock levels, transfers
5. **Tasks & SOPs** — priority-driven work queue with escalation ladders
6. **Reports** — KPIs, cohort charts, shift close
7. **Print** — invoice / Rx card / job-card templates
8. **Jarvis** — agentic layer (8 autonomous agents handling background ops)
9. **Store Setup** — onboarding / config

The design philosophy is **operational, not marketing**: dense information layouts, tabular numerics, neutral palette with Better Vision red as a rare accent, editorial-serif only for hero titles. This is a tool, not a landing page.

## About the Design Files

The files in this bundle are **design references created in HTML** — prototypes showing intended look and behavior, not production code to copy directly. They are a single HTML file per screen, each built with React 18 + Babel-standalone inline, loading a shared `shell/` directory for tokens, chrome, and mock data.

Your task is to **recreate these designs in the target codebase's existing environment** — using its established patterns, component library, routing, state management, and data layer. If no environment exists yet, choose the most appropriate framework for the project (React + TS + CSS Modules/Tailwind is a natural fit given the source) and implement from there. Do not ship the HTML files.

## Fidelity

**High-fidelity.** Every screen has final typography, spacing, colors, component density, and interaction patterns. Copy, placeholder data, and microcopy are all intentional and should be preserved unless the target codebase has a content-source-of-truth that supersedes them. Pixel-level recreation is expected.

The one exception: the escalation countdown timers, "now" clocks, and live KPIs are mocked as static values in the prototypes. Production should wire them to real data with the same visual treatment.

---

## Design System

All tokens live in `shell/tokens.css` (included). Key values:

### Color

| Token | Hex | Use |
|---|---|---|
| `--bg` | `#fafaf9` | App canvas |
| `--bg-sunk` | `#f4f4f2` | Below-surface panels |
| `--surface` | `#ffffff` | Cards, modals, inputs |
| `--surface-2` | `#fbfbfa` | Table header rows |
| `--line` | `#ececea` | Standard borders |
| `--line-strong` | `#d8d8d5` | Input/button borders |
| `--line-soft` | `#f1f1ef` | Table row dividers |
| `--ink` | `#141413` | Primary text |
| `--ink-2` | `#2a2a28` | Secondary text |
| `--ink-3` | `#55554f` | Body / tertiary |
| `--ink-4` | `#8a8a82` | Labels, hints, metadata |
| `--ink-5` | `#b6b6ae` | Placeholder / disabled |
| `--bv` | `#CD201A` | Better Vision brand red — use as RARE accent |
| `--bv-600` | `#B81A15` | BV hover |
| `--bv-50` | `#fbe8e6` | BV tint background |
| `--wz` | `#0e8c8c` | WizOpt (alt brand) teal, toggled via `[data-brand="wizopt"]` |
| `--ok` | `#0d7b4c` | Success |
| `--ok-50` | `#e5f3ed` | Success tint |
| `--warn` | `#b46a00` | Warning |
| `--warn-50` | `#fbf1df` | Warning tint |
| `--err` | `#b42318` | Error / overdue / escalation |
| `--err-50` | `#fbeae7` | Error tint |
| `--info` | `#1e5eb3` | Info |
| `--info-50` | `#e5eef9` | Info tint |

**Priority palette** (for Tasks & SOPs — five tiers P0–P4):
- P0 `#8b0000` / bg `#f8e8e6` — Safety / compliance
- P1 `#b42318` / bg `#fbeae7` — Active escalation (< 30m)
- P2 `#b46a00` / bg `#fbf1df` — Today (before shift close)
- P3 `#1e5eb3` / bg `#e5eef9` — Week (plannable)
- P4 `#55554f` / bg `#f1f1ef` — Backlog

### Typography

Three families, loaded via Google Fonts:

- **Inter** — all UI text, including numerics (tabular variant `font-variant-numeric: tabular-nums`)
  - Body 13.5px / 1.45
  - Labels 10.5–11px uppercase, tracking `.08–.12em`, often in mono
  - Button 12.5px / 500
- **Instrument Serif** — editorial display only (hero page titles, detail-panel H3s). Never for numerics.
  - Hub hero 52px (dialed down from 64px — see design history)
  - Page titles 32px
  - Detail headings 22px
- **JetBrains Mono** — codes, SKUs, timestamps, IDs, kbd shortcuts
  - 10–12px

The `.figure` utility class is the canonical way to render big operational numbers: Inter 600, tabular nums, tight tracking (`-0.02em`). Used on KPI stats, cart totals, countdown timers, token numbers, escalation counts. **Do not use Instrument Serif for numbers** — this was changed deliberately; serif figures read as editorial/decorative, tabular Inter reads as precise operational data.

### Radius & Shadow

- `--r-xs` 4px · `--r-sm` 6px · `--r-md` 8px · `--r-lg` 12px · `--r-xl` 16px · `--r-2xl` 20px
- Shadows are very subtle. `--sh-xs` through `--sh-pop`. Prefer `0 0 0 .5px rgba(17,17,17,.04)` hairlines over heavy drop shadows.

### Density

Two densities toggleable via `[data-density="compact"]` attribute on root. Default row height 44px, cell 34px, button 36px. Compact drops to 36/28/32.

---

## Shell (Chrome)

Every screen uses the same shell: a 64px left rail and a 56px top bar.

**Rail (`shell/shell.jsx → Rail`):**
- 40×40 brand glyph at top (`B` for Better Vision, `W` for WizOpt)
- Nav items: Hub, POS, Clinical, Inventory, Tasks & SOPs, Reports, Print, Jarvis, Store Setup
- Each item: 20×20 line icon (1.6 stroke), label on hover-tooltip
- Active indicator: 3px BV-red bar on left edge
- Dividers after Hub (item 0) and after Print (item 6) to group: [overview] / [operational] / [support]
- User avatar at bottom (initials in 36px circle)

**Top bar (`shell/shell.jsx → Topbar`):**
- Breadcrumbs (13px ink-3, separators ink-5, current item weight 500)
- Cmd-K search box (280px, ⌘K kbd hint)
- Store pill (green dot + store name + code in mono, 30px pill)
- Role pill ("ROLE" label in 10px mono + value in 12px ink-2)
- Bell / notifications icon
- Page actions (right-aligned, passed as `actions` prop)

---

## Screens

Each module is a separate HTML file at project root; all share `shell/` assets. Every screen below should be a distinct route in the target codebase.

### 1. Hub (`hub.html`)

**Purpose:** Landing page + launcher. On first-time views, doubles as a "Who's this for?" explainer.

**Layout:**
- Full-width hero band (120px vertical padding) with eyebrow, large serif H1 ("Run the floor. Close the day.") with `<em>` for italic emphasis, subhead paragraph, and a single primary CTA
- KPI meta grid (4 columns) below hero: Stores · Open orders · Revenue today · Tasks open — each with `.figure` value and mono label
- Module grid (3×3): one tile per module with icon, title, one-line description, and micro-stat
- Hub-specific styling: subtle canvas background wash (`var(--bg)`), hero sits on top without a card

### 2. POS (`pos.html`)

**Purpose:** Checkout. Cart → review → tender → invoice.

**Layout:** Two-panel split, roughly 60/40.
- **Left (cart):** Customer pill at top; line items as cards (frame thumbnail placeholder, SKU in mono, Rx reference if applicable, qty stepper, line total); "Add item" search bar sticky at bottom of the list
- **Right (summary/tender):** Totals stack (subtotal, discount, tax 18% GST, **grand total** rendered in `.figure` 38px), tender method picker (Cash / UPI / Card tabs), customer capture, "Complete sale" primary button full-width
- Receipt/invoice preview renders on the right after sale completes

### 3. Clinical (`clinical.html`)

**Purpose:** Optometrist's exam flow, from walk-in to dispense.

**Layout:** Token hero at top; exam fields in a multi-step form below.
- **Token hero:** Large `.figure` token number (e.g. `T-042`), patient name in Instrument Serif, status chip, time-elapsed counter
- **Rx table:** Eye (OD/OS) × columns (SPH, CYL, AXIS, ADD, PD). Values shown as mono inline. This is the densest data on the platform — tabular nums are critical.
- **Dispense section:** Lens type chips, frame picker, job-card handoff button
- Patient history timeline on the right rail

### 4. Inventory (`inventory.html`)

**Purpose:** SKU catalogue, stock levels, transfers, reorder.

**Layout:**
- Stat strip at top (5 cells): SKU count · Stock value · Low stock · Non-moving · Transfers in-flight. All `.figure` values with mono labels.
- Main table with filter chips above (Category, Brand, Stock status)
- Columns: SKU (mono) · Name · Category · Stock · Reserved · Available · Value · Last moved · Actions
- Low-stock rows highlighted with warn tint on the leftmost 3px bar

### 5. Tasks & SOPs (`tasks.html`)

**Purpose:** Priority-driven work queue. Every task is tied to an SOP (Standard Operating Procedure) and has an auto-escalation ladder.

**Layout:** Two-panel split.
- **Left (list):**
  - H2 title ("The shift, by priority.") in Instrument Serif 32px
  - Segmented tab ("Mine (4) / Team / Completed")
  - Priority strip: 5-cell strip showing P0–P4 counts with colored left-edge bars, `.figure` numbers, and one-line descriptors
  - Task cards: priority pill (left) · title · TSK-xxxx (mono) · SOP-xxx (mono) · Stage · owner avatar (right). Overdue tasks get `.overdue` class — red border, err-50 bg, and an "Escalates to X in Ym" banner at the bottom of the card with a zap icon.
- **Right (detail panel, fixed-width ~560px):**
  - H3 title · owner
  - `AUTO-ESCALATES IN` bar with `.figure` countdown in err-red (38px)
  - Next owner + following step preview
  - **Escalation ladder**: stepped list with numbered rungs (done = ok green, current = err red filled, pending = line-strong ring), name + role, time when each step fires (mono, right-aligned)
  - Attached SOP: SOP ID in mono + bold · Trigger line · Owner/Approver line · Numbered step list in ink-3
  - Activity timeline at bottom

**New-task modal** (1060px wide, overlay at z-200):
- Split: form 1fr + preview sidebar 340px
- Form fields (in order): Title (required) · Description · Priority chip picker (5 tiles showing bar color + label + descriptor) · Due-in presets (rounded pills, active = ink fill) + Attach SOP dropdown (2-col row) · Owner picker (rounded pills with initials avatar) · Watchers (multi-select pills) · Auto-escalation toggle (iOS-style switch, 36×22px, ok-green when on) · Attach context (chips for customer/order/SKU/job with + buttons)
- Preview sidebar (live-updating):
  - "Preview · how this appears" — full task card mirror, flashes red if `willOverdue`
  - "Escalation ladder" — four steps with current highlighted
  - "Notifications" — who gets pinged on create / overdue
- Footer: `Esc` and `⌘↵` kbd hints, Cancel + Create task button (shows `· P2 · 2h` suffix indicating live selection)
- Open animation: scale + fade (`.96 → 1` over 220ms)
- New task flash: 1.4s highlight animation on the newly-created row in the list

### 6. Reports (`reports.html`)

Dense data screen — KPI grid + multiple line/bar charts + cohort matrix. Less interactive; mostly read-only print surface.

### 7. Print (`print.html`)

Template previews for invoice, Rx card, and job-card. Uses `@media print` rules in `shell/shell.css`. Chrome is hidden via `.no-print` classes.

### 8. Jarvis (`jarvis.html`)

**Purpose:** Agentic layer — 8 named autonomous agents that run operational tasks in the background (e.g. "Reorder bot watches stock levels and opens tasks when SKUs drop below threshold").

**Layout:**
- Hero with pulse visualization (agent activity heartbeat over 24h)
- Hero stats: Agents online · Actions in 24h (`.figure`)
- Agent grid: card per agent with avatar/glyph, name, one-line mission, current status chip, "last action" timestamp in mono, interventions-this-shift count

### 9. Store Setup (`setup.html`)

Multi-step onboarding for new stores — checklist + forms.

---

## Interactions & Behavior

### Global

- **Cmd-K search**: opens a command palette overlay. Prototypes show the trigger; palette itself is not fully designed.
- **Brand toggle**: `[data-brand="wizopt"]` on `<html>` swaps the BV red token for WizOpt teal everywhere.
- **Density toggle**: `[data-density="compact"]` reduces row/cell/button heights by ~6–8px each.
- **Tweaks panel** (dev-only, bottom-right, toggled via `body.tweaks-on`): used in the prototype for live knobs; do not ship.

### Tasks & SOPs

- Clicking a task card selects it and populates the right-hand detail panel.
- "+ New task" in the top-bar actions opens the modal.
- Modal keyboard: `Esc` closes, `⌘↵` / `Ctrl+↵` submits.
- Modal submit:
  1. Generates a new TSK-xxxx id (random in prototype; real impl should use server-issued id)
  2. Prepends to task list
  3. Selects the new task
  4. Flashes the new row for 1.4s (`@keyframes flashNew` — BV-50 bg + BV border + subtle glow, fades to surface)
- Priority change in the modal auto-sets a sensible default for "Due in" (P0=10m, P1=30m, P2=2h, P3=1d, P4=3d). User can override.
- "Will overdue" warning shown inline when P0/P1 selected with due < 10m.
- Auto-escalation toggle: when on, preview shows the 4-step ladder (Owner → ASM → Ops Head → Regional) with cumulative wait times derived from the "Due in" value.

### POS

- Add-item search (local filter in prototype; server-side in production)
- Qty steppers in line items: +/- buttons, minimum 1
- Tender tabs switch the tender capture form
- "Complete sale" advances to invoice preview; the print button opens `window.print()` on the invoice template

### Animations

All in `shell/shell.css` and screen-specific `<style>` blocks:
- Card hover: no movement, subtle border darken (line → line-strong)
- Button active: `translateY(0.5px)` — tactile press feedback
- Modal enter: `fade` (180ms) + `pop` (220ms cubic-bezier(.2,.9,.3,1.2))
- Toggle switch: 150ms background + transform
- New-row flash: 1400ms with three keyframe stops

---

## State Management

Each prototype keeps state locally with `useState`. In production, expect:

- **Server state**: tasks, SOPs, inventory, customers, invoices, clinical records — all CRUD-through-API. Use React Query / tRPC / RTK Query as the codebase dictates.
- **Client state**: selected task, modal open/close, filter state, density, brand. Lift to app-level context only for density/brand; others stay local.
- **Real-time**: escalation timers must tick live. Use a single app-level `useNow()` hook that re-renders every 1s (or every 15s for coarse timers), not per-component intervals. Overdue state is derived — don't store it.
- **Auth / role**: Topbar shows the user's role; gate actions by role server-side, visual-only gating client-side.

---

## Design Tokens Quick-Reference

```css
/* Surfaces */          --bg #fafaf9  --bg-sunk #f4f4f2  --surface #fff
/* Lines */             --line #ececea  --line-strong #d8d8d5
/* Ink */               --ink #141413  --ink-2 #2a2a28  --ink-3 #55554f  --ink-4 #8a8a82
/* Brand */             --bv #CD201A  --bv-600 #B81A15  --bv-50 #fbe8e6
/* Semantic */          --ok #0d7b4c  --warn #b46a00  --err #b42318  --info #1e5eb3
/* Priority */          P0 #8b0000  P1 #b42318  P2 #b46a00  P3 #1e5eb3  P4 #55554f
/* Radius */            xs 4  sm 6  md 8  lg 12  xl 16  2xl 20
/* Spacing scale */     4 · 6 · 8 · 10 · 12 · 14 · 16 · 18 · 20 · 22 · 24 · 32 · 48
/* Font sizes */        10.5 (mono label) · 11 · 11.5 · 12 · 12.5 · 13 · 13.5 (body) · 14 · 22 · 24 · 32 · 38 · 52
```

---

## Assets

No raster images or logos — the brand is expressed as a single-letter glyph (`B` / `W`) in Instrument Serif. Frame / product imagery in POS and Inventory is rendered as a placeholder pattern (diagonal stripes, neutral colors) — **replace with real product images** when wiring to inventory service.

All icons are inline SVG (1.6 stroke, round caps, 24×24 viewBox) defined as a map in `shell/shell.jsx → Icon`. Copy these directly or swap for an equivalent icon library (Lucide is the closest match in weight and style).

Fonts come from Google Fonts:
```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Instrument+Serif:ital@0;1&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
```

---

## Files In This Bundle

```
design_handoff_ims_2_0/
├── README.md                 ← you are here
├── hub.html                  ← Module 1: Dashboard / launcher
├── pos.html                  ← Module 2: Checkout
├── clinical.html             ← Module 3: Exam flow
├── inventory.html            ← Module 4: SKU / stock
├── tasks.html                ← Module 5: Tasks & SOPs (with new-task modal)
├── reports.html              ← Module 6: Reports & KPIs
├── print.html                ← Module 7: Print templates
├── jarvis.html               ← Module 8: Agentic layer
├── setup.html                ← Module 9: Store Setup
└── shell/
    ├── tokens.css            ← All design tokens
    ├── shell.css             ← Shared component styles (rail, topbar, buttons, chips, cards, inputs, tbl, etc.)
    ├── shell.jsx             ← Shell React components + Icon map
    └── data.js               ← MOCK data for every screen (good reference for data shapes)
```

Open any `.html` file directly in a browser to see the live prototype. `tasks.html` is the deepest design — start there if you're implementing module-by-module.

---

## Implementation Notes

1. **Start with the shell.** Port the rail + topbar + design tokens + base component classes (`.btn`, `.chip`, `.pill-P*`, `.input`, `.card`, `.tbl`) before any screen. Every screen composes from those primitives.
2. **Route each module** as a top-level route. Don't nest them — each is an independent surface.
3. **Keep `.figure` as a utility.** Applying it to every large number is what gives the UI its operational feel. Do not substitute Instrument Serif.
4. **Mock data shapes are in `shell/data.js`.** Use them as the starting point for your TypeScript types.
5. **Print styles are real.** The `@media print` rules in `shell/shell.css` produce actual usable invoices and Rx cards — preserve them.
6. **Escalation ladder is a component, not a one-off.** It appears in the detail panel and the new-task modal preview — extract once.
