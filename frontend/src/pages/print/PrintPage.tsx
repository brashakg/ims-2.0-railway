// ============================================================================
// IMS 2.0 — Print templates index
// ============================================================================
// Design: docs/design/print.html (3-col: template list / preview / inspector).
//
// Scope for Phase 1.8: a single index page that surfaces every printable
// document in the app. The actual print output is produced by the existing
// print components (POSReceipt, EyeTestTokenPrint, DayEndReport, GRN / PO
// print templates, etc.) — this page is the discovery surface, not a
// competing renderer.
//
// Each template card either:
//  - deep-links to the page where that print is triggered (e.g. a POS
//    receipt is printed from inside a completed sale), OR
//  - window.prints a self-contained sample render (for templates that
//    need an independent preview path).
//
// Pixel-perfect inspector + live template editing is Phase 2 work.

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Printer, FileText, Eye, Ticket, Package, Receipt, RotateCcw, ExternalLink } from 'lucide-react';

type TemplateKind = 'paper' | 'thermal';
type PaperSize = 'A4' | 'A5' | 'A6' | '80mm';

interface PrintTemplate {
  id: string;
  name: string;
  sub: string;
  size: PaperSize;
  kind: TemplateKind;
  icon: typeof FileText;
  meta: { lastEdited: string; usage30d: number };
  fields: Array<[string, string]>;
  /** Where the real print is triggered today. null = no live implementation yet. */
  trigger:
    | { type: 'route'; to: string; label: string }
    | { type: 'inline'; label: string; helper: string }
    | null;
}

const TEMPLATES: PrintTemplate[] = [
  {
    id: 'invoice',
    name: 'Tax invoice',
    sub: 'A4 · GST-compliant customer copy',
    size: 'A4',
    kind: 'paper',
    icon: FileText,
    meta: { lastEdited: '12 Apr 2026', usage30d: 1284 },
    fields: [
      ['invoice.number', 'system'],
      ['cust.*', 'POS step 1'],
      ['lines[]', 'POS step 3'],
      ['tax.cgst / sgst', 'GST engine'],
      ['doctor.rx', 'Clinical'],
      ['sign.signatory', 'store config'],
    ],
    trigger: { type: 'route', to: '/pos', label: 'Open POS → complete a sale → Print from Step 6' },
  },
  {
    id: 'rx',
    name: 'Prescription card',
    sub: 'A5 · customer keepsake, printable from Clinical',
    size: 'A5',
    kind: 'paper',
    icon: Eye,
    meta: { lastEdited: '12 Apr 2026', usage30d: 312 },
    fields: [
      ['patient.*', 'Clinical intake'],
      ['rx.{sph, cyl, axis, add}', 'refraction'],
      ['findings', 'clinical notes'],
      ['doctor.signature', 'e-sign'],
    ],
    trigger: { type: 'route', to: '/clinical', label: 'Open Clinical → pick a completed test → Print Rx' },
  },
  {
    id: 'job',
    name: 'Lens job card',
    sub: 'A5 · lab dispatch (auto-generated on Rx orders)',
    size: 'A5',
    kind: 'paper',
    icon: Package,
    meta: { lastEdited: '03 Apr 2026', usage30d: 218 },
    fields: [
      ['job.priority', 'POS queue'],
      ['rx.*', 'Clinical'],
      ['frame.sku', 'POS step 3'],
      ['lens.spec', 'optician'],
      ['checklist.steps[]', 'workflow'],
    ],
    trigger: { type: 'route', to: '/workshop', label: 'Open Workshop → select a job → Print job card' },
  },
  {
    id: 'token',
    name: 'Queue token',
    sub: '80mm thermal · eye-test check-in',
    size: '80mm',
    kind: 'thermal',
    icon: Ticket,
    meta: { lastEdited: '28 Mar 2026', usage30d: 942 },
    fields: [
      ['token.number', 'queue engine'],
      ['chamber', 'rota'],
      ['eta', 'predictor'],
    ],
    trigger: { type: 'route', to: '/clinical', label: 'Open Clinical → add to queue → Print token' },
  },
  {
    id: 'challan',
    name: 'Delivery challan',
    sub: 'A4 · inter-store transfer',
    size: 'A4',
    kind: 'paper',
    icon: Receipt,
    meta: { lastEdited: '20 Feb 2026', usage30d: 24 },
    fields: [
      ['dispatch.consignor / consignee', 'warehouse'],
      ['lines[]', 'pick list'],
      ['eway.number', 'GST portal'],
      ['transport.vehicle', 'logistics'],
    ],
    trigger: { type: 'route', to: '/inventory/replenishment', label: 'Open Inventory → Transfers → Print challan' },
  },
  {
    id: 'credit',
    name: 'Credit note',
    sub: 'A5 · refund / exchange receipt',
    size: 'A5',
    kind: 'paper',
    icon: RotateCcw,
    meta: { lastEdited: '14 Mar 2026', usage30d: 18 },
    fields: [
      ['cn.againstInvoice', 'POS lookup'],
      ['reason', 'SOP-CX-04'],
      ['approver', 'manager PIN'],
      ['wallet.credit', 'ledger'],
    ],
    trigger: { type: 'route', to: '/returns', label: 'Open Returns → approve a refund → Print credit note' },
  },
  {
    id: 'dayend',
    name: 'Day-end shift close',
    sub: 'A4 · reconciliation + tender split',
    size: 'A4',
    kind: 'paper',
    icon: Receipt,
    meta: { lastEdited: '08 Apr 2026', usage30d: 186 },
    fields: [
      ['shift.openingBalance', 'cash drawer'],
      ['tender.breakdown', 'POS'],
      ['variance', 'counted vs expected'],
      ['signatory', 'closing cashier'],
    ],
    trigger: { type: 'route', to: '/reports/day-end', label: 'Open Day-End report → Print' },
  },
];

export default function PrintPage() {
  const navigate = useNavigate();
  const [selId, setSelId] = useState(TEMPLATES[0].id);
  const sel = TEMPLATES.find((t) => t.id === selId) ?? TEMPLATES[0];
  const SelIcon = sel.icon;

  return (
    <div
      className="pr-body"
      style={{
        display: 'grid',
        gridTemplateColumns: '240px 1fr 320px',
        height: 'calc(100vh - 52px)',
        minHeight: 0,
      }}
    >
      {/* ── Left: template list ── */}
      <nav
        style={{
          background: 'var(--surface)',
          borderRight: '1px solid var(--line)',
          padding: '20px 12px',
          overflowY: 'auto',
        }}
      >
        <span className="eyebrow" style={{ padding: '0 10px 8px', display: 'block' }}>
          Documents · {TEMPLATES.length} active
        </span>
        {TEMPLATES.map((t) => {
          const Icon = t.icon;
          const on = t.id === selId;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setSelId(t.id)}
              style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: 10,
                borderRadius: 8,
                marginBottom: 2,
                border: '1px solid transparent',
                background: on ? 'var(--ink)' : 'transparent',
                color: on ? '#fff' : 'var(--ink)',
                cursor: 'pointer',
                textAlign: 'left',
                transition: 'background .1s',
              }}
              onMouseOver={(e) => {
                if (!on) e.currentTarget.style.background = 'var(--bg-sunk)';
              }}
              onMouseOut={(e) => {
                if (!on) e.currentTarget.style.background = 'transparent';
              }}
            >
              <div
                style={{
                  width: 32,
                  height: t.kind === 'thermal' ? 44 : 42,
                  borderRadius: 3,
                  background: on ? '#1a1a19' : '#fff',
                  border: `1px solid ${on ? '#3a3a37' : 'var(--line-strong)'}`,
                  flexShrink: 0,
                  display: 'grid',
                  placeItems: 'center',
                  color: on ? 'rgba(255,255,255,.4)' : 'var(--ink-5)',
                }}
              >
                <Icon className="w-3.5 h-3.5" />
              </div>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.2 }}>{t.name}</div>
                <div
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 10.5,
                    color: on ? 'rgba(255,255,255,.55)' : 'var(--ink-4)',
                    marginTop: 2,
                    textTransform: 'uppercase',
                    letterSpacing: '.08em',
                  }}
                >
                  {t.size}
                </div>
              </div>
              <span
                style={{
                  marginLeft: 'auto',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 9.5,
                  fontWeight: 500,
                  color: on ? 'rgba(255,255,255,.55)' : 'var(--ink-4)',
                  border: `1px solid ${on ? '#3a3a37' : 'var(--line)'}`,
                  padding: '3px 5px',
                  borderRadius: 3,
                  textTransform: 'uppercase',
                  letterSpacing: '.06em',
                }}
              >
                {t.kind}
              </span>
            </button>
          );
        })}
      </nav>

      {/* ── Center: preview stage ── */}
      <div
        style={{
          overflow: 'auto',
          padding: 40,
          background: 'var(--bg)',
          display: 'grid',
          placeItems: 'center',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 28 }}>
          {/* Placeholder paper preview — actual per-template render lives
              with the feature that triggers the print. */}
          <div
            style={{
              background: '#fff',
              boxShadow: '0 30px 60px -20px rgba(0,0,0,.22), 0 6px 20px -10px rgba(0,0,0,.12)',
              width: sel.size === 'A4' ? 794 : sel.size === 'A5' ? 559 : sel.size === 'A6' ? 420 : 302,
              minHeight: sel.size === 'A4' ? 1123 : sel.size === 'A5' ? 794 : sel.size === 'A6' ? 594 : 460,
              padding: 48,
              color: 'var(--ink)',
              fontFamily: 'var(--font-sans)',
              fontSize: 12,
              position: 'relative',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24, paddingBottom: 16, borderBottom: '1px solid var(--ink)' }}>
              <div>
                <div style={{ fontFamily: 'var(--font-display)', fontSize: 28, color: 'var(--bv)' }}>B</div>
                <div style={{ fontFamily: 'var(--font-display)', fontSize: 24, letterSpacing: '-.01em' }}>{sel.name}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-4)', marginTop: 4 }}>{sel.sub.toUpperCase()}</div>
              </div>
              <div style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--ink-3)' }}>
                <div>Better Vision Opticals</div>
                <div>GSTIN: 07AABCB1234M1Z5</div>
                <div>tpl.{sel.id}.v3</div>
              </div>
            </div>

            <div style={{ display: 'grid', gap: 16, color: 'var(--ink-3)', lineHeight: 1.6 }}>
              <p style={{ margin: 0, fontSize: 13 }}>
                This is a placeholder preview of the <strong style={{ color: 'var(--ink)' }}>{sel.name}</strong>. The production render uses the live data from the point where this document is triggered — see the <em>Trigger</em> section in the inspector on the right.
              </p>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-4)', background: 'var(--bg-sunk)', padding: 14, borderRadius: 6 }}>
                {sel.fields.map(([k]) => `{{ ${k} }}`).join('\n')}
              </div>
              <div style={{ color: 'var(--ink-5)', fontSize: 11 }}>
                To print for real: {sel.trigger?.label ?? 'Template not yet wired to a live source'}.
              </div>
            </div>
          </div>

          <div
            style={{
              display: 'flex',
              gap: 10,
              alignItems: 'center',
              font: '500 11px/1 var(--font-mono)',
              color: 'var(--ink-4)',
              textTransform: 'uppercase',
              letterSpacing: '.12em',
            }}
          >
            <span>{sel.name}</span>
            <span style={{ color: 'var(--ink-3)' }}>·</span>
            <span style={{ color: 'var(--ink-3)' }}>
              {sel.size} {sel.size === 'A4' ? '· 210 × 297 mm' : sel.size === 'A5' ? '· 148 × 210 mm' : sel.size === '80mm' ? '· 80 × ~120 mm' : ''}
            </span>
            <span style={{ color: 'var(--ink-3)' }}>·</span>
            <span style={{ color: 'var(--ink-3)' }}>preview at 1.0×</span>
          </div>
        </div>
      </div>

      {/* ── Right: inspector ── */}
      <aside
        style={{
          background: 'var(--surface)',
          borderLeft: '1px solid var(--line)',
          padding: 20,
          overflowY: 'auto',
        }}
      >
        <h3 style={{ margin: 0, font: '600 14px/1.2 var(--font-sans)', color: 'var(--ink)', display: 'flex', alignItems: 'center', gap: 8 }}>
          <SelIcon className="w-4 h-4" /> {sel.name}
        </h3>
        <div style={{ fontSize: 12, color: 'var(--ink-4)', margin: '4px 0 18px', lineHeight: 1.5 }}>
          {sel.sub}. Print-ready, GST-compliant where applicable, renders identically on any Chromium browser.
        </div>

        {/* Meta */}
        <div style={{ marginBottom: 22 }}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>Document meta</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '6px 14px', fontSize: 11.5 }}>
            <span style={{ color: 'var(--ink-4)' }}>Template ID</span>
            <span className="mono" style={{ color: 'var(--ink)' }}>tpl.{sel.id}.v3</span>
            <span style={{ color: 'var(--ink-4)' }}>Size</span>
            <span className="mono" style={{ color: 'var(--ink)' }}>{sel.size}</span>
            <span style={{ color: 'var(--ink-4)' }}>Kind</span>
            <span className="mono" style={{ color: 'var(--ink)' }}>{sel.kind}</span>
            <span style={{ color: 'var(--ink-4)' }}>Last edited</span>
            <span className="mono" style={{ color: 'var(--ink)' }}>{sel.meta.lastEdited}</span>
            <span style={{ color: 'var(--ink-4)' }}>Used · 30d</span>
            <span className="mono" style={{ color: 'var(--ink)' }}>{sel.meta.usage30d.toLocaleString('en-IN')}×</span>
          </div>
        </div>

        {/* Trigger */}
        {sel.trigger && (
          <div style={{ marginBottom: 22 }}>
            <div className="eyebrow" style={{ marginBottom: 10 }}>Trigger</div>
            <div style={{ fontSize: 12, color: 'var(--ink-3)', marginBottom: 10, lineHeight: 1.5 }}>
              {sel.trigger.label}
            </div>
            {sel.trigger.type === 'route' && (
              <button
                type="button"
                onClick={() => navigate((sel.trigger as { to: string }).to)}
                className="btn sm primary"
                style={{ width: '100%' }}
              >
                Go to {sel.trigger.to} <ExternalLink className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        )}

        {/* Data bindings */}
        <div style={{ marginBottom: 22 }}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>Data bindings</div>
          <div style={{ fontSize: 12 }}>
            {sel.fields.map(([k, v], i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  padding: '6px 0',
                  borderBottom: i === sel.fields.length - 1 ? 'none' : '1px dashed var(--line)',
                }}
              >
                <span className="mono" style={{ color: 'var(--ink-3)', fontSize: 10.5 }}>{'{{ ' + k + ' }}'}</span>
                <span className="mono" style={{ color: 'var(--ink-4)', fontSize: 10.5 }}>{v}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Actions */}
        <div style={{ display: 'grid', gap: 8 }}>
          <button type="button" className="btn sm" onClick={() => window.print()}>
            <Printer className="w-4 h-4" /> Print preview (stub)
          </button>
        </div>
      </aside>
    </div>
  );
}

export { PrintPage };
