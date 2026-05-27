/* global React, MOCK, BV_LEGAL, LegalHeader, StaffHeader, PartyBlock, LegalFooter,
   StatusStamp, FieldChip, inWords, inr, inrI,
   kvK, kvV, lblSm, tblHead, tblCell, tblNum */
/* Eight more templates covering: sale order/estimate, sale return,
   stock-purchase tally (delivery person), DayBook, damage report,
   workshop/QC sticker, expense voucher, vendor ledger. */

/* ═══════════════════════════════════════════════════════════════════════════
   19. SALE ORDER / ESTIMATE — A4 — customer · advance taken, order confirmed
   ═══════════════════════════════════════════════════════════════════════════ */
function TplSaleOrder() {
  const lines = [
    { d: 'Tom Ford TF5234 Optical Frame',          hsn: '9003', desc2: 'Havana · 52-18-145 · acetate',                qty: 1, unit: 'Nos', rate: 18750.00, gst: 12 },
    { d: 'Zeiss DriveSafe · 1.67 index',           hsn: '9001', desc2: 'DuraVision Platinum coating · Rx mounted',     qty: 1, unit: 'Pair', rate: 22500.00, gst: 12 },
    { d: 'Frame engraving · 2-letter monogram',    hsn: '9984', desc2: 'Initials "AS" · inner temple',                 qty: 1, unit: 'Job', rate: 500.00,  gst: 18 },
  ];
  const sub = lines.reduce((a, r) => a + r.qty * r.rate, 0);
  const tax = lines.reduce((a, r) => a + r.qty * r.rate * r.gst / 100, 0);
  const total = Math.round(sub + tax);
  const advance = 15000;
  const balance = total - advance;

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <LegalHeader
        docType="SALE ORDER · pro-forma estimate · NOT A TAX INVOICE"
        docNo="SO/BV/2025-26/01188"
        copy="ORIGINAL FOR CUSTOMER · ☐ COPY FOR STORE"
        meta={[
          ['Estimate date', '19-Apr-2026 · 15:08 IST'],
          ['Valid until',   '26-Apr-2026 (7 days)'],
          ['Order status',  'Confirmed · advance received'],
          ['Promised by',   '25-Apr-2026 (lens fit)'],
          ['Place of supply', '07 · Delhi'],
          ['Reverse charge', 'No'],
          ['Cashier',       'Sonia K. · POS-01'],
        ]}
        showBank={false}
      />

      <PartyBlock blocks={[
        { h: 'Customer', rows: [
          ['Name',     'Ms. Aanya Sharma'],
          ['Address',  'B-42, Panchsheel Park, New Delhi 110017'],
          ['State / Code', 'Delhi / 07'],
          ['Phone',    '+91 98115 22100'],
          ['Customer No.', 'CUS-00214 · loyalty Silver'],
          ['GSTIN',    '— (B2C — un-registered)'],
        ] },
        { h: 'Order context', rows: [
          ['Source',     'In-store consultation · Sonia K.'],
          ['Rx ref.',    'RX/GK1/2026/4418 dated 18-Apr-2026'],
          ['Doctor',     'Dr. Ritu Malhotra · DMC-4412'],
          ['Channel',    'Walk-in'],
          ['Loyalty',    '1,420 pts (≈ ₹ 355) · not redeemed'],
          ['Customer wants', 'Premium frame + progressive lens'],
        ] },
      ]}/>

      {/* Line items */}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr>
            <th style={{ ...tblHead, width: 26 }}>#</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Description</th>
            <th style={tblHead}>HSN / SAC</th>
            <th style={tblHead}>Qty</th>
            <th style={tblHead}>UoM</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Rate</th>
            <th style={tblHead}>GST</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Taxable</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Total (₹)</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((r, i) => {
            const t = r.qty * r.rate * r.gst / 100;
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
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r.gst}%</td>
                <td style={tblNum}>{(r.qty * r.rate).toFixed(2)}</td>
                <td style={{ ...tblNum, fontWeight: 600 }}>{(r.qty * r.rate + t).toFixed(2)}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={7} style={{ ...tblHead, textAlign: 'right' }}>ESTIMATE TOTAL (incl. GST)</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{sub.toFixed(2)}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontSize: 12 }}>{total.toFixed(2)}</td>
          </tr>
        </tfoot>
      </table>

      {/* Advance + balance block */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '12px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Advance received</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em' }}>{inrI(advance)}</div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 3 }}>UPI · GPay · 482911022404<br />19-Apr 15:08 IST</div>
        </div>
        <div style={{ padding: '12px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Balance due at delivery</div>
          <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--err)', marginTop: 4, fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em' }}>{inrI(balance)}</div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 3 }}>Tax invoice issued on payment & delivery on / before 26-Apr</div>
        </div>
        <div style={{ padding: '12px 16px' }}>
          <div style={lblSm}>Order total</div>
          <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em' }}>{inrI(total)}</div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 3 }}>{inWords(total)}</div>
        </div>
      </div>

      {/* Terms */}
      <div style={{ padding: '10px 18px', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={lblSm}>Terms of this estimate</div>
        <ol style={{ margin: '4px 0 0 16px', padding: 0, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
          <li><b>This is a pro-forma estimate, not a tax invoice.</b> A full GST tax invoice will be issued on receipt of balance and product collection.</li>
          <li>Prices hold for <b>7 days from this date</b>. Beyond that the estimate may be re-issued at prevailing rates.</li>
          <li>Advance of <b>{inrI(advance)}</b> is non-refundable but adjustable against any purchase within 60 days, should the customer change the order.</li>
          <li>Lens orders enter production once advance is received; partial production may not be cancellable.</li>
          <li>Engraving and personalisation are non-returnable in any case.</li>
          <li>Estimate items reserved on the floor for 48 hours; release thereafter if no further confirmation.</li>
        </ol>
      </div>

      <LegalFooter
        rule="Sale Order is a commercial commitment under the Indian Contract Act 1872 · GST output liability arises only on invoice"
        amountWords={inWords(total) + ' total · ' + inWords(balance) + ' balance'}
        declaration="The customer confirms acceptance of the items listed above and authorises us to commence lens production / fitting. The customer acknowledges that the advance is non-refundable but adjustable as stated."
        signLabel="For Better Vision · estimate prepared by"
        showBank={false}
      />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   20. SALE RETURN — A5 — customer · against original invoice
   ═══════════════════════════════════════════════════════════════════════════ */
function TplSaleReturn() {
  const items = [
    { d: 'Ray-Ban RB2140 Wayfarer · Black · 50',  hsn: '9003', qty: 1, rate: 6339.29, gst: 12,
      reason: 'R-2 · Size mismatch (too tight on bridge)',
      cond:   'Unused · original packaging intact' },
  ];
  const taxable = items.reduce((a, r) => a + r.qty * r.rate, 0);
  const tax = items.reduce((a, r) => a + r.qty * r.rate * r.gst / 100, 0);
  const total = Math.round(taxable + tax);

  return (
    <div className="paper a5" data-doc style={{ color: 'var(--ink)' }}>
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center',
        padding: '6px 16px', background: 'var(--ink)', color: '#fff',
        fontSize: 10, letterSpacing: '.16em', fontWeight: 600, textTransform: 'uppercase',
      }}>
        <span>SALE RETURN · Form SR-01</span>
        <span style={{ opacity: .8, fontSize: 9.5 }}>ORIGINAL FOR CUSTOMER</span>
      </div>

      {/* Compact identity */}
      <div style={{ padding: '10px 16px', borderBottom: '1.5px solid var(--ink)', display: 'grid', gridTemplateColumns: '34px 1fr auto', gap: 10, alignItems: 'flex-start' }}>
        <div style={{ width: 34, height: 34, border: '1.5px solid var(--ink)', display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 18 }}>B</div>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700 }}>{BV_LEGAL.legal}</div>
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 1 }}>GK-I Flagship · <span className="mono">GSTIN {BV_LEGAL.gstin}</span></div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={lblSm}>SR No.</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700, marginTop: 1 }}>SR/BV/2025-26/0044</div>
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 3 }}>Date <span className="mono">21-Apr-2026 · 11:04</span></div>
        </div>
      </div>

      <PartyBlock blocks={[
        { h: 'Returning customer', rows: [
          ['Name',          'Mr. Rohan Iyer'],
          ['Phone',         '+91 98933 40127'],
          ['Customer No.',  'CUS-10390'],
          ['Against invoice','BV/GK1/2025-26/248904 dated 11-Apr'],
          ['Invoice value', inr(8950)],
          ['Days since sale','10 days (within 7-day window? No → ASM override applied)'],
        ] },
      ]}/>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr>
            <th style={{ ...tblHead, width: 24 }}>#</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Item returned</th>
            <th style={tblHead}>HSN</th>
            <th style={tblHead}>Qty</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Rate</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>CGST</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>SGST</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Refund (₹)</th>
          </tr>
        </thead>
        <tbody>
          {items.map((r, i) => {
            const t = r.qty * r.rate * r.gst / 100;
            return (
              <tr key={i}>
                <td style={tblCell}>{i + 1}</td>
                <td style={{ ...tblCell, textAlign: 'left' }}>
                  <div style={{ fontWeight: 600 }}>{r.d}</div>
                  <div style={{ fontSize: 10, color: 'var(--ink-4)', marginTop: 2 }}><b>Reason:</b> {r.reason}</div>
                  <div style={{ fontSize: 10, color: 'var(--ink-4)' }}><b>Condition:</b> {r.cond}</div>
                </td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r.hsn}</td>
                <td style={tblNum}>{r.qty}</td>
                <td style={tblNum}>{r.rate.toFixed(2)}</td>
                <td style={tblNum}>{(t / 2).toFixed(2)}</td>
                <td style={tblNum}>{(t / 2).toFixed(2)}</td>
                <td style={{ ...tblNum, fontWeight: 600 }}>{(r.qty * r.rate + t).toFixed(2)}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={7} style={{ ...tblHead, textAlign: 'right' }}>TOTAL REFUND</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontSize: 13 }}>{total.toFixed(2)}</td>
          </tr>
        </tfoot>
      </table>

      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>QC verification at return</div>
          <ul style={{ margin: '4px 0 0 14px', padding: 0, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
            <li>Frame inspected · no scratches, no missing screws, original temple tips intact</li>
            <li>Original box, cleaning cloth and warranty card returned</li>
            <li>Customer ID verified · Photo matches</li>
            <li>SOP-CX-04 exchange window exception approved by SM (10-day rule waived)</li>
          </ul>
          <div style={{ marginTop: 8, fontSize: 10.5, color: 'var(--ink-3)' }}>
            Linked Credit Note <b className="mono">CN/BV/2025-26/0418-09</b> will be auto-issued for tax reversal.
          </div>
        </div>
        <div style={{ padding: '10px 16px' }}>
          <div style={lblSm}>Refund mode</div>
          <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse', marginTop: 4 }}>
            <tbody>
              <tr><td style={kvK}>Mode</td><td style={{ ...kvV, textAlign: 'right' }}>BV Wallet credit</td></tr>
              <tr><td style={kvK}>Reference</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>WAL-2026-04-188</td></tr>
              <tr><td style={kvK}>Use within</td><td style={{ ...kvV, textAlign: 'right' }}>No expiry</td></tr>
              <tr><td style={kvK}>Approver</td><td style={{ ...kvV, textAlign: 'right' }}>Sonia K. · SM PIN</td></tr>
            </tbody>
          </table>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 6, padding: '6px 0 0', borderTop: '2px solid var(--ink)' }}>
            <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.14em' }}>Credited</span>
            <span style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>− {inr(total)}</span>
          </div>
        </div>
      </div>

      {/* Customer + store sign block */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1px solid var(--ink-4)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>I confirm the return</div>
          <div style={{ height: 32, marginTop: 4, borderBottom: '0.5px solid var(--ink-4)' }} />
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 4 }}>Customer signature</div>
        </div>
        <div style={{ padding: '10px 16px' }}>
          <div style={lblSm}>Accepted by store</div>
          <div style={{ height: 32, marginTop: 4, borderBottom: '0.5px solid var(--ink-4)' }} />
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 4, display: 'flex', justifyContent: 'space-between' }}>
            <span>Sonia K. · Store Manager</span>
            <span style={{ color: 'var(--ink-4)' }}>[Stamp]</span>
          </div>
        </div>
      </div>

      <div style={{ padding: '7px 16px', fontSize: 9, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em', textAlign: 'center' }}>
        Sec. 34 CGST · output tax reversed via linked credit note · returned stock re-inducted to W-01
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   21. STOCK PURCHASE TALLY — A4 — counter-side quick tally at receipt
       (less formal than a GRN; precedes the GRN once tally + invoice match)
   ═══════════════════════════════════════════════════════════════════════════ */
function TplStockTally() {
  const lines = [
    { d: 'Crizal Prevencia 1.6 Blue-cut · −1.50 / Plano',     hsn:'9001', exp: 4, got: 4, lot: 'CZ-2604-1', exp_date: '04/2029', qa: 'OK' },
    { d: 'Crizal Prevencia 1.6 Blue-cut · −2.00 / Plano',     hsn:'9001', exp: 4, got: 4, lot: 'CZ-2604-2', exp_date: '04/2029', qa: 'OK' },
    { d: 'Crizal Prevencia 1.6 Blue-cut · −2.50 / −0.75x180', hsn:'9001', exp: 2, got: 2, lot: 'CZ-2604-3', exp_date: '04/2029', qa: 'OK' },
    { d: 'Varilux X · 1.60 · Rx as per Rx-22, lot 0418',      hsn:'9001', exp: 1, got: 1, lot: 'VX-2604-A', exp_date: '04/2029', qa: 'OK' },
    { d: 'Varilux X · 1.60 · Rx as per Rx-23, lot 0418',      hsn:'9001', exp: 1, got: 0, lot: '—',          exp_date: '—',         qa: 'Missing — flagged' },
  ];
  const exp = lines.reduce((a, r) => a + r.exp, 0);
  const got = lines.reduce((a, r) => a + r.got, 0);

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <StaffHeader
        docType="STOCK PURCHASE TALLY · Form Inv-09"
        docNo="TLY/2026/0419/04"
        copy="COUNTER ACK · pre-GRN"
        meta={[
          ['Date',     '19-Apr · 16:32'],
          ['Carrier',  'Essilor van'],
          ['Cashier',  'Sonia K.'],
          ['Pkts',     '5 of 5'],
        ]}
      />

      {/* Carrier / delivery details */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        {[
          { h: 'Delivery person', rows: [
            ['Name',     'Raghav Yadav'],
            ['ID shown', 'Aadhaar · last 4: 5512'],
            ['Phone',    '+91 99113 88044'],
            ['Vendor',   'Essilor Pune Lab · V-0044'],
            ['Vehicle',  'Two-wheeler · DL-3S-AC-2231'],
          ] },
          { h: 'Consignment', rows: [
            ['Source',   'Essilor Pune (job dispatch)'],
            ['Docket',   'ESL/PN/22041 · van load 4'],
            ['Packets',  '5 packets · sealed pouches'],
            ['Origin time','08:40 IST · same-day'],
            ['Insurance','Vendor policy · 1L cover'],
          ] },
          { h: 'Receiving', rows: [
            ['Received at','19-Apr-2026 · 16:32 IST'],
            ['Bay',      'GK-I receiving · POS-01'],
            ['Received by','Sonia Khatri · SM'],
            ['Seal check','4 of 5 OK · 1 packet missing entirely'],
            ['Action',   'Tally → GRN once invoice arrives'],
          ] },
        ].map((b, i) => (
          <div key={b.h} style={{ padding: '10px 16px', borderRight: i === 2 ? 'none' : '1px solid var(--ink-4)' }}>
            <div style={lblSm}>{b.h}</div>
            <table style={{ marginTop: 4, width: '100%', fontSize: 10.5, borderCollapse: 'collapse' }}>
              <tbody>{b.rows.map(([k, v]) => <tr key={k}><td style={kvK}>{k}</td><td style={{ ...kvV, fontFamily: /Phone|Aadhaar|ID|Vehicle|Docket/.test(k) ? 'var(--font-mono)' : 'var(--font-sans)' }}>{v}</td></tr>)}</tbody>
            </table>
          </div>
        ))}
      </div>

      {/* Tally table */}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr>
            <th style={{ ...tblHead, width: 26 }}>#</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Description (manifest)</th>
            <th style={tblHead}>HSN</th>
            <th style={tblHead}>Expected</th>
            <th style={tblHead}>Tallied</th>
            <th style={tblHead}>Δ</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Lot / Batch</th>
            <th style={tblHead}>Expiry</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Tally check</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((r, i) => {
            const v = r.got - r.exp;
            const isMissing = r.got < r.exp;
            return (
              <tr key={i} style={isMissing ? { background: 'var(--err-50)' } : null}>
                <td style={tblCell}>{i + 1}</td>
                <td style={{ ...tblCell, textAlign: 'left' }}>{r.d}</td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r.hsn}</td>
                <td style={tblNum}>{r.exp}</td>
                <td style={{ ...tblNum, fontWeight: 600 }}>{r.got}</td>
                <td style={{ ...tblCell, color: v < 0 ? 'var(--err)' : v > 0 ? 'var(--warn)' : 'var(--ink-4)', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{v === 0 ? '0' : (v > 0 ? '+' + v : v)}</td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', textAlign: 'left', fontSize: 10 }}>{r.lot}</td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontSize: 10 }}>{r.exp_date}</td>
                <td style={{ ...tblCell, textAlign: 'left', fontSize: 10, color: isMissing ? 'var(--err)' : 'var(--ok)', fontWeight: 600 }}>{r.qa}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={3} style={{ ...tblHead, textAlign: 'right' }}>TOTAL</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{exp}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: 700 }}>{got}</td>
            <td style={{ ...tblHead, textAlign: 'right', color: got < exp ? 'var(--err)' : 'var(--ok)' }}>{got - exp}</td>
            <td colSpan={3}/>
          </tr>
        </tfoot>
      </table>

      {/* Discrepancy + next steps */}
      <div style={{ borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '6px 16px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', borderBottom: '1px solid var(--ink-4)' }}>
          Discrepancy log
        </div>
        <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--ink-2)', lineHeight: 1.55 }}>
          One Varilux X packet (Rx-23, expected packet 5 of 5) is missing from the consignment. Delivery person reports no awareness; vendor van log shows packet was loaded at Pune. Lab supervisor contacted at 16:34; tracer raised, replacement promised by 21-Apr morning.
          Tally posted with shortage; <b>GRN will only be created once replacement arrives + vendor invoice matches</b>. Customer for Rx-23 (CUS-00214) notified via WhatsApp — promised delivery shifted from 23-Apr → 24-Apr.
        </div>
      </div>

      {/* Sign-offs */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', borderBottom: '1px solid var(--ink-4)' }}>
        {[
          ['Delivered by', 'Raghav Yadav', 'Essilor van · 16:32'],
          ['Tallied by',   'Sonia K.',     'Store Manager · 16:38'],
          ['Witness',      'Karan T.',     'Optician · 16:38'],
        ].map(([h, n, s]) => (
          <div key={h} style={{ padding: '14px 16px', borderRight: '1px solid var(--ink-4)' }}>
            <div style={lblSm}>{h}</div>
            <div style={{ height: 28, marginTop: 4, borderBottom: '0.5px solid var(--ink-4)' }} />
            <div style={{ fontSize: 10.5, marginTop: 3, fontWeight: 600 }}>{n}</div>
            <div style={{ fontSize: 9.5, color: 'var(--ink-3)' }}>{s}</div>
          </div>
        ))}
      </div>

      <div style={{ padding: '7px 16px', fontSize: 9, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em', textAlign: 'center' }}>
        SOP-INV-09 · tally is not a GST document · supersedes once GRN issued · retained 18 months
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   22. DAYBOOK — A4 — daily journal · all heads of receipt + payment
   ═══════════════════════════════════════════════════════════════════════════ */
function TplDayBook() {
  const txns = [
    { t: '10:12', vch: 'BV/.../249172', head: 'Sales · Frame + Lens',     dr: 0,     cr: 12400,  cust: 'Walk-in · Cash',                ref: 'INV' },
    { t: '10:38', vch: 'BV/.../249174', head: 'Sales · CL trial',          dr: 0,     cr: 1980,   cust: 'Priya Nair · CUS-11562',         ref: 'INV' },
    { t: '11:30', vch: 'CN/.../0418-03',head: 'Sales return · refund',     dr: 8950,  cr: 0,      cust: 'Rahul Sinha · CUS-00188',        ref: 'CN' },
    { t: '12:14', vch: 'BV/.../249178', head: 'Sales · Sunglasses',        dr: 0,     cr: 4800,   cust: 'Walk-in · Card',                 ref: 'INV' },
    { t: '13:02', vch: 'EXP/.../0419-7',head: 'Expense · Cleaning supply', dr: 320,   cr: 0,      cust: 'Petty cash · Karan T.',          ref: 'EXP' },
    { t: '13:55', vch: 'BV/.../249180', head: 'Sales · Frame + Lens',      dr: 0,     cr: 7900,   cust: 'Walk-in · UPI',                  ref: 'INV' },
    { t: '14:22', vch: 'BV/.../249183', head: 'Sales · Frame + Lens + CL', dr: 0,     cr: 28110,  cust: 'Aanya Sharma · CUS-00214',       ref: 'INV' },
    { t: '15:08', vch: 'SO/.../01188',  head: 'Sale order · advance',      dr: 0,     cr: 15000,  cust: 'Aanya Sharma · CUS-00214',       ref: 'SO' },
    { t: '15:40', vch: 'EXP/.../0419-8',head: 'Expense · Tea & snacks',    dr: 180,   cr: 0,      cust: 'Petty cash · Riya P.',           ref: 'EXP' },
    { t: '16:32', vch: 'TLY/.../0419-4',head: 'Stock receipt (memo)',      dr: 0,     cr: 0,      cust: 'Essilor van · Raghav Y.',         ref: 'MEMO' },
    { t: '17:10', vch: 'BV/.../249189', head: 'Sales · Repair fee',        dr: 0,     cr: 450,    cust: 'Walk-in · Cash',                 ref: 'INV' },
    { t: '18:45', vch: 'BV/.../249192', head: 'Sales · Frame',             dr: 0,     cr: 4200,   cust: 'Walk-in · Card',                 ref: 'INV' },
    { t: '19:30', vch: 'DN/.../0418-04',head: 'Debit note · vendor short', dr: 1848,  cr: 0,      cust: 'J&J · vendor settle',            ref: 'DN' },
    { t: '20:15', vch: 'BNK/.../0419-1',head: 'Bank deposit · cash',       dr: 5000,  cr: 0,      cust: 'HDFC ****4421 · drop slip',       ref: 'BNK' },
  ];
  const sales   = txns.filter(t => t.ref === 'INV').reduce((a, t) => a + t.cr, 0);
  const refunds = txns.filter(t => t.ref === 'CN').reduce((a, t) => a + t.dr, 0);
  const adv     = txns.filter(t => t.ref === 'SO').reduce((a, t) => a + t.cr, 0);
  const exp     = txns.filter(t => t.ref === 'EXP').reduce((a, t) => a + t.dr, 0);

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <LegalHeader
        docType="DAY BOOK · primary financial journal"
        docNo="DB/BV/2025-26/0419"
        copy="ORIGINAL FOR ACCOUNTS · ☐ DUPLICATE FOR ASM"
        meta={[
          ['Business date',  'Tuesday, 19-Apr-2026'],
          ['Store',          'GK-I Flagship · BV-DELHI-GK1'],
          ['Closing cashier','Sonia K.'],
          ['Vouchers',       txns.length + ' entries'],
          ['Net debit',      inr(txns.reduce((a, t) => a + t.dr, 0))],
          ['Net credit',     inr(txns.reduce((a, t) => a + t.cr, 0))],
        ]}
        showBank={false}
      />

      {/* KPI strip per head */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', borderBottom: '1.5px solid var(--ink)' }}>
        {[
          ['Sales (Inv)',  inr(sales), null],
          ['Refunds (CN)', '− ' + inr(refunds), 'var(--err)'],
          ['Advances (SO)',inr(adv), null],
          ['Expenses',     '− ' + inr(exp), 'var(--warn)'],
          ['Net for day',  inr(sales + adv - refunds - exp), 'var(--ok)'],
        ].map(([k, v, c], i) => (
          <div key={k} style={{ padding: '10px 14px', borderRight: i === 4 ? 'none' : '1px solid var(--ink-4)' }}>
            <div style={{ ...lblSm, fontSize: 9 }}>{k}</div>
            <div style={{ fontSize: 17, fontWeight: 700, marginTop: 3, color: c || 'var(--ink)', fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em' }}>{v}</div>
          </div>
        ))}
      </div>

      {/* The journal */}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10.5 }}>
        <thead>
          <tr>
            <th style={tblHead}>Time</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Voucher</th>
            <th style={tblHead}>Type</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Account head</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Counter-party</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Debit (₹)</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Credit (₹)</th>
          </tr>
        </thead>
        <tbody>
          {txns.map((t, i) => (
            <tr key={i}>
              <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{t.t}</td>
              <td style={{ ...tblCell, textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10 }}>{t.vch}</td>
              <td style={{ ...tblCell, fontWeight: 600, fontSize: 10 }}>{t.ref}</td>
              <td style={{ ...tblCell, textAlign: 'left' }}>{t.head}</td>
              <td style={{ ...tblCell, textAlign: 'left', color: 'var(--ink-3)' }}>{t.cust}</td>
              <td style={tblNum}>{t.dr ? t.dr.toFixed(2) : '—'}</td>
              <td style={tblNum}>{t.cr ? t.cr.toFixed(2) : '—'}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={5} style={{ ...tblHead, textAlign: 'right' }}>TOTALS</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{txns.reduce((a, t) => a + t.dr, 0).toFixed(2)}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{txns.reduce((a, t) => a + t.cr, 0).toFixed(2)}</td>
          </tr>
        </tfoot>
      </table>

      {/* Closing reconciliation */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderTop: '1px solid var(--ink-4)', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Closing reconciliation</div>
          <table style={{ width: '100%', fontSize: 11, marginTop: 4, borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={kvK}>Opening cash float</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(2000)}</td></tr>
              <tr><td style={kvK}>+ Cash sales today</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(6420)}</td></tr>
              <tr><td style={kvK}>− Cash expenses</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(500)}</td></tr>
              <tr><td style={kvK}>− Cash refunded (CN)</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(0)}</td></tr>
              <tr><td style={kvK}>− Bank deposit · 20:15</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(5000)}</td></tr>
              <tr style={{ background: 'var(--bg-sunk)' }}>
                <td style={{ ...kvK, fontWeight: 700, color: 'var(--ink)' }}>Expected closing cash</td>
                <td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 700 }}>{inr(2920)}</td>
              </tr>
              <tr><td style={kvK}>Counted (Z-report)</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(3040)}</td></tr>
              <tr><td style={{ ...kvK, color: 'var(--warn)', fontWeight: 700 }}>Variance</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--warn)' }}>+ {inr(120)}</td></tr>
            </tbody>
          </table>
        </div>
        <div style={{ padding: '10px 16px' }}>
          <div style={lblSm}>Cross-references</div>
          <table style={{ width: '100%', fontSize: 10.5, marginTop: 4, borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={kvK}>Z-report</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>Z/.../0419-01</td></tr>
              <tr><td style={kvK}>GSTR-1 (April)</td><td style={{ ...kvV, textAlign: 'right' }}>Auto-staged</td></tr>
              <tr><td style={kvK}>Bank deposit slip</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>BNK/.../0419-1</td></tr>
              <tr><td style={kvK}>Variance task</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>TSK-2211</td></tr>
              <tr><td style={kvK}>Pending</td><td style={{ ...kvV, textAlign: 'right' }}>1 GRN await invoice</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <LegalFooter
        rule="Maintained per Sec. 35 CGST Act 2017 r/w Rule 56 · entries cross-verified against GSTR-1"
        amountWords={null}
        declaration="The day-book records all primary transactions for the business day. Output tax has been collected against tax invoices and is remitted in the appropriate GST return. Variance against expected closing cash is tracked separately as a task."
        signLabel="For Better Vision · Accounts"
        showBank={false}
      />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   23. DAMAGE PRODUCT REPORT — A4 — internal write-off log
   ═══════════════════════════════════════════════════════════════════════════ */
function TplDamageReport() {
  const items = [
    { d: 'Ray-Ban RB4171 Erika · Tortoise',  sku: 'FRM-RB-4171-TOR', hsn: '9003', qty: 1, cost: 1760, reason: 'R-1 · Dropped during display refresh', who: 'Karan T.', date: '17-Apr · 11:14', fixture: 'W-01', sal: 'No' },
    { d: 'Vogue VO5234 · Rose gold',         sku: 'FRM-VO-5234-RG',  hsn: '9003', qty: 1, cost: 1485, reason: 'R-3 · Hinge defective at QC',         who: 'Riya P.',  date: '18-Apr · 09:55', fixture: 'D-02', sal: 'Vendor claim raised' },
    { d: 'Acuvue Oasys 1-Day · 30pk · −2.50',sku: 'BV-AC-OAS-250',   hsn: '9001', qty: 2, cost: 1485, reason: 'R-2 · Expired (passed cold-chain time)',who: 'Sonia K.', date: '15-Apr · 18:02', fixture: 'CF-01',sal: 'Destroyed per SOP' },
    { d: 'Microfibre cloth · L',             sku: 'BV-CN-MICRO',     hsn: '9605', qty: 6, cost: 82,    reason: 'R-4 · Soiled in transit',              who: 'Ankit V.', date: '12-Apr · 08:50', fixture: 'D-02', sal: 'Return to vendor' },
  ];
  const totalLoss = items.reduce((a, r) => a + r.qty * r.cost, 0);

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <LegalHeader
        docType="DAMAGED / WRITE-OFF REGISTER · Form Inv-12"
        docNo="DMG/BV/2025-26/0419"
        copy="ORIGINAL FOR ACCOUNTS · ☐ DUPLICATE FOR ASM"
        meta={[
          ['Period',         'Week 16 · 12 – 19 Apr-2026'],
          ['Store',          'GK-I Flagship · BV-DELHI-GK1'],
          ['Entries',        items.length + ' lines'],
          ['Total cost-loss', inr(totalLoss)],
          ['Insurance ref.', 'Bajaj Allianz · pol BV-RTL-2026'],
          ['Approver',       'Priya B. · Ops Head'],
          ['Filed under',    'SOP-INV-12 · damage register'],
        ]}
        showBank={false}
      />

      <div style={{ padding: '8px 18px', background: 'var(--bg-sunk)', borderBottom: '1px solid var(--ink-4)', fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
        <b>Damage codes:</b> R-1 in-store handling · R-2 expiry / cold-chain · R-3 vendor / manufacturing defect ·
        R-4 transit · R-5 customer return-damage · R-6 theft / shrinkage (separate SOP).
        Items written off here are removed from saleable stock; cost-loss is posted against P&L · damage.
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr>
            <th style={{ ...tblHead, width: 24 }}>#</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>SKU</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Item</th>
            <th style={tblHead}>HSN</th>
            <th style={tblHead}>Qty</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Cost / unit</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Loss</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Reason</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Reported by · when</th>
            <th style={tblHead}>Last fixture</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Disposal / salvage</th>
          </tr>
        </thead>
        <tbody>
          {items.map((r, i) => (
            <tr key={i}>
              <td style={tblCell}>{i + 1}</td>
              <td style={{ ...tblCell, textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10 }}>{r.sku}</td>
              <td style={{ ...tblCell, textAlign: 'left', fontWeight: 500 }}>{r.d}</td>
              <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r.hsn}</td>
              <td style={tblNum}>{r.qty}</td>
              <td style={tblNum}>{r.cost.toFixed(2)}</td>
              <td style={{ ...tblNum, fontWeight: 600 }}>{(r.qty * r.cost).toFixed(2)}</td>
              <td style={{ ...tblCell, textAlign: 'left', fontSize: 10, color: 'var(--ink-3)' }}>{r.reason}</td>
              <td style={{ ...tblCell, textAlign: 'left', fontSize: 10 }}>
                <div>{r.who}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--ink-4)' }}>{r.date}</div>
              </td>
              <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{r.fixture}</td>
              <td style={{ ...tblCell, textAlign: 'left', fontSize: 10 }}>{r.sal}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={6} style={{ ...tblHead, textAlign: 'right' }}>TOTAL COST-LOSS (₹)</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontSize: 13 }}>{totalLoss.toFixed(2)}</td>
            <td colSpan={4}/>
          </tr>
        </tfoot>
      </table>

      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Investigation summary</div>
          <ol style={{ margin: '4px 0 0 16px', padding: 0, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
            <li>R-1 (Ray-Ban): photo evidence on file (DMG-0419-A.jpg); no insurance claim (under deductible of ₹ 5,000).</li>
            <li>R-3 (Vogue): defective hinge identified at fitting; vendor (Luxottica) acknowledged DOA — replacement promised by 22-Apr · debit note DN/…/0419-02 raised.</li>
            <li>R-2 (Acuvue): one packet exceeded permissible time outside fridge during 18-Apr power outage (45 min); destroyed per SOP-INV-12.</li>
            <li>R-4 (cloths): water damage during transit; vendor compensating with replacement batch.</li>
          </ol>
        </div>
        <div style={{ padding: '10px 16px' }}>
          <div style={lblSm}>Financial posting</div>
          <table style={{ width: '100%', fontSize: 11, marginTop: 4, borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={kvK}>P&amp;L · Damage</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(totalLoss)}</td></tr>
              <tr><td style={kvK}>Recoverable (vendor)</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--ok)' }}>− {inr(1485 + 6 * 82)}</td></tr>
              <tr><td style={kvK}>Insurance (deductible)</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>NIL</td></tr>
              <tr style={{ background: 'var(--bg-sunk)' }}>
                <td style={{ ...kvK, fontWeight: 700, color: 'var(--ink)' }}>Net P&amp;L impact</td>
                <td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--err)' }}>{inr(totalLoss - 1485 - 6 * 82)}</td>
              </tr>
              <tr><td style={kvK}>ITC reversal</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr((totalLoss - 1485 - 6 * 82) * 0.12)} in GSTR-3B (Apr)</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <LegalFooter
        rule="ITC reversal per Sec. 17(5)(h) CGST Act — goods destroyed / lost are not eligible for input tax credit"
        amountWords={'Total cost-loss of ' + inWords(totalLoss)}
        declaration="We confirm that the items listed have been physically verified as damaged / written-off and removed from saleable inventory. Insurance and vendor recovery actions noted above. ITC reversal will be reflected in GSTR-3B for the relevant period."
        signLabel="For Better Vision · Inventory & ASM"
        showBank={false}
      />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   24. WORKSHOP / QC STICKER — small thermal · stuck on lens packet
   ═══════════════════════════════════════════════════════════════════════════ */
function TplQcSticker() {
  return (
    <div className="paper sticker" data-doc style={{ padding: '10px 12px', color: 'var(--ink)', fontFamily: 'var(--font-sans)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingBottom: 6, borderBottom: '2px solid var(--ink)' }}>
        <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
          <div style={{ width: 22, height: 22, border: '1.5px solid var(--ink)', display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 12 }}>B</div>
          <div>
            <div style={{ fontWeight: 700, fontSize: 11, letterSpacing: '.02em' }}>BV Workshop</div>
            <div style={{ fontSize: 7.5, color: 'var(--ink-3)', textTransform: 'uppercase', letterSpacing: '.1em' }}>QC Pass</div>
          </div>
        </div>
        <div style={{ padding: '2px 6px', border: '1.5px solid var(--ok)', color: 'var(--ok)', fontWeight: 700, fontSize: 8.5, letterSpacing: '.14em' }}>PASS</div>
      </div>

      <div style={{ marginTop: 8, fontSize: 9.5 }}>
        <div style={{ ...lblSm, fontSize: 7.5 }}>Job number</div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, letterSpacing: '.04em', marginTop: 1 }}>JB-GK1-0418</div>
      </div>

      <div style={{ marginTop: 6, padding: '5px 0', borderTop: '1px dashed var(--ink-5)', borderBottom: '1px dashed var(--ink-5)' }}>
        <table style={{ width: '100%', fontSize: 8.5, borderCollapse: 'collapse' }}>
          <tbody>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 7 }}>Customer</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right', fontSize: 8.5, fontWeight: 600 }}>Aanya S.</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 7 }}>Cust ID</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right', fontSize: 8.5, fontFamily: 'var(--font-mono)' }}>CUS-00214</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 7 }}>Lens</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right', fontSize: 8.5 }}>Varilux X · 1.60</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 7 }}>Coat</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right', fontSize: 8.5 }}>Crizal Alizé UV</td></tr>
          </tbody>
        </table>
      </div>

      <div style={{ marginTop: 6 }}>
        <div style={{ ...lblSm, fontSize: 7.5 }}>Power confirmed</div>
        <table style={{ width: '100%', marginTop: 2, borderCollapse: 'collapse', fontSize: 8.5, border: '1px solid var(--ink-4)' }}>
          <thead>
            <tr style={{ background: 'var(--bg-sunk)' }}>
              <th style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontWeight: 600, fontSize: 7.5 }}>Eye</th>
              <th style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontWeight: 600, fontSize: 7.5 }}>SPH</th>
              <th style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontWeight: 600, fontSize: 7.5 }}>CYL</th>
              <th style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontWeight: 600, fontSize: 7.5 }}>AX</th>
              <th style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontWeight: 600, fontSize: 7.5 }}>ADD</th>
            </tr>
          </thead>
          <tbody>
            <tr><td style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontWeight: 700 }}>OD</td><td style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontVariantNumeric: 'tabular-nums' }}>−2.50</td><td style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontVariantNumeric: 'tabular-nums' }}>−0.75</td><td style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontVariantNumeric: 'tabular-nums' }}>175</td><td style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontVariantNumeric: 'tabular-nums' }}>+1.25</td></tr>
            <tr><td style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontWeight: 700 }}>OS</td><td style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontVariantNumeric: 'tabular-nums' }}>−3.00</td><td style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontVariantNumeric: 'tabular-nums' }}>−0.50</td><td style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontVariantNumeric: 'tabular-nums' }}>5</td><td style={{ padding: '2px 3px', border: '1px solid var(--ink-5)', fontVariantNumeric: 'tabular-nums' }}>+1.25</td></tr>
          </tbody>
        </table>
      </div>

      <div style={{ marginTop: 6, fontSize: 8.5 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 7 }}>QC date / time</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 8.5 }}>22-Apr · 14:08</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 7 }}>QC officer</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right', fontSize: 8.5 }}>Riya P. · EMP-0144</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 7 }}>Cosmetic</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right', fontSize: 8.5, color: 'var(--ok)', fontWeight: 600 }}>● OK</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 7 }}>Power tol.</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right', fontSize: 8.5, color: 'var(--ok)', fontWeight: 600 }}>±0.06 (within)</td></tr>
            <tr><td style={{ ...kvK, padding: '1px 0', fontSize: 7 }}>Fitting</td><td style={{ ...kvV, padding: '1px 0', textAlign: 'right', fontSize: 8.5, color: 'var(--ok)', fontWeight: 600 }}>● 18 mm</td></tr>
          </tbody>
        </table>
      </div>

      {/* Bar-code-style signature */}
      <div style={{ marginTop: 8, padding: '4px 0', borderTop: '1px dashed var(--ink-5)', textAlign: 'center' }}>
        <div style={{ display: 'flex', gap: 1, justifyContent: 'center', height: 20 }}>
          {Array.from({ length: 32 }).map((_, i) => (
            <div key={i} style={{ width: i % 4 === 0 ? 2 : 1, background: 'var(--ink)' }} />
          ))}
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 7, letterSpacing: '.16em', marginTop: 2 }}>QC-22041408-RP-OK</div>
      </div>

      <div style={{ marginTop: 4, fontSize: 7, color: 'var(--ink-4)', textAlign: 'center', textTransform: 'uppercase', letterSpacing: '.1em' }}>
        Affix on lens packet · do not remove · workshop sticker
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   25. EXPENSE VOUCHER — A6 — petty cash / store expense + approval
   ═══════════════════════════════════════════════════════════════════════════ */
function TplExpenseVoucher() {
  const amt = 1240;
  return (
    <div className="paper a6" data-doc style={{ color: 'var(--ink)' }}>
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center',
        padding: '5px 12px', background: 'var(--ink)', color: '#fff',
        fontSize: 9.5, letterSpacing: '.16em', fontWeight: 600, textTransform: 'uppercase',
      }}>
        <span>EXPENSE VOUCHER · Form Acc-04</span>
        <span style={{ opacity: .8, fontSize: 9 }}>STORE COPY</span>
      </div>

      <div style={{ padding: '10px 14px', borderBottom: '1.5px solid var(--ink)', display: 'grid', gridTemplateColumns: '28px 1fr', gap: 8 }}>
        <div style={{ width: 28, height: 28, border: '1.5px solid var(--ink)', display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 14 }}>B</div>
        <div>
          <div style={{ fontSize: 11.5, fontWeight: 700 }}>{BV_LEGAL.legal}</div>
          <div style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 1 }}>GK-I Flagship · BV-DELHI-GK1</div>
        </div>
      </div>

      <div style={{ padding: '10px 14px', borderBottom: '1.5px solid var(--ink)', display: 'grid', gridTemplateColumns: '1fr auto', gap: 8 }}>
        <div>
          <div style={lblSm}>Voucher No.</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, marginTop: 1 }}>EXP/BV/2025-26/0419-07</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={lblSm}>Date</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600, marginTop: 1 }}>19-Apr-2026</div>
        </div>
      </div>

      <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--ink-4)' }}>
        <div style={lblSm}>Pay to</div>
        <div style={{ fontSize: 13, fontWeight: 700, marginTop: 3 }}>Karan T. · EMP-0151</div>
        <div style={{ fontSize: 10, color: 'var(--ink-3)' }}>Reimbursement on behalf of Optician role</div>
      </div>

      <div style={{ padding: '12px 14px', borderBottom: '1.5px solid var(--ink)', display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, alignItems: 'baseline' }}>
        <div>
          <div style={lblSm}>Amount</div>
          <div style={{ fontSize: 30, fontWeight: 700, marginTop: 4, fontVariantNumeric: 'tabular-nums', letterSpacing: '-.02em', lineHeight: 1 }}>{inrI(amt)}</div>
          <div style={{ fontSize: 10, color: 'var(--ink-3)', fontStyle: 'italic', marginTop: 4 }}>{inWords(amt)}</div>
        </div>
        <div style={{ textAlign: 'right', minWidth: 80 }}>
          <div style={lblSm}>Mode</div>
          <div style={{ fontSize: 11, fontWeight: 600, marginTop: 2 }}>Petty cash</div>
        </div>
      </div>

      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--ink-4)' }}>
        <div style={lblSm}>Expense head &amp; description</div>
        <table style={{ width: '100%', fontSize: 10.5, marginTop: 4, borderCollapse: 'collapse' }}>
          <tbody>
            <tr><td style={kvK}>Head</td><td style={kvV}>5320 · Workshop tools &amp; consumables</td></tr>
            <tr><td style={kvK}>Sub-head</td><td style={kvV}>5321 · Edging blades</td></tr>
            <tr><td style={kvK}>Description</td><td style={kvV}>4 × edging blade (Essilor compatible) · purchased from Khanna Optical, Connaught Place</td></tr>
            <tr><td style={kvK}>Bill ref.</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)' }}>K-OPT/INV-2026/4498</td></tr>
            <tr><td style={kvK}>GST charged</td><td style={kvV}>₹ 132.92 (18%) — credit available, claimed in GSTR-3B</td></tr>
          </tbody>
        </table>
      </div>

      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--ink-4)' }}>
        <div style={lblSm}>Approval chain</div>
        <table style={{ width: '100%', fontSize: 10, marginTop: 4, borderCollapse: 'collapse' }}>
          <tbody>
            <tr>
              <td style={{ ...kvK, fontSize: 9 }}>Requested</td>
              <td style={{ ...kvV, fontSize: 10 }}>Karan T. · 16:14</td>
              <td style={{ color: 'var(--ok)', fontWeight: 600, fontSize: 10, textAlign: 'right' }}>● raised</td>
            </tr>
            <tr>
              <td style={{ ...kvK, fontSize: 9 }}>SM approved</td>
              <td style={{ ...kvV, fontSize: 10 }}>Sonia K. · 16:18 (PIN-verified)</td>
              <td style={{ color: 'var(--ok)', fontWeight: 600, fontSize: 10, textAlign: 'right' }}>● approved</td>
            </tr>
            <tr>
              <td style={{ ...kvK, fontSize: 9 }}>ASM countersign</td>
              <td style={{ ...kvV, fontSize: 10 }}>Auto-cleared (≤ ₹ 2,000 SM threshold)</td>
              <td style={{ color: 'var(--ink-4)', fontSize: 10, textAlign: 'right' }}>○ not req.</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div style={{ padding: '12px 14px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <div>
          <div style={lblSm}>Paid by</div>
          <div style={{ height: 22, marginTop: 3, borderBottom: '0.5px solid var(--ink-4)' }} />
          <div style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>Sonia K. · cash 16:18</div>
        </div>
        <div>
          <div style={lblSm}>Received by</div>
          <div style={{ height: 22, marginTop: 3, borderBottom: '0.5px solid var(--ink-4)' }} />
          <div style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>Karan T.</div>
        </div>
      </div>

      <div style={{ padding: '6px 14px', fontSize: 8, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em', textAlign: 'center', borderTop: '1px solid var(--ink-4)' }}>
        Form Acc-04 · paid-out posted to DayBook · attach physical bill before filing
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   26. VENDOR LEDGER STATEMENT — A4 — AP statement to vendor
   ═══════════════════════════════════════════════════════════════════════════ */
function TplVendorLedger() {
  const txns = [
    { d: '01-Apr-26', ref: 'OB',                    typ: 'Opening',  desc: 'Brought forward from previous month',                dr: 0,     cr: 4500,  bal: -4500 },
    { d: '03-Apr-26', ref: 'PMT-2611',              typ: 'Payment',  desc: 'NEFT settlement of prior month invoice',             dr: 0,     cr: 0,     bal: -4500, note: 'cleared' },
    { d: '03-Apr-26', ref: 'PMT-2611',              typ: 'Payment',  desc: 'Out: NEFT · HDFC ****4421',                          dr: 4500,  cr: 0,     bal: 0 },
    { d: '11-Apr-26', ref: 'PO/.../0038',           typ: 'PO',       desc: 'Acuvue 1-Day · 24 boxes · ordered',                  dr: 0,     cr: 0,     bal: 0, note: 'memo' },
    { d: '13-Apr-26', ref: 'JJ/24/04/2204',         typ: 'Bill',     desc: 'Vendor invoice received (PO/.../0038)',              dr: 0,     cr: 41580, bal: -41580 },
    { d: '14-Apr-26', ref: 'GRN/.../0413-16',       typ: 'GRN',      desc: 'Goods received in full · no variance',                dr: 0,     cr: 0,     bal: -41580 },
    { d: '17-Apr-26', ref: 'JJ/24/04/2240',         typ: 'Bill',     desc: 'Vendor invoice for PO/.../0042 received',            dr: 0,     cr: 41580, bal: -83160 },
    { d: '18-Apr-26', ref: 'GRN/.../0418-22',       typ: 'GRN',      desc: '23 of 24 received · 1 short',                          dr: 0,     cr: 0,     bal: -83160 },
    { d: '19-Apr-26', ref: 'DN/.../0418-04',        typ: 'Debit',    desc: 'Short shipment · debit raised (incl. IGST)',         dr: 1848,  cr: 0,     bal: -81312 },
  ];
  const drT = txns.reduce((a, t) => a + t.dr, 0);
  const crT = txns.reduce((a, t) => a + t.cr, 0);
  const closing = Math.abs(txns[txns.length - 1].bal);

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <LegalHeader
        docType="VENDOR STATEMENT OF ACCOUNT · AP"
        docNo="VLG/V-0044/2026-04"
        copy="ORIGINAL FOR VENDOR"
        meta={[
          ['Statement period', '01-Apr-2026 → 30-Apr-2026'],
          ['Statement date',   '19-Apr-2026 · 21:36 IST'],
          ['Vendor code',      'V-0044'],
          ['Payment terms',    'Net-30 · NEFT'],
          ['Closing balance',  inr(closing) + ' (Cr.) payable'],
          ['Statement of',     'Johnson & Johnson India Pvt. Ltd.'],
        ]}
        showBank={true}
      />

      <PartyBlock blocks={[
        { h: 'Vendor', rows: [
          ['Legal name',  'Johnson & Johnson India Pvt. Ltd.'],
          ['Address',     '4th Flr, Arena Space, JVLR, Andheri (E), Mumbai 400059'],
          ['State / Code','Maharashtra / 27'],
          ['GSTIN',       '27AAACJ4863N1ZE'],
          ['PAN',         'AAACJ4863N'],
          ['Contact',     'Ramesh M. · +91 22 6188 9100 · ramesh.m@jnj.com'],
          ['Net terms',   'Net-30 from GRN sign-off'],
        ] },
        { h: 'Buyer', rows: [
          ['Legal name',   BV_LEGAL.legal],
          ['Address',      'Plot 12, Okhla Phase II, New Delhi 110020'],
          ['State / Code', 'Delhi / 07'],
          ['GSTIN',        BV_LEGAL.gstin],
          ['PAN',          BV_LEGAL.pan],
          ['AP contact',   'accounts@bettervision.in · +91 11 4135 2014'],
          ['Vendor since', 'Aug 2019 · 78 invoices · ₹ 18.4L lifetime'],
        ] },
      ]}/>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr>
            <th style={{ ...tblHead, textAlign: 'left' }}>Date</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Reference</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Type</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Description</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Debit (₹)</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Credit (₹)</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Balance</th>
          </tr>
        </thead>
        <tbody>
          {txns.map((t, i) => (
            <tr key={i}>
              <td style={{ ...tblCell, textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10 }}>{t.d}</td>
              <td style={{ ...tblCell, textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10 }}>{t.ref}</td>
              <td style={{ ...tblCell, textAlign: 'left', fontWeight: 500 }}>{t.typ}</td>
              <td style={{ ...tblCell, textAlign: 'left' }}>{t.desc}{t.note && <span style={{ color: 'var(--ink-4)', fontSize: 9.5, marginLeft: 6, fontStyle: 'italic' }}>· {t.note}</span>}</td>
              <td style={tblNum}>{t.dr ? t.dr.toFixed(2) : '—'}</td>
              <td style={tblNum}>{t.cr ? t.cr.toFixed(2) : '—'}</td>
              <td style={{ ...tblNum, fontFamily: 'var(--font-mono)', fontWeight: 600, color: t.bal < 0 ? 'var(--err)' : 'var(--ink)' }}>
                {t.bal === 0 ? inr(0) : (t.bal < 0 ? inr(Math.abs(t.bal)) + ' Cr' : inr(t.bal) + ' Dr')}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={4} style={{ ...tblHead, textAlign: 'right' }}>PERIOD TOTAL</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{drT.toFixed(2)}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{crT.toFixed(2)}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontSize: 12, color: 'var(--err)' }}>{inr(closing)} Cr</td>
          </tr>
        </tfoot>
      </table>

      {/* Aging buckets */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', borderTop: '1.5px solid var(--ink)', borderBottom: '1.5px solid var(--ink)' }}>
        {[
          ['Current', inr(41580), '0–30 d'],
          ['31–60 d', inr(39732), 'next due'],
          ['61–90 d', inr(0),     'OK'],
          ['90+ d',   inr(0),     'OK'],
          ['Total payable', inr(closing), 'gross'],
        ].map(([k, v, s], i) => (
          <div key={k} style={{ padding: '10px 14px', borderRight: i === 4 ? 'none' : '1px solid var(--ink-4)' }}>
            <div style={{ ...lblSm, fontSize: 9 }}>{k}</div>
            <div style={{ fontSize: 17, fontWeight: 700, marginTop: 3, fontVariantNumeric: 'tabular-nums', letterSpacing: '-.01em', color: i === 4 ? 'var(--err)' : 'var(--ink)' }}>{v}</div>
            <div style={{ fontSize: 10, color: 'var(--ink-4)', marginTop: 2 }}>{s}</div>
          </div>
        ))}
      </div>

      <LegalFooter
        rule="Statement of account · please reconcile and raise discrepancies within 15 days · payable per Net-30 terms"
        amountWords={'Closing balance ' + inWords(closing) + ' payable to vendor'}
        declaration="The transactions above represent all bills, GRNs, debit notes and payments recorded against this vendor account for the stated period. Please confirm reconciliation. Outstanding balance shall be settled per agreed terms; any discrepancy must be reported within 15 days, beyond which the statement shall be treated as accepted."
        signLabel="For Better Vision · Accounts Payable"
      />
    </div>
  );
}

Object.assign(window, {
  TplSaleOrder, TplSaleReturn, TplStockTally, TplDayBook,
  TplDamageReport, TplQcSticker, TplExpenseVoucher, TplVendorLedger,
});
