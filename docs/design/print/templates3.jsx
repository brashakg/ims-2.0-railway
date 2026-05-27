/* global React, MOCK, BV_LEGAL, LegalHeader, StaffHeader, PartyBlock, LegalFooter,
   StatusStamp, FieldChip, inWords, inr, inrI,
   kvK, kvV, lblSm, tblHead, tblCell, tblNum */
/* Six more templates covering app features that needed printed paperwork:
   appointments (Clinical), warranty (POS sale), gift vouchers (POS product),
   customer ledger (loyalty/wallet), debit note (GRN shortage flow),
   shift handover (between cashiers). Same statutory aesthetic. */

/* ═══════════════════════════════════════════════════════════════════════════
   13. EYE-EXAM APPOINTMENT SLIP — A5 — customer
   ═══════════════════════════════════════════════════════════════════════════ */
function TplAppointment() {
  return (
    <div className="paper a5" data-doc style={{ color: 'var(--ink)' }}>
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center',
        padding: '6px 16px', background: 'var(--ink)', color: '#fff',
        fontSize: 10, letterSpacing: '.16em', fontWeight: 600, textTransform: 'uppercase',
      }}>
        <span>EYE-EXAM APPOINTMENT · Form Clin-02</span>
        <span style={{ opacity: .8, fontSize: 9.5 }}>PATIENT COPY</span>
      </div>

      {/* Compact identity header for A5 */}
      <div style={{ padding: '10px 16px', borderBottom: '1.5px solid var(--ink)', display: 'grid', gridTemplateColumns: '34px 1fr auto', gap: 10, alignItems: 'flex-start' }}>
        <div style={{ width: 34, height: 34, border: '1.5px solid var(--ink)', display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 18 }}>B</div>
        <div>
          <div style={{ fontSize: 13.5, fontWeight: 700 }}>{BV_LEGAL.legal}</div>
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', textTransform: 'uppercase', letterSpacing: '.08em', marginTop: 1 }}>Optometry &amp; Vision Care · GK-I clinic</div>
          <div style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 4, lineHeight: 1.5 }}>
            {BV_LEGAL.storeAddr}<br />
            {BV_LEGAL.phone} · <span className="mono">Drug Lic. {BV_LEGAL.drug}</span>
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={lblSm}>Appointment No.</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700, marginTop: 2 }}>AP/GK1/2026/0419-07</div>
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 4 }}>Booked <span className="mono">19-Apr-2026 · 11:18 IST</span><br />Booking ref. <span className="mono">CLN-2026-04-1107</span></div>
        </div>
      </div>

      {/* The big "When / Where / Who" block — what the patient actually needs */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '14px 18px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Your appointment</div>
          <div style={{ fontSize: 20, fontWeight: 700, marginTop: 6, letterSpacing: '-.01em' }}>Saturday, 25-Apr-2026</div>
          <div style={{ fontSize: 28, fontWeight: 700, marginTop: 2, fontVariantNumeric: 'tabular-nums', letterSpacing: '-.02em' }}>10:30 IST</div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 6 }}>Estimated duration · 35–40 min</div>
          <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            <FieldChip k="Type" v="Comprehensive eye exam" />
            <FieldChip k="Mode" v="In-person" />
          </div>
        </div>
        <div style={{ padding: '14px 18px' }}>
          <div style={lblSm}>Doctor</div>
          <div style={{ fontSize: 14, fontWeight: 700, marginTop: 4 }}>Dr. Ritu Malhotra</div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 1 }}>MBBS, DOMS · Optometrist</div>
          <table style={{ marginTop: 6, fontSize: 10, borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={kvK}>DMC reg.</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)', fontSize: 10 }}>DMC/R-4412/2014</td></tr>
              <tr><td style={kvK}>Chamber</td><td style={{ ...kvV, fontSize: 10 }}>Chamber 2 · ground floor</td></tr>
              <tr><td style={kvK}>Available</td><td style={{ ...kvV, fontSize: 10 }}>Sat: 10:00 – 14:00 IST</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Patient block */}
      <PartyBlock blocks={[
        { h: 'Patient', rows: [
          ['Name',          'Mr. Vikram Shah'],
          ['Age / Sex',     '38 yrs / Male'],
          ['Phone',         '+91 98112 90744'],
          ['Patient ID',    'CUS-00942 (new)'],
          ['Visit reason',  'Refraction · screen-strain'],
          ['Existing Rx',   'Bring current spectacles, if any'],
        ] },
      ]}/>

      {/* What to bring + prep instructions */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>What to bring</div>
          <ul style={{ margin: '4px 0 0 16px', padding: 0, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
            <li>Government-issued photo ID (Aadhaar / DL / passport)</li>
            <li>Current spectacles &amp; contact lenses, if any</li>
            <li>Past prescription card / Rx, even from another clinic</li>
            <li>List of current medications</li>
            <li>Insurance / corporate eyewear card, if applicable</li>
          </ul>
        </div>
        <div style={{ padding: '10px 16px' }}>
          <div style={lblSm}>Before you visit · prep</div>
          <ul style={{ margin: '4px 0 0 16px', padding: 0, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
            <li><b>No contact lenses</b> for at least 24 hours before exam</li>
            <li>Avoid eye drops / makeup on exam day</li>
            <li>Arrive 10 min early for pre-screening (auto-refraction)</li>
            <li>If pupils may be dilated, arrange transport home</li>
            <li>Minor (&lt; 18)? A parent / guardian must accompany</li>
          </ul>
        </div>
      </div>

      {/* Booking + cancellation terms */}
      <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--ink-4)', fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
        <div style={lblSm}>Cancellation &amp; reschedule</div>
        <div style={{ marginTop: 4 }}>
          Free of charge if cancelled / rescheduled <b>≥ 4 hours</b> before slot. Late-cancellation or no-show forfeits the ₹ 200 booking advance (refunded against any same-day in-store purchase). Reschedule via WhatsApp on <b className="mono">{BV_LEGAL.phone}</b> or scan the QR overleaf.
        </div>
      </div>

      <div style={{ padding: '10px 16px', display: 'grid', gridTemplateColumns: '1fr auto', gap: 12, alignItems: 'center' }}>
        <div style={{ fontSize: 10, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em' }}>
          Token will be issued at the clinic upon arrival · please remain seated until called
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={lblSm}>Booked by</div>
          <div style={{ fontSize: 11, marginTop: 2 }}>Karan T. · POS-02 · 11:18 IST</div>
        </div>
      </div>

      <div style={{ padding: '7px 16px', fontSize: 9, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em', textAlign: 'center', borderTop: '1px solid var(--ink-4)' }}>
        Form Clin-02 · NCAHP-registered practice · please retain this slip for the entire visit
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   14. WARRANTY CARD — A6 — customer · given with every frame + lens sale
   ═══════════════════════════════════════════════════════════════════════════ */
function TplWarranty() {
  return (
    <div className="paper a6" data-doc style={{ color: 'var(--ink)' }}>
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center',
        padding: '5px 12px', background: 'var(--ink)', color: '#fff',
        fontSize: 9.5, letterSpacing: '.16em', fontWeight: 600, textTransform: 'uppercase',
      }}>
        <span>WARRANTY CARD · Form WX-01</span>
        <span style={{ opacity: .8, fontSize: 9 }}>CUSTOMER COPY</span>
      </div>

      <div style={{ padding: '10px 14px', borderBottom: '1.5px solid var(--ink)', display: 'grid', gridTemplateColumns: '28px 1fr', gap: 8, alignItems: 'flex-start' }}>
        <div style={{ width: 28, height: 28, border: '1.5px solid var(--ink)', display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 14 }}>B</div>
        <div>
          <div style={{ fontSize: 11.5, fontWeight: 700 }}>{BV_LEGAL.legal}</div>
          <div style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 1 }}>GK-I Flagship · {BV_LEGAL.phone}</div>
          <div style={{ fontSize: 9, color: 'var(--ink-3)', fontFamily: 'var(--font-mono)' }}>GSTIN {BV_LEGAL.gstin}</div>
        </div>
      </div>

      <div style={{ padding: '10px 14px', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={lblSm}>Warranty No.</div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, marginTop: 1 }}>WX/GK1/2025-26/249183</div>
        <div style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 4 }}>
          Issued <b className="mono">19-Apr-2026</b> · against invoice <b className="mono">BV/GK1/2025-26/249183</b>
        </div>
      </div>

      {/* Customer + product */}
      <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--ink-4)' }}>
        <div style={lblSm}>Customer</div>
        <table style={{ width: '100%', fontSize: 10, borderCollapse: 'collapse', marginTop: 3 }}>
          <tbody>
            <tr><td style={kvK}>Name</td><td style={{ ...kvV, fontWeight: 700, fontSize: 10 }}>Ms. Aanya Sharma</td></tr>
            <tr><td style={kvK}>Phone</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)', fontSize: 10 }}>+91 98115 22100</td></tr>
            <tr><td style={kvK}>Customer ID</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)', fontSize: 10 }}>CUS-00214</td></tr>
          </tbody>
        </table>
      </div>

      <div style={{ padding: '8px 14px', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={lblSm}>Product covered</div>
        <table style={{ width: '100%', fontSize: 10, borderCollapse: 'collapse', marginTop: 3 }}>
          <tbody>
            <tr><td style={kvK}>Frame</td><td style={{ ...kvV, fontSize: 10 }}>Ray-Ban Wayfarer RB2140 · Black · 50-22-150</td></tr>
            <tr><td style={kvK}>Frame SKU</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)', fontSize: 10 }}>FRM-RB-2140-BLK-50</td></tr>
            <tr><td style={kvK}>Lens</td><td style={{ ...kvV, fontSize: 10 }}>Essilor Varilux X · 1.60 · Crizal Alizé UV</td></tr>
            <tr><td style={kvK}>Lens SKU</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)', fontSize: 10 }}>LNS-EX-160-CRZ-BR15</td></tr>
            <tr><td style={kvK}>Rx ref.</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)', fontSize: 10 }}>RX/GK1/2026/4418</td></tr>
          </tbody>
        </table>
      </div>

      {/* Warranty terms — clear, two columns */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1px solid var(--ink-4)' }}>
        <div style={{ padding: '8px 12px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Frame</div>
          <div style={{ fontSize: 18, fontWeight: 700, marginTop: 2 }}>12 months</div>
          <div style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>from invoice date<br />Expires <b className="mono">18-Apr-2027</b></div>
        </div>
        <div style={{ padding: '8px 12px' }}>
          <div style={lblSm}>Lens</div>
          <div style={{ fontSize: 18, fontWeight: 700, marginTop: 2 }}>6 months</div>
          <div style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>from invoice date<br />Expires <b className="mono">18-Oct-2026</b></div>
        </div>
      </div>

      <div style={{ padding: '8px 12px', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={lblSm}>Covered</div>
        <ul style={{ margin: '2px 0 0 14px', padding: 0, fontSize: 9.5, color: 'var(--ink-3)', lineHeight: 1.5 }}>
          <li>Manufacturing defects · hinge / screw / temple failure</li>
          <li>Lens coating peel within stated period</li>
          <li>Plating issues on frame body</li>
          <li>Free lifetime <b>fitting / adjustment</b> at any BV store</li>
        </ul>
        <div style={{ ...lblSm, marginTop: 6 }}>Not covered</div>
        <ul style={{ margin: '2px 0 0 14px', padding: 0, fontSize: 9.5, color: 'var(--ink-3)', lineHeight: 1.5 }}>
          <li>Accidental damage, scratches, breakage from misuse</li>
          <li>Loss or theft of the product</li>
          <li>Damage from un-authorised repair / servicing</li>
        </ul>
      </div>

      <div style={{ padding: '8px 14px', display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, alignItems: 'flex-end' }}>
        <div style={{ fontSize: 9, color: 'var(--ink-4)', lineHeight: 1.5 }}>
          Claim by visiting any Better Vision store with this card + original invoice.<br />
          Subject to {BV_LEGAL.state} jurisdiction. SOP-CX-08.
        </div>
        <div>
          <div style={lblSm}>Issued by</div>
          <div style={{ height: 30, marginTop: 2, borderBottom: '0.5px solid var(--ink-4)', width: 100 }} />
          <div style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2, display: 'flex', justifyContent: 'space-between', width: 100 }}>
            <span>Sonia K.</span><span style={{ color: 'var(--ink-4)' }}>[Seal]</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   15. GIFT VOUCHER — A5 — customer · POS product
   ═══════════════════════════════════════════════════════════════════════════ */
function TplGiftVoucher() {
  const amt = 5000;
  return (
    <div className="paper a5" data-doc style={{ color: 'var(--ink)', position: 'relative', overflow: 'hidden' }}>
      {/* Subtle guilloché-style background watermark — bordered grid for anti-counterfeit feel */}
      <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', opacity: 0.06,
        backgroundImage: 'repeating-linear-gradient(45deg, var(--ink) 0 1px, transparent 1px 14px), repeating-linear-gradient(-45deg, var(--ink) 0 1px, transparent 1px 14px)',
      }} />

      <div style={{
        display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center',
        padding: '6px 16px', background: 'var(--ink)', color: '#fff',
        fontSize: 10, letterSpacing: '.16em', fontWeight: 600, textTransform: 'uppercase',
        position: 'relative',
      }}>
        <span>GIFT VOUCHER · Form GV-01</span>
        <span style={{ opacity: .8, fontSize: 9.5 }}>BEARER INSTRUMENT · DO NOT FOLD</span>
      </div>

      <div style={{ padding: '14px 18px', borderBottom: '1.5px solid var(--ink)', display: 'grid', gridTemplateColumns: '34px 1fr auto', gap: 10, alignItems: 'flex-start', position: 'relative' }}>
        <div style={{ width: 34, height: 34, border: '1.5px solid var(--ink)', display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 18 }}>B</div>
        <div>
          <div style={{ fontSize: 13.5, fontWeight: 700 }}>{BV_LEGAL.legal}</div>
          <div style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 2 }}>{BV_LEGAL.storeAddr}</div>
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', fontFamily: 'var(--font-mono)', marginTop: 1 }}>GSTIN {BV_LEGAL.gstin}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={lblSm}>Voucher No.</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700, marginTop: 2 }}>GV/2026/00488</div>
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 4, fontFamily: 'var(--font-mono)' }}>Check digits · <b>7142-8810</b></div>
        </div>
      </div>

      {/* The big amount */}
      <div style={{ padding: '20px 24px', borderBottom: '1.5px solid var(--ink)', display: 'grid', gridTemplateColumns: '1fr auto', gap: 20, alignItems: 'center', position: 'relative' }}>
        <div>
          <div style={lblSm}>Voucher value (incl. all taxes)</div>
          <div style={{ fontSize: 56, fontWeight: 700, lineHeight: 1, letterSpacing: '-.03em', fontVariantNumeric: 'tabular-nums', marginTop: 4 }}>{inrI(amt)}</div>
          <div style={{ fontSize: 11, fontStyle: 'italic', color: 'var(--ink-3)', marginTop: 6 }}>{inWords(amt)}</div>
        </div>
        <div style={{ textAlign: 'center', padding: '14px 18px', border: '2px solid var(--ink)' }}>
          <div style={{ fontSize: 8.5, color: 'var(--ink-3)', textTransform: 'uppercase', letterSpacing: '.16em', fontWeight: 600 }}>Valid until</div>
          <div style={{ fontSize: 17, fontWeight: 700, marginTop: 4, fontVariantNumeric: 'tabular-nums' }}>18-Apr-2027</div>
          <div style={{ fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>12 months from issue</div>
        </div>
      </div>

      {/* Issued to / from + serial barcode */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1.5px solid var(--ink)', position: 'relative' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Purchased by</div>
          <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse', marginTop: 3 }}>
            <tbody>
              <tr><td style={kvK}>Name</td><td style={{ ...kvV, fontWeight: 600 }}>Mr. Rohan Iyer</td></tr>
              <tr><td style={kvK}>Phone</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)' }}>+91 98933 40127</td></tr>
              <tr><td style={kvK}>Customer ID</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)' }}>CUS-10390</td></tr>
              <tr><td style={kvK}>Paid by</td><td style={kvV}>Card · HDFC ****4421</td></tr>
              <tr><td style={kvK}>Issued at</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)' }}>19-Apr · 15:08 IST</td></tr>
            </tbody>
          </table>
        </div>
        <div style={{ padding: '10px 16px' }}>
          <div style={lblSm}>For (recipient)</div>
          <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse', marginTop: 3 }}>
            <tbody>
              <tr><td style={kvK}>Name</td><td style={{ ...kvV, fontWeight: 600 }}>Ms. Aanya Sharma</td></tr>
              <tr><td style={kvK}>Greeting</td><td style={kvV}>"Happy birthday, dear!"</td></tr>
              <tr><td style={kvK}>Personal note</td><td style={{ ...kvV, fontStyle: 'italic', color: 'var(--ink-3)' }}>Treat yourself to something nice ♥ — Rohan</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Terms */}
      <div style={{ padding: '10px 16px', position: 'relative' }}>
        <div style={lblSm}>Terms of use</div>
        <ol style={{ margin: '4px 0 0 16px', padding: 0, fontSize: 9.5, color: 'var(--ink-3)', lineHeight: 1.5 }}>
          <li>This voucher is a <b>bearer instrument</b> · whoever presents it can redeem · keep safe.</li>
          <li>Redeem at any Better Vision store in India · single use · partial redemption allowed up to twice.</li>
          <li>Not exchangeable for cash. Not refundable. Lost / stolen vouchers will not be re-issued.</li>
          <li>If unused by <b className="mono">18-Apr-2027</b>, value lapses · no extension.</li>
          <li>Cashier will verify check-digits + customer phone OTP at redemption.</li>
        </ol>
      </div>

      <div style={{ padding: '8px 16px', display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 14, alignItems: 'center', borderTop: '1px dashed var(--ink-5)', position: 'relative' }}>
        {/* Serial barcode placeholder */}
        <div style={{ display: 'inline-block', textAlign: 'center' }}>
          <div style={{ display: 'flex', gap: 1, height: 26 }}>
            {Array.from({ length: 28 }).map((_, i) => (
              <div key={i} style={{ width: i % 3 === 0 ? 2 : 1, background: 'var(--ink)' }} />
            ))}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, letterSpacing: '.18em', marginTop: 2 }}>GV-2026-00488-7142</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={lblSm}>Issued by</div>
          <div style={{ fontSize: 11, marginTop: 2 }}>Sonia K. · EMP-0142</div>
          <div style={{ fontSize: 9, color: 'var(--ink-4)', marginTop: 1, fontStyle: 'italic' }}>(seal required at redemption)</div>
        </div>
      </div>

      <div style={{ padding: '6px 16px', fontSize: 8.5, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em', textAlign: 'center', borderTop: '1px solid var(--ink-4)', position: 'relative' }}>
        Form GV-01 · Tax payable on redemption per item HSN · valid only with this physical voucher
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   16. CUSTOMER LEDGER STATEMENT — A4 — customer · wallet + loyalty
   ═══════════════════════════════════════════════════════════════════════════ */
function TplLedger() {
  const txns = [
    { d: '01-Apr-26', ref: 'BV-OB',          typ: 'Opening',     desc: 'Brought forward from FY 25-26',                     dr: 0,     cr: 0,    bal: 420 },
    { d: '04-Apr-26', ref: 'BV/GK1/…248120', typ: 'Invoice',     desc: 'Annual eye exam + Acuvue trial pack',               dr: 1980,  cr: 0,    bal: 420 },
    { d: '04-Apr-26', ref: 'PAY-31204',      typ: 'Payment',     desc: 'UPI · GPay · txn 482911...',                        dr: 0,     cr: 1980, bal: 420 },
    { d: '11-Apr-26', ref: 'BV/GK1/…248904', typ: 'Invoice',     desc: 'Ray-Ban Aviator + Crizal lens (size 50)',           dr: 8950,  cr: 0,    bal: 420 },
    { d: '11-Apr-26', ref: 'PAY-31288',      typ: 'Payment',     desc: 'Card · HDFC ****4421',                              dr: 0,     cr: 8950, bal: 420 },
    { d: '19-Apr-26', ref: 'CN/…/0418-03',   typ: 'Credit note', desc: 'Exchange · frame size mismatch (BV-Wallet)',        dr: 0,     cr: 8950, bal: 9370 },
    { d: '19-Apr-26', ref: 'BV/GK1/…249220', typ: 'Invoice',     desc: 'Ray-Ban Wayfarer (exchange, size 52)',              dr: 8950,  cr: 0,    bal: 420 },
    { d: '19-Apr-26', ref: 'BV/GK1/…249183', typ: 'Invoice',     desc: 'Wayfarer + Varilux X + Acuvue + cloth',             dr: 28110, cr: 0,    bal: 420 },
    { d: '19-Apr-26', ref: 'PAY-31402',      typ: 'Payment',     desc: 'Card 20,000 + UPI 8,110',                           dr: 0,     cr: 28110, bal: 420 },
    { d: '19-Apr-26', ref: 'LOY-2611',       typ: 'Loyalty',     desc: '+281 points earned on invoice 249183',              dr: 0,     cr: 0,    bal: 420 },
  ];
  const drTot = txns.reduce((a, t) => a + t.dr, 0);
  const crTot = txns.reduce((a, t) => a + t.cr, 0);

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <LegalHeader
        docType="CUSTOMER STATEMENT OF ACCOUNT"
        docNo="STMT/CUS-00214/2026-04"
        copy="ORIGINAL FOR CUSTOMER"
        meta={[
          ['Statement period', '01-Apr-2026 → 30-Apr-2026'],
          ['Generated',        '19-Apr-2026 · 21:30 IST'],
          ['Statement type',   'Monthly · auto-emailed'],
          ['Currency',         'INR'],
          ['Loyalty tier',     'BV-Member · Silver'],
          ['Next tier',        'Gold · 580 pts to go'],
          ['Statement of',     'Ms. Aanya Sharma · CUS-00214'],
        ]}
        showBank={false}
      />

      {/* Customer block */}
      <PartyBlock blocks={[
        { h: 'Account holder', rows: [
          ['Name',        'Ms. Aanya Sharma'],
          ['Phone',       '+91 98115 22100'],
          ['Email',       'aanya.sharma@gmail.com'],
          ['Address',     'B-42, Panchsheel Park, New Delhi 110017'],
          ['Customer ID', 'CUS-00214 · since Aug 2022'],
          ['KYC',         'Phone OTP · last verified 19-Apr-2026'],
        ] },
        { h: 'Balances · as of 19-Apr', rows: [
          ['Outstanding (Dr.)', inr(0)],
          ['Wallet credit (Cr.)', inr(420)],
          ['Loyalty points',    '1,420 pts (≈ ₹ 355)'],
          ['Lifetime value',    inr(82460)],
          ['Visits · last 12m', '4 visits · 3 invoices'],
          ['Pending pickups',   '1 · JB-GK1-0418 (23-Apr)'],
        ] },
      ]}/>

      {/* Transactions ledger */}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr>
            <th style={{ ...tblHead, textAlign: 'left' }}>Date</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Ref. No.</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Type</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Description</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Debit (₹)</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Credit (₹)</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Wallet bal.</th>
          </tr>
        </thead>
        <tbody>
          {txns.map((t, i) => (
            <tr key={i}>
              <td style={{ ...tblCell, textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10 }}>{t.d}</td>
              <td style={{ ...tblCell, textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10 }}>{t.ref}</td>
              <td style={{ ...tblCell, textAlign: 'left', fontWeight: 500 }}>{t.typ}</td>
              <td style={{ ...tblCell, textAlign: 'left' }}>{t.desc}</td>
              <td style={tblNum}>{t.dr ? t.dr.toFixed(2) : '—'}</td>
              <td style={tblNum}>{t.cr ? t.cr.toFixed(2) : '—'}</td>
              <td style={{ ...tblNum, fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{inr(t.bal)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={4} style={{ ...tblHead, textAlign: 'right' }}>PERIOD TOTAL</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{drTot.toFixed(2)}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{crTot.toFixed(2)}</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontSize: 12 }}>{inr(420)}</td>
          </tr>
        </tfoot>
      </table>

      {/* Loyalty + wallet breakdowns */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Loyalty (BV-Points)</div>
          <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse', marginTop: 4 }}>
            <tbody>
              <tr><td style={kvK}>Opening</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>1,139</td></tr>
              <tr><td style={kvK}>Earned · period</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--ok)' }}>+ 281</td></tr>
              <tr><td style={kvK}>Redeemed</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>0</td></tr>
              <tr><td style={kvK}>Expired</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>0</td></tr>
              <tr><td style={{ ...kvK, fontWeight: 700, color: 'var(--ink)' }}>Closing</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 700 }}>1,420</td></tr>
            </tbody>
          </table>
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 6 }}>Earn rate: 1 pt / ₹100 spend · Redeem: ₹1 per 4 pts on next visit. Tier upgrades quarterly.</div>
        </div>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>BV-Wallet</div>
          <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse', marginTop: 4 }}>
            <tbody>
              <tr><td style={kvK}>Opening</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(420)}</td></tr>
              <tr><td style={kvK}>Credit notes</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--ok)' }}>+ {inr(8950)}</td></tr>
              <tr><td style={kvK}>Redemptions</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>− {inr(8950)}</td></tr>
              <tr><td style={kvK}>Top-ups</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(0)}</td></tr>
              <tr><td style={{ ...kvK, fontWeight: 700, color: 'var(--ink)' }}>Closing</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 700 }}>{inr(420)}</td></tr>
            </tbody>
          </table>
          <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 6 }}>Wallet balance never expires. Usable at all BV stores. Non-transferable.</div>
        </div>
        <div style={{ padding: '10px 16px' }}>
          <div style={lblSm}>Year-to-date · summary</div>
          <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse', marginTop: 4 }}>
            <tbody>
              <tr><td style={kvK}>Net spend YTD</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(43200)}</td></tr>
              <tr><td style={kvK}>Tax paid YTD</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(5184)}</td></tr>
              <tr><td style={kvK}>Avg basket</td><td style={{ ...kvV, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(14400)}</td></tr>
              <tr><td style={kvK}>NPS rating</td><td style={{ ...kvV, textAlign: 'right' }}>9 / 10 (Mar)</td></tr>
              <tr><td style={kvK}>Last visit</td><td style={{ ...kvV, textAlign: 'right' }}>19-Apr-2026</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <LegalFooter
        rule="Issued under SOP-CX-12 · Customer statements policy"
        amountWords={null}
        declaration="This statement reflects all transactions recorded against your account during the stated period. Please report any discrepancy within 30 days. Goods and services were taxed at the prevailing GST rates at the time of supply; CGST and SGST are remitted to the Government of India."
        signLabel="For Better Vision · Customer Service"
        showBank={false}
      />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   17. DEBIT NOTE — A4 — vendor-facing · Sec 34 CGST · against vendor invoice
   ═══════════════════════════════════════════════════════════════════════════ */
function TplDebitNote() {
  const lines = [
    { d: 'Acuvue Oasys 1-Day · 30-pack · −2.50', hsn: '9001', qty: 1, rate: 1485.00, gst: 12, reason: 'Short shipment' },
  ];
  const taxable = lines.reduce((a, r) => a + r.qty * r.rate, 0);
  const igst = lines.reduce((a, r) => a + r.qty * r.rate * r.gst / 100, 0);
  const total = Math.round(taxable + igst);

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <LegalHeader
        docType="DEBIT NOTE · Sec. 34 CGST Act 2017"
        docNo="DN/BV/2025-26/0418-04"
        copy="ORIGINAL FOR VENDOR · ☐ DUPLICATE FOR ACCOUNTS · ☐ TRIPLICATE FOR STORE"
        meta={[
          ['DN date',          '19-Apr-2026'],
          ['Against PO',       'PO/BV/2025-26/0042'],
          ['Against vendor inv.', 'JJ/24/04/2240 dated 17-Apr-2026'],
          ['Against GRN',      'GRN/BV/2025-26/0418-22'],
          ['Reason code',      'V-SHRT-01 · short shipment'],
          ['Supply type',      'Inter-state · IGST'],
          ['Settlement',       'Net off in next payment cycle'],
        ]}
        showBank={false}
      />

      <PartyBlock blocks={[
        { h: 'To · Vendor', rows: [
          ['Legal name', 'Johnson & Johnson India Pvt. Ltd.'],
          ['Address',    '4th Flr, Arena Space, Andheri (E), Mumbai 400059'],
          ['State / Code', 'Maharashtra / 27'],
          ['GSTIN',      '27AAACJ4863N1ZE'],
          ['PAN',        'AAACJ4863N'],
          ['Vendor code','V-0044'],
        ] },
        { h: 'From · Buyer (raising DN)', rows: [
          ['Legal name',  BV_LEGAL.legal],
          ['Address',     'Plot 12, Okhla Phase II, New Delhi 110020'],
          ['State / Code','Delhi / 07'],
          ['GSTIN',       BV_LEGAL.gstin],
          ['PAN',         BV_LEGAL.pan],
          ['Raised by',   'Sonia Khatri · SM · 19-Apr 09:48'],
        ] },
      ]}/>

      {/* Lines */}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr>
            <th style={{ ...tblHead, width: 26 }}>#</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Item being debited</th>
            <th style={tblHead}>HSN</th>
            <th style={tblHead}>Qty</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Rate (₹)</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Taxable</th>
            <th style={tblHead}>IGST</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>IGST amt</th>
            <th style={{ ...tblHead, textAlign: 'left' }}>Reason</th>
            <th style={{ ...tblHead, textAlign: 'right' }}>Total (₹)</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((r, i) => {
            const tax = r.qty * r.rate * r.gst / 100;
            return (
              <tr key={i}>
                <td style={tblCell}>{i + 1}</td>
                <td style={{ ...tblCell, textAlign: 'left' }}>{r.d}</td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r.hsn}</td>
                <td style={tblNum}>{r.qty}</td>
                <td style={tblNum}>{r.rate.toFixed(2)}</td>
                <td style={tblNum}>{(r.qty * r.rate).toFixed(2)}</td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{r.gst}%</td>
                <td style={tblNum}>{tax.toFixed(2)}</td>
                <td style={{ ...tblCell, textAlign: 'left', fontSize: 10, color: 'var(--err)', fontWeight: 600 }}>{r.reason}</td>
                <td style={{ ...tblNum, fontWeight: 600 }}>{(r.qty * r.rate + tax).toFixed(2)}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={5} style={{ ...tblHead, textAlign: 'right' }}>TOTAL DEBITED TO VENDOR</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{taxable.toFixed(2)}</td>
            <td style={tblHead}>—</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{igst.toFixed(2)}</td>
            <td style={tblHead}>—</td>
            <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontSize: 12 }}>{total.toFixed(2)}</td>
          </tr>
        </tfoot>
      </table>

      {/* Investigation + settlement */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Investigation summary</div>
          <ol style={{ margin: '4px 0 0 14px', padding: 0, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
            <li>PO <span className="mono">PO/BV/2025-26/0042</span> requested 24 boxes (6 × 4 powers); vendor invoice <span className="mono">JJ/24/04/2240</span> billed 24 boxes.</li>
            <li>GRN <span className="mono">GRN/BV/2025-26/0418-22</span> recorded 23 boxes received; carton 4-of-4 sealed at origin and marked "5 of 5" — vendor packing slip error.</li>
            <li>Root cause confirmed by vendor (Ramesh M., 19-Apr 14:20) — under-ship at packing stage. Replacement scheduled 22-Apr-2026.</li>
            <li>This debit note reverses tax originally claimed as Input Tax Credit on the short item; ITC reduced by ₹ 198 in GSTR-3B for April 2026.</li>
          </ol>
        </div>
        <div style={{ padding: '10px 16px', fontSize: 11 }}>
          <div style={lblSm}>Settlement</div>
          <table style={{ width: '100%', fontSize: 10.5, marginTop: 4, borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={kvK}>Mode</td><td style={{ ...kvV, textAlign: 'right' }}>Net off, next AP cycle</td></tr>
              <tr><td style={kvK}>Replacement ETA</td><td style={{ ...kvV, textAlign: 'right' }}>22-Apr-2026</td></tr>
              <tr><td style={kvK}>If replaced</td><td style={{ ...kvV, textAlign: 'right' }}>DN auto-reversed</td></tr>
              <tr><td style={kvK}>GSTR-1 reporting</td><td style={{ ...kvV, textAlign: 'right' }}>April 2026 return</td></tr>
            </tbody>
          </table>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 8, padding: '8px 0 0', borderTop: '2px solid var(--ink)' }}>
            <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.14em' }}>Net debit</span>
            <span style={{ fontSize: 18, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>− {inr(total)}</span>
          </div>
        </div>
      </div>

      <LegalFooter
        rule="Sec. 34 CGST Act 2017 r/w Rule 53 · DN reported in GSTR-1 of issuer's outward returns"
        amountWords={inWords(total) + ' debited to vendor'}
        declaration="We declare that this debit note relates to a short receipt against vendor invoice referenced above. The amount debited represents goods not received; output IGST originally claimed as input tax credit is correspondingly reversed in the buyer's GSTR-3B for the period."
        signLabel="For Better Vision · Procurement"
      />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   18. SHIFT HANDOVER SHEET — A4 — staff · cashier-to-cashier
   ═══════════════════════════════════════════════════════════════════════════ */
function TplShiftHandover() {
  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <StaffHeader
        docType="SHIFT HANDOVER · Form Ops-05"
        docNo="SH/2026/0419/M-E"
        copy="STORE COPY · COUNTERSIGNED"
        meta={[
          ['Date',     '19-Apr-26'],
          ['Closing',  'Morning shift'],
          ['Opening',  'Evening shift'],
          ['At',       '15:00 IST'],
        ]}
      />

      {/* Outgoing / Incoming staff */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Outgoing · Morning shift</div>
          <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse', marginTop: 4 }}>
            <tbody>
              <tr><td style={kvK}>Cashier</td><td style={{ ...kvV, fontWeight: 600 }}>Riya P. · EMP-0144</td></tr>
              <tr><td style={kvK}>Optometrist</td><td style={kvV}>Dr. R. Malhotra · DMC-4412</td></tr>
              <tr><td style={kvK}>Optician</td><td style={kvV}>Karan T. · EMP-0151</td></tr>
              <tr><td style={kvK}>Shift</td><td style={kvV}>10:00 → 15:00 IST · 5 h</td></tr>
              <tr><td style={kvK}>POS station</td><td style={kvV}>POS-01 (handed to incoming)</td></tr>
            </tbody>
          </table>
        </div>
        <div style={{ padding: '10px 16px' }}>
          <div style={lblSm}>Incoming · Evening shift</div>
          <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse', marginTop: 4 }}>
            <tbody>
              <tr><td style={kvK}>Cashier</td><td style={{ ...kvV, fontWeight: 600 }}>Sonia K. · EMP-0142 (SM)</td></tr>
              <tr><td style={kvK}>Optometrist</td><td style={kvV}>Dr. Anjali Vohra · DMC-5810</td></tr>
              <tr><td style={kvK}>Optician</td><td style={kvV}>Manish G. · EMP-0167</td></tr>
              <tr><td style={kvK}>Shift</td><td style={kvV}>15:00 → 21:14 IST · 6 h 14 m</td></tr>
              <tr><td style={kvK}>POS station</td><td style={kvV}>POS-01 (taking over)</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Cash drawer carried forward */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ borderRight: '1px solid var(--ink-4)' }}>
          <div style={{ padding: '6px 16px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', borderBottom: '1px solid var(--ink-4)' }}>
            Cash drawer at handover
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr>
                <th style={{ ...tblHead, textAlign: 'left' }}>Denom.</th>
                <th style={tblHead}>Pieces</th>
                <th style={{ ...tblHead, textAlign: 'right' }}>Value</th>
              </tr>
            </thead>
            <tbody>
              {[
                ['₹ 500', 4, 2000], ['₹ 200', 6, 1200], ['₹ 100', 9, 900],
                ['₹ 50',  8,  400], ['₹ 20', 12,  240], ['₹ 10', 10, 100],
                ['Coins', '—', 60],
              ].map((c, i) => (
                <tr key={i}>
                  <td style={{ ...tblCell, textAlign: 'left' }}>{c[0]}</td>
                  <td style={{ ...tblCell, fontFamily: 'var(--font-mono)' }}>{c[1]}</td>
                  <td style={tblNum}>{inr(c[2])}</td>
                </tr>
              ))}
              <tr>
                <td colSpan={2} style={{ ...tblHead, textAlign: 'right' }}>HANDED OVER</td>
                <td style={{ ...tblHead, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{inr(4900)}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div>
          <div style={{ padding: '6px 16px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', borderBottom: '1px solid var(--ink-4)' }}>
            Shift totals · morning
          </div>
          <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={{ ...kvK, padding: '5px 16px' }}>Tx count</td><td style={{ ...kvV, padding: '5px 16px', textAlign: 'right', fontFamily: 'var(--font-mono)' }}>22</td></tr>
              <tr><td style={{ ...kvK, padding: '5px 16px' }}>Gross sales</td><td style={{ ...kvV, padding: '5px 16px', textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(24180)}</td></tr>
              <tr><td style={{ ...kvK, padding: '5px 16px' }}>Cash tx</td><td style={{ ...kvV, padding: '5px 16px', textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(2900)}</td></tr>
              <tr><td style={{ ...kvK, padding: '5px 16px' }}>Card tx</td><td style={{ ...kvV, padding: '5px 16px', textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(14600)}</td></tr>
              <tr><td style={{ ...kvK, padding: '5px 16px' }}>UPI tx</td><td style={{ ...kvV, padding: '5px 16px', textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(6680)}</td></tr>
              <tr><td style={{ ...kvK, padding: '5px 16px' }}>Refunds</td><td style={{ ...kvV, padding: '5px 16px', textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{inr(0)}</td></tr>
              <tr style={{ background: 'var(--bg-sunk)' }}><td style={{ ...kvK, padding: '5px 16px', fontWeight: 700, color: 'var(--ink)' }}>Variance</td><td style={{ ...kvV, padding: '5px 16px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 700 }}>+ ₹ 0</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Operational status — open items handed over */}
      <div style={{ borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '6px 16px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', borderBottom: '1px solid var(--ink-4)' }}>
          Open items · being handed over to evening shift
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10.5 }}>
          <thead>
            <tr>
              <th style={{ ...tblHead, textAlign: 'left' }}>Type</th>
              <th style={{ ...tblHead, textAlign: 'left' }}>Reference</th>
              <th style={{ ...tblHead, textAlign: 'left' }}>Details</th>
              <th style={{ ...tblHead, textAlign: 'left' }}>Action by evening</th>
              <th style={tblHead}>Pri.</th>
            </tr>
          </thead>
          <tbody>
            {[
              ['Held cart',   'HLD-0031', 'Ananya Mehta · 3 items · ₹ 14,630 · awaiting Rx confirmation', 'Re-engage by 17:30 or release', 'P2'],
              ['Held cart',   'HLD-0030', 'Walk-in · 1 item · ₹ 1,980', 'Auto-release at 18:00', 'P3'],
              ['Job card',    'JB-0417',  'Lab confirmation pending · DTDC AWB lost', 'Chase lab by 16:00', 'P1'],
              ['Task · TSK-2209', '—',     'Job card JB-0417 escalation watch · 23 m to esc.', 'Resolve or document', 'P1'],
              ['Customer cb', '—',         'Rohan Iyer · CL trial pickup · expects 18:00', 'Call before 17:45', 'P2'],
              ['Inv. count',  'CC/0419/A1','Cycle count A-12 → A-18 · 42 of 78 done', 'Resume or pass to closing', 'P3'],
              ['Reorder',     'PO-D-0042', 'J&J approved · awaiting GRN replacement 22-Apr', 'Watch only', 'P3'],
            ].map((r, i) => (
              <tr key={i}>
                <td style={{ ...tblCell, textAlign: 'left', fontWeight: 600 }}>{r[0]}</td>
                <td style={{ ...tblCell, textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10 }}>{r[1]}</td>
                <td style={{ ...tblCell, textAlign: 'left', fontSize: 10 }}>{r[2]}</td>
                <td style={{ ...tblCell, textAlign: 'left', fontSize: 10, color: 'var(--ink-3)' }}>{r[3]}</td>
                <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontWeight: 600, color: r[4] === 'P1' ? 'var(--err)' : r[4] === 'P2' ? 'var(--warn)' : 'var(--ink-3)' }}>{r[4]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Notes + sign off */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Notes from outgoing shift</div>
          <ul style={{ margin: '4px 0 0 14px', padding: 0, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
            <li>POS-02 printer paper running low — replace before evening peak.</li>
            <li>Customer Aanya Sharma (CUS-00214) collecting <b>JB-GK1-0418</b> from 23-Apr; reminded by SMS already.</li>
            <li>Jarvis · Stock Sentinel auto-drafted PO-D-0043 for Crizal Prevencia 1.6 in CYL −0.25 row — held for SM review.</li>
            <li>A/C blower in chamber 2 needs servicing — log to facilities by EOD.</li>
            <li>No customer complaints this shift; one positive NPS (10/10).</li>
          </ul>
        </div>
        <div style={{ padding: '10px 16px' }}>
          <div style={lblSm}>Signatures</div>
          <div style={{ marginTop: 6 }}>
            <div style={{ height: 32, borderBottom: '0.5px solid var(--ink-4)' }} />
            <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 2, display: 'flex', justifyContent: 'space-between' }}>
              <span><b>Outgoing</b> · Riya P.</span>
              <span style={{ fontFamily: 'var(--font-mono)' }}>15:02 IST</span>
            </div>
          </div>
          <div style={{ marginTop: 12 }}>
            <div style={{ height: 32, borderBottom: '0.5px solid var(--ink-4)' }} />
            <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 2, display: 'flex', justifyContent: 'space-between' }}>
              <span><b>Incoming</b> · Sonia K.</span>
              <span style={{ fontFamily: 'var(--font-mono)' }}>15:04 IST</span>
            </div>
          </div>
          <div style={{ marginTop: 12 }}>
            <div style={{ height: 32, borderBottom: '0.5px solid var(--ink-4)' }} />
            <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 2 }}>ASM countersign · if cash variance &gt; ±₹50</div>
          </div>
        </div>
      </div>

      <div style={{ padding: '7px 16px', fontSize: 9, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em', textAlign: 'center' }}>
        SOP-OPS-05 · sheet retained at store for 90 days · escalations route to ASM auto-generated as TSK-Pn tasks
      </div>
    </div>
  );
}

Object.assign(window, {
  TplAppointment, TplWarranty, TplGiftVoucher, TplLedger, TplDebitNote, TplShiftHandover,
});
