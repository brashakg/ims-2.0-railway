/* global React, ReactDOM, Shell, Icon, MOCK, Seg, useTweaks */
const { useState, useMemo } = React;

const fmt = (n) => '₹ ' + Number(n).toLocaleString('en-IN');

const STEPS = [
  { id: 'cust',  title: 'Customer',    sub: 'Lookup or walk-in' },
  { id: 'rx',    title: 'Prescription',sub: 'Enter or pick latest' },
  { id: 'prod',  title: 'Products',    sub: 'Frames, lenses, CL, access.' },
  { id: 'pay',   title: 'Payment',     sub: 'Split tender, advance' },
  { id: 'rev',   title: 'Review',      sub: 'Totals, GST, notes' },
  { id: 'done',  title: 'Print & Send',sub: 'Invoice, job card, WhatsApp' },
];

/* ── Step 1: Customer ──────────────────────────────────────── */
function StepCustomer({ cust, setCust }) {
  const [q, setQ] = useState('');
  const filtered = MOCK.customers.filter(c =>
    !q || (c.name + c.phone + c.id).toLowerCase().includes(q.toLowerCase())
  );
  return (
    <div>
      <div className="work-head">
        <h2>Who's this for?</h2>
        <div className="sub">Lookup by phone, name or customer ID. Or add a walk-in.</div>
      </div>
      <div className="cust-search-wrap">
        <Icon.search width="16" height="16" />
        <input className="input cust-search" placeholder="Search phone, name, or CUS-ID…" value={q} onChange={e => setQ(e.target.value)} autoFocus />
      </div>
      <div className="row" style={{gap:8, marginBottom:18}}>
        <button className="btn sm" onClick={() => setCust({ id:'WALKIN', name:'Walk-in', phone:'—' })}><span>+ Walk-in</span></button>
        <button className="btn sm"><span>+ New customer</span></button>
        <span className="hint">or scan loyalty card</span>
      </div>
      <div className="cust-grid">
        {filtered.map(c => (
          <div key={c.id} className={'cust-card' + (cust?.id === c.id ? ' selected' : '')} onClick={() => setCust(c)}>
            <div className="name">{c.name}</div>
            <div className="ph">{c.phone}</div>
            <div className="row2">
              <span className="chip">{c.loyalty === '—' ? 'New' : c.loyalty}</span>
              <span className="chip" style={{background:'var(--bg-sunk)'}}>Since {c.since}</span>
              {c.wallet > 0 && <span className="chip info">Wallet ₹{c.wallet}</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Step 2: Rx ───────────────────────────────────────────── */
function StepRx({ rx, setRx }) {
  const update = (eye, k, v) => {
    setRx(prev => prev.map(r => r.eye === eye ? { ...r, [k]: v } : r));
  };
  return (
    <div>
      <div className="work-head">
        <h2>Prescription</h2>
        <div className="sub">Pull from last exam, type fresh, or flag as external Rx.</div>
      </div>
      <div className="row" style={{marginBottom:14, gap:8, flexWrap:'wrap'}}>
        <button className="btn sm primary"><span>Use last exam · 14 Mar 2026</span></button>
        <button className="btn sm"><span>+ Fresh Rx</span></button>
        <button className="btn sm"><span>External doctor (upload)</span></button>
        <button className="btn sm"><span>No Rx · accessory only</span></button>
      </div>
      <div className="rx-card">
        <div className="rx-grid">
          <span></span>
          <span className="rx-head">Sph</span>
          <span className="rx-head">Cyl</span>
          <span className="rx-head">Axis</span>
          <span className="rx-head">Add</span>
          <span className="rx-head">PD</span>
          <span className="rx-head">VA</span>
          {rx.map(r => (
            <React.Fragment key={r.eye}>
              <span className="rx-eye">{r.eye}</span>
              <input className="input" value={r.sph} onChange={e=>update(r.eye,'sph',e.target.value)} />
              <input className="input" value={r.cyl} onChange={e=>update(r.eye,'cyl',e.target.value)} />
              <input className="input" value={r.axis} onChange={e=>update(r.eye,'axis',e.target.value)} />
              <input className="input" value={r.add} onChange={e=>update(r.eye,'add',e.target.value)} placeholder="—" />
              <input className="input" value={r.pd} onChange={e=>update(r.eye,'pd',e.target.value)} />
              <input className="input" defaultValue="6/6" />
            </React.Fragment>
          ))}
        </div>
        <div className="rx-row2">
          <label><input type="checkbox" defaultChecked /> Issued by Dr. R. Malhotra (in-store)</label>
          <label><input type="checkbox" /> Family member share — link to primary</label>
          <label><input type="checkbox" /> Progressive / bifocal</label>
          <label><input type="checkbox" /> Blue-cut recommended</label>
        </div>
        <div style={{marginTop:12, padding:'10px 12px', background:'var(--warn-50)', borderRadius:8, fontSize:12, color:'var(--warn)'}}>
          <strong>Rx Reconciler</strong> will compare this prescription against the frames & lenses chosen in the next step.
        </div>
      </div>
    </div>
  );
}

/* ── Step 3: Products ─────────────────────────────────────── */
function StepProducts({ cart, addItem }) {
  const [tab, setTab] = useState('Frame');
  const [q, setQ] = useState('');
  const items = MOCK.catalog.filter(i =>
    (tab === 'All' || i.type === tab) &&
    (!q || (i.brand + i.model + i.sku).toLowerCase().includes(q.toLowerCase()))
  );
  const inCart = (sku) => cart.some(c => c.sku === sku);
  return (
    <div>
      <div className="work-head">
        <h2>Add products</h2>
        <div className="sub">Frames, lenses, contact lenses, accessories. Scan barcode or search.</div>
      </div>
      <div className="prod-layout">
        <div className="prod-search-row">
          <div className="cust-search-wrap" style={{flex:1}}>
            <Icon.search width="16" height="16" />
            <input className="input cust-search" placeholder="Scan barcode or search by brand, model, SKU…" value={q} onChange={e=>setQ(e.target.value)} autoFocus />
          </div>
          <div className="prod-tabs">
            {['All','Frame','Lens','CL','Access.'].map(t =>
              <button key={t} className={tab===t?'on':''} onClick={()=>setTab(t)}>{t}</button>
            )}
          </div>
        </div>
        <div className="card">
          <table className="tbl prod-table">
            <thead><tr>
              <th style={{width:64}}></th>
              <th>Product</th>
              <th>Size</th>
              <th className="right">MRP</th>
              <th className="right">Price</th>
              <th className="right">Stock</th>
              <th style={{width:90}}></th>
            </tr></thead>
            <tbody>
              {items.map(it => (
                <tr key={it.sku} className={inCart(it.sku) ? 'added' : ''}>
                  <td className="pic"><div className="ph">FRAME</div></td>
                  <td>
                    <div className="brand">{it.brand}</div>
                    <div className="model">{it.model}</div>
                    <div className="sku">{it.sku} · {it.color}</div>
                  </td>
                  <td className="mono">{it.size}</td>
                  <td className="right mono mute" style={{textDecoration: it.mrp !== it.price ? 'line-through' : 'none'}}>{fmt(it.mrp)}</td>
                  <td className="right mono strong">{fmt(it.price)}</td>
                  <td className="right">
                    <span className={'chip ' + (it.stock < 3 ? 'warn' : 'ok')}>{it.stock}</span>
                  </td>
                  <td className="right">
                    {inCart(it.sku)
                      ? <span className="chip ok">Added</span>
                      : <button className="btn sm" onClick={() => addItem(it)}><span>+ Add</span></button>
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ── Step 4: Payment ──────────────────────────────────────── */
function StepPayment({ grand, splits, setSplits }) {
  const methods = [
    { id:'cash', n:'Cash', d:'Drawer · CSH-01', ic:'₹' },
    { id:'card', n:'Card', d:'Pine Labs A920', ic:'CC' },
    { id:'upi',  n:'UPI',  d:'BharatQR · ICICI', ic:'UPI' },
    { id:'wallet', n:'Loyalty wallet', d:'₹ 420 available', ic:'LW' },
    { id:'emi', n:'EMI', d:'Bajaj Finserv', ic:'EMI' },
    { id:'advance', n:'Advance', d:'Job order · pay balance later', ic:'ADV' },
  ];
  const paid = splits.reduce((a,b) => a + Number(b.amt||0), 0);
  const bal = grand - paid;
  const addSplit = (m) => setSplits([...splits, { method: m, amt: bal > 0 ? bal : 0, ref:'' }]);
  const update = (i, k, v) => setSplits(splits.map((s, idx) => idx===i ? {...s, [k]:v} : s));
  const remove = (i) => setSplits(splits.filter((_, idx) => idx!==i));

  return (
    <div>
      <div className="work-head">
        <h2>Payment</h2>
        <div className="sub">Split across tender types. Advance is allowed for job orders.</div>
      </div>
      <div style={{display:'grid', gridTemplateColumns:'1fr', gap:18, maxWidth:900}}>
        <div className="card">
          <div className="card-head"><h3>Tender methods</h3><span className="meta">Click to add to split</span></div>
          <div className="card-body">
            <div style={{display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:10}}>
              {methods.map(m => (
                <div key={m.id} className="pay-method" onClick={()=>addSplit(m.id)}>
                  <div className="ic">{m.ic}</div>
                  <div>
                    <div className="n">{m.n}</div>
                    <div className="d">{m.d}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>Split ({splits.length})</h3>
            <span className="meta">Balance: <strong className={bal === 0 ? 'strong' : ''} style={{color: bal === 0 ? 'var(--ok)' : 'var(--ink)'}}>{fmt(bal)}</strong></span>
          </div>
          <div className="card-body">
            {splits.length === 0 && <div className="hint" style={{padding:'12px 0'}}>No split lines yet. Click a tender method above to add one.</div>}
            {splits.map((s, i) => (
              <div key={i} className="split-line">
                <select value={s.method} onChange={e=>update(i,'method',e.target.value)}>
                  {methods.map(m => <option key={m.id} value={m.id}>{m.n}</option>)}
                </select>
                <input placeholder="Reference (last 4 / txn id)" value={s.ref} onChange={e=>update(i,'ref',e.target.value)} />
                <input className="num" value={s.amt} onChange={e=>update(i,'amt',e.target.value)} />
                <button className="btn sm ghost icon" onClick={()=>remove(i)}><Icon.x width="12" height="12" /></button>
              </div>
            ))}
            {splits.length > 0 && bal !== 0 && (
              <div style={{marginTop:10, padding:'10px 12px', background:'var(--warn-50)', borderRadius:8, fontSize:12, color:'var(--warn)'}}>
                {bal > 0 ? `Under-tendered by ${fmt(bal)}` : `Change due ${fmt(-bal)}`}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Step 5: Review ─────────────────────────────────────── */
function StepReview({ cust, rx, cart, splits, subtotal, gst, grand }) {
  return (
    <div>
      <div className="work-head">
        <h2>Review & confirm</h2>
        <div className="sub">Final check before we charge and print. Press ⌘↵ to confirm.</div>
      </div>
      <div className="review-grid">
        <div className="col" style={{gap:16}}>
          <div className="review-sec">
            <h3>Customer</h3>
            <div className="kv">
              <span className="k">Name</span><span className="v strong">{cust?.name || 'Walk-in'}</span>
              <span className="k">Phone</span><span className="v mono">{cust?.phone || '—'}</span>
              <span className="k">Loyalty</span><span className="v">{cust?.loyalty || '—'}</span>
            </div>
          </div>
          <div className="review-sec">
            <h3>Prescription</h3>
            <table className="tbl" style={{marginTop:-6}}>
              <thead><tr><th></th><th>Sph</th><th>Cyl</th><th>Axis</th><th>Add</th><th>PD</th></tr></thead>
              <tbody>{rx.map(r => (
                <tr key={r.eye}>
                  <td className="strong">{r.eye}</td>
                  <td className="mono">{r.sph}</td><td className="mono">{r.cyl}</td>
                  <td className="mono">{r.axis}</td><td className="mono">{r.add||'—'}</td>
                  <td className="mono">{r.pd}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <div className="review-sec">
            <h3>Items ({cart.length})</h3>
            {cart.map(c => (
              <div key={c.sku} style={{display:'flex', justifyContent:'space-between', padding:'6px 0', borderBottom:'1px solid var(--line-soft)'}}>
                <div>
                  <div style={{fontSize:12.5, fontWeight:500}}>{c.brand} · {c.model}</div>
                  <div className="mono" style={{fontSize:11, color:'var(--ink-4)'}}>{c.sku}</div>
                </div>
                <div className="mono tnum">{fmt(c.price * c.qty)}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="col" style={{gap:16}}>
          <div className="review-sec">
            <h3>Totals</h3>
            <div className="kv">
              <span className="k">Subtotal</span><span className="v mono tnum right">{fmt(subtotal)}</span>
              <span className="k">Discount</span><span className="v mono tnum right">— ₹ 0</span>
              <span className="k">CGST 6%</span><span className="v mono tnum right">{fmt(gst/2)}</span>
              <span className="k">SGST 6%</span><span className="v mono tnum right">{fmt(gst/2)}</span>
            </div>
            <div style={{borderTop:'1px solid var(--line)', marginTop:12, paddingTop:12, display:'flex', justifyContent:'space-between', alignItems:'baseline'}}>
              <span className="eyebrow">Grand total</span>
              <span className="figure" style={{fontSize:34, letterSpacing:'-.02em'}}>{fmt(grand)}</span>
            </div>
          </div>
          <div className="review-sec">
            <h3>Payment</h3>
            {splits.length === 0
              ? <div className="hint">Nothing tendered yet.</div>
              : splits.map((s, i) => (
                <div key={i} style={{display:'flex', justifyContent:'space-between', fontSize:12.5, padding:'6px 0'}}>
                  <span className="strong" style={{textTransform:'capitalize'}}>{s.method}</span>
                  <span className="mono tnum">{fmt(s.amt)}</span>
                </div>
              ))}
          </div>
          <div className="review-sec">
            <h3>Notes</h3>
            <textarea className="input" rows={3} placeholder="Optional delivery note, lens coating, etc." style={{height:'auto', padding:'10px'}}></textarea>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Step 6: Done (Receipt) ─────────────────────────────── */
function StepDone({ cust, cart, grand, onNew, receiptStyle = 'thermal' }) {
  const txn = 'BV-' + String(Math.floor(100000 + Math.random()*900000));
  return (
    <div>
      <div className="work-head">
        <h2>Sale complete</h2>
        <div className="sub">Invoice printed · job card queued for lens lab · WhatsApp receipt sent.</div>
      </div>
      <div style={{display:'grid', gridTemplateColumns:'auto 1fr', gap:32, maxWidth:900}}>
        <div style={{border:'1px solid var(--line-strong)', borderRadius:12, background:'var(--surface)', boxShadow:'var(--sh-sm)'}}>
          {receiptStyle === 'editorial' ? (
            <div style={{width:380, padding:32, fontFamily:'var(--font-sans)', fontSize:12, color:'var(--ink)'}}>
              <div style={{fontFamily:'var(--font-display)', fontSize:38, lineHeight:1, letterSpacing:'-.02em', marginBottom:4}}>Better Vision</div>
              <div style={{fontSize:11, color:'var(--ink-4)', marginBottom:22, fontFamily:'var(--font-mono)', textTransform:'uppercase', letterSpacing:'.1em'}}>A receipt, of sorts.</div>
              <div style={{display:'grid', gridTemplateColumns:'auto 1fr', gap:'8px 16px', fontSize:12, marginBottom:20}}>
                <span style={{color:'var(--ink-4)'}}>Invoice</span><span className="mono">{txn}</span>
                <span style={{color:'var(--ink-4)'}}>Date</span><span>19 April 2026 · 14:22</span>
                <span style={{color:'var(--ink-4)'}}>Cashier</span><span>Sonia K.</span>
                <span style={{color:'var(--ink-4)'}}>For</span><span>{cust?.name || 'Walk-in'}</span>
              </div>
              <div style={{borderTop:'1px solid var(--ink)', paddingTop:14, marginBottom:14}}>
                {cart.map(c => (
                  <div key={c.sku} style={{display:'flex', justifyContent:'space-between', padding:'6px 0', borderBottom:'1px dashed var(--line)'}}>
                    <span>{c.qty} × {c.model}</span>
                    <span style={{fontFamily:'var(--font-figure)', fontWeight:500, fontVariantNumeric:'tabular-nums'}}>{fmt(c.price*c.qty)}</span>
                  </div>
                ))}
              </div>
              <div style={{display:'flex', justifyContent:'space-between', fontSize:11, color:'var(--ink-4)'}}><span>Subtotal</span><span className="mono">{fmt(grand/1.12)}</span></div>
              <div style={{display:'flex', justifyContent:'space-between', fontSize:11, color:'var(--ink-4)', marginBottom:14}}><span>GST 12%</span><span className="mono">{fmt(grand - grand/1.12)}</span></div>
              <div style={{display:'flex', justifyContent:'space-between', alignItems:'baseline', borderTop:'2px solid var(--ink)', paddingTop:14}}>
                <span style={{fontFamily:'var(--font-mono)', fontSize:11, textTransform:'uppercase', letterSpacing:'.12em'}}>Total</span>
                <span style={{fontFamily:'var(--font-figure)', fontWeight:600, fontSize:30, letterSpacing:'-.02em', fontVariantNumeric:'tabular-nums'}}>{fmt(grand)}</span>
              </div>
              <div style={{fontFamily:'var(--font-display)', fontStyle:'italic', fontSize:16, marginTop:28, color:'var(--ink-3)', textAlign:'center'}}>See clearly.</div>
              <div style={{fontSize:10, color:'var(--ink-4)', textAlign:'center', marginTop:6}}>Job card JB-0418 · pickup 23 Apr · GSTIN 07AABCB1234M1Z5</div>
            </div>
          ) : (
          <div className="receipt">
            <div className="bv-logo">B</div>
            <div className="center big">Better Vision</div>
            <div className="center">GK-I Flagship, New Delhi<br/>GSTIN 07AABCB1234M1Z5</div>
            <hr/>
            <table>
              <tbody>
                <tr><td>Invoice</td><td className="r">{txn}</td></tr>
                <tr><td>Date</td><td className="r">19-Apr-2026 · 14:22</td></tr>
                <tr><td>Cashier</td><td className="r">Sonia K.</td></tr>
                <tr><td>Customer</td><td className="r">{cust?.name || 'Walk-in'}</td></tr>
              </tbody>
            </table>
            <hr/>
            <table>
              <tbody>
                {cart.map(c => (
                  <tr key={c.sku}>
                    <td>{c.qty} × {c.model.substring(0, 22)}</td>
                    <td className="r">{fmt(c.price*c.qty)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <hr/>
            <table>
              <tbody>
                <tr><td>Subtotal</td><td className="r">{fmt(grand/1.12)}</td></tr>
                <tr><td>GST 12%</td><td className="r">{fmt(grand - grand/1.12)}</td></tr>
                <tr><td className="big">TOTAL</td><td className="r big">{fmt(grand)}</td></tr>
              </tbody>
            </table>
            <hr/>
            <div className="center">Thank you. See clearly.<br/>Job card JB-0418 · pickup 23-Apr</div>
          </div>
          )}
        </div>
        <div className="col" style={{gap:10}}>
          <button className="btn lg primary"><Icon.printer width="14" height="14" /> <span>Reprint invoice</span></button>
          <button className="btn lg"><Icon.file width="14" height="14" /> <span>Print job card</span></button>
          <button className="btn lg"><Icon.ticket width="14" height="14" /> <span>Print token</span></button>
          <button className="btn lg"><span>WhatsApp receipt</span></button>
          <button className="btn lg"><span>Email receipt</span></button>
          <div className="divider" />
          <button className="btn lg accent" onClick={onNew}><Icon.plus width="14" height="14" /> <span>Start new sale</span></button>
        </div>
      </div>
    </div>
  );
}

/* ── Cart sidebar ─────────────────────────────────────── */
function Cart({ cust, cart, setCart, subtotal, gst, grand, step, accentOnTotal = true }) {
  const qty = (sku, d) => setCart(cart.map(c => c.sku===sku ? {...c, qty: Math.max(1, c.qty+d)} : c));
  const remove = (sku) => setCart(cart.filter(c => c.sku !== sku));
  const discount = 0;
  return (
    <aside className="cart">
      <div className="cart-head">
        <div className="row">
          <h3>Current sale</h3>
          <span className="txid mono">BV-DRFT-{String(step).padStart(2,'0')}</span>
        </div>
        <div className="cart-cust">
          <div className="av">{cust ? (cust.name[0] || 'W') : 'W'}</div>
          <div>
            <div className="n">{cust?.name || 'No customer'}</div>
            <div className="m">{cust?.phone || 'add in step 1'}</div>
          </div>
        </div>
      </div>
      <div className="cart-items">
        {cart.length === 0 && <div className="cart-empty">Cart is empty.<br/>Add items in step 3.</div>}
        {cart.map(c => (
          <div key={c.sku} className="cart-item">
            <div className="ph">{c.type.slice(0,5).toUpperCase()}</div>
            <div>
              <div className="brand">{c.brand}</div>
              <div className="model">{c.model}</div>
              <div className="meta mono">{c.sku}</div>
              <div className="qty-row">
                <button className="qty-btn" onClick={()=>qty(c.sku,-1)}>−</button>
                <span className="qty-val">{c.qty}</span>
                <button className="qty-btn" onClick={()=>qty(c.sku,1)}>+</button>
                <button className="btn sm ghost" style={{marginLeft:'auto', padding:'0 6px', height:22, fontSize:10}} onClick={()=>remove(c.sku)}><span>Remove</span></button>
              </div>
            </div>
            <div className="px">{fmt(c.price*c.qty)}</div>
          </div>
        ))}
      </div>
      <div className="cart-tot">
        <div className="row"><span className="l">Subtotal</span><span className="r tnum">{fmt(subtotal)}</span></div>
        <div className="row"><span className="l">Discount</span><span className="r tnum">— ₹ {discount}</span></div>
        <div className="row"><span className="l">GST (12%)</span><span className="r tnum">{fmt(gst)}</span></div>
        <div className="grand" style={accentOnTotal ? {background:'var(--ink)', color:'#fff', marginLeft:-14, marginRight:-14, marginBottom:-14, padding:'14px', borderRadius:'0 0 var(--r-lg) var(--r-lg)'} : null}><span className="l" style={accentOnTotal?{color:'#fff'}:null}>Total</span><span className="r tnum">{fmt(grand)}</span></div>
      </div>
      <div className="cart-actions">
        <button className="btn block"><span>Hold cart</span></button>
        <button className="btn block ghost"><span>Clear</span></button>
      </div>
    </aside>
  );
}

/* ── Main POS ─────────────────────────────────────────── */
function TweaksPanel({ t, set }) {
  const Tog = ({ k }) => <div className={'tgl-t' + (t[k] ? ' on' : '')} onClick={() => set({ [k]: !t[k] })} />;
  const Seg = ({ k, opts }) => (
    <div className="seg-t">
      {opts.map(o => <button key={o} className={t[k]===o?'on':''} onClick={()=>set({[k]:o})}>{o}</button>)}
    </div>
  );
  return (
    <div className="tweaks-panel">
      <div className="th"><span className="dot"/><span className="t">Tweaks</span><span className="k">pos.html</span></div>
      <div className="body">
        <div className="grp">
          <div className="lbl">Primary CTA style</div>
          <Seg k="ctaStyle" opts={['primary','accent','ghost']} />
        </div>
        <div className="grp">
          <div className="lbl">Confirm & print</div>
          <Seg k="confirmMode" opts={['single-button','split']} />
        </div>
        <div className="grp">
          <div className="lbl">Step rail</div>
          <Seg k="stepRailDensity" opts={['regular','dense']} />
        </div>
        <div className="grp">
          <div className="lbl">Cart pane width <span className="v">{t.cartWidth}px</span></div>
          <input type="range" min="320" max="460" step="10" value={t.cartWidth} onChange={e=>set({cartWidth:+e.target.value})} />
        </div>
        <div className="grp">
          <div className="lbl">Receipt style</div>
          <Seg k="receiptStyle" opts={['thermal','editorial']} />
        </div>
        <div className="grp">
          <div className="row-t"><span className="n">Show hotkey hints</span><Tog k="showHotkeys"/></div>
          <div className="row-t"><span className="n">Accent grand total</span><Tog k="accentOnTotal"/></div>
          <div className="row-t"><span className="n">Show held carts list</span><Tog k="showHeldCarts"/></div>
        </div>
      </div>
    </div>
  );
}

function POS() {
  const [t, setT, tweaksOn] = useTweaks(window.POS_TWEAK_DEFAULTS || {
    ctaStyle:'primary', showHotkeys:true, stepRailDensity:'regular', cartWidth:380,
    confirmMode:'single-button', receiptStyle:'thermal', accentOnTotal:true, showHeldCarts:true
  });
  React.useEffect(() => {
    document.documentElement.style.setProperty('--cart-w', t.cartWidth + 'px');
    document.body.classList.toggle('dense-rail', t.stepRailDensity === 'dense');
  }, [t.cartWidth, t.stepRailDensity]);

  const [step, setStep] = useState(0);
  const [cust, setCust] = useState(MOCK.customers[0]);
  const [rx, setRx] = useState(MOCK.rxPresets);
  const [cart, setCart] = useState([
    { ...MOCK.catalog[0], qty: 1 },
    { ...MOCK.catalog[4], qty: 1 },
  ]);
  const [splits, setSplits] = useState([]);

  const subtotal = cart.reduce((a, c) => a + c.price * c.qty, 0);
  const gst = Math.round(subtotal * 0.12);
  const grand = subtotal + gst;

  const addItem = (it) => {
    if (cart.find(c => c.sku === it.sku)) return;
    setCart([...cart, { ...it, qty: 1 }]);
  };

  const reset = () => { setStep(0); setCart([]); setSplits([]); setCust(null); };

  const stepProps = { cust, setCust, rx, setRx, cart, setCart, addItem, splits, setSplits, subtotal, gst, grand, onNew: reset, receiptStyle: t.receiptStyle };

  return (
    <Shell active="pos" crumbs={['POS', 'New sale']} role="Store Manager"
      actions={<>
        <button className="btn sm"><Icon.clipboard width="14" height="14" /> <span>Hold</span></button>
        <button className="btn sm"><span>Recall</span></button>
      </>}>
      <div className="pos-body">
        {/* Stepper */}
        <aside className="steps-rail">
          <div className="eyebrow">Checkout · 6 steps</div>
          {STEPS.map((s, i) => (
            <div key={s.id} className={'step' + (i===step ? ' active' : i<step ? ' done' : '')} onClick={()=>setStep(i)}>
              <div className="step-num"><span>{i+1}</span></div>
              <div>
                <div className="step-title">{s.title}</div>
                <div className="step-sub">{s.sub}</div>
              </div>
            </div>
          ))}
          {t.showHeldCarts && (
          <div className="held">
            <div className="eyebrow" style={{marginBottom:10}}>Held carts · {MOCK.heldOrders.length}</div>
            {MOCK.heldOrders.map(h => (
              <div key={h.id} className="held-item">
                <div className="row1">
                  <span>{h.cust}</span>
                  <span className="code">{h.id}</span>
                </div>
                <div className="row2">
                  <span>{h.items} items · {fmt(h.total)}</span>
                  <span>{h.age}</span>
                </div>
              </div>
            ))}
          </div>
          )}
        </aside>

        {/* Work area */}
        <section className="pos-work">
          {step === 0 && <StepCustomer {...stepProps} />}
          {step === 1 && <StepRx {...stepProps} />}
          {step === 2 && <StepProducts {...stepProps} />}
          {step === 3 && <StepPayment {...stepProps} />}
          {step === 4 && <StepReview {...stepProps} />}
          {step === 5 && <StepDone {...stepProps} />}

          {step < 5 && (
            <div className="action-bar">
              <button className="btn" onClick={()=>setStep(Math.max(0, step-1))} disabled={step===0}><span>← Back</span></button>
              <div className="spacer" />
              {t.showHotkeys && (
                <div className="hotkeys">
                  <span><span className="kbd">⌘F</span> <span>search</span></span>
                  <span><span className="kbd">⌘H</span> <span>hold</span></span>
                  <span><span className="kbd">⌘↵</span> <span>next</span></span>
                </div>
              )}
              {step === 4
                ? (t.confirmMode === 'split'
                    ? <><button className="btn lg" onClick={()=>setStep(5)}><span>Confirm →</span></button>
                        <button className="btn lg accent" onClick={()=>setStep(5)}><span>Print · {fmt(grand)}</span></button></>
                    : <button className={'btn lg ' + t.ctaStyle} onClick={()=>setStep(5)}><span>Confirm & Print · {fmt(grand)} →</span></button>)
                : <button className={'btn lg ' + t.ctaStyle} onClick={()=>setStep(step+1)}><span>Next · {STEPS[step+1].title} →</span></button>}
            </div>
          )}
        </section>

        {/* Cart */}
        <Cart cust={cust} cart={cart} setCart={setCart} subtotal={subtotal} gst={gst} grand={grand} step={step} accentOnTotal={t.accentOnTotal} receiptStyle={t.receiptStyle} />
      </div>
      {tweaksOn && <TweaksPanel t={t} set={setT} />}
    </Shell>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<POS />);
