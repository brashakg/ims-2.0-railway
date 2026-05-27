/* global React */
/* Shared statutory primitives — bordered, ALL-CAPS, sans-only, no editorial flourish.
   These are the building blocks used by all 12 redesigned templates. Visual language
   borrows from real Indian GST tax invoices / delivery challans: visible borders,
   tight K:V grids, declaration + signature footers, copy markers, statutory rule
   references inline. */

const BV_LEGAL = {
  legal: 'Better Vision Opticals Private Limited',
  trade: 'Better Vision',
  regAddr: 'Plot 12, Okhla Industrial Area Phase-II, New Delhi 110020',
  stateCode: '07',
  state: 'Delhi',
  storeAddr: 'Shop 14, M-Block Market, Greater Kailash-I, New Delhi 110048',
  phone: '+91 11 4135 2010',
  email: 'gk1@bettervision.in',
  web: 'bettervision.in',
  gstin: '07AABCB1234M1Z5',
  pan: 'AABCB1234M',
  cin: 'U33200DL2018PTC332100',
  drug: 'DL-OPT-GK1-2018-0421',
  msme: 'UDYAM-DL-08-0044721',
  bankName: 'HDFC Bank Ltd.',
  bankBranch: 'Greater Kailash-I, New Delhi',
  bankAc: '50200012442100',
  bankIfsc: 'HDFC0000142',
};

/* ─── 1. Document header band ───────────────────────────────────────
   ALL-CAPS document type top-strip, copy marker (Original/Duplicate/Triplicate),
   then supplier identity block + meta grid. Matches actual GST invoice layouts. */
function LegalHeader({ docType, docNo, copy = 'ORIGINAL FOR RECIPIENT', meta = [], showBank = false }) {
  return (
    <React.Fragment>
      {/* Top copy-marker bar — statutory per Rule 48 */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center',
        padding: '6px 18px', background: 'var(--ink)', color: '#fff',
        fontFamily: 'var(--font-sans)', fontSize: 10, letterSpacing: '.16em',
        fontWeight: 600, textTransform: 'uppercase',
      }}>
        <span>{docType}</span>
        <span style={{ opacity: .8, fontSize: 9.5 }}>{copy}</span>
      </div>

      {/* Supplier identity + doc meta */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', borderBottom: '1.5px solid var(--ink)' }}>
        <div style={{ padding: '12px 18px', borderRight: '1px solid var(--ink-4)' }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            <div style={{
              width: 34, height: 34, border: '1.5px solid var(--ink)', display: 'grid', placeItems: 'center',
              fontFamily: 'var(--font-sans)', fontSize: 18, fontWeight: 700, color: 'var(--ink)',
            }}>B</div>
            <div>
              <div style={{ fontSize: 13.5, fontWeight: 700, letterSpacing: '.01em', color: 'var(--ink)' }}>{BV_LEGAL.legal}</div>
              <div style={{ fontSize: 9.5, color: 'var(--ink-3)', textTransform: 'uppercase', letterSpacing: '.08em', marginTop: 1 }}>(Trade name: {BV_LEGAL.trade} · Est. 1987)</div>
            </div>
          </div>
          <table style={{ marginTop: 8, fontSize: 10.5, borderCollapse: 'collapse' }}>
            <tbody>
              <tr><td style={kvK}>Registered office</td><td style={kvV}>{BV_LEGAL.regAddr}</td></tr>
              <tr><td style={kvK}>Place of supply</td><td style={kvV}>{BV_LEGAL.storeAddr}</td></tr>
              <tr><td style={kvK}>Contact</td><td style={kvV}>{BV_LEGAL.phone} · {BV_LEGAL.email} · {BV_LEGAL.web}</td></tr>
              <tr><td style={kvK}>GSTIN / UIN</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{BV_LEGAL.gstin}</td></tr>
              <tr><td style={kvK}>State / Code</td><td style={kvV}>{BV_LEGAL.state} / <span className="mono">{BV_LEGAL.stateCode}</span></td></tr>
              <tr><td style={kvK}>PAN</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)' }}>{BV_LEGAL.pan}</td></tr>
              <tr><td style={kvK}>CIN</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)' }}>{BV_LEGAL.cin}</td></tr>
              <tr><td style={kvK}>Drug Licence</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)' }}>{BV_LEGAL.drug}</td></tr>
            </tbody>
          </table>
          {showBank && (
            <table style={{ marginTop: 6, fontSize: 10, borderCollapse: 'collapse', borderTop: '1px dashed var(--line-strong)', paddingTop: 6 }}>
              <tbody>
                <tr><td style={kvK}>Bank</td><td style={kvV}>{BV_LEGAL.bankName} · {BV_LEGAL.bankBranch}</td></tr>
                <tr><td style={kvK}>A/c · IFSC</td><td style={{ ...kvV, fontFamily: 'var(--font-mono)' }}>{BV_LEGAL.bankAc} · {BV_LEGAL.bankIfsc}</td></tr>
              </tbody>
            </table>
          )}
        </div>

        <div style={{ padding: '12px 18px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1px dashed var(--line-strong)', paddingBottom: 8, marginBottom: 8 }}>
            <div>
              <div style={lblSm}>{docType.split('·')[0].trim()} No.</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 700, letterSpacing: '.02em', marginTop: 2 }}>{docNo}</div>
            </div>
            <div>
              <div style={lblSm}>HSN / SAC range</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, fontWeight: 600, marginTop: 2 }}>9001 · 9003 · 9605</div>
            </div>
          </div>
          <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse' }}>
            <tbody>
              {meta.map(([k, v]) => (
                <tr key={k}>
                  <td style={kvK}>{k}</td>
                  <td style={{ ...kvV, textAlign: 'right' }}>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </React.Fragment>
  );
}

const kvK = { padding: '2px 8px 2px 0', fontSize: 9.5, color: 'var(--ink-3)', textTransform: 'uppercase', letterSpacing: '.08em', whiteSpace: 'nowrap', verticalAlign: 'top', lineHeight: 1.45 };
const kvV = { padding: '2px 0', fontSize: 10.5, color: 'var(--ink)', verticalAlign: 'top', lineHeight: 1.45 };
const lblSm = { fontSize: 9, color: 'var(--ink-3)', textTransform: 'uppercase', letterSpacing: '.1em', fontWeight: 500 };

/* ─── 1b. Staff header — minimal, internal docs only (job card, count sheet,
   labels, Z-report). Just BV logo, branch, doc title + number + meta strip.
   Looks operational, not statutory. ────────────────────────────────────────── */
function StaffHeader({ docType, docNo, copy = 'INTERNAL USE ONLY', meta = [] }) {
  return (
    <React.Fragment>
      {/* Slim doc-type strip — same family as LegalHeader but no statutory copy */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center',
        padding: '5px 16px', background: 'var(--ink)', color: '#fff',
        fontSize: 9.5, letterSpacing: '.16em', fontWeight: 600, textTransform: 'uppercase',
      }}>
        <span>{docType}</span>
        <span style={{ opacity: .7, fontSize: 9 }}>{copy}</span>
      </div>

      {/* Logo + branch · doc number · meta */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'auto 1fr auto',
        padding: '8px 16px', borderBottom: '1.5px solid var(--ink)',
        gap: 14, alignItems: 'center',
      }}>
        {/* Logo + branch only — no GSTIN / CIN / addresses */}
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <div style={{
            width: 30, height: 30, border: '1.5px solid var(--ink)',
            display: 'grid', placeItems: 'center', fontWeight: 700, fontSize: 16,
          }}>B</div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: '.01em' }}>{BV_LEGAL.trade}</div>
            <div style={{ fontSize: 9, color: 'var(--ink-3)', textTransform: 'uppercase', letterSpacing: '.1em', marginTop: 1 }}>GK-I Flagship · BV-DELHI-GK1</div>
          </div>
        </div>

        {/* Doc no */}
        <div style={{ textAlign: 'center' }}>
          <div style={{ ...lblSm, fontSize: 8.5 }}>Document No.</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700, marginTop: 1, letterSpacing: '.02em' }}>{docNo}</div>
        </div>

        {/* Meta — single row of chips */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {meta.map(([k, v]) => (
            <FieldChip key={k} k={k} v={v} mono={/No\.|Date|Time|ID|Code/.test(k)} />
          ))}
        </div>
      </div>
    </React.Fragment>
  );
}

/* ─── 2. Two-column "Bill to / Ship to" or "Consignor / Consignee" block ───── */
function PartyBlock({ blocks }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${blocks.length}, 1fr)`, borderBottom: '1.5px solid var(--ink)' }}>
      {blocks.map((b, i) => (
        <div key={i} style={{ padding: '10px 16px', borderRight: i === blocks.length - 1 ? 'none' : '1px solid var(--ink-4)' }}>
          <div style={lblSm}>{b.h}</div>
          <div style={{ marginTop: 4 }}>
            <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse' }}>
              <tbody>
                {b.rows.map(([k, v], j) => (
                  <tr key={j}>
                    <td style={kvK}>{k}</td>
                    <td style={{ ...kvV, fontFamily: /GSTIN|PAN|Code|No\./.test(k) ? 'var(--font-mono)' : 'var(--font-sans)', fontWeight: /Name|Customer/.test(k) ? 600 : 400 }}>{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

/* ─── 3. Statutory table with visible borders ──────────────────────────────── */
const tblHead = {
  padding: '7px 8px',
  fontSize: 9.5, fontWeight: 600, color: 'var(--ink)',
  textTransform: 'uppercase', letterSpacing: '.06em',
  background: 'var(--bg-sunk)',
  border: '1px solid var(--ink-4)',
  textAlign: 'center',
};
const tblCell = {
  padding: '7px 8px', fontSize: 11, color: 'var(--ink-2)',
  border: '1px solid var(--ink-5)', textAlign: 'center', verticalAlign: 'top',
};
const tblNum = { ...tblCell, textAlign: 'right', fontFamily: 'var(--font-sans)', fontVariantNumeric: 'tabular-nums', fontWeight: 500 };

/* ─── 4. Footer — declaration + signature + statutory text ─────────────────── */
function LegalFooter({ rule, declaration, signLabel = 'For ' + BV_LEGAL.legal, jurisdictionText = 'Subject to ' + BV_LEGAL.state + ' jurisdiction only', amountWords, showBank = true, leftExtra }) {
  return (
    <React.Fragment>
      {/* Amount in words + declaration */}
      {(amountWords || declaration || leftExtra) && (
        <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', borderBottom: '1px solid var(--ink-4)' }}>
          <div style={{ padding: '10px 18px', borderRight: '1px solid var(--ink-4)' }}>
            {amountWords && (
              <div style={{ marginBottom: declaration ? 10 : 0 }}>
                <div style={lblSm}>Amount chargeable (in words)</div>
                <div style={{ fontSize: 11.5, fontWeight: 600, marginTop: 3, lineHeight: 1.45 }}>{amountWords}</div>
              </div>
            )}
            {declaration && (
              <div>
                <div style={lblSm}>Declaration</div>
                <div style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 3, lineHeight: 1.55 }}>{declaration}</div>
              </div>
            )}
            {leftExtra}
          </div>

          <div style={{ padding: '10px 18px' }}>
            {showBank && (
              <div style={{ marginBottom: 10 }}>
                <div style={lblSm}>Bank details · for NEFT / RTGS</div>
                <table style={{ width: '100%', fontSize: 10, borderCollapse: 'collapse', marginTop: 3 }}>
                  <tbody>
                    <tr><td style={kvK}>A/c name</td><td style={{ ...kvV, fontSize: 10 }}>{BV_LEGAL.legal}</td></tr>
                    <tr><td style={kvK}>Bank</td><td style={{ ...kvV, fontSize: 10 }}>{BV_LEGAL.bankName}</td></tr>
                    <tr><td style={kvK}>A/c · IFSC</td><td style={{ ...kvV, fontSize: 10, fontFamily: 'var(--font-mono)' }}>{BV_LEGAL.bankAc} · {BV_LEGAL.bankIfsc}</td></tr>
                  </tbody>
                </table>
              </div>
            )}
            <div>
              <div style={lblSm}>{signLabel}</div>
              <div style={{ height: 42, marginTop: 4, marginBottom: 2, borderBottom: '0.5px solid var(--ink-4)' }} />
              <div style={{ fontSize: 10, color: 'var(--ink-3)', display: 'flex', justifyContent: 'space-between' }}>
                <span>Authorised signatory</span>
                <span style={{ color: 'var(--ink-4)' }}>[Seal]</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Statutory footnote line */}
      <div style={{
        padding: '7px 18px', fontSize: 9, color: 'var(--ink-4)',
        textTransform: 'uppercase', letterSpacing: '.08em', textAlign: 'center',
      }}>
        {rule && <span>{rule} · </span>}
        E. & O. E. · {jurisdictionText} · system-generated · retain for 7 years per CGST Rule 56
      </div>
    </React.Fragment>
  );
}

/* ─── 5. Small status pill (e.g. "PAID", "PENDING") — bordered, no fill ───── */
function StatusStamp({ children, tone = 'ok' }) {
  const color = tone === 'ok' ? 'var(--ok)' : tone === 'err' ? 'var(--err)' : tone === 'warn' ? 'var(--warn)' : 'var(--ink)';
  return (
    <span style={{
      display: 'inline-block', padding: '4px 10px',
      border: `1.5px solid ${color}`, color, fontWeight: 700,
      fontSize: 10.5, letterSpacing: '.14em', textTransform: 'uppercase',
      fontFamily: 'var(--font-sans)',
    }}>{children}</span>
  );
}

/* ─── 6. Amount-in-words (Indian numbering — Crore/Lakh) ───────────────────── */
function inWords(num) {
  if (num === 0) return 'Zero';
  const a = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen'];
  const b = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety'];
  const two = (n) => n < 20 ? a[n] : b[Math.floor(n / 10)] + (n % 10 ? '-' + a[n % 10] : '');
  const three = (n) => {
    if (n === 0) return '';
    if (n < 100) return two(n);
    return a[Math.floor(n / 100)] + ' Hundred' + (n % 100 ? ' ' + two(n % 100) : '');
  };

  const rupees = Math.floor(num);
  const paise = Math.round((num - rupees) * 100);

  const cr = Math.floor(rupees / 10000000);
  const lakh = Math.floor((rupees % 10000000) / 100000);
  const thou = Math.floor((rupees % 100000) / 1000);
  const rest = rupees % 1000;

  let out = '';
  if (cr) out += two(cr) + ' Crore ';
  if (lakh) out += two(lakh) + ' Lakh ';
  if (thou) out += two(thou) + ' Thousand ';
  if (rest) out += three(rest);
  out = 'Indian Rupees ' + (out.trim() || 'Zero');
  if (paise) out += ' and ' + two(paise) + ' Paise';
  out += ' Only';
  return out;
}

/* ─── 7. Formatters ──────────────────────────────────────────────────────── */
const inr  = (n) => '₹' + Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const inrI = (n) => '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });

/* ─── 8. Compact bordered chip used for "REVERSE CHARGE: NO" badges ──────── */
function FieldChip({ k, v, mono = false }) {
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'baseline', gap: 6,
      padding: '3px 8px', border: '1px solid var(--ink-4)',
      fontSize: 9.5,
    }}>
      <span style={{ color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em' }}>{k}</span>
      <span style={{ fontWeight: 600, fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)' }}>{v}</span>
    </div>
  );
}

Object.assign(window, {
  BV_LEGAL, LegalHeader, StaffHeader, PartyBlock, LegalFooter, StatusStamp, FieldChip,
  inWords, inr, inrI,
  kvK, kvV, lblSm, tblHead, tblCell, tblNum,
});
