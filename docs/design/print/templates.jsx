/* global React, BV_LEGAL, LegalHeader, PartyBlock, LegalFooter, StatusStamp, FieldChip,
   inWords, inr, inrI, kvK, kvV, lblSm, tblHead, tblCell, tblNum */
/* The 6 customer-facing statutory documents. Visual language: bordered, ALL-CAPS,
   sans-only, copy-marker bars, declarations, authorised-signatory blocks.
   Pulls real data from MOCK / matches the POS + Clinical flows. */

/* ═══════════════════════════════════════════════════════════════════════════
   1. TAX INVOICE — A4 — Rule 46 CGST · 21 mandatory fields
   ═══════════════════════════════════════════════════════════════════════════ */
function TplInvoice() {
  const lines = [
    { d: 'Ray-Ban RB2140 Wayfarer Classic',         hsn: '9003', desc2: 'Black · 50-22-150 · acetate',                   qty: 1, unit: 'Nos', rate: 6339.29, gst: 12 },
    { d: 'Essilor Varilux X-Series · 1.60',         hsn: '9001', desc2: 'Crizal Alizé UV · Rx mounted · brown-15',       qty: 1, unit: 'Pair', rate: 15089.29, gst: 12 },
    { d: 'Acuvue Oasys 1-Day · 30-pack',            hsn: '9001', desc2: '−2.50 (1 box) + −3.00 (1 box)',                  qty: 2, unit: 'Box', rate: 1785.71, gst: 12 },
    { d: 'BV Microfibre cloth · Large',             hsn: '9605', desc2: '210 × 210 mm · grey',                            qty: 2, unit: 'Nos', rate: 89.28,   gst: 18 },
  ];
  const sub  = lines.reduce((a, r) => a + r.qty * r.rate, 0);
  const disc = 600;
  const taxable = sub - disc;
  const cgst = lines.reduce((a, r) => a + (r.qty * r.rate * r.gst) / 100 / 2, 0) - (disc * 12 / 100 / 2);
  const sgst = cgst;
  const total = Math.round(taxable + cgst + sgst);

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <LegalHeader
        docType="TAX INVOICE · Rule 46, CGST Rules 2017"
        docNo="BV/GK1/2025-26/249183"
        copy="ORIGINAL FOR RECIPIENT"
        meta={[
          ['Invoice date', '19-Apr-2026 · 14:22 IST'],
          ['Due date',     '19-Apr-2026 · paid'],
          ['Place of supply', '07 · Delhi (intra-state)'],
          ['Reverse charge', 'No'],
          ['Supply type',  'B2C (small) · sale of goods'],
          ['IRN',          '5fab2c8e91d4… (e-invoice exempt)'],
          ['Payment terms','Card 70% · UPI 30% · settled'],
        ]}
        showBank={false}
      />

      <PartyBlock blocks={[
        { h: 'Bill to · Recipient', rows: [
          ['Name',     'Ms. Aanya Sharma'],
          ['Address',  'B-42, Panchsheel Park, New Delhi 110017'],
          ['State / Code', 'Delhi / 07'],
          ['Phone',    '+91 98115 22100'],
          ['Customer No.', 'CUS-00214 (since Aug 2022)'],
          ['GSTIN',    '— (B2C — un-registered)'],
        ] },
        { h: 'Ship to · Place of delivery', rows: [
          ['Mode',      'In-store collection · at counter'],
          ['Address',   BV_LEGAL.storeAddr],
          ['State / Code', 'Delhi / 07'],
          ['Cashier',   'Sonia Khatri · EMP-0142 · POS-01'],
          ['Shift',     '10:00 – 21:00 · 19-Apr-2026'],
          ['Doctor Rx', 'Dr. Ritu Malhotra · DMC-4412 · 18-Apr-2026'],
        ] },
      ]}/>

      {/* Line items */}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr>
            <th style={{ ...tblHead, width: 28 }}>#</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Description of goods</th>
            <th style={tblHead}>HSN</th>
            <th style={tblHead}>Qty</th>
            <th style={tblHead}>UoM</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Rate (₹)</th>
            <th style={tblHead}>Per</th>
            <th style={tblHead}>Disc.</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Taxable (₹)</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>CGST</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>SGST</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Total (₹)</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((r, i) => {
            const tax = r.qty * r.rate * r.gst / 100;
            return (
              <tr key={i}>
                <td style={tblCell}>{i + 1}</td>
                <td style={{ ...tblCell, textAlign: 'left' }}>
                  <div style={{ fontWeight: 600 }}>{r.d}</div>
                  <div style={{ fontSize: 10, color: 'var(--ink-4)', marginTop: 1 }}>{r.desc2}</div>
                </td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r.hsn}</td>
                <td style={tblNum}>{r.qty}</td>
                <td style={{ ...tblCell, color: 'var(--ink-4)' }}>{r.unit}</td>
                <td style={tblNum}>{r.rate.toFixed(2)}</td>
                <td style={{ ...tblCell, color: 'var(--ink-4)' }}>{r.unit}</td>
                <td style={tblNum}>—</td>
                <td style={tblNum}>{(r.qty * r.rate).toFixed(2)}</td>
                <td style={tblNum}>{(tax / 2).toFixed(2)}<div style={{ fontSize: 8.5, color: 'var(--ink-4)' }}>{r.gst / 2}%</div></td>
                <td style={tblNum}>{(tax / 2).toFixed(2)}<div style={{ fontSize: 8.5, color: 'var(--ink-4)' }}>{r.gst / 2}%</div></td>
                <td style={{ ...tblNum, fontWeight: 600 }}>{(r.qty * r.rate + tax).toFixed(2)}</td>
              </tr>
            );
          })}
          {/* Discount line */}
          <tr>
            <td style={tblCell}>—</td>
            <td style={{ ...tblCell, textAlign: 'left', fontStyle: 'italic', color: 'var(--ink-3)' }}>Less: BV-MEMBER loyalty discount (5%)</td>
            <td style={tblCell}>—</td>
            <td style={tblCell}>—</td>
            <td style={tblCell}>—</td>
            <td style={tblNum}>—</td>
            <td style={tblCell}>—</td>
            <td style={tblNum}>—</td>
            <td style={tblNum}>− 600.00</td>
            <td style={tblNum}>− 36.00</td>
            <td style={tblNum}>− 36.00</td>
            <td style={{ ...tblNum, fontWeight: 600 }}>− 672.00</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <td style={{ ...tblHead, textAlign: 'right' }} colSpan={8}>TOTAL</td>
            <td style={{ ...tblHead, textAlign: 'right', fontFamily: 'var(--font-sans)', fontVariantNumeric: 'tabular-nums' }}>{taxable.toFixed(2)}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontFamily: 'var(--font-sans)', fontVariantNumeric: 'tabular-nums' }}>{cgst.toFixed(2)}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontFamily: 'var(--font-sans)', fontVariantNumeric: 'tabular-nums' }}>{sgst.toFixed(2)}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontFamily: 'var(--font-sans)', fontVariantNumeric: 'tabular-nums' }}>{total.toFixed(2)}</td>
          </tr>
        </tfoot>
      </table>

      {/* HSN-wise consolidated tax */}
      <div style={{ borderBottom: '1px solid var(--ink-4)' }}>
        <div style={{ padding: '6px 18px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', borderBottom: '1px solid var(--ink-4)' }}>
          Tax summary · HSN-wise (Rule 46 CGST)
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10.5 }}>
          <thead>
            <tr>
              <th style={tblHead}>HSN</th>
              <th style={{ ...tblHead, textAlign: 'right' }}>Taxable value</th>
              <th style={tblHead}>CGST rate</th>
              <th style={{ ...tblHead, textAlign: 'right' }}>CGST amt</th>
              <th style={tblHead}>SGST rate</th>
              <th style={{ ...tblHead, textAlign: 'right' }}>SGST amt</th>
              <th style={{ ...tblHead, textAlign: 'right' }}>Total tax</th>
            </tr>
          </thead>
          <tbody>
            {[
              ['9001', 22232.14, 6, 1333.93, 6, 1333.93],
              ['9003', 6339.29, 6, 380.36, 6, 380.36],
              ['9605', 178.57,  9, 16.07,  9, 16.07],
            ].map((r, i) => (
              <tr key={i}>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r[0]}</td>
                <td style={tblNum}>{r[1].toFixed(2)}</td>
                <td style={tblCell}>{r[2]}%</td>
                <td style={tblNum}>{r[3].toFixed(2)}</td>
                <td style={tblCell}>{r[4]}%</td>
                <td style={tblNum}>{r[5].toFixed(2)}</td>
                <td style={{ ...tblNum, fontWeight: 600 }}>{(r[3] + r[5]).toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Totals strip + grand */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '10px 18px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Notes</div>
          <ol style={{ margin: '4px 0 0 16px', padding: 0, fontSize: 10, color: 'var(--ink-3)', lineHeight: 1.55 }}>
            <li>Lens job order <b className="mono">JB-GK1-0418</b> · ready for pickup on or after 23-Apr-2026 · bring this invoice.</li>
            <li>Frames exchangeable within 7 days if unused, unscratched, and with original packaging.</li>
            <li>Contact lens sales are non-refundable once dispensed (per SOP-CX-04).</li>
            <li>Lens warranty 6 months · frame warranty 12 months · manufacturing defects only.</li>
          </ol>
        </div>
        <div style={{ padding: '10px 18px', fontSize: 11 }}>
          {[
            ['Sub-total',        sub],
            ['Less: discount',  -disc],
            ['Taxable value',    taxable],
            ['Add: CGST',        cgst],
            ['Add: SGST',        sgst],
            ['Round-off',        0],
          ].map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', color: 'var(--ink-3)' }}>
              <span>{k}</span>
              <span style={{ fontFamily: 'var(--font-sans)', fontVariantNumeric: 'tabular-nums', color: 'var(--ink)' }}>
                {v < 0 ? '− ' : ''}{Math.abs(v).toFixed(2)}
              </span>
            </div>
          ))}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 8, padding: '8px 0 0', borderTop: '2px solid var(--ink)' }}>
            <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.14em' }}>Grand total</span>
            <span style={{ fontFamily: 'var(--font-sans)', fontWeight: 700, fontSize: 20, fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em' }}>{inr(total)}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 6 }}>
            <StatusStamp tone="ok">Paid in full</StatusStamp>
          </div>
        </div>
      </div>

      <LegalFooter
        rule="Issued under Sec. 31 CGST Act 2017 r/w Rule 46"
        amountWords={inWords(total)}
        declaration={`We declare that this invoice shows the actual price of the goods described and that all particulars are true and correct. Output tax of CGST + SGST (intra-state supply, place of supply ${BV_LEGAL.state}/${BV_LEGAL.stateCode}) is collected as shown.`}
      />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   2. PRESCRIPTION CARD — A5 — patient copy
   ═══════════════════════════════════════════════════════════════════════════ */
function TplRxCard() {
  return (
    <div className="paper a5" data-doc style={{ color: 'var(--ink)' }}>
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center',
        padding: '6px 16px', background: 'var(--ink)', color: '#fff',
        fontSize: 10, letterSpacing: '.16em', fontWeight: 600, textTransform: 'uppercase',
      }}>
        <span>SPECTACLE PRESCRIPTION · Form Rx-01</span>
        <span style={{ opacity: .8, fontSize: 9.5 }}>PATIENT COPY</span>
      </div>

      <div style={{ padding: '12px 18px', borderBottom: '1.5px solid var(--ink)', display: 'grid', gridTemplateColumns: '34px 1fr auto', gap: 10, alignItems: 'flex-start' }}>
        <div style={{ width: 34, height: 34, border: '1.5px solid var(--ink)', display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 18 }}>B</div>
        <div>
          <div style={{ fontSize: 13.5, fontWeight: 700 }}>{BV_LEGAL.legal}</div>
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', textTransform: 'uppercase', letterSpacing: '.08em', marginTop: 1 }}>Optometry & Vision Care · GK-I clinic</div>
          <div style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 4, lineHeight: 1.5 }}>
            {BV_LEGAL.storeAddr}<br />
            {BV_LEGAL.phone} · <span className="mono">Drug Lic. {BV_LEGAL.drug}</span>
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={lblSm}>Rx No.</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700, marginTop: 2 }}>RX/GK1/2026/4418</div>
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 6 }}>Issued <span className="mono">18-Apr-2026</span><br />Valid until <span className="mono">18-Apr-2027</span></div>
        </div>
      </div>

      {/* Patient info */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '8px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Patient</div>
          <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse', marginTop: 4 }}>
            <tbody>
              <tr><td style={kvK}>Name</td><td style={{ ...kvV, fontWeight: 700 }}>Ms. Aanya Sharma</td></tr>
              <tr><td style={kvK}>Age / Sex</td><td style={kvV}>34 yrs / Female</td></tr>
              <tr><td style={kvK}>Phone</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)' }}>+91 98115 22100</td></tr>
              <tr><td style={kvK}>Patient ID</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)' }}>CUS-00214</td></tr>
              <tr><td style={kvK}>Chief complaint</td><td style={kvV}>Blurred near vision, occasional headaches</td></tr>
              <tr><td style={kvK}>Last exam</td><td style={kvV}>14-Mar-2024 · here</td></tr>
            </tbody>
          </table>
        </div>
        <div style={{ padding: '8px 16px' }}>
          <div style={lblSm}>Examination</div>
          <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse', marginTop: 4 }}>
            <tbody>
              <tr><td style={kvK}>Exam date</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)' }}>18-Apr-2026</td></tr>
              <tr><td style={kvK}>Method</td><td style={kvV}>Subjective · auto-refraction · slit-lamp</td></tr>
              <tr><td style={kvK}>IOP (OD/OS)</td><td style={kvV}>14 / 15 mmHg (NCT)</td></tr>
              <tr><td style={kvK}>Cover test</td><td style={kvV}>Orthophoric · stereo 40″</td></tr>
              <tr><td style={kvK}>Dilation</td><td style={kvV}>Not performed</td></tr>
              <tr><td style={kvK}>Referral</td><td style={kvV}>None · review in 12 months</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Rx table */}
      <div style={{ padding: '10px 16px', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={lblSm}>Refraction (best corrected visual acuity)</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 6, fontSize: 11 }}>
          <thead>
            <tr>
              <th style={{ ...tblHead, textAlign: 'left' }}>Eye</th>
              <th style={tblHead}>SPH</th>
              <th style={tblHead}>CYL</th>
              <th style={tblHead}>AXIS</th>
              <th style={tblHead}>ADD</th>
              <th style={tblHead}>PRISM</th>
              <th style={tblHead}>BASE</th>
              <th style={tblHead}>VA</th>
              <th style={tblHead}>PD (mm)</th>
            </tr>
          </thead>
          <tbody>
            {[
              ['OD · Right', '−2.50', '−0.75', '175°', '+1.25', '—', '—', '6/6', '32.0'],
              ['OS · Left',  '−3.00', '−0.50', '5°',   '+1.25', '—', '—', '6/6', '31.5'],
            ].map(r => (
              <tr key={r[0]}>
                <td style={{ ...tblCell, textAlign: 'left', fontWeight: 700, background: 'var(--bg-sunk)' }}>{r[0]}</td>
                {r.slice(1).map((v, j) => (
                  <td key={j} style={{ ...tblCell, fontWeight: j < 5 ? 600 : 400, fontSize: j < 5 ? 14 : 11, fontVariantNumeric: 'tabular-nums' }}>{v}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          <FieldChip k="Lens type" v="Progressive · 1.60 index" />
          <FieldChip k="Coating" v="Crizal Alizé UV" />
          <FieldChip k="Tint" v="Brown-15" />
          <FieldChip k="Fitting height" v="18 mm" mono />
        </div>
      </div>

      {/* Clinical notes + advice */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '8px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Clinical findings</div>
          <ul style={{ margin: '4px 0 0 14px', padding: 0, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.5 }}>
            <li>Anterior segment: clear cornea, deep AC, reactive pupils</li>
            <li>Posterior segment: healthy macula, normal cup-disc ratio</li>
            <li>Colour vision: normal (Ishihara 17/17)</li>
            <li>No diabetic / hypertensive retinopathy signs</li>
          </ul>
        </div>
        <div style={{ padding: '8px 16px' }}>
          <div style={lblSm}>Advice to patient</div>
          <ul style={{ margin: '4px 0 0 14px', padding: 0, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.5 }}>
            <li>Wear progressive lens for distance + near; remove for reading only if comfortable.</li>
            <li>Avoid night-driving until adapted (≈ 2 weeks).</li>
            <li>20-20-20 rule for screen work.</li>
            <li>Return for review in 12 months or sooner if symptomatic.</li>
          </ul>
        </div>
      </div>

      {/* Optometrist signature block — registration is statutory */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderBottom: '1px solid var(--ink-4)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Prescribing optometrist</div>
          <div style={{ fontSize: 13, fontWeight: 700, marginTop: 4 }}>Dr. Ritu Malhotra</div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 1 }}>MBBS, DOMS · Consultant Optometrist</div>
          <table style={{ marginTop: 6, fontSize: 10, borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={kvK}>DMC reg.</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)', fontSize: 10 }}>DMC/R-4412/2014</td></tr>
              <tr><td style={kvK}>NCAHP UID</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)', fontSize: 10 }}>NCAHP-OPT-IN-22-04412</td></tr>
              <tr><td style={kvK}>Practice at</td><td style={{ ...kvV, fontSize: 10 }}>BV-GK1 · Chamber 2</td></tr>
            </tbody>
          </table>
        </div>
        <div style={{ padding: '10px 16px' }}>
          <div style={lblSm}>Signature</div>
          <div style={{ height: 42, marginTop: 4, borderBottom: '0.5px solid var(--ink-4)' }} />
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 4, display: 'flex', justifyContent: 'space-between' }}>
            <span>Optometrist · with clinic stamp</span>
            <span style={{ color: 'var(--ink-4)' }}>[Stamp]</span>
          </div>
        </div>
      </div>

      <div style={{ padding: '7px 16px', fontSize: 9, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em', textAlign: 'center' }}>
        This Rx is valid for 12 months · the patient may use this Rx with any registered optician of their choice · NCAHP-registered practice
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   3. LENS JOB CARD — A5 — internal lab dispatch
   ═══════════════════════════════════════════════════════════════════════════ */
function TplJobCard() {
  return (
    <div className="paper a5" data-doc style={{ color: 'var(--ink)' }}>
      <StaffHeader
        docType="LENS LAB · JOB ORDER · Form Lab-03"
        docNo="JB-GK1-0418"
        copy="STORE COPY · NOT FOR CUSTOMER"
        meta={[
          ['Created',  '19-Apr · 14:25'],
          ['Promised', '23-Apr · 18:00'],
          ['Priority', 'Standard'],
          ['Lab',      'Essilor Pune'],
        ]}
      />

      <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '8px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Job barcode</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 700, marginTop: 2, letterSpacing: '.06em' }}>JB-GK1-0418</div>
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 2 }}>Linked invoice <span className="mono">BV/GK1/2025-26/249183</span></div>
        </div>
        <div style={{ padding: '8px 16px' }}>
          <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={kvK}>Dispatch</td><td style={kvV}>20-Apr · DTDC AWB-D-2241</td></tr>
              <tr><td style={kvK}>Vendor</td><td style={kvV}>Essilor Pune · V-0044</td></tr>
              <tr><td style={kvK}>SOP</td><td style={kvV}>SOP-LAB-03 · retain 12 m</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <PartyBlock blocks={[
        { h: 'Customer', rows: [
          ['Name',  'Ms. Aanya Sharma'],
          ['Phone', '+91 98115 22100'],
          ['Patient ID', 'CUS-00214'],
          ['WhatsApp', 'Opt-in · status updates'],
        ] },
        { h: 'Frame to fit', rows: [
          ['SKU',   'FRM-RB-2140-BLK-50'],
          ['Model', 'Ray-Ban RB2140 Wayfarer'],
          ['Spec',  'Acetate · Black · 50-22-150'],
          ['Bridge / Tmpl', '22 mm / 150 mm'],
        ] },
      ]}/>

      {/* Lens specification */}
      <div style={{ borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '6px 16px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em' }}>Lens specification</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10.5 }}>
          <tbody>
            <tr><td style={{ ...kvK, padding: '4px 16px' }}>Lens type</td><td style={{ ...kvV, padding: '4px 8px' }}>Progressive · Essilor Varilux X-Series</td>
                <td style={{ ...kvK, padding: '4px 8px' }}>Material / Index</td><td style={{ ...kvV, padding: '4px 16px 4px 8px' }}>Plastic CR-39 · 1.60</td></tr>
            <tr><td style={{ ...kvK, padding: '4px 16px' }}>Coating</td><td style={{ ...kvV, padding: '4px 8px' }}>Crizal Alizé UV (anti-reflective)</td>
                <td style={{ ...kvK, padding: '4px 8px' }}>Tint</td><td style={{ ...kvV, padding: '4px 16px 4px 8px' }}>Brown-15 · uniform</td></tr>
            <tr><td style={{ ...kvK, padding: '4px 16px' }}>Fitting height</td><td style={{ ...kvV, padding: '4px 8px' }}>18 mm both eyes</td>
                <td style={{ ...kvK, padding: '4px 8px' }}>Decentration</td><td style={{ ...kvV, padding: '4px 16px 4px 8px' }}>Per PD; verify before edging</td></tr>
          </tbody>
        </table>
      </div>

      {/* Rx — what the lab cuts to */}
      <div style={{ padding: '8px 16px', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={lblSm}>Prescription · lens must be cut to</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 4, fontSize: 11 }}>
          <thead>
            <tr>
              <th style={{ ...tblHead, textAlign: 'left' }}>Eye</th>
              <th style={tblHead}>SPH</th><th style={tblHead}>CYL</th><th style={tblHead}>AXIS</th>
              <th style={tblHead}>ADD</th><th style={tblHead}>PD</th><th style={tblHead}>OC ht</th>
              <th style={tblHead}>Prism</th>
            </tr>
          </thead>
          <tbody>
            {[['OD', '−2.50', '−0.75', '175°', '+1.25', '32.0', '18', '—'],
              ['OS', '−3.00', '−0.50', '5°',   '+1.25', '31.5', '18', '—']].map(r => (
              <tr key={r[0]}>
                <td style={{ ...tblCell, textAlign: 'left', fontWeight: 700, background: 'var(--bg-sunk)' }}>{r[0]}</td>
                {r.slice(1).map((v, j) => (
                  <td key={j} style={{ ...tblCell, fontWeight: 600, fontSize: 13 }}>{v}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ marginTop: 6, fontSize: 9.5, color: 'var(--ink-3)' }}>
          Reconciled by <b>Sonia K.</b> against Rx <span className="mono">RX/GK1/2026/4418</span> · cross-check OK · no tolerance breach.
        </div>
      </div>

      {/* Process checklist */}
      <div style={{ padding: '8px 16px', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={lblSm}>Process checklist · sign each step</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 4, fontSize: 10.5 }}>
          <thead>
            <tr>
              <th style={{ ...tblHead, width: 26 }}>✓</th>
              <th style={{ ...tblHead, textAlign: 'left' }}>Step</th>
              <th style={tblHead}>Owner</th>
              <th style={tblHead}>Date / time</th>
              <th style={{ ...tblHead, textAlign: 'left' }}>Sign</th>
            </tr>
          </thead>
          <tbody>
            {[
              ['☑', 'Rx reconciled against patient card',     'Sonia K.',     '19/4 14:25', '— signed —'],
              ['☑', 'Lens surfacing (Rx grinding)',           'Essilor Pune', '20/4 11:10', '— signed —'],
              ['☑', 'Coating · Crizal Alizé UV applied',      'Essilor Pune', '21/4 09:45', '— signed —'],
              ['☐', 'Edging & fitting in frame',              'Karan T.',     '—',          ' '],
              ['☐', 'QC · power check + cosmetic',            'Riya P.',     '—',          ' '],
              ['☐', 'Ready · customer SMS',                   'POS-01',       '—',          ' '],
            ].map((r, i) => (
              <tr key={i}>
                <td style={{ ...tblCell, fontSize: 14, color: r[0] === '☑' ? 'var(--ok)' : 'var(--ink-4)', fontWeight: 700 }}>{r[0]}</td>
                <td style={{ ...tblCell, textAlign: 'left' }}>{r[1]}</td>
                <td style={tblCell}>{r[2]}</td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontSize: 10 }}>{r[3]}</td>
                <td style={{ ...tblCell, textAlign: 'left', minWidth: 80, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-4)' }}>{r[4]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ padding: '8px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 10, color: 'var(--ink-3)' }}>
        <span>Pickup against invoice <b className="mono">BV/GK1/2025-26/249183</b> · balance due <b>{inr(0)}</b></span>
        <span>SOP-LAB-03 · job card retained 12 months</span>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   4. QUEUE TOKEN — 80 mm thermal — clinical waiting room
   ═══════════════════════════════════════════════════════════════════════════ */
function TplToken() {
  return (
    <div className="paper thermal" data-doc style={{ padding: '14px 14px 18px', color: 'var(--ink)', fontFamily: 'var(--font-sans)', fontSize: 10.5 }}>
      <div style={{ textAlign: 'center', borderBottom: '2px solid var(--ink)', paddingBottom: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 14, letterSpacing: '.02em' }}>{BV_LEGAL.legal}</div>
        <div style={{ fontSize: 9, color: 'var(--ink-3)', textTransform: 'uppercase', letterSpacing: '.12em', marginTop: 2 }}>GK-I clinic · optometry chamber</div>
        <div style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>{BV_LEGAL.storeAddr.split(',').slice(0, 2).join(',')}<br />{BV_LEGAL.phone} · <span className="mono">Drug Lic. {BV_LEGAL.drug}</span></div>
      </div>

      <div style={{ textAlign: 'center', padding: '14px 0 6px' }}>
        <div style={{ fontSize: 9, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.16em' }}>Your token</div>
        <div style={{ fontSize: 64, fontWeight: 700, lineHeight: 1, letterSpacing: '-.04em', margin: '4px 0', fontVariantNumeric: 'tabular-nums' }}>T-042</div>
        <div style={{ fontSize: 11 }}>For <b>Comprehensive eye exam</b></div>
      </div>

      <div style={{ borderTop: '1px solid var(--ink-4)', borderBottom: '1px solid var(--ink-4)', padding: '8px 0', margin: '8px 0' }}>
        <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse' }}>
          <tbody>
            <tr><td style={{ ...kvK, padding: '2px 0' }}>Issued at</td><td style={{ ...kvV, padding: '2px 0', textAlign: 'right', fontFamily: 'var(--font-mono)' }}>14:03 IST</td></tr>
            <tr><td style={{ ...kvK, padding: '2px 0' }}>Estimated wait</td><td style={{ ...kvV, padding: '2px 0', textAlign: 'right', fontFamily: 'var(--font-mono)' }}>≈ 15 min</td></tr>
            <tr><td style={{ ...kvK, padding: '2px 0' }}>Ahead of you</td><td style={{ ...kvV, padding: '2px 0', textAlign: 'right', fontFamily: 'var(--font-mono)' }}>2</td></tr>
            <tr><td style={{ ...kvK, padding: '2px 0' }}>Doctor</td><td style={{ ...kvV, padding: '2px 0', textAlign: 'right' }}>Dr. R. Malhotra · Ch. 2</td></tr>
            <tr><td style={{ ...kvK, padding: '2px 0' }}>DMC reg.</td><td style={{ ...kvV, padding: '2px 0', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 9.5 }}>DMC/R-4412/2014</td></tr>
          </tbody>
        </table>
      </div>

      <div style={{ fontSize: 10, color: 'var(--ink-3)', lineHeight: 1.55, textAlign: 'center' }}>
        Please remain seated. You will be called by token number and SMS. Walk-ins are subject to chamber availability — appointments take precedence.
      </div>

      <div style={{ marginTop: 10, padding: '8px 10px', border: '1px solid var(--ink-4)', textAlign: 'center', fontSize: 9.5, color: 'var(--ink-3)' }}>
        <div style={{ fontSize: 9, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.1em', marginBottom: 3 }}>What to expect</div>
        Pre-screen (5 min) → Refraction (10 min) → Doctor consult (10 min) → Frame selection
      </div>

      <div style={{ marginTop: 12, paddingTop: 8, borderTop: '2px solid var(--ink)', textAlign: 'center', fontSize: 9, color: 'var(--ink-4)' }}>
        <div style={{ textTransform: 'uppercase', letterSpacing: '.12em' }}>Thank you for choosing Better Vision</div>
        <div style={{ marginTop: 4 }}>Token retained for 24 h · SOP-CLN-01</div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   5. DELIVERY CHALLAN — A4 — Rule 55 CGST · TRIPLICATE
   ═══════════════════════════════════════════════════════════════════════════ */
function TplChallan() {
  const rows = [
    { d: 'Ray-Ban RB4171 Erika · Tortoise · 54-18-135', sku: 'FRM-RB-4171-TOR', hsn: '9003', qty: 12, unit: 'Nos', val: 3200 },
    { d: 'Oakley OO9208 Radar · Matte Black · 38',       sku: 'FRM-OO-9208-BLK', hsn: '9003', qty: 6,  unit: 'Nos', val: 8900 },
    { d: 'Vogue VO5234 · Rose gold · 52-17-140',         sku: 'FRM-VO-5234-RG',  hsn: '9003', qty: 18, unit: 'Nos', val: 2700 },
    { d: 'BV Microfibre pouch · Assorted',               sku: 'ACC-BV-POUCH',    hsn: '9605', qty: 200,unit: 'Nos', val: 45 },
  ];
  const taxable = rows.reduce((a, r) => a + r.qty * r.val, 0);

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <LegalHeader
        docType="DELIVERY CHALLAN · Rule 55, CGST Rules 2017"
        docNo="DC/BV/2025-26/0418-11"
        copy="ORIGINAL FOR CONSIGNEE · ☐ DUPLICATE FOR TRANSPORTER · ☐ TRIPLICATE FOR CONSIGNOR"
        meta={[
          ['Challan date',  '19-Apr-2026 · 08:12 IST'],
          ['Purpose',       'Inter-store stock transfer (same PAN)'],
          ['Reverse charge','No · not a supply'],
          ['Place of supply','07 · Delhi'],
          ['e-Way Bill No.','881 2304 4551'],
          ['e-Way validity','Valid till 20-Apr 23:59 IST'],
          ['Transport mode','Road · Vehicle '],
          ['Vehicle No.',   'MH-12-KX-3389'],
        ]}
      />

      <PartyBlock blocks={[
        { h: 'Consignor · From', rows: [
          ['Name',          BV_LEGAL.legal],
          ['Branch',        'HQ Warehouse · Okhla Phase II'],
          ['Address',       'Plot 12, Okhla Phase II, New Delhi 110020'],
          ['State / Code',  'Delhi / 07'],
          ['GSTIN',         BV_LEGAL.gstin],
          ['Dispatched by', 'Ankit Verma · WH Lead'],
        ] },
        { h: 'Consignee · To', rows: [
          ['Name',          BV_LEGAL.legal],
          ['Branch',        'GK-I Flagship · BV-DELHI-GK1'],
          ['Address',       BV_LEGAL.storeAddr],
          ['State / Code',  'Delhi / 07'],
          ['GSTIN',         BV_LEGAL.gstin + ' (same entity)'],
          ['Received by',   'Sonia Khatri · Store Manager'],
        ] },
      ]}/>

      {/* Lines */}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr>
            <th style={{ ...tblHead, width: 28 }}>#</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Description of goods</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>SKU</th>
            <th style={tblHead}>HSN</th>
            <th style={tblHead}>Qty</th>
            <th style={tblHead}>UoM</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Unit value</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Total value (₹)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td style={tblCell}>{i + 1}</td>
              <td style={{ ...tblCell, textAlign: 'left', fontWeight: 500 }}>{r.d}</td>
              <td style={{ ...tblCell, textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10 }}>{r.sku}</td>
              <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r.hsn}</td>
              <td style={tblNum}>{r.qty}</td>
              <td style={{ ...tblCell, color: 'var(--ink-4)' }}>{r.unit}</td>
              <td style={tblNum}>{inr(r.val)}</td>
              <td style={{ ...tblNum, fontWeight: 600 }}>{inr(r.qty * r.val)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td style={{ ...tblHead, textAlign: 'right' }} colSpan={4}>TOTAL CONSIGNMENT VALUE (NON-SALE · ₹)</td>
            <td style={tblHead}>{rows.reduce((a, r) => a + r.qty, 0)}</td>
            <td style={tblHead}>—</td>
            <td style={tblHead}>—</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontSize: 12 }}>{inr(taxable)}</td>
          </tr>
        </tfoot>
      </table>

      {/* Transport + boxes */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        {[
          { h: 'Transport', rows: [
            ['Mode',     'Road'],
            ['Vehicle',  'MH-12-KX-3389'],
            ['Driver',   'Praveen Singh'],
            ['Docket',   'BlueDart BB-DL-22100'],
          ] },
          { h: 'e-Way Bill', rows: [
            ['EWB No.',     '881 2304 4551'],
            ['Generated',   '19-Apr 07:58'],
            ['Valid till',  '20-Apr 23:59'],
            ['Distance',    '8 km'],
          ] },
          { h: 'Boxes / Seals', rows: [
            ['Boxes',     '3 of 3'],
            ['Seal #',    '4412 · intact'],
            ['Weight',    '14.2 kg'],
            ['Insurance', 'BlueDart policy 0044'],
          ] },
        ].map((b, i) => (
          <div key={b.h} style={{ padding: '8px 16px', borderRight: i === 2 ? 'none' : '1px solid var(--ink-4)' }}>
            <div style={lblSm}>{b.h}</div>
            <table style={{ width: '100%', fontSize: 10, borderCollapse: 'collapse', marginTop: 4 }}>
              <tbody>
                {b.rows.map(([k, v]) => (
                  <tr key={k}><td style={kvK}>{k}</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)', fontSize: 10 }}>{v}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>

      <LegalFooter
        rule="Issued under Rule 55(2) CGST Rules 2017 · in triplicate"
        declaration="We hereby certify that the goods mentioned above are being transported for inter-branch stock transfer (no sale taking place) and that this document does not constitute a tax invoice. GST will be charged on the eventual sale at the receiving branch."
        amountWords={null}
        showBank={false}
        leftExtra={
          <div style={{ marginTop: 10, fontSize: 9.5, color: 'var(--ink-3)' }}>
            <div style={lblSm}>Receiving branch acknowledgement</div>
            <table style={{ marginTop: 4, fontSize: 9.5 }}>
              <tbody>
                <tr><td style={kvK}>Received on</td><td style={{ ...kvV, fontSize: 9.5, fontFamily: 'var(--font-mono)' }}>____ / ____ / 2026 at ____ : ____</td></tr>
                <tr><td style={kvK}>Condition</td><td style={{ ...kvV, fontSize: 9.5 }}>☐ Seal intact ☐ Boxes count OK ☐ No damage</td></tr>
                <tr><td style={kvK}>Variance</td><td style={{ ...kvV, fontSize: 9.5 }}>None / _________________________</td></tr>
              </tbody>
            </table>
          </div>
        }
      />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   6. CREDIT NOTE — A5 — Section 34 CGST · against original invoice
   ═══════════════════════════════════════════════════════════════════════════ */
function TplCreditNote() {
  const rows = [
    { d: 'Ray-Ban RB2140 Wayfarer · Black · size 52 (exchange)', hsn: '9003', qty: 1, rate: 6339.29, gst: 12 },
    { d: 'Lens coating upgrade (not applicable on exchange)',     hsn: '9001', qty: 1, rate: 892.86,  gst: 12 },
  ];
  const sub  = rows.reduce((a, r) => a + r.qty * r.rate, 0);
  const cgst = rows.reduce((a, r) => a + (r.qty * r.rate * r.gst) / 100 / 2, 0);
  const sgst = cgst;
  const total = Math.round(sub + cgst + sgst);

  return (
    <div className="paper a5" data-doc style={{ color: 'var(--ink)' }}>
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center',
        padding: '6px 16px', background: 'var(--ink)', color: '#fff',
        fontSize: 10, letterSpacing: '.16em', fontWeight: 600, textTransform: 'uppercase',
      }}>
        <span>CREDIT NOTE · Sec. 34 CGST Act 2017</span>
        <span style={{ opacity: .8, fontSize: 9.5 }}>ORIGINAL FOR RECIPIENT</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={{ fontSize: 13, fontWeight: 700 }}>{BV_LEGAL.legal}</div>
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 1 }}>{BV_LEGAL.storeAddr}</div>
          <table style={{ marginTop: 6, fontSize: 10, borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={kvK}>GSTIN</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)' }}>{BV_LEGAL.gstin}</td></tr>
              <tr><td style={kvK}>State / Code</td><td style={kvV}>{BV_LEGAL.state} / <span className="mono">{BV_LEGAL.stateCode}</span></td></tr>
            </tbody>
          </table>
        </div>
        <div style={{ padding: '10px 16px' }}>
          <div style={lblSm}>Credit Note No.</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700, marginTop: 2 }}>CN/BV/2025-26/0418-03</div>
          <table style={{ width: '100%', fontSize: 10, borderCollapse: 'collapse', marginTop: 6 }}>
            <tbody>
              <tr><td style={kvK}>Date</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>19-Apr-2026</td></tr>
              <tr><td style={kvK}>Against invoice</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>BV/GK1/2025-26/248904</td></tr>
              <tr><td style={kvK}>Original date</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>11-Apr-2026</td></tr>
              <tr><td style={kvK}>Reason code</td><td style={{ ...kvV, textAlign: 'right' }}>R-2 · Frame size mismatch</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <PartyBlock blocks={[
        { h: 'Issued to', rows: [
          ['Name',       'Mr. Rahul Sinha'],
          ['Address',    'D-44, Defence Colony, New Delhi 110024'],
          ['Phone',      '+91 98104 33200'],
          ['Customer No.', 'CUS-00188'],
          ['GSTIN',      '— (B2C — un-registered)'],
        ] },
      ]}/>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10.5 }}>
        <thead>
          <tr>
            <th style={{ ...tblHead, width: 24 }}>#</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Item being reversed</th>
            <th style={tblHead}>HSN</th>
            <th style={tblHead}>Qty</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Rate</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Taxable</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>CGST 6%</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>SGST 6%</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Total</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const tax = r.qty * r.rate * r.gst / 100;
            return (
              <tr key={i}>
                <td style={tblCell}>{i + 1}</td>
                <td style={{ ...tblCell, textAlign: 'left' }}>{r.d}</td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r.hsn}</td>
                <td style={tblNum}>{r.qty}</td>
                <td style={tblNum}>{r.rate.toFixed(2)}</td>
                <td style={tblNum}>{(r.qty * r.rate).toFixed(2)}</td>
                <td style={tblNum}>{(tax / 2).toFixed(2)}</td>
                <td style={tblNum}>{(tax / 2).toFixed(2)}</td>
                <td style={{ ...tblNum, fontWeight: 600 }}>{(r.qty * r.rate + tax).toFixed(2)}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={5} style={{ ...tblHead, textAlign: 'right' }}>TOTAL CREDITED</td>
            <td style={{ ...tblHead, textAlign: 'right', fontFamily: 'var(--font-sans)', fontVariantNumeric: 'tabular-nums' }}>{sub.toFixed(2)}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontFamily: 'var(--font-sans)', fontVariantNumeric: 'tabular-nums' }}>{cgst.toFixed(2)}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontFamily: 'var(--font-sans)', fontVariantNumeric: 'tabular-nums' }}>{sgst.toFixed(2)}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontFamily: 'var(--font-sans)', fontVariantNumeric: 'tabular-nums', fontSize: 12 }}>{total.toFixed(2)}</td>
          </tr>
        </tfoot>
      </table>

      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Reason & approval</div>
          <ol style={{ margin: '4px 0 0 14px', padding: 0, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
            <li>Customer reported frame size mismatch within 7-day exchange window.</li>
            <li>Approved by Sonia Khatri · Store Manager · 19-Apr 11:30 (PIN-verified).</li>
            <li>Settled to BV-Wallet · applied against new invoice <span className="mono">BV/GK1/2025-26/249220</span> the same day.</li>
            <li>This credit note will be reported in GSTR-1 for April 2026 and tax output reversed accordingly.</li>
          </ol>
        </div>
        <div style={{ padding: '10px 16px', fontSize: 11 }}>
          <div style={lblSm}>Settlement</div>
          <table style={{ width: '100%', fontSize: 10.5, marginTop: 4, borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={kvK}>Mode</td><td style={{ ...kvV, textAlign: 'right' }}>BV Wallet credit</td></tr>
              <tr><td style={kvK}>Reference</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>WAL-2026-04-188</td></tr>
              <tr><td style={kvK}>Wallet balance</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(0)}</td></tr>
            </tbody>
          </table>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 8, padding: '8px 0 0', borderTop: '2px solid var(--ink)' }}>
            <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.14em' }}>Net credit</span>
            <span style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>− {inr(total)}</span>
          </div>
        </div>
      </div>

      <LegalFooter
        rule="Sec. 34 CGST Act 2017 · CN must be issued within 30 days · reported in GSTR-1"
        amountWords={'Refund of ' + inWords(total)}
        declaration="We declare that this credit note reverses tax originally collected on the referenced invoice. The recipient is not entitled to any further input tax credit on the reversed amount."
        showBank={false}
      />
    </div>
  );
}

Object.assign(window, { TplInvoice, TplRxCard, TplJobCard, TplToken, TplChallan, TplCreditNote });
