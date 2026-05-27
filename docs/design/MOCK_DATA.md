# MOCK DATA — Shape of the data layer

All screens are populated from `shell/data.js`, which exposes a single `window.MOCK` object. This is the contract your real backend must satisfy. Every key is documented below with type hints and sample values.

---

## Why this matters

The HTML prototypes pull from `MOCK` everywhere — when implementing in your real codebase, your data fetching layer (REST, GraphQL, RPC, whatever) should produce records of these shapes for the components to bind cleanly. If you keep these shapes during the data-layer build-out, the screens drop in almost verbatim.

---

## Top-level structure

```ts
MOCK = {
  store,         // current store identity
  cashier,       // current logged-in user
  catalog,       // product master (8 SKUs in mock; you'll have thousands)
  queue,         // clinical waiting queue
  tasks,         // ops + auto tasks
  agents,        // Jarvis agent definitions
  fixtures,      // display fixtures (13 in mock)
  placements,    // SKU → fixture mappings (13 in mock)
}
```

---

## `MOCK.store`

```ts
{
  code: 'BV-DELHI-GK1',        // internal branch code
  name: 'GK-I Flagship',
  city: 'Delhi',
  gst:  '07AABCB1234M1Z5',     // store GSTIN
}
```

In production, this would be the **active store context** for the logged-in user. Multi-store setups should make this swappable.

---

## `MOCK.cashier`

```ts
{
  name: 'Sonia K.',
  role: 'Store Manager',
  empId: 'EMP-0142',
  shift: '10:00 – 21:00 IST',
}
```

The currently authenticated user. Drives the `Shell` props (avatar, role label in breadcrumb).

---

## `MOCK.catalog` (product master)

```ts
[
  {
    sku: 'BV-RB-AV-5823',
    brand: 'Ray-Ban',
    model: 'Aviator Classic',
    type: 'Frame',                      // 'Frame' | 'Lens' | 'CL' | 'Access.'
    color: 'Gold / Green G-15',
    size: '58-14-135',                   // eye-bridge-temple in mm
    mrp: 8950,                           // MRP incl. tax
    price: 7160,                         // selling price (default sell)
    stock: 3,
  },
  // … 7 more SKUs covering Frame / Lens / CL / Access.
]
```

**Categories** (`type` field):
- `Frame` — physical frames (acetate, metal, titanium)
- `Lens` — prescription lenses (single-vision, progressive, blue-cut)
- `CL` — contact lenses (sold in boxes/packs)
- `Access.` — accessories (cloths, cases, solutions)

**HSN mapping** (the print templates lift this):
- Frame → `9003`
- Lens, CL → `9001`
- Accessories → `9605`
- Services (fitting, engraving) → `9984` (SAC)

---

## `MOCK.queue` (clinical waiting room)

```ts
[
  {
    tok: 'T-041',
    name: 'Aanya Sharma',
    age: 34,
    purpose: 'Annual eye exam',
    status: 'Waiting',     // 'Waiting' | 'In exam' | 'Called' | 'Done'
    eta: '15 min',
    booked: '13:30',
  },
  // …
]
```

Status flow: `Waiting → Called → In exam → Done`. The Token print template binds the first entry; the Clinical exam screen binds the selected one.

---

## `MOCK.tasks`

```ts
[
  {
    id: 'TSK-2211',
    pri: 'P1',                       // 'P1' | 'P2' | 'P3' | 'P4'
    title: 'Close POS drawer cash count',
    desc: 'Variance + ₹120 within ±₹200 tolerance',
    due: '21:30',
    owner: 'Sonia K.',
    src: 'Jarvis · AG-ATT',          // who raised the task
    linkedTo: 'Z/.../0419-01',       // linked artifact ref
    sla: 'auto-resolves 09:30',
    status: 'open',                  // 'open' | 'in-progress' | 'done' | 'snoozed'
  },
  // …
]
```

Priority semantics:
- **P1** — block-the-day, red; SLA in minutes/hours
- **P2** — same day; orange/warn
- **P3** — within 24h; info
- **P4** — within the week; muted

---

## `MOCK.agents` (Jarvis)

```ts
[
  {
    id: 'AG-INV',
    name: 'Stock Sentinel',
    desc: 'Flags low stock, auto-drafts reorder',
    on: true,                        // running vs paused
    lastRun: '2m ago',
    actions24h: 18,
  },
  // … 7 more
]
```

8 agents total. The `on` flag drives the pulsing green dot indicator in the Jarvis screen.

---

## `MOCK.fixtures` (display zones — user-requested system)

```ts
[
  {
    id: 'W-01',
    code: 'W-01',                    // shown to staff
    name: 'Wall · Designer & Heritage',
    type: 'wall',                    // 'window' | 'wall' | 'pillar' | 'counter' | 'cabinet' | 'gondola' | 'drawer' | 'fridge'
    floor: 'ground',                 // 'ground' | 'storage' | 'clinic'
    zone: 'A',                       // 'A' | 'B' | 'C' | '—'
    capacity: 80,                    // max units
    lockable: false,
    merch: ['Frame'],                // which catalog types belong here
    lastAudit: '15-Apr',
    // optional flags:
    mannequin: true,
    spotlit: true,
    tempCtrl: '2-8°C',
    noQR: true,
    key: 'SM only',
  },
  // … 12 more
]
```

13 fixtures across 3 floors:
- **Ground floor** (customer-facing): WD-01 (window), W-01/02/03 (walls), P-01/02 (pillars), C-01/02 (counters), LC-01 (locked cabinet), GP-01 (gondola)
- **Storage** (back-room): D-01 (lens drawer), D-02 (frame overflow)
- **Clinic**: CF-01 (CL fridge)

`type` icons are rendered in JSX as simple SVG line marks (no AI slop).

---

## `MOCK.placements` (SKU → fixture map)

```ts
[
  {
    sku: 'BV-RB-AV-5823',
    fixture: 'WD-01',                // fixture id
    qty: 1,                          // units placed at this fixture
    position: 'mannequin · centre',  // human-readable spot within the fixture
  },
  {
    sku: 'BV-RB-AV-5823',
    fixture: 'P-01',
    qty: 1,
    position: 'shelf-2 · slot-04',
  },
  {
    sku: 'BV-RB-AV-5823',
    fixture: 'D-02',
    qty: 1,
    position: 'tray-3',
  },
  // … 10 more
]
```

A single SKU can have **multiple placements** — typically one primary (display) + one back-stock (drawer/fridge). The GRN receive modal lets staff create both placements in one shot when goods physically arrive.

---

## Other data shapes used by screens

These aren't currently in `MOCK` but appear inline in the screens. When building the real backend, model them similarly:

### Invoice (from Tax Invoice template)

```ts
{
  number: 'BV/GK1/2025-26/249183',
  date: '2026-04-19T14:22:00+05:30',
  customer: { name, address, state, code, phone, custId, gstin? },
  items: [{ d, desc2, hsn, qty, unit, rate, gst }],
  discount: { code: 'BV-MEMBER', amount: 600 },
  payments: [{ tender, ref, amount }],
  job: { id: 'JB-GK1-0418', readyAt },
  loyalty: { earned: 281, balance: 1420 },
  hsnSummary: [{ hsn, taxable, cgstRate, cgstAmt, sgstRate, sgstAmt }],
}
```

### PO (from Purchase Order template)

```ts
{
  number: 'PO/BV/2025-26/0042',
  date,
  requiredBy,
  vendor: { name, address, state, code, gstin, pan, contact },
  shipTo: { branch, address, gstin, receivingPerson },
  lines: [{ d, hsn, qty, unit, rate, gst }],
  terms: { payment: 'Net-30', delivery: 'DDP', currency: 'INR' },
  reason: 'Stock 14, run-rate 2.1, cover 6.6d',
  approval: { drafter: 'Jarvis · AG-INV', approver: 'Priya B.', approvedAt },
}
```

### GRN (from Goods Receipt Note template)

```ts
{
  number: 'GRN/BV/2025-26/0418-22',
  receivedAt,
  againstPO: 'PO/.../0042',
  vendorInvoice: { number: 'JJ/24/04/2240', date },
  vendor: { name, gstin, state, code },
  carrier: { name, docket, driver, vehicle },
  boxes: { received: 3, ordered: 3, sealNumber: '4412', sealStatus: 'intact' },
  qa: 'OK' | 'Issues',
  lines: [{ d, hsn, ord, rec, batch, expiry, qa, value }],
  variance: { type: 'short' | 'over' | 'damage', qty, value, debitNote? },
  placements: [{ sku, fixture, qty, position }],  // assigned during receive
  signOff: { receivedBy, verifiedBy, postedAt },
}
```

### Z-Report (from Z-Report template)

```ts
{
  number: 'Z/BV/2025-26/0419-01',
  businessDate,
  shift: { start, end, duration },
  closingCashier: { name, empId },
  workstation: 'POS-01',
  kpis: { gross, net, txns, avgBasket, refunds, cashVariance },
  tenders: [{ mode, txCount, system, counted, variance }],
  cashCount: { denominations: [{ denom, pieces, value }], counted, opening, expected, variance },
  gstSummary: [{ hsn, category, rate, taxable, cgst, sgst }],
  events: [{ time, type, ref, desc }],
  signOff: { counter, witness, asm },
}
```

### Cash Register session (new in Accounts)

```ts
{
  sessionId: 'CR/2026/0419/PM',
  date,
  shift,
  responsible: { name, empId, role },
  opening: 2000,
  cashSales: 6420,
  refunds: 0,
  expenses: 500,
  bankDeposit: 5000,
  denominations: [
    { face: 500,  pcs: 5 },
    { face: 200,  pcs: 8 },
    { face: 100,  pcs: 12 },
    { face: 50,   pcs: 10 },
    { face: 20,   pcs: 18 },
    { face: 10,   pcs: 16 },
    { face: 10,   pcs: 4, kind: 'coin' },
    { face: 5,    pcs: 6, kind: 'coin' },
    { face: 2,    pcs: 5, kind: 'coin' },
    { face: 1,    pcs: 10, kind: 'coin' },
  ],
  counted,        // computed: Σ face × pcs
  expected,       // opening + cashSales − refunds − expenses − bankDeposit
  variance,       // counted − expected
  signOff,        // 'pending' | 'auto-cleared' | 'ASM cleared'
}
```

### Vendor account (from Vendor Ledger)

```ts
{
  vendorId: 'V-0044',
  legalName: 'Johnson & Johnson India Pvt. Ltd.',
  address, state, code, gstin, pan,
  contact: { name, phone, email },
  terms: 'Net-30',
  bankDetails: { ... },
  transactions: [
    { date, ref, type, desc, dr, cr, balance, note? }
  ],
  aging: { current, '31-60', '61-90', '90+' },
}
```

`type` enum: `Opening | Payment | PO | Bill | GRN | Debit | Credit | Adjustment`

### Customer account (from Customer Statement)

```ts
{
  custId: 'CUS-00214',
  name,
  honorific: 'Ms.',
  phone,
  email,
  address, state, code,
  gstin: '— (B2C — un-registered)',
  kyc: { method: 'Phone OTP', verifiedAt, aadhaarLast4: '5512' },
  loyalty: { tier: 'Silver', points: 1420 },
  wallet: { balance: 420 },
  family: { spouseId: 'CUS-10390', bundleSavingsYtd: 800 },
  ltv: 82460,
  visits: 4,
  pendingPickups: ['JB-GK1-0418'],
  transactions: [
    { date, ref, type, desc, dr, cr, balance }
  ],
  ytd: { spend, taxPaid, avgBasket, nps },
}
```

---

## Numeric formatting conventions

| Context | Format |
| --- | --- |
| Currency display in UI | `₹ 28,110` — Indian numbering, no decimals on summary |
| Currency in tax docs | `28,110.00` or `₹28,110.00` — 2 decimals (paise) |
| Indian numbering helper | `Intl.NumberFormat('en-IN').format(n)` |
| Amount in words | Custom helper covering Crore / Lakh / Thousand · always ends with `Only` · paise as `… and X Paise Only` if non-zero |
| Date in UI + docs | `19-Apr-2026` |
| Date+time | `19-Apr-2026 · 14:22 IST` |
| Phone | `+91 98115 22100` — country code + space + 5+5 split |
| GSTIN | Mono font, no spaces: `07AABCB1234M1Z5` |
| Tabular nums | `font-variant-numeric: tabular-nums` on every column with numbers |

---

## Sample identities used throughout the prototypes

| Identity | Used in |
| --- | --- |
| Aanya Sharma · CUS-00214 | Sample customer in Invoice, Rx, Job Card, Statement, Handover |
| Rahul Sinha · CUS-00188 | Sample customer in Credit Note |
| Rohan Iyer · CUS-10390 | Sample customer in Sale Return, Voucher purchaser |
| Meera Joshi | Sample patient in Clinical exam queue |
| Sonia Khatri · EMP-0142 | Store Manager (active user) |
| Riya P. · EMP-0144 | Inventory clerk |
| Karan T. · EMP-0151 | Optician |
| Ankit V. | WH Lead |
| Dr. Ritu Malhotra · DMC-4412 | Optometrist on Rx cards |
| Priya B. | Ops Head (approver) |
| Johnson & Johnson India · V-0044 | Sample vendor (CL supplier) |
| Essilor India · V-0021 | Sample vendor (lens lab) |
| Luxottica · V-0008 | Sample vendor (Ray-Ban frames) |

Replace these with real data once the production backend is wired up.
