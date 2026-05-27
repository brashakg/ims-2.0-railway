/* global React */
// Mock data used across screens

window.MOCK = {
  store: { code: 'BV-DELHI-GK1', name: 'GK-I Flagship', city: 'Delhi', gst: '07AABCB1234M1Z5' },
  cashier: { name: 'Sonia K.', role: 'Store Manager', id: 'EMP-0142' },
  customers: [
    { id: 'CUS-10842', name: 'Ananya Mehta',  phone: '+91 98110 22104', since: '2023-04', loyalty: 'Silver', wallet: 420 },
    { id: 'CUS-10390', name: 'Rohan Iyer',    phone: '+91 98933 40127', since: '2022-08', loyalty: 'Gold',   wallet: 0 },
    { id: 'CUS-11562', name: 'Priya Nair',    phone: '+91 97123 55840', since: '2024-11', loyalty: '—',      wallet: 120 },
  ],
  catalog: [
    { sku: 'BV-RB-AV-5823', brand: 'Ray-Ban',   model: 'Aviator Classic RB3025', type: 'Frame', color: 'Gold',   size: '58-14', mrp: 8990, price: 7640, stock: 3 },
    { sku: 'BV-OK-HX-1120', brand: 'Oakley',    model: 'Holbrook',               type: 'Frame', color: 'Matte Black', size: '55-18', mrp: 10200, price: 8670, stock: 7 },
    { sku: 'BV-VO-VO5485',  brand: 'Vogue',     model: 'VO5485 Cat-Eye',         type: 'Frame', color: 'Tortoise', size: '52-17', mrp: 5990, price: 5090, stock: 2 },
    { sku: 'BV-TT-TI-102',  brand: 'Titan',     model: 'Titanium Rimless TI-102',type: 'Frame', color: 'Gunmetal', size: '54-18', mrp: 7500, price: 6375, stock: 5 },
    { sku: 'BV-VL-ORMA-15', brand: 'Varilux',   model: 'Comfort Max Orma 1.5',   type: 'Lens',  color: '—',       size: 'Std',   mrp: 12500, price: 11250, stock: 99 },
    { sku: 'BV-CZ-BLUE-16', brand: 'Crizal',    model: 'Prevencia 1.6 Blue-cut', type: 'Lens',  color: '—',       size: 'Std',   mrp: 7800,  price: 7020,  stock: 99 },
    { sku: 'BV-AC-JJ-DP30', brand: 'Acuvue',    model: 'Oasys 1-Day 30pk',       type: 'CL',    color: '—',       size: '—',     mrp: 2200,  price: 1980, stock: 14 },
    { sku: 'BV-CN-MICRO',   brand: 'Better Vision', model: 'Microfiber Cloth',   type: 'Access.',color:'Red',    size: '—',     mrp: 150,   price: 150, stock: 220 },
  ],
  rxPresets: [
    { eye: 'OD', sph: '-2.25', cyl: '-0.75', axis: '180', add: '', pd: '32' },
    { eye: 'OS', sph: '-2.50', cyl: '-0.50', axis: '175', add: '', pd: '31' },
  ],
  heldOrders: [
    { id: 'HLD-0031', cust: 'Ananya Mehta', items: 3, total: 14630, age: '8m' },
    { id: 'HLD-0030', cust: 'Walk-in',       items: 1, total: 1980,  age: '21m' },
  ],
  queue: [
    { tok: 'T-042', name: 'Vikram Shah',   age: 38, purpose: 'Refraction',      waited: 4,  status: 'Waiting' },
    { tok: 'T-043', name: 'Meera Joshi',   age: 62, purpose: 'Annual check-up', waited: 12, status: 'In exam', room: 'R-2' },
    { tok: 'T-044', name: 'Arjun Kapoor',  age: 9,  purpose: 'New Rx',          waited: 1,  status: 'Waiting', flag: 'Minor' },
    { tok: 'T-045', name: 'Zoya Ansari',   age: 29, purpose: 'Contact lens',    waited: 0,  status: 'Called' },
  ],
  tasks: [
    { id: 'TSK-2211', title: 'Close POS drawer cash count — variance ₹120', pri:'P1', due:'5m',  owner:'SK', sop:'SOP-FIN-02', stage:'Open', esc:'Escalates to ASM in 5m' },
    { id: 'TSK-2210', title: 'Reorder Acuvue Oasys 1-Day — low stock',      pri:'P2', due:'2h',  owner:'RP', sop:'SOP-INV-07', stage:'In Progress' },
    { id: 'TSK-2209', title: 'Job card JB-0417 pending lab confirmation',   pri:'P1', due:'23m', owner:'AS', sop:'SOP-OPS-11', stage:'Open', esc:'Escalates to Ops Head in 23m' },
    { id: 'TSK-2208', title: 'Weekly deep-clean display counters',          pri:'P3', due:'1d',  owner:'MK', sop:'SOP-OPS-02', stage:'Open' },
    { id: 'TSK-2207', title: 'Customer NPS follow-up — CUS-10842',          pri:'P4', due:'2d',  owner:'—',  sop:'SOP-CX-04',  stage:'Open' },
    { id: 'TSK-2206', title: 'Investigate non-moving Vogue VO5485',          pri:'P2', due:'3d',  owner:'SK', sop:'SOP-INV-12', stage:'Open' },
  ],
  agents: [
    { id:'AG-INV', name:'Stock Sentinel',   desc:'Flags low stock, auto-drafts reorder', on:true,  lastRun:'2m ago', actions24h: 18 },
    { id:'AG-PRI', name:'Price Patrol',     desc:'Watches MRP/competitor deltas',        on:true,  lastRun:'14m ago', actions24h: 6 },
    { id:'AG-AGE', name:'Aging Advisor',    desc:'Surfaces aged inventory for discount', on:true,  lastRun:'1h ago',  actions24h: 3 },
    { id:'AG-ESC', name:'Escalation Engine',desc:'Auto-escalates overdue P1/P2 tasks',   on:true,  lastRun:'live',    actions24h: 12 },
    { id:'AG-RX',  name:'Rx Reconciler',    desc:'Verifies Rx ↔ lens order alignment',   on:true,  lastRun:'32m ago', actions24h: 4 },
    { id:'AG-CX',  name:'NPS Nudger',       desc:'Schedules follow-ups post-delivery',   on:false, lastRun:'paused',  actions24h: 0 },
    { id:'AG-ATT', name:'Attendance Sentinel',desc:'Shift & break anomaly detection',    on:true,  lastRun:'6m ago',  actions24h: 2 },
    { id:'AG-COPY',name:'Copy & Content',   desc:'Generates SMS/WhatsApp offers',        on:false, lastRun:'paused',  actions24h: 0 },
  ],

  /* ── Display fixtures (where SKUs physically live) ─────────────────
     Code · type · floor · zone · capacity (pieces it can hold) · lockable
     · merch: which categories belong here · last audit date. */
  fixtures: [
    { id:'WD-01', code:'WD-01', name:'Window display · street-facing',  type:'window',  floor:'ground', zone:'A', capacity:16,  lockable:false, merch:['Frame','Sun'],         lastAudit:'14-Apr', mannequin:true },
    { id:'W-01',  code:'W-01',  name:'Wall · Designer & Heritage',      type:'wall',    floor:'ground', zone:'A', capacity:80,  lockable:false, merch:['Frame'],               lastAudit:'15-Apr' },
    { id:'W-02',  code:'W-02',  name:'Wall · Sport & Outdoor',          type:'wall',    floor:'ground', zone:'B', capacity:60,  lockable:false, merch:['Frame','Sun'],         lastAudit:'15-Apr' },
    { id:'W-03',  code:'W-03',  name:'Wall · Kids & Teen',              type:'wall',    floor:'ground', zone:'C', capacity:48,  lockable:false, merch:['Frame'],               lastAudit:'12-Apr' },
    { id:'P-01',  code:'P-01',  name:'Pillar · entrance statement',     type:'pillar',  floor:'ground', zone:'A', capacity:32,  lockable:false, merch:['Frame','Sun'],         lastAudit:'15-Apr', spotlit:true },
    { id:'P-02',  code:'P-02',  name:'Pillar · mid-floor capsule',      type:'pillar',  floor:'ground', zone:'B', capacity:24,  lockable:false, merch:['Frame'],               lastAudit:'16-Apr' },
    { id:'C-01',  code:'C-01',  name:'Counter · Sunglasses & Polarised',type:'counter', floor:'ground', zone:'A', capacity:40,  lockable:false, merch:['Sun','Frame'],         lastAudit:'17-Apr' },
    { id:'C-02',  code:'C-02',  name:'Counter · CL & Accessories',      type:'counter', floor:'ground', zone:'B', capacity:60,  lockable:false, merch:['Access.','CL'],        lastAudit:'17-Apr' },
    { id:'LC-01', code:'LC-01', name:'Locked cabinet · Premium',        type:'cabinet', floor:'ground', zone:'A', capacity:18,  lockable:true,  merch:['Frame'],               lastAudit:'12-Apr', key:'SM only' },
    { id:'GP-01', code:'GP-01', name:'Gondola · Try-me',                type:'gondola', floor:'ground', zone:'B', capacity:24,  lockable:false, merch:['Frame'],               lastAudit:'14-Apr', noQR:true },
    { id:'D-01',  code:'D-01',  name:'Back drawer · Lens overflow',     type:'drawer',  floor:'storage',zone:'—', capacity:300, lockable:true,  merch:['Lens'],                lastAudit:'10-Apr', key:'SM+ASM' },
    { id:'D-02',  code:'D-02',  name:'Back drawer · Frame overflow',    type:'drawer',  floor:'storage',zone:'—', capacity:200, lockable:true,  merch:['Frame'],               lastAudit:'10-Apr', key:'SM+ASM' },
    { id:'CF-01', code:'CF-01', name:'CL fridge · clinical chamber',    type:'fridge',  floor:'clinic', zone:'—', capacity:120, lockable:true,  merch:['CL'],                  lastAudit:'18-Apr', tempCtrl:'2-8°C' },
  ],

  /* ── Placements (SKU → fixture mapping) ─────────────────────────────
     Some SKUs split across primary display fixture + back overflow. */
  placements: [
    { sku:'BV-RB-AV-5823', fixture:'WD-01', qty:1, position:'mannequin · centre' },
    { sku:'BV-RB-AV-5823', fixture:'P-01',  qty:1, position:'shelf-2 · slot-04' },
    { sku:'BV-RB-AV-5823', fixture:'D-02',  qty:1, position:'tray-3' },
    { sku:'BV-OK-HX-1120', fixture:'W-02',  qty:5, position:'row-2 · slot 8–12' },
    { sku:'BV-OK-HX-1120', fixture:'D-02',  qty:2, position:'tray-1' },
    { sku:'BV-VO-VO5485',  fixture:'C-01',  qty:2, position:'tray-A · slot-3' },
    { sku:'BV-TT-TI-102',  fixture:'LC-01', qty:4, position:'top shelf · slot-2' },
    { sku:'BV-TT-TI-102',  fixture:'D-02',  qty:1, position:'tray-2' },
    { sku:'BV-VL-ORMA-15', fixture:'D-01',  qty:99, position:'bin-A1 · power matrix' },
    { sku:'BV-CZ-BLUE-16', fixture:'D-01',  qty:99, position:'bin-A2 · power matrix' },
    { sku:'BV-AC-JJ-DP30', fixture:'CF-01', qty:14, position:'shelf-2 · power −2.50 to −3.50' },
    { sku:'BV-CN-MICRO',   fixture:'C-02',  qty:60,  position:'jar-1' },
    { sku:'BV-CN-MICRO',   fixture:'D-02',  qty:160, position:'box-12' },
  ],
};
