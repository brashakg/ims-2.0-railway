/* global React, BV_LEGAL, LegalHeader, PartyBlock, LegalFooter, FieldChip,
   inWords, inr, inrI, kvK, kvV, lblSm, tblHead, tblCell, tblNum */
/* Customer Delivery Handover — issued when customer collects finished
   spectacles / contact lenses. Closes the loop: Job Card → QC Sticker →
   Handover. Acts as proof of delivery and dispensing record. */

/* ═══════════════════════════════════════════════════════════════════════════
   27. CUSTOMER DELIVERY HANDOVER — A4 — customer · proof of delivery
   ═══════════════════════════════════════════════════════════════════════════ */
function TplHandover() {
  const checks = [
    { ok: true,  k: 'Frame fitted & adjusted',           v: 'Pantoscopic tilt 8°, face-form 5°, temple bend symmetric' },
    { ok: true,  k: 'Lens optical centre to PD',         v: 'OD 32.0 / OS 31.5 mm · within ±0.5 mm tolerance' },
    { ok: true,  k: 'Fitting height verified',           v: '18 mm both eyes · matches Rx card' },
    { ok: true,  k: 'Power confirmed (lensometer)',      v: 'OD −2.50/−0.75x175 +1.25 ADD · OS −3.00/−0.50x5 +1.25 ADD · ±0.06 D' },
    { ok: true,  k: 'Coating inspection',                v: 'Crizal Alizé UV · no peel, no scratches, AR uniform' },
    { ok: true,  k: 'Frame integrity',                   v: 'Both hinges firm · no screw play · nose pads aligned' },
    { ok: true,  k: 'Vision test on customer',           v: 'Reading card 6/6 OU · near 0.4 M · no double vision' },
    { ok: true,  k: 'Comfort confirmation',              v: 'Customer wore 5 minutes · reports no pressure points' },
    { ok: false, k: 'Adaptation counsel given',          v: '7-day adaptation expected for progressive · come back if persistent strain' },
  ];
  const allDone = checks.every(c => c.ok);

  return (
    <div className="paper a4" data-doc style={{ color: 'var(--ink)' }}>
      <LegalHeader
        docType="DELIVERY HANDOVER · proof of receipt &amp; fitting"
        docNo="DH/BV/2025-26/0423-11"
        copy="ORIGINAL FOR CUSTOMER · ☐ DUPLICATE FOR STORE"
        meta={[
          ['Handover date',  '23-Apr-2026 · 18:14 IST'],
          ['Against invoice','BV/GK1/2025-26/249183 (paid in full)'],
          ['Linked job',     'JB-GK1-0418 (Essilor Pune)'],
          ['QC stamp',       'QC-22041408-RP-OK (passed 22-Apr 14:08)'],
          ['Promised by',    '23-Apr · 18:00 — delivered 14 m late'],
          ['Dispensed by',   'Karan T. · Optician · EMP-0151'],
          ['Witnessed by',   'Sonia K. · SM'],
        ]}
        showBank={false}
      />

      <PartyBlock blocks={[
        { h: 'Customer · receiving party', rows: [
          ['Name',       'Ms. Aanya Sharma'],
          ['Phone',      '+91 98115 22100'],
          ['Customer No.','CUS-00214 · loyalty Silver'],
          ['ID verified', 'Aadhaar · last 4 digits **2100'],
          ['Mode',       'In-person · accompanied'],
          ['Rx ref.',    'RX/GK1/2026/4418 (Dr. R. Malhotra · DMC-4412)'],
        ] },
        { h: 'Order context', rows: [
          ['Ordered on', '19-Apr-2026 · POS-01'],
          ['Invoice val.','₹ 28,110 · balance ₹ 0'],
          ['Warranty',   'Frame 12m · Lens 6m · Card WX/.../249183'],
          ['Lifetime',   'Free fit / adjust at any BV store'],
          ['Next visit', 'Suggested review: Apr-2027'],
          ['NPS',        'Will be requested · SMS at 19:30'],
        ] },
      ]}/>

      {/* Items delivered */}
      <div style={{ borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '6px 18px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', borderBottom: '1px solid var(--ink-4)' }}>
          Items physically handed over
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr>
              <th style={{ ...tblHead, width: 26 }}>#</th>
              <th style={{ ...tblHead, textAlign: 'left' }}>Item</th>
              <th style={{ ...tblHead, textAlign: 'left' }}>SKU</th>
              <th style={{ ...tblHead, textAlign: 'left' }}>Serial / batch</th>
              <th style={tblHead}>Qty</th>
              <th style={{ ...tblHead, textAlign: 'left' }}>Accompanying</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td style={tblCell}>1</td>
              <td style={{ ...tblCell, textAlign: 'left' }}>
                <div style={{ fontWeight: 600 }}>Ray-Ban RB2140 Wayfarer Classic</div>
                <div style={{ fontSize: 10, color: 'var(--ink-4)', marginTop: 1 }}>Black · 50-22-150 · acetate · with Varilux X 1.60 lens edged</div>
              </td>
              <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontSize: 10, textAlign: 'left' }}>FRM-RB-2140-BLK-50</td>
              <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontSize: 10, textAlign: 'left' }}>RB-260418-04</td>
              <td style={tblNum}>1</td>
              <td style={{ ...tblCell, textAlign: 'left', fontSize: 10 }}>Original box · cleaning cloth · screwdriver</td>
            </tr>
            <tr>
              <td style={tblCell}>2</td>
              <td style={{ ...tblCell, textAlign: 'left' }}>
                <div style={{ fontWeight: 600 }}>Acuvue Oasys 1-Day · 30-pack</div>
                <div style={{ fontSize: 10, color: 'var(--ink-4)', marginTop: 1 }}>−2.50 (1 box) + −3.00 (1 box) · 2 of 2 delivered</div>
              </td>
              <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontSize: 10, textAlign: 'left' }}>BV-AC-OAS-{'{250,300}'}</td>
              <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontSize: 10, textAlign: 'left' }}>AO-2604-2500 / 3000</td>
              <td style={tblNum}>2</td>
              <td style={{ ...tblCell, textAlign: 'left', fontSize: 10 }}>Carrying case · solution sample</td>
            </tr>
            <tr>
              <td style={tblCell}>3</td>
              <td style={{ ...tblCell, textAlign: 'left' }}>
                <div style={{ fontWeight: 600 }}>BV Microfibre cloth · L</div>
                <div style={{ fontSize: 10, color: 'var(--ink-4)', marginTop: 1 }}>210 × 210 mm · grey · 2 pcs</div>
              </td>
              <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontSize: 10, textAlign: 'left' }}>BV-CN-MICRO</td>
              <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontSize: 10, textAlign: 'left' }}>—</td>
              <td style={tblNum}>2</td>
              <td style={{ ...tblCell, textAlign: 'left', fontSize: 10 }}>—</td>
            </tr>
            <tr>
              <td style={tblCell}>4</td>
              <td style={{ ...tblCell, textAlign: 'left' }}>
                <div style={{ fontWeight: 600 }}>Warranty card</div>
                <div style={{ fontSize: 10, color: 'var(--ink-4)', marginTop: 1 }}>Form WX-01 · 12m frame + 6m lens cover</div>
              </td>
              <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontSize: 10, textAlign: 'left' }}>—</td>
              <td style={{ ...tblCell, fontFamily: 'var(--font-mono)', fontSize: 10, textAlign: 'left' }}>WX/GK1/.../249183</td>
              <td style={tblNum}>1</td>
              <td style={{ ...tblCell, textAlign: 'left', fontSize: 10 }}>Issued at handover</td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Fitting + dispensing checklist */}
      <div style={{ borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '6px 18px', background: 'var(--bg-sunk)', fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', borderBottom: '1px solid var(--ink-4)', display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center' }}>
          <span>Dispensing checks performed at handover · SOP-DSP-01</span>
          <span style={{ color: allDone ? 'var(--ok)' : 'var(--warn)', fontSize: 10 }}>{allDone ? '● All checks passed' : '○ 1 advisory'}</span>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <tbody>
            {checks.map((c, i) => (
              <tr key={i} style={{ borderBottom: '1px solid var(--line-soft)' }}>
                <td style={{ padding: '7px 12px', textAlign: 'center', width: 32, fontSize: 16, fontWeight: 700, color: c.ok ? 'var(--ok)' : 'var(--warn)' }}>
                  {c.ok ? '☑' : '○'}
                </td>
                <td style={{ padding: '7px 8px', fontWeight: 600, width: 220 }}>{c.k}</td>
                <td style={{ padding: '7px 12px', color: 'var(--ink-3)', fontSize: 10.5 }}>{c.v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Care + service */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '10px 16px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Care instructions given verbally</div>
          <ol style={{ margin: '4px 0 0 16px', padding: 0, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
            <li>Clean lens daily with the provided microfibre cloth and BV cleaning spray (avoid tissue / shirt).</li>
            <li>Hold the frame at both temples when putting on / removing — single-hand pulls bend the bridge.</li>
            <li>Store in the box when not worn; never face-down on a surface.</li>
            <li>Avoid extreme heat (car dashboards) — coating may craze.</li>
            <li>For contact lenses: wash hands · single use only · do not sleep with CLs in.</li>
          </ol>
        </div>
        <div style={{ padding: '10px 16px' }}>
          <div style={lblSm}>Free services included · lifetime</div>
          <ul style={{ margin: '4px 0 0 16px', padding: 0, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.55 }}>
            <li>Frame adjustment / re-fitting at any BV store, India.</li>
            <li>Ultrasonic cleaning · monthly walk-in.</li>
            <li>Nose-pad replacement · 1 free per year.</li>
            <li>30-day adaptation guarantee · we'll re-cut lens if adaptation fails despite counsel.</li>
            <li>WhatsApp follow-up at <b className="mono">{BV_LEGAL.phone}</b> for any concern.</li>
          </ul>
        </div>
      </div>

      {/* Adaptation watch + next steps */}
      <div style={{ padding: '10px 18px', borderBottom: '1.5px solid var(--ink)', background: 'var(--warn-50)' }}>
        <div style={{ fontSize: 9.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.1em', color: 'var(--warn)', marginBottom: 4 }}>Adaptation watch · first-time progressive wearer</div>
        <div style={{ fontSize: 11, color: 'var(--ink-2)', lineHeight: 1.55 }}>
          Customer is new to progressive lenses. Typical adaptation window 7–10 days. Eye-strain or balance discomfort during this period is normal; please call <b className="mono">{BV_LEGAL.phone}</b> if persistent beyond 10 days. We will re-verify Rx and refit at no charge per the 30-day adaptation guarantee. Auto-callback scheduled by Jarvis on <b className="mono">30-Apr-2026</b>.
        </div>
      </div>

      {/* Receipt acknowledgement */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderBottom: '1px solid var(--ink-4)' }}>
        <div style={{ padding: '12px 18px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={lblSm}>Customer acknowledgement</div>
          <div style={{ fontSize: 11, color: 'var(--ink-2)', lineHeight: 1.55, marginTop: 4 }}>
            I confirm that I have physically received the items listed above on the date stated, that the dispensing checks have been performed in my presence, and that the product feels comfortable and clear. I understand the warranty terms and the 30-day adaptation guarantee.
          </div>
          <div style={{ marginTop: 14 }}>
            <div style={{ height: 36, borderBottom: '0.5px solid var(--ink-4)', width: 260 }} />
            <div style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 3, display: 'flex', justifyContent: 'space-between', width: 260 }}>
              <span>Customer signature</span>
              <span style={{ fontFamily: 'var(--font-mono)' }}>23-Apr · 18:18</span>
            </div>
          </div>
        </div>

        <div style={{ padding: '12px 18px', display: 'grid', gridTemplateColumns: '1fr', gap: 14 }}>
          <div>
            <div style={lblSm}>Dispensed by</div>
            <div style={{ height: 26, marginTop: 3, borderBottom: '0.5px solid var(--ink-4)' }} />
            <div style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 3, display: 'flex', justifyContent: 'space-between' }}>
              <span>Karan T. · Optician · EMP-0151</span>
              <span style={{ color: 'var(--ink-4)' }}>[Seal]</span>
            </div>
          </div>
          <div>
            <div style={lblSm}>Witnessed by</div>
            <div style={{ height: 26, marginTop: 3, borderBottom: '0.5px solid var(--ink-4)' }} />
            <div style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 3 }}>Sonia K. · Store Manager</div>
          </div>
        </div>
      </div>

      <div style={{ padding: '7px 18px', fontSize: 9, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em', textAlign: 'center' }}>
        SOP-DSP-01 · handover closes the order loop · retained 7 years per CGST Rule 56 · customer keeps original copy
      </div>
    </div>
  );
}

Object.assign(window, { TplHandover });
