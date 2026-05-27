/* global React, MOCK, BV_LEGAL, LegalHeader, PartyBlock, LegalFooter, StatusStamp, FieldChip,
   inWords, inr, inrI, kvK, kvV, lblSm, tblHead, tblCell, tblNum */
/* Operational documents pulled from inventory / POS / finance modules. Same
   statutory visual language as the 6 customer-facing docs. */

/* ─── Compact CODE-128-style barcode (visual approximation) ───────────────── */
function Barcode({ value, height = 32, scale = 1.5, showText = true }) {
  const bars = [];
  bars.push({ w: 6, on: 0 });
  bars.push({ w: 2, on: 1 }, { w: 1, on: 0 }, { w: 2, on: 1 });
  for (let i = 0; i < value.length; i++) {
    const c = value.charCodeAt(i);
    [(c & 3) + 1, ((c >> 2) & 3) + 1, ((c >> 4) & 3) + 1, ((c >> 1) & 3) + 1, ((c >> 3) & 3) + 1, ((c >> 5) & 1) + 1]
      .forEach((w, j) => bars.push({ w, on: j % 2 === 0 ? 1 : 0 }));
  }
  bars.push({ w: 2, on: 1 }, { w: 1, on: 0 }, { w: 3, on: 1 }, { w: 1, on: 0 }, { w: 2, on: 1 }, { w: 6, on: 0 });
  let x = 0;
  const segs = bars.map((b, i) => {
    const r = <rect key={i} x={x} y={0} width={b.w * scale} height={height} fill={b.on ? '#000' : 'none'} />;
    x += b.w * scale;
    return r;
  });
  return (
    <div style={{ display: 'inline-block', textAlign: 'center', lineHeight: 1 }}>
      <svg viewBox={`0 0 ${x} ${height}`} width={x} height={height} style={{ display: 'block' }}>{segs}</svg>
      {showText && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, letterSpacing: '.18em', marginTop: 2 }}>{value}</div>}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   7. BARCODE SHELF-LABEL SHEET — A4 — 32-up (8 rows × 4 cols)
   ═══════════════════════════════════════════════════════════════════════════ */
function TplBarcodeLabels() {
  const base = MOCK.catalog.filter(c => c.type === 'Frame' || c.type === 'Access.');
  const labels = [];
  for (let i = 0; i < 32; i++) labels.push(base[i % base.length]);

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <StaffHeader
        docType="SHELF LABEL SHEET · Form Inv-02"
        docNo="LBL/2026/0419/04"
        copy="32-UP · 50 × 32 mm · AVERY L7160"
        meta={[
          ['Date',    '19-Apr-26 · 14:08'],
          ['By',      'Sonia K.'],
          ['Printer', 'Brother QL-820NWB'],
          ['Labels',  '32'],
        ]}
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 0 }}>
        {labels.map((it, i) => (
          <div key={i} style={{
            padding: '8px 10px',
            borderRight: i % 4 === 3 ? 'none' : '1px solid var(--ink-5)',
            borderBottom: '1px solid var(--ink-5)',
            display: 'grid', gridTemplateRows: 'auto auto 1fr auto', gap: 4,
            minHeight: 118,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <div style={{ fontSize: 8, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 600 }}>{it.brand}</div>
                <div style={{ fontSize: 11, fontWeight: 700, lineHeight: 1.15, marginTop: 1 }}>{it.model.length > 22 ? it.model.slice(0, 22) + '…' : it.model}</div>
              </div>
              <div style={{ width: 22, height: 22, border: '1.5px solid var(--ink)', display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 11, flexShrink: 0 }}>B</div>
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, color: 'var(--ink-3)' }}>
              {it.color} · {it.size}
            </div>
            <div style={{ display: 'grid', placeItems: 'center' }}>
              <Barcode value={it.sku} height={22} scale={1.0} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', borderTop: '1px solid var(--ink-5)', paddingTop: 3 }}>
              <span style={{ fontSize: 8, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 }}>MRP incl. tax</span>
              <span style={{ fontSize: 13, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{inrI(it.mrp)}</span>
            </div>
          </div>
        ))}
      </div>

      <div style={{ padding: '7px 16px', fontSize: 9, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em', textAlign: 'center' }}>
        MRP printed is inclusive of all taxes · per Legal Metrology (Packaged Commodities) Rules 2011 · barcodes verified against catalog
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   8. STOCK CYCLE-COUNT SHEET — A4 — SOP-INV-04
   ═══════════════════════════════════════════════════════════════════════════ */
function TplCountSheet() {
  // Build per-fixture groups from MOCK.placements (only ground-floor fixtures
  // by default — that's what a typical floor cycle-count covers; lens drawers
  // + CL fridge are separately scheduled). Counter is filtered by floor below.
  const FLOORS_TO_COUNT = ['ground'];
  const groups = MOCK.fixtures
    .filter(f => FLOORS_TO_COUNT.includes(f.floor))
    .map(f => {
      const items = MOCK.placements
        .filter(p => p.fixture === f.id)
        .map(p => ({ ...p, ...MOCK.catalog.find(c => c.sku === p.sku) }))
        .filter(it => it.sku);
      return { fixture: f, items };
    })
    .filter(g => g.items.length > 0);

  const totalLines = groups.reduce((a, g) => a + g.items.length, 0);
  const totalUnits = groups.reduce((a, g) => a + g.items.reduce((b, i) => b + i.qty, 0), 0);

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <StaffHeader
        docType="STOCK CYCLE-COUNT SHEET · grouped by fixture · Form Inv-04"
        docNo="CC/2026/0419/A1"
        copy="SOP-INV-04 · WORKING COPY"
        meta={[
          ['Scope',    'Ground floor'],
          ['Fixtures', groups.length + ' of ' + MOCK.fixtures.filter(f => f.floor === 'ground').length],
          ['Counter',  'Riya P.'],
          ['Witness',  'Sonia K. (SM)'],
          ['Started',  '19-Apr · 10:04'],
        ]}
      />

      <div style={{ padding: '8px 16px', background: 'var(--bg-sunk)', borderBottom: '1px solid var(--ink-4)', fontSize: 10.5, lineHeight: 1.6, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
        <div><b>1.</b> Walk each fixture <i>in code order</i> (W-01 → C-02 → LC-01 …). Don't skip ahead.</div>
        <div><b>2.</b> Count what's on the fixture; mismatched stock found elsewhere goes in the notes column.</div>
        <div><b>3.</b> Variance &gt; ±5% qty or ±₹500 value flags fixture for re-count + ASM sign-off.</div>
      </div>

      {/* Per-fixture grouped table */}
      {groups.map((g, gi) => {
        const cap = g.fixture.capacity;
        const placed = g.items.reduce((a, i) => a + i.qty, 0);
        const pct = Math.min(100, Math.round((placed / cap) * 100));
        return (
          <div key={g.fixture.id}>
            {/* Fixture section header strip */}
            <div style={{
              display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center',
              padding: '7px 16px', borderTop: gi === 0 ? 'none' : '2px solid var(--ink)',
              borderBottom: '1px solid var(--ink-4)', background: 'var(--bg-sunk)',
            }}>
              <div style={{ display: 'flex', gap: 12, alignItems: 'baseline' }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 13, letterSpacing: '.04em' }}>{g.fixture.code}</span>
                <span style={{ fontSize: 11.5, fontWeight: 600 }}>{g.fixture.name}</span>
                <span style={{ fontSize: 9.5, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em' }}>
                  Zone {g.fixture.zone} · cap. {cap} · {pct}% used
                </span>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                {g.fixture.lockable && <span style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.08em', padding: '3px 6px', border: '1px solid var(--warn)', color: 'var(--warn)' }}>Keyed</span>}
                <span style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.08em', padding: '3px 6px', border: '1px solid var(--ink-4)', color: 'var(--ink-3)' }}>Last audit {g.fixture.lastAudit}</span>
                <span style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.08em', padding: '3px 6px', border: '1px solid var(--ink-4)', color: 'var(--ink-3)' }}>{g.items.length} SKU · {placed} u</span>
              </div>
            </div>

            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10.5 }}>
              <thead>
                <tr>
                  <th style={{ ...tblHead, width: 24 }}>#</th>
                  <th style={{ ...tblHead, textAlign: 'left' }}>Position</th>
                  <th style={{ ...tblHead, textAlign: 'left' }}>SKU</th>
                  <th style={{ ...tblHead, textAlign: 'left' }}>Product</th>
                  <th style={tblHead}>UoM</th>
                  <th style={tblHead}>System</th>
                  <th style={tblHead}>Counted</th>
                  <th style={tblHead}>Δ</th>
                  <th style={tblHead}>Par</th>
                  <th style={{ ...tblHead, textAlign: 'left' }}>Notes / signed</th>
                </tr>
              </thead>
              <tbody>
                {g.items.map((r, i) => (
                  <tr key={i}>
                    <td style={tblCell}>{i + 1}</td>
                    <td style={{ ...tblCell, textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--ink-3)' }}>{r.position}</td>
                    <td style={{ ...tblCell, textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10 }}>{r.sku}</td>
                    <td style={{ ...tblCell, textAlign: 'left' }}>
                      <div style={{ fontSize: 9, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.06em' }}>{r.brand}</div>
                      <div style={{ fontWeight: 500 }}>{r.model} <span style={{ color: 'var(--ink-4)', fontSize: 9.5 }}>· {r.color} · {r.size}</span></div>
                    </td>
                    <td style={{ ...tblCell, color: 'var(--ink-4)' }}>{r.type === 'CL' ? 'Box' : 'Nos'}</td>
                    <td style={tblNum}>{r.qty}</td>
                    <td style={{ ...tblCell, width: 60, background: '#fdfdfa' }}>
                      <div style={{ height: 16, borderBottom: '1px solid var(--ink-5)' }} />
                    </td>
                    <td style={{ ...tblCell, width: 40, background: '#fdfdfa' }}>
                      <div style={{ height: 16, borderBottom: '1px solid var(--ink-5)' }} />
                    </td>
                    <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--ink-4)' }}>{r.stock < 5 ? '⚠ ' + r.stock : r.stock}</td>
                    <td style={{ ...tblCell, textAlign: 'left', minWidth: 130, background: '#fdfdfa' }}>
                      <div style={{ height: 16, borderBottom: '1px dotted var(--ink-5)' }} />
                    </td>
                  </tr>
                ))}
                {/* Fixture sign-off row */}
                <tr style={{ background: '#fafaf6' }}>
                  <td colSpan={5} style={{ ...tblCell, textAlign: 'right', fontWeight: 600, fontSize: 10, color: 'var(--ink-3)' }}>
                    Fixture {g.fixture.code} complete · signature:
                  </td>
                  <td colSpan={5} style={{ ...tblCell, borderBottom: '1px solid var(--ink-4)' }}>
                    <div style={{ height: 18, borderBottom: '0.5px solid var(--ink-4)' }} />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        );
      })}

      <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', borderTop: '2px solid var(--ink)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Session summary · to be completed at close</div>
          <table style={{ width: '100%', marginTop: 4, fontSize: 11, borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={kvK}>Fixtures counted</td><td style={kvV}>____ / {groups.length}</td>
                  <td style={kvK}>Lines counted</td><td style={kvV}>____ / {totalLines}</td></tr>
              <tr><td style={kvK}>Units expected</td><td style={kvV}>{totalUnits}</td>
                  <td style={kvK}>Variances flagged</td><td style={kvV}>____</td></tr>
              <tr><td style={kvK}>Time taken</td><td style={kvV}>____ min</td>
                  <td style={kvK}>Net value Δ</td><td style={kvV}>{inr(0).replace('0.00', '________')}</td></tr>
              <tr><td style={kvK}>ASM PIN required?</td><td style={kvV}>☐ Yes ☐ No</td>
                  <td style={kvK}>Posted to ERP</td><td style={kvV}>☐ Yes ____ / ____</td></tr>
            </tbody>
          </table>
        </div>
        <div style={{ padding: '10px 16px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div>
            <div style={lblSm}>Counter signature</div>
            <div style={{ height: 36, marginTop: 4, borderBottom: '0.5px solid var(--ink-4)' }} />
            <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 2 }}>Riya P. · EMP-0144</div>
          </div>
          <div>
            <div style={lblSm}>ASM signature</div>
            <div style={{ height: 36, marginTop: 4, borderBottom: '0.5px solid var(--ink-4)' }} />
            <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 2 }}>Required if variance &gt; tol.</div>
          </div>
        </div>
      </div>

      <div style={{ padding: '7px 16px', fontSize: 9, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em', textAlign: 'center' }}>
        SOP-INV-04 · sheet must be filed within 24 h of close · retained 18 months for internal audit
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   9. THERMAL POS RECEIPT — 80 mm — Rule 46 (compact)
   ═══════════════════════════════════════════════════════════════════════════ */
function TplThermalReceipt() {
  const items = [
    { d: 'Ray-Ban Wayfarer RB2140',                  q: 1, p: 7100 },
    { d: '  Black · 50-22-150 · FRM-RB-WF',          q: 1, p: 0, sub: true },
    { d: 'Varilux X · 1.60 Crizal',                  q: 1, p: 16900 },
    { d: '  OD −2.50/−0.75x175  OS −3.00/−0.50x5',   q: 1, p: 0, sub: true },
    { d: 'Acuvue Oasys 1-Day 30pk',                  q: 2, p: 4000 },
    { d: 'BV Microfibre cloth · L',                  q: 2, p: 210 },
  ];
  const total = 28110;

  return (
    <div className="paper thermal" data-doc style={{ padding: '14px 14px 18px', color: 'var(--ink)', fontFamily: 'var(--font-sans)', fontSize: 10 }}>
      <div style={{ textAlign: 'center', borderBottom: '2px solid var(--ink)', paddingBottom: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 13, letterSpacing: '.02em' }}>{BV_LEGAL.legal}</div>
        <div style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>(Trade name: Better Vision)</div>
        <div style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 3 }}>
          GK-I Flagship · M-Block Market, GK-I<br />
          New Delhi 110048 · {BV_LEGAL.phone}
        </div>
        <div style={{ marginTop: 6, fontSize: 9, fontFamily: 'var(--font-mono)' }}>
          GSTIN {BV_LEGAL.gstin}<br />
          PAN {BV_LEGAL.pan} · State 07-DL
        </div>
      </div>

      <div style={{ textAlign: 'center', padding: '6px 0 4px', fontSize: 11, fontWeight: 700, letterSpacing: '.16em', textTransform: 'uppercase' }}>
        TAX INVOICE
      </div>
      <div style={{ textAlign: 'center', fontSize: 8.5, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.1em' }}>
        Original for recipient · Rule 46 CGST
      </div>

      <div style={{ marginTop: 8, fontSize: 9.5 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 9 }}>Invoice No.</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 9.5 }}>BV/GK1/2025-26/249183</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 9 }}>Date · Time</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 9.5 }}>19-Apr-26 · 14:22</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 9 }}>Place of supply</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right' }}>07 · Delhi</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 9 }}>Reverse charge</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right' }}>No</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 9 }}>Cashier</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right' }}>Sonia K. · POS-01</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 9 }}>Customer</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right' }}>Aanya Sharma</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 9 }}>Phone · ID</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 9 }}>98115 22100 · CUS-00214</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 9 }}>Customer GSTIN</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right' }}>— (B2C)</td></tr>
          </tbody>
        </table>
      </div>

      <div style={{ borderTop: '1px solid var(--ink)', borderBottom: '1px solid var(--ink)', margin: '8px 0 0', padding: '4px 0', display: 'grid', gridTemplateColumns: '1fr 38px 56px', fontSize: 8.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.08em' }}>
        <span>Item / HSN</span>
        <span style={{ textAlign: 'center' }}>Qty</span>
        <span style={{ textAlign: 'right' }}>Amount</span>
      </div>

      {items.map((it, i) => (
        <div key={i} style={{
          display: 'grid', gridTemplateColumns: '1fr 38px 56px',
          padding: '3px 0', fontSize: it.sub ? 8.5 : 10,
          color: it.sub ? 'var(--ink-3)' : 'var(--ink)',
          borderBottom: it.sub ? 'none' : '1px dashed var(--ink-5)',
        }}>
          <span style={{ lineHeight: 1.3 }}>{it.d}</span>
          {!it.sub ? <span style={{ textAlign: 'center', fontVariantNumeric: 'tabular-nums' }}>{it.q}</span> : <span />}
          {!it.sub ? <span style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: 500 }}>{it.p.toFixed(2)}</span> : <span />}
        </div>
      ))}

      <div style={{ marginTop: 6, fontSize: 9.5 }}>
        {[
          ['Sub-total',            28210.00],
          ['Loyalty discount',     -600.00],
          ['Taxable value',        27610.00],
          ['CGST (assorted rates)', 1666.18],
          ['SGST (assorted rates)', 1666.18],
          ['Round-off',             -0.36],
        ].map(([k, v]) => (
          <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0', color: 'var(--ink-3)' }}>
            <span>{k}</span>
            <span style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--ink)' }}>{v < 0 ? '− ' : ''}{Math.abs(v).toFixed(2)}</span>
          </div>
        ))}
      </div>

      <div style={{ borderTop: '2px solid var(--ink)', marginTop: 6, paddingTop: 5, display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.16em' }}>Grand total</span>
        <span style={{ fontWeight: 700, fontSize: 18, letterSpacing: '-.01em', fontVariantNumeric: 'tabular-nums' }}>{inrI(total)}.00</span>
      </div>
      <div style={{ fontSize: 8.5, color: 'var(--ink-3)', textAlign: 'right', marginTop: 1, lineHeight: 1.3 }}>{inWords(total).replace('Indian Rupees ', '')}</div>

      {/* HSN-wise tax */}
      <div style={{ borderTop: '1px solid var(--ink-4)', borderBottom: '1px solid var(--ink-4)', marginTop: 8, padding: '5px 0', fontSize: 8.5 }}>
        <div style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.08em', marginBottom: 3 }}>HSN-wise tax</div>
        <div style={{ display: 'grid', gridTemplateColumns: '36px 1fr 50px 50px', gap: 2, color: 'var(--ink-3)', fontVariantNumeric: 'tabular-nums' }}>
          <span>9001</span><span>Lens · 12%</span><span style={{ textAlign: 'right' }}>21629.46</span><span style={{ textAlign: 'right' }}>2595.54</span>
          <span>9003</span><span>Frame · 12%</span><span style={{ textAlign: 'right' }}>5803.57</span><span style={{ textAlign: 'right' }}>696.43</span>
          <span>9605</span><span>Cloth · 18%</span><span style={{ textAlign: 'right' }}>176.97</span><span style={{ textAlign: 'right' }}>31.83</span>
        </div>
      </div>

      <div style={{ marginTop: 8, fontSize: 9 }}>
        <div style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.08em', fontSize: 8.5, color: 'var(--ink-4)' }}>Tendered</div>
        <table style={{ width: '100%', fontSize: 9, borderCollapse: 'collapse', marginTop: 2 }}>
          <tbody>
            <tr><td style={{ ...kvK, fontSize: 9, padding: '1px 0' }}>HDFC ****4421</td><td style={{ ...kvV, fontSize: 9, padding: '1px 0', fontFamily: 'var(--font-mono)' }}>auth 102211</td><td style={{ ...kvV, fontSize: 9, padding: '1px 0', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>20000.00</td></tr>
            <tr><td style={{ ...kvK, fontSize: 9, padding: '1px 0' }}>UPI · GPay</td><td style={{ ...kvV, fontSize: 9, padding: '1px 0', fontFamily: 'var(--font-mono)' }}>482911022113</td><td style={{ ...kvV, fontSize: 9, padding: '1px 0', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>8110.00</td></tr>
          </tbody>
        </table>
      </div>

      <div style={{ marginTop: 8, borderTop: '1px dashed var(--ink-5)', paddingTop: 6, fontSize: 9 }}>
        <div style={{ fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.08em', fontSize: 8.5, color: 'var(--ink-4)' }}>Lens job order</div>
        <div style={{ marginTop: 2 }}>Job <b className="mono">JB-GK1-0418</b><br />Ready <b>23-Apr-26 · 18:00</b><br /><span style={{ color: 'var(--ink-3)' }}>Bring this slip + photo ID for pickup</span></div>
      </div>

      <div style={{ marginTop: 8, borderTop: '1px dashed var(--ink-5)', paddingTop: 6, fontSize: 9, textAlign: 'center', color: 'var(--ink-3)', lineHeight: 1.55 }}>
        Goods once sold will only be exchanged within 7 days against this invoice; CL sales are final.<br />
        Subject to {BV_LEGAL.state} jurisdiction only.<br />
        E. & O. E.
      </div>

      <div style={{ marginTop: 10, paddingTop: 8, borderTop: '2px solid var(--ink)', textAlign: 'center', fontSize: 9, color: 'var(--ink-4)' }}>
        <div style={{ textTransform: 'uppercase', letterSpacing: '.14em', fontWeight: 600 }}>Thank you for choosing Better Vision</div>
        <div style={{ marginTop: 3, fontFamily: 'var(--font-mono)', fontSize: 8.5 }}>{BV_LEGAL.web}/invoice/249183</div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   10. PURCHASE ORDER — A4 — vendor contract
   ═══════════════════════════════════════════════════════════════════════════ */
function TplPurchaseOrder() {
  const lines = [
    { d: 'Acuvue Oasys 1-Day · 30-pack · −1.50', hsn: '9001', qty: 6, unit: 'Box', rate: 1485.00, gst: 12 },
    { d: 'Acuvue Oasys 1-Day · 30-pack · −2.00', hsn: '9001', qty: 6, unit: 'Box', rate: 1485.00, gst: 12 },
    { d: 'Acuvue Oasys 1-Day · 30-pack · −2.50', hsn: '9001', qty: 6, unit: 'Box', rate: 1485.00, gst: 12 },
    { d: 'Acuvue Oasys 1-Day · 30-pack · −3.00', hsn: '9001', qty: 6, unit: 'Box', rate: 1485.00, gst: 12 },
  ];
  const sub  = lines.reduce((a, r) => a + r.qty * r.rate, 0);
  const igst = lines.reduce((a, r) => a + r.qty * r.rate * r.gst / 100, 0);
  const total = Math.round(sub + igst);
  const qtyTotal = lines.reduce((a, r) => a + r.qty, 0);

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <LegalHeader
        docType="PURCHASE ORDER · vendor procurement"
        docNo="PO/BV/2025-26/0042"
        copy="ORIGINAL FOR VENDOR · ☐ DUPLICATE FOR PROCUREMENT · ☐ TRIPLICATE FOR ACCOUNTS"
        meta={[
          ['PO date',          '19-Apr-2026'],
          ['Required by',      '23-Apr-2026 · 18:00'],
          ['Payment terms',    'Net-30 · NEFT'],
          ['Delivery terms',   'Door-delivered · DDP'],
          ['Currency',         'INR'],
          ['Supply type',      'Inter-state · IGST applicable'],
          ['Buyer reference',  'Jarvis Stock Sentinel'],
          ['Approval',         'Priya B. · Ops Head'],
        ]}
        showBank={false}
      />

      <PartyBlock blocks={[
        { h: 'Vendor', rows: [
          ['Legal name',  'Johnson & Johnson India Pvt. Ltd.'],
          ['Address',     '4th Flr, Arena Space, JVLR, Andheri (E)'],
          ['City / Pin',  'Mumbai 400059'],
          ['State / Code','Maharashtra / 27'],
          ['GSTIN',       '27AAACJ4863N1ZE'],
          ['PAN',         'AAACJ4863N'],
          ['Contact',     'Ramesh M. · +91 22 6188 9100'],
        ] },
        { h: 'Bill to · Buyer', rows: [
          ['Legal name', BV_LEGAL.legal],
          ['Branch',     'HQ Procurement'],
          ['Address',    'Plot 12, Okhla Phase II, New Delhi 110020'],
          ['State / Code', 'Delhi / 07'],
          ['GSTIN',      BV_LEGAL.gstin],
          ['PAN',        BV_LEGAL.pan],
          ['Contact',    'procurement@bettervision.in'],
        ] },
        { h: 'Ship to', rows: [
          ['Branch',     'GK-I Flagship'],
          ['Address',    BV_LEGAL.storeAddr],
          ['State / Code', 'Delhi / 07'],
          ['GSTIN',      BV_LEGAL.gstin],
          ['Receiving',  'Sonia Khatri · SM'],
          ['Working hrs','10:00 – 19:00 IST'],
        ] },
      ]}/>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr>
            <th style={{ ...tblHead, width: 26 }}>#</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Description</th>
            <th style={tblHead}>HSN</th>
            <th style={tblHead}>Qty</th>
            <th style={tblHead}>UoM</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Unit price</th>
            <th style={tblHead}>IGST</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Taxable</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Total</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((r, i) => {
            const taxable = r.qty * r.rate;
            const tax = taxable * r.gst / 100;
            return (
              <tr key={i}>
                <td style={tblCell}>{i + 1}</td>
                <td style={{ ...tblCell, textAlign: 'left' }}>{r.d}</td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r.hsn}</td>
                <td style={tblNum}>{r.qty}</td>
                <td style={{ ...tblCell, color: 'var(--ink-4)' }}>{r.unit}</td>
                <td style={tblNum}>{r.rate.toFixed(2)}</td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r.gst}%</td>
                <td style={tblNum}>{taxable.toFixed(2)}</td>
                <td style={{ ...tblNum, fontWeight: 600 }}>{(taxable + tax).toFixed(2)}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={3} style={{ ...tblHead, textAlign: 'right' }}>TOTAL</td>
            <td style={{ ...tblHead, fontVariantNumeric: 'tabular-nums' }}>{qtyTotal}</td>
            <td style={tblHead}>Boxes</td>
            <td style={tblHead}>—</td>
            <td style={tblHead}>—</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{sub.toFixed(2)}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontSize: 12 }}>{total.toFixed(2)}</td>
          </tr>
        </tfoot>
      </table>

      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Terms & conditions</div>
          <ol style={{ margin: '4px 0 0 14px', padding: 0, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
            <li>Delivery to GK-I store between 09:00 – 18:00 IST on or before <b>23-Apr-2026</b>.</li>
            <li>Batch expiry must be ≥ 18 months from receipt date. Short-dated stock will be rejected.</li>
            <li>Vendor invoice must reference this PO number; goods received without GRN-match will be returned at vendor cost.</li>
            <li>Payment by NEFT to designated account, Net-30 from GRN sign-off date.</li>
            <li>Price holds for entire qty. Partial supply &gt; 10% requires re-approval (mail trail).</li>
            <li>This PO is governed by the laws of India; disputes under Delhi jurisdiction only.</li>
          </ol>
        </div>
        <div style={{ padding: '10px 16px', fontSize: 11 }}>
          <div style={lblSm}>Reason for order</div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55, marginTop: 4 }}>
            Stock on hand 14 boxes against avg-daily run-rate 2.1; cover 6.6 days against 10-day SLA. Auto-drafted by Jarvis · Stock Sentinel at 09:42 IST on 19-Apr; approved by Ops Head 11:14.
          </div>
          <div style={{ marginTop: 10, fontSize: 11 }}>
            {[
              ['Sub-total',     sub.toFixed(2)],
              ['IGST · 12%',    igst.toFixed(2)],
              ['Freight',       'Included'],
              ['Insurance',     'Included'],
            ].map(([k, v]) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', color: 'var(--ink-3)' }}>
                <span>{k}</span><span style={{ fontFamily: 'var(--font-sans)', fontVariantNumeric: 'tabular-nums', color: 'var(--ink)' }}>{v}</span>
              </div>
            ))}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 6, paddingTop: 6, borderTop: '2px solid var(--ink)' }}>
              <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.14em' }}>PO value</span>
              <span style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{inrI(total)}</span>
            </div>
          </div>
        </div>
      </div>

      <LegalFooter
        rule="Issued under Better Vision Procurement Policy v2.1 (Apr-2025)"
        declaration="We confirm this PO has been generated against approved budget and reorder draft PO-D-0042. The vendor's acceptance, evidenced by acknowledgement or first shipment, shall constitute a binding contract subject to the terms overleaf."
        amountWords={inWords(total)}
      />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   11. GOODS RECEIPT NOTE — A4 — against PO
   ═══════════════════════════════════════════════════════════════════════════ */
function TplGRN() {
  const lines = [
    { d: 'Acuvue Oasys 1-Day · 30pk · −1.50', hsn: '9001', ord: 6, rec: 6, batch: 'AO-2604-1150', exp: '04/2028', qa: 'OK',     val: 9900 },
    { d: 'Acuvue Oasys 1-Day · 30pk · −2.00', hsn: '9001', ord: 6, rec: 6, batch: 'AO-2604-2000', exp: '04/2028', qa: 'OK',     val: 9900 },
    { d: 'Acuvue Oasys 1-Day · 30pk · −2.50', hsn: '9001', ord: 6, rec: 5, batch: 'AO-2604-2500', exp: '04/2028', qa: 'Short 1', val: 8250 },
    { d: 'Acuvue Oasys 1-Day · 30pk · −3.00', hsn: '9001', ord: 6, rec: 6, batch: 'AO-2604-3000', exp: '03/2028', qa: 'OK',     val: 9900 },
  ];
  const ordTotal = lines.reduce((a, r) => a + r.ord, 0);
  const recTotal = lines.reduce((a, r) => a + r.rec, 0);
  const valTotal = lines.reduce((a, r) => a + r.val, 0);

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <LegalHeader
        docType="GOODS RECEIPT NOTE · GRN"
        docNo="GRN/BV/2025-26/0418-22"
        copy="ORIGINAL FOR ACCOUNTS · ☐ DUPLICATE FOR VENDOR · ☐ TRIPLICATE FOR STORE"
        meta={[
          ['Received',       '18-Apr-2026 · 09:40 IST'],
          ['Against PO',     'PO/BV/2025-26/0042'],
          ['Vendor invoice', 'JJ/24/04/2240 dated 17-Apr-2026'],
          ['Vendor GSTIN',   '27AAACJ4863N1ZE'],
          ['Place of supply','07 · Delhi'],
          ['Supply type',    'Inter-state (Maharashtra → Delhi)'],
          ['Status',         'Pending settlement — 1 short'],
        ]}
        showBank={false}
      />

      <PartyBlock blocks={[
        { h: 'Receiving branch · Consignee', rows: [
          ['Branch',     'GK-I Flagship'],
          ['Address',    BV_LEGAL.storeAddr],
          ['GSTIN',      BV_LEGAL.gstin],
          ['Received by','Sonia Khatri · SM · EMP-0142'],
          ['Bay',        'Receiving · 09:40 → 10:08'],
        ] },
        { h: 'Vendor · Consignor', rows: [
          ['Legal name', 'Johnson & Johnson India Pvt. Ltd.'],
          ['Address',    '4th Flr, Arena Space, Andheri (E), Mumbai 400059'],
          ['GSTIN',      '27AAACJ4863N1ZE'],
          ['State / Code', 'Maharashtra / 27'],
          ['Carrier',    'BlueDart Surface · BB-DL-22100'],
        ] },
      ]}/>

      {/* KPI strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', borderBottom: '1.5px solid var(--ink)' }}>
        {[
          ['Qty ordered',  ordTotal + ' Boxes', null],
          ['Qty received', recTotal + ' Boxes (1 short)', 'var(--err)'],
          ['Value received', inr(valTotal), null],
          ['Variance',    '− 1 Box · ' + inr(1650), 'var(--err)'],
        ].map(([k, v, c], i) => (
          <div key={k} style={{ padding: '10px 16px', borderRight: i === 3 ? 'none' : '1px solid var(--ink-4)' }}>
            <div style={lblSm}>{k}</div>
            <div style={{ fontSize: 15, fontWeight: 700, marginTop: 2, color: c || 'var(--ink)', fontVariantNumeric: 'tabular-nums' }}>{v}</div>
          </div>
        ))}
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10.5 }}>
        <thead>
          <tr>
            <th style={{ ...tblHead, width: 26 }}>#</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Item</th>
            <th style={tblHead}>HSN</th>
            <th style={tblHead}>Ord.</th>
            <th style={tblHead}>Rec.</th>
            <th style={tblHead}>Δ</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Batch</th>
            <th style={tblHead}>Expiry</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>QA</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Value (₹)</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((r, i) => {
            const v = r.rec - r.ord;
            return (
              <tr key={i}>
                <td style={tblCell}>{i + 1}</td>
                <td style={{ ...tblCell, textAlign: 'left' }}>{r.d}</td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r.hsn}</td>
                <td style={tblNum}>{r.ord}</td>
                <td style={{ ...tblNum, fontWeight: 600 }}>{r.rec}</td>
                <td style={{ ...tblCell, color: v < 0 ? 'var(--err)' : 'var(--ok)', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{v === 0 ? '0' : (v > 0 ? '+' + v : v)}</td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', textAlign: 'left', fontSize: 10 }}>{r.batch}</td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontSize: 10 }}>{r.exp}</td>
                <td style={{ ...tblCell, textAlign: 'left', fontWeight: 600, color: r.qa === 'OK' ? 'var(--ok)' : 'var(--err)', fontSize: 10 }}>{r.qa}</td>
                <td style={{ ...tblNum, fontWeight: 600 }}>{inr(r.val)}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={9} style={{ ...tblHead, textAlign: 'right' }}>TOTAL RECEIVED VALUE (₹)</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontSize: 12 }}>{inr(valTotal)}</td>
          </tr>
        </tfoot>
      </table>

      {/* Discrepancy callout — but framed conservatively as a documented finding */}
      <div style={{ borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '6px 16px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', borderBottom: '1px solid var(--ink-4)' }}>
          Discrepancy log · short shipment
        </div>
        <div style={{ padding: '8px 16px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, fontSize: 10.5 }}>
          <table style={{ borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={kvK}>Item affected</td><td style={kvV}>Acuvue Oasys 1-Day −2.50</td></tr>
              <tr><td style={kvK}>Variance</td><td style={kvV}>Received 5 vs ordered 6 (−1 box)</td></tr>
              <tr><td style={kvK}>Carton seal at origin</td><td style={kvV}>Intact · marked "5 of 5"</td></tr>
              <tr><td style={kvK}>Root cause</td><td style={kvV}>Vendor packing slip error (under-ship)</td></tr>
            </tbody>
          </table>
          <table style={{ borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={kvK}>Action raised</td><td style={kvV}>Debit note <span className="mono">DN/BV/2025-26/0418-04</span></td></tr>
              <tr><td style={kvK}>Debit value</td><td style={kvV}>{inr(1650)} + IGST 12% = {inr(1848)}</td></tr>
              <tr><td style={kvK}>Replacement ETA</td><td style={kvV}>22-Apr-2026 (confirmed by vendor)</td></tr>
              <tr><td style={kvK}>Settlement</td><td style={kvV}>Net off in next payment cycle</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <LegalFooter
        rule="SOP-INV-08 · GRN must be raised within 24 h of receipt; retained 7 years (CGST Rule 56)"
        amountWords={inWords(valTotal) + ' received against PO'}
        declaration={`We confirm physical receipt of goods listed above on 18-Apr-2026 at GK-I Flagship store. Quantities and batch / expiry details verified against vendor invoice JJ/24/04/2240. One box short — debit note raised; remaining stock posted to ledger. Vendor invoice held pending replacement.`}
        signLabel="For Better Vision · receiving branch"
      />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   12. DAY-END Z-REPORT — A4 — SOP-FIN-02 cash drawer close
   ═══════════════════════════════════════════════════════════════════════════ */
function TplZReport() {
  const tenders = [
    { k: 'Cash',            tx: 9,  amt: 6420,  exp: 6300, dv: +120 },
    { k: 'Card · Credit',   tx: 14, amt: 22100, exp: 22100, dv: 0 },
    { k: 'Card · Debit',    tx: 7,  amt: 9840,  exp: 9840,  dv: 0 },
    { k: 'UPI · GPay',      tx: 8,  amt: 7250,  exp: 7250,  dv: 0 },
    { k: 'UPI · PhonePe',   tx: 3,  amt: 1800,  exp: 1800,  dv: 0 },
    { k: 'BV Wallet',       tx: 1,  amt: 420,   exp: 420,   dv: 0 },
    { k: 'Gift voucher',    tx: 0,  amt: 0,     exp: 0,     dv: 0 },
  ];
  const tot = tenders.reduce((a, t) => ({ tx: a.tx + t.tx, amt: a.amt + t.amt, exp: a.exp + t.exp, dv: a.dv + t.dv }), { tx: 0, amt: 0, exp: 0, dv: 0 });
  const cashCount = [
    ['₹ 500', 5, 2500], ['₹ 200', 8, 1600], ['₹ 100', 12, 1200],
    ['₹ 50', 10, 500], ['₹ 20', 18, 360], ['₹ 10', 16, 160],
    ['Coins', '—', 100],
  ];

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <StaffHeader
        docType="Z-REPORT · DAY-END CASH RECONCILIATION"
        docNo="Z/BV/2025-26/0419-01"
        copy="FINANCE COPY · SOP-FIN-02"
        meta={[
          ['Date',    '19-Apr-26'],
          ['Shift',   '10:00 → 21:14'],
          ['Cashier', 'Sonia K.'],
          ['POS',     'POS-01'],
        ]}
      />

      {/* KPI strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', borderBottom: '1.5px solid var(--ink)' }}>
        {[
          ['Gross sales',  inrI(48210), null],
          ['Net sales',    inrI(47830), null],
          ['Transactions', '42',         null],
          ['Avg basket',   inrI(1148),  null],
          ['Refunds (CN)', inrI(380) + ' · 2 tx', null],
          ['Cash variance', '+ ₹ 120 (within tol.)', 'var(--warn)'],
        ].map(([k, v, c], i) => (
          <div key={k} style={{ padding: '8px 12px', borderRight: i === 5 ? 'none' : '1px solid var(--ink-4)' }}>
            <div style={{ ...lblSm, fontSize: 8.5 }}>{k}</div>
            <div style={{ fontSize: 14, fontWeight: 700, marginTop: 2, color: c || 'var(--ink)', fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em' }}>{v}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr' }}>
        {/* Tender table */}
        <div style={{ borderRight: '1px solid var(--ink-4)' }}>
          <div style={{ padding: '6px 16px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', borderBottom: '1px solid var(--ink-4)' }}>
            Tender reconciliation
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr>
                <th style={{ ...tblHead, textAlign: 'left' }}>Tender mode</th>
                <th style={tblHead}>Tx</th>
                <th style={{ ...tblHead, textAlign: 'right' }}>System</th>
                <th style={{ ...tblHead, textAlign: 'right' }}>Counted</th>
                <th style={{ ...tblHead, textAlign: 'right' }}>Δ</th>
              </tr>
            </thead>
            <tbody>
              {tenders.map(t => (
                <tr key={t.k}>
                  <td style={{ ...tblCell, textAlign: 'left' }}>{t.k}</td>
                  <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{t.tx}</td>
                  <td style={tblNum}>{inr(t.exp)}</td>
                  <td style={{ ...tblNum, fontWeight: 600 }}>{inr(t.amt)}</td>
                  <td style={{ ...tblCell, textAlign: 'right', fontFamily: 'var(--font-mono)', color: t.dv === 0 ? 'var(--ink-4)' : t.dv > 0 ? 'var(--warn)' : 'var(--err)', fontWeight: 600 }}>
                    {t.dv === 0 ? '0' : (t.dv > 0 ? '+' + t.dv : t.dv)}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td style={{ ...tblHead, textAlign: 'left' }}>TOTAL</td>
                <td style={{ ...tblHead, fontVariantNumeric: 'tabular-nums' }}>{tot.tx}</td>
                <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{inr(tot.exp)}</td>
                <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{inr(tot.amt)}</td>
                <td style={{ ...tblHead, textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--warn)' }}>+ {tot.dv}</td>
              </tr>
            </tfoot>
          </table>
        </div>

        {/* Cash denomination */}
        <div>
          <div style={{ padding: '6px 16px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', borderBottom: '1px solid var(--ink-4)' }}>
            Cash count
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr>
                <th style={{ ...tblHead, textAlign: 'left' }}>Denom.</th>
                <th style={tblHead}>Pcs</th>
                <th style={{ ...tblHead, textAlign: 'right' }}>Value</th>
              </tr>
            </thead>
            <tbody>
              {cashCount.map((c, i) => (
                <tr key={i}>
                  <td style={{ ...tblCell, textAlign: 'left' }}>{c[0]}</td>
                  <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{c[1]}</td>
                  <td style={tblNum}>{inr(c[2])}</td>
                </tr>
              ))}
              <tr>
                <td style={{ ...tblCell, textAlign: 'left', fontWeight: 600, background: 'var(--bg-sunk)' }}>Counted cash</td>
                <td style={{ ...tblCell, background: 'var(--bg-sunk)' }} />
                <td style={{ ...tblNum, fontWeight: 700, background: 'var(--bg-sunk)' }}>{inr(6420)}</td>
              </tr>
              <tr>
                <td style={{ ...tblCell, textAlign: 'left', color: 'var(--ink-4)' }}>− Opening float</td>
                <td style={tblCell} />
                <td style={{ ...tblNum, color: 'var(--ink-3)' }}>{inr(2000)}</td>
              </tr>
              <tr>
                <td style={{ ...tblCell, textAlign: 'left', color: 'var(--ink-4)' }}>= System cash</td>
                <td style={tblCell} />
                <td style={tblNum}>{inr(4300)}</td>
              </tr>
              <tr>
                <td style={{ ...tblHead, textAlign: 'left', color: 'var(--warn)' }}>Variance</td>
                <td style={tblHead} />
                <td style={{ ...tblHead, textAlign: 'right', color: 'var(--warn)', fontVariantNumeric: 'tabular-nums' }}>+ ₹ 120.00</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* GST summary + events */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', borderTop: '1.5px solid var(--ink)' }}>
        <div style={{ borderRight: '1px solid var(--ink-4)' }}>
          <div style={{ padding: '6px 16px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', borderBottom: '1px solid var(--ink-4)' }}>
            Output tax summary · HSN-wise
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr>
                <th style={{ ...tblHead, textAlign: 'left' }}>HSN</th>
                <th style={{ ...tblHead, textAlign: 'left' }}>Category</th>
                <th style={tblHead}>Rate</th>
                <th style={{ ...tblHead, textAlign: 'right' }}>Taxable</th>
                <th style={{ ...tblHead, textAlign: 'right' }}>CGST</th>
                <th style={{ ...tblHead, textAlign: 'right' }}>SGST</th>
              </tr>
            </thead>
            <tbody>
              {[
                ['9001', 'Lenses (Rx, CL)',    '12%', 26420, 1585.20, 1585.20],
                ['9003', 'Frames',             '12%', 18920, 1135.20, 1135.20],
                ['9605', 'Accessories',        '18%',   452,   40.68,   40.68],
                ['9984', 'Service · fitting',  '18%',  1240,  111.60,  111.60],
              ].map((r, i) => (
                <tr key={i}>
                  <td style={{ ...tblCell, textAlign: 'left', fontFamily: 'var(--font-mono)' }}>{r[0]}</td>
                  <td style={{ ...tblCell, textAlign: 'left' }}>{r[1]}</td>
                  <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r[2]}</td>
                  <td style={tblNum}>{Number(r[3]).toFixed(2)}</td>
                  <td style={tblNum}>{Number(r[4]).toFixed(2)}</td>
                  <td style={tblNum}>{Number(r[5]).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td colSpan={3} style={{ ...tblHead, textAlign: 'right' }}>OUTPUT TAX TOTAL</td>
                <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>47032.00</td>
                <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>2872.68</td>
                <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>2872.68</td>
              </tr>
            </tfoot>
          </table>
        </div>

        <div>
          <div style={{ padding: '6px 16px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', borderBottom: '1px solid var(--ink-4)' }}>
            Ops events log · 19-Apr-2026
          </div>
          <div style={{ padding: '8px 16px', fontSize: 10.5, lineHeight: 1.65, color: 'var(--ink-2)' }}>
            <div><span style={{ color: 'var(--ink-4)', fontFamily: 'var(--font-mono)' }}>10:04</span> · Cycle count A-12 → A-18 opened (Riya P.)</div>
            <div><span style={{ color: 'var(--ink-4)', fontFamily: 'var(--font-mono)' }}>11:30</span> · Credit note <span className="mono">CN/…/0418-03</span> · {inr(8950)}</div>
            <div><span style={{ color: 'var(--ink-4)', fontFamily: 'var(--font-mono)' }}>14:22</span> · Invoice <span className="mono">…249183</span> · {inr(28110)} · 3 lines</div>
            <div><span style={{ color: 'var(--ink-4)', fontFamily: 'var(--font-mono)' }}>16:48</span> · PO <span className="mono">PO/…/0042</span> approved · J&amp;J</div>
            <div><span style={{ color: 'var(--ink-4)', fontFamily: 'var(--font-mono)' }}>20:15</span> · GRN <span className="mono">GRN/…/0418-22</span> · 1 short</div>
            <div><span style={{ color: 'var(--ink-4)', fontFamily: 'var(--font-mono)' }}>21:14</span> · Drawer counted · Z-report sealed</div>
          </div>
          <div style={{ padding: '6px 16px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', borderTop: '1px solid var(--ink-4)' }}>
            Variance log · auto-task
          </div>
          <div style={{ padding: '8px 16px', fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
            <b>TSK-2211 · P1</b> · cash variance + ₹ 120 within ± ₹ 200 tolerance — does not escalate; will reconcile against next-day opening float. Owner: Sonia K. Auto-resolves at 09:30 IST tomorrow if no further drift.
          </div>
        </div>
      </div>

      <LegalFooter
        rule="SOP-FIN-02 · Z-report sealed at print · drawer locked until next opening · retained 7 years (CGST Rule 56)"
        amountWords={'Net sales of ' + inWords(47830) + ' settled across ' + tot.tx + ' tenders'}
        declaration="We declare that the day's takings recorded herein have been physically counted, reconciled against POS records, and lodged in the cash drawer / merchant accounts. Any variance is documented in the variance log above; resolution is tracked in Tasks."
        signLabel="For Better Vision · GK-I Flagship"
      />
    </div>
  );
}

Object.assign(window, {
  TplBarcodeLabels, TplCountSheet, TplThermalReceipt,
  TplPurchaseOrder, TplGRN, TplZReport, Barcode,
});
