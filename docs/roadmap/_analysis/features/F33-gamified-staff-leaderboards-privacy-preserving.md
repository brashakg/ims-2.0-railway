# Feature #33: Gamified Staff Leaderboards (Privacy-Preserving)
META: effort=M days=5 risk=LOW roi=4 quickwin=yes deps=none phase=3

## Existing overlap
IMS already has a complete daily-points engine and leaderboard backend:

- `backend/api/routers/points.py` — 9-category daily scoring (attendance, conversion, task, visufit, punctuality, behaviour, kicker_1/2, reviews), MTD aggregation, 30-day rolling leaderboard, soft-delete with audit trail, eligibility bands, visufit gate
- `backend/database/repositories/points_log_repository.py` + `incentive_settings_repository.py` — CRUD + MTD queries already implemented
- `frontend/src/pages/incentive/DailyScorecardPage.tsx`, `MTDLeaderboardPage.tsx`, `IncentiveSettingsPage.tsx` — basic leaderboard pages already exist

What is **not** yet built: the gamified presentation layer (avatars/badges/tiers/titles, "Top Watch Salesman"-style earned titles, privacy shield ensuring no revenue figures reach junior roles, cross-store visibility control, animated rank-change indicators). The data engine is done; this feature is a UX + display-rules layer on top.

## Reuse (extend, don't rebuild)
- `points_log` collection — already has all scoring fields; add `badge_earned[]` and `title_earned` fields per daily row (or snapshot on MTD)
- `incentive_settings` collection — add `leaderboard_config` sub-doc (visible_categories, title_thresholds, badge_definitions, cross_store_visible, show_rank_numbers)
- `GET /api/v1/points/leaderboard` (points.py:621–645) — extend to return tier label, earned title, avatar_key, masked_score_if_needed; add `?scope=store|area|org` query param
- `GET /api/v1/points/mtd` (points.py:584–618) — extend to include rank delta (vs prior week), streak_days, badges_this_month
- `frontend/src/pages/incentive/MTDLeaderboardPage.tsx` — redesign as the gamified board; keep the existing data-fetch hook, replace the table with cards/podium layout
- `incentive_settings_repository.py` — extend `get_settings` / `save_settings` to include leaderboard_config blob

## Data model
New fields on **existing** `incentive_settings` collection (no new collection needed):

```
leaderboard_config: {
  visible_categories: ["attendance","conversion","task","visufit","punctuality","behaviour"],
  # owner controls which of the 9 categories are shown on the public board
  show_rank_numbers: true,          # if false, show "Top 3 / Mid / Developing" tiers only
  cross_store_visible: false,       # AREA_MANAGER+ can flip true for org-wide board
  title_thresholds: [
    { min_avg: 85, title: "Elite Performer" },
    { min_avg: 70, title: "Rising Star" },
    { min_avg: 55, title: "On Track" }
  ],
  badge_definitions: [
    { key: "perfect_attendance", label: "Perfect Attendance", icon: "shield" },
    { key: "top_conversion",     label: "Top Converter",      icon: "target"  },
    { key: "visufit_champion",   label: "Visufit Champion",   icon: "eye"     }
    # owner can add custom badges (max 10)
  ]
}
```

New fields added to each **`points_log`** row (additive, backward-compatible):

```
badges_earned: ["perfect_attendance"]   # computed and stamped on MTD sweep or daily write
title_earned: "Rising Star"             # stamped when MTD avg crosses a threshold
rank_at_write: 3                        # snapshot rank at time of write (for delta calc)
```

Avatar is derived client-side from `staff_id` initials + a deterministic colour — no image uploads needed.

## Backend
All in `backend/api/routers/points.py` unless noted.

- **Extend `GET /leaderboard`** — add `?scope=store|area|org` (org requires AREA_MANAGER+); strip monetary fields from response for SALES_STAFF/CASHIER/SALES_CASHIER roles (middleware already has role from token); add `tier_label`, `title_earned`, `badge_keys[]`, `rank_delta` (rank now vs 7 days ago) to each row
- **Extend `GET /mtd`** — add `streak_days` (consecutive days with a log entry this month), `badges_this_month[]`, `rank_delta`
- **New `GET /leaderboard/titles`** — returns title_thresholds + badge_definitions from `incentive_settings.leaderboard_config`; used by frontend to render badge icons without hardcoding
- **New `POST /leaderboard/settings`** (SUPERADMIN/ADMIN only) — upsert `leaderboard_config` sub-doc; validates badge count ≤ 10, title_thresholds sorted descending, visible_categories subset of the 9 valid keys
- **Privacy enforcement in `_build_leaderboard_row()` helper** (new pure function): receives `user_role` param; if role is SALES_STAFF / SALES_CASHIER / CASHIER / WORKSHOP_STAFF → omit `total_sales_value`, `conversion_rupees`, any monetary field; return only score totals, rank, title, badges — no rupee figures ever

## Frontend
All in `frontend/src/pages/incentive/` — restrained light-only, neutral palette with single accent per the design preference constraint.

- **`GamifiedLeaderboardPage.tsx`** (new, replaces or supplements `MTDLeaderboardPage.tsx`) — podium layout for top 3 (cards with avatar initials circle, rank medal icon, earned title badge chip, score bar); ranked list below for positions 4+; scope toggle (Store / Area / Org) visible only to AREA_MANAGER+; no rupee figures anywhere on the page for roles below AREA_MANAGER
- **`LeaderboardCard.tsx`** (new component) — single staff card: avatar (initials + deterministic colour from staff_id hash), rank number or "Top Tier" / "Mid Tier" / "Developing" if `show_rank_numbers=false`, title chip (e.g. "Rising Star"), badge row (shield/target/eye icons), score bar (percentage of max, no raw rupee number), rank-delta arrow (up/down/same vs prior week)
- **`BadgeRow.tsx`** (new small component) — renders earned badge icons from `badge_keys[]` + definitions fetched from `/leaderboard/titles`; tooltip on hover shows badge label
- **`IncentiveSettingsPage.tsx`** (extend existing) — add "Leaderboard Config" section: category visibility checkboxes (the 9 categories), show_rank_numbers toggle, cross_store_visible toggle (SUPERADMIN/ADMIN only), title threshold editor (min_avg + label pairs), badge definition editor (key + label + icon select, max 10)
- Routing: add `/incentive/leaderboard` to `App.tsx` lazy-load; link from existing `/incentive` hub

## Business rules
- Revenue figures (rupee values, conversion_rupees, total_sales_value) are **never** exposed to roles SALES_STAFF, SALES_CASHIER, CASHIER, WORKSHOP_STAFF — enforced server-side in the leaderboard response builder, not just CSS hidden
- `show_rank_numbers=false` mode replaces numeric ranks with tier bands ("Top Tier" = top 20%, "Mid Tier" = next 50%, "Developing" = bottom 30%) — thresholds computed as percentile of the store's headcount; owner sets the display mode
- Cross-store leaderboard (`scope=org`) is only queryable by AREA_MANAGER, ADMIN, SUPERADMIN; store staff see only their own store
- Badge awarding is **read-only computed** (derived from points_log data, never manually granted by managers) — prevents favouritism claims; SUPERADMIN can only edit the badge *definitions*, not award badges to individuals
- Title thresholds must be strictly decreasing (backend validates `title_thresholds[i].min_avg > title_thresholds[i+1].min_avg`)
- Maximum 10 custom badge definitions per store (enforced in settings PATCH)
- Leaderboard data is MTD-only (resets each month); no cross-month historical ranking shown to staff (ADMIN/SUPERADMIN can still access raw points_log for audit)
- Audit: every leaderboard_config change logged to `audit_logs` with before/after state

## RBAC
| Role | Can see leaderboard | Scope available | Sees rupee values | Can edit config |
|---|---|---|---|---|
| SUPERADMIN / ADMIN | Yes | Store + Area + Org | Yes | Yes |
| AREA_MANAGER | Yes | Store + Area + Org | Yes (their area) | No |
| STORE_MANAGER | Yes | Store only | Yes (their store) | No |
| ACCOUNTANT | Yes | Store only | Yes | No |
| OPTOMETRIST | Yes | Store only | No (scores only) | No |
| SALES_STAFF / SALES_CASHIER / CASHIER | Yes | Store only | No (scores only) | No |
| WORKSHOP_STAFF / CATALOG_MANAGER | Yes | Store only | No (scores only) | No |

## Integrations
- **Jarvis / MEGAPHONE**: When a staff member earns a new title (e.g., crosses "Elite Performer" threshold at month-end), MEGAPHONE can send a WhatsApp congratulation (DISPATCH_MODE-gated, quiet-hours respected). Hook: add `leaderboard.title_earned` event type to event bus (`registry.py`) dispatched by the MTD sweep; MEGAPHONE subscribes
- No Shopify / Razorpay / Tally involvement

## Risk notes
- **No POS or money touch** — this feature reads points_log (already computed), never touches orders or payments; risk is LOW
- **Perception risk** — if a manager can manually award badges, staff will cry foul; design locks badge award to computed-only (mitigated above)
- **Privacy leakage** — the only risk is a client-side CSS hide without server enforcement; design enforces the rupee strip server-side in `_build_leaderboard_row()`; no feature flag needed (this feature has no financial side effect)
- **Visufit dependency** — visufit_usage_pct_mtd is currently caller-supplied (not auto-fetched from clinical module); leaderboard inherits this as-is; visufit badge accuracy depends on that gap being closed separately

## Recommendation
**Build now (quick win)** — the scoring engine, collections, and basic leaderboard page already exist; this is a 5-day UX + privacy-enforcement layer with no new collections and no POS/financial risk. High motivational ROI for shop-floor staff at low engineering cost.

## Owner decisions
- Q: Should the leaderboard show **numeric ranks** (1st, 2nd, 3rd…) or **tier bands** (Top Tier / Mid Tier / Developing) to junior staff? | Why: Numeric ranks can demotivate the bottom half; tier bands are more inclusive but reduce competitive drive | Options: (a) Always show numbers / (b) Always show tier bands / (c) STORE_MANAGER configurable per store via the settings toggle
- Q: Which of the **9 scoring categories** should be visible on the public leaderboard (vs kept internal)? | Why: Showing "behaviour" or "kicker" scores publicly may create awkward conversations; owner decides what's motivating vs sensitive | Options: (a) All 9 / (b) Only attendance + conversion + task + visufit / (c) Owner picks per store via the category-visibility checkboxes
- Q: Should the **org-wide leaderboard** (cross-store, for Area Manager view) rank staff from Better Vision and WizOpt together, or in separate boards? | Why: Mixing chains may be unfair if store footfall differs significantly | Options: (a) Combined org board / (b) Separate board per brand / (c) Area-scoped only (staff see only their area, not full org)
- Q: Should earning a top title (e.g., "Elite Performer") trigger a **WhatsApp congratulations message** to the staff member via MEGAPHONE? | Why: Drives engagement but adds WhatsApp costs and requires MSG91 live mode to be enabled | Options: (a) Yes, auto-send at month-end MTD sweep / (b) No automated message, recognition is in-app only / (c) Yes, but manager manually triggers it from the leaderboard UI