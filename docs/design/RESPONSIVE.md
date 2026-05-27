# RESPONSIVE — Breakpoint strategy per screen

The IMS is primarily designed for **iPad horizontal on a counter** (≈1180–1280px landscape). It also works at desktop (≥1280px), iPad vertical (768–1024px), and mobile (<640px) for staff who use phones in stockroom/back-office contexts.

---

## Universal breakpoints

| Name | Width | Primary device |
| --- | --- | --- |
| **Desktop** | ≥ 1280px | Workstation, large laptop |
| **iPad H** | 1024px – 1280px | iPad / Galaxy Tab landscape |
| **iPad V** | 768px – 1024px | iPad portrait |
| **Mobile** | < 640px | Phone (back-of-house quick lookups) |

CSS media queries used:
```css
@media (max-width: 1280px) { /* iPad H */ }
@media (max-width: 1024px) { /* iPad V */ }
@media (max-width: 640px)  { /* Mobile */ }
```

---

## Universal patterns

### Stat strips
Strips of 4–6 KPI cells across the top of dashboards collapse:
- Desktop: 4–6 columns (single row)
- iPad H: 3 columns (2 rows for 5/6-cell strips)
- iPad V: 2 columns
- Mobile: 1 column, each cell gets a bottom border instead of right

### Three-column layouts (Clinical, POS, Setup)
Side panels become drawers progressively:
- Desktop: 3 columns visible always
- iPad H: middle expands; one side panel narrows
- iPad V: side panels collapse into drawers triggered by toggle buttons in the page header (`☰ Queue`, `Rx →`, etc.)
- Mobile: full single column; both drawers triggered by toggles

### Tab bars
Horizontal scrolling at ≤ 1280px:
```css
.tabs { overflow-x: auto; white-space: nowrap; flex-wrap: nowrap; }
```

### Dense tables
Tables wider than the viewport are wrapped in `overflow-x: auto` containers at iPad V and below. The implementation should consider:
- Sticky first column (SKU / Date) on dense tables
- Hiding optional columns at narrower widths

### Form grids
- Desktop: `repeat(auto-fill, minmax(240px, 1fr))` for customer/Rx field grids
- iPad V: 2 columns
- Mobile: 1 column

---

## Per-screen specifics

### Hub (`hub.html`)
Already had thorough breakpoints from the start (1080 / 900 / 600). 12-col module grid collapses to 6 / 4 / 2 / 1. Hero meta grid stacks earlier than other content.

### POS (`pos.html`)
| Breakpoint | Behaviour |
| --- | --- |
| Desktop | 220px rail + 1fr work + var(--cart-w) cart |
| iPad H | Rail collapses to 72px icon-only (step subtitles hidden); cart stays |
| iPad V | Rail to 64px; **cart becomes drawer** triggered by `Cart →` button in action bar; payment grid + review grid become single-column; Rx step grid narrows |
| Mobile | Rail becomes horizontal scrolling top-bar; customer grid + Rx grid stack; split-payment lines tighten |

The bottom action bar with `Esc / ← / →` hotkey hints stays visible at all breakpoints.

### Clinical (`clinical.html`)
| Breakpoint | Behaviour |
| --- | --- |
| Desktop | 320px queue + 1fr exam + 360px Rx preview |
| iPad H | Rx preview collapses to drawer (`Rx →` toggle in dhead actions) |
| iPad V | Queue also collapses to drawer (`☰ Queue` toggle); exam grid becomes single column |
| Mobile | Refraction grid horizontally scrolls (`overflow-x: auto`); pretest goes 2-col; dhead actions stack to full-width buttons |

### Inventory (`inventory.html`)
| Breakpoint | Behaviour |
| --- | --- |
| Desktop | Single-body with horizontal tab bar; Display layout: 1fr cards + 360px side panel |
| iPad H | Stat strips and fixture summary → 3 cols; fixture cards → 2-col grid; Display layout side panel narrows to 320px |
| iPad V | Stat strips → 2 cols; fixture side panel becomes static below cards (sticky removed); GRN modal width 96vw; vendor grid in modal → 2 cols |
| Mobile | All stat strips → 1 col; fixture cards → 1 col; GRN stepper → 2x2 grid; transfer strip stacks |

### Print (`print.html`)
| Breakpoint | Behaviour |
| --- | --- |
| Desktop | 260px sidebar + 1fr preview + 320px inspector |
| iPad H | Inspector narrows; thumbnails get smaller |
| iPad V | Inspector becomes drawer; preview centers |
| Mobile | Sidebar list becomes top-row horizontal scroll; preview takes full width |

### Accounts (`accounts.html`)
| Breakpoint | Behaviour |
| --- | --- |
| Desktop | 1fr inbox + 380px detail panel (approval); 280px parties list + 1fr ledger (vendor/customer) |
| iPad H | Stat strips → 3 cols; approval panel narrows to 320px; party list to 260px |
| iPad V | Stat strips → 2 cols; approval panel goes static (loses sticky); party list/ledger stacks vertically; cash register's 2-col denomination + reconciliation cards stack |
| Mobile | Approval rows collapse: pri + content one row, amount/age another; ledger detail-bal → 1 col; led-detail-head stacks |

### Pricing (`pricing.html`)
| Breakpoint | Behaviour |
| --- | --- |
| Desktop | 280px scope picker + 1fr price grid |
| iPad H | Scope picker to 240px; offer grid stays 2-col; bulk-action bar wraps to 2 rows |
| iPad V | Scope picker collapses (drops above grid); bulk-action bar stacks; price grid gets `overflow-x: auto`; offer grid → 1 col; calendar rows shrink name col |
| Mobile | Stat strips → 1 col; calendar rows → 1 col (name above bar); audit rows stack their 4 cells |

### Tasks (`tasks.html`)
| Breakpoint | Behaviour |
| --- | --- |
| Desktop | 1fr task list + 400px side panel |
| iPad H | Side panel narrows |
| iPad V | Side panel becomes overlay drawer from right |
| Mobile | Single column; selecting a task pushes you to detail view (not implemented; left as a hook) |

### Reports (`reports.html`)
| Breakpoint | Behaviour |
| --- | --- |
| Desktop | KPI grid 4-col + chart row 2fr/1fr |
| iPad H | KPI grid 3-col + chart row stacks |
| iPad V | KPI grid 2-col |
| Mobile | KPI grid 1-col; charts wrap full-width with horizontal scroll for bar charts if needed |

### Jarvis (`jarvis.html`)
| Breakpoint | Behaviour |
| --- | --- |
| Desktop | Hero 1fr + pulse stats + agent grid 2-col + log table |
| iPad H | Hero h2 wraps earlier; pulse stats wrap |
| iPad V | Agent grid → 1 col; log row shrinks columns |
| Mobile | Full stack |

**Known issue** (pre-existing, flagged by verifier): At around 870px the hero h2 "Eight agents. One shift. Quietly keeping things in line." wraps to 3 lines and overlaps the description. Fix is to reduce hero h2 `font-size` from `44px` to `36px` below 1024px and `28px` below 768px, plus adjust `max-width` of the description.

### Setup (`setup.html`)
| Breakpoint | Behaviour |
| --- | --- |
| Desktop | 240px section nav + 1fr content + 380px audit log |
| iPad H | Audit log narrows |
| iPad V | Audit log becomes drawer from right; section nav narrows |
| Mobile | Section nav becomes top-row horizontal scrolling tabs; audit log accessible via drawer |

---

## Implementation guidance

### Container queries (if framework supports)

When implementing in production, prefer **container queries** over media queries for the side-panel collapses. The breakpoints described above are window-based, but the right semantic is "this side panel collapses when *its parent container* is below 1024px wide" — which lets the same component work in dialogs, embedded views, etc.

```css
.side-panel {
  container-type: inline-size;
}

@container (max-width: 1024px) {
  .side-panel { /* drawer behaviour */ }
}
```

### Drawers vs. overlays

The current design uses **slide-in drawers** (`position: fixed; right: 0` or `left: 0`) for collapsed panels. The implementation should:
- Animate in/out (transform translateX, 200ms ease-out)
- Trap focus when open (accessibility)
- Close on outside-click + Esc key
- Show a backdrop with subtle dimming on mobile

### Touch-target sizing

iPad and mobile breakpoints should bump all clickable elements to **44×44px minimum** per Apple HIG / Material standards:
- Increase button heights
- Increase row `padding`
- Decrease font-size only after spacing is generous enough

The current prototypes are designed at mouse-precision sizing; the developer should bump touch-target dimensions during the iPad/mobile implementation.

### Performance

Dense tables (Stock ledger has 1,240+ rows in production; current mock has 8) should be **virtualized** at all breakpoints. React-window / TanStack Virtual or equivalent. The mock prototypes do not virtualize — that's the developer's call to add.

### Print

Print stylesheets (`@media print`) are not in the prototypes — the print module already produces print-ready layouts via the templates. For other screens, `@media print` rules to hide the rail, action bars, and panels would be a polish step.
