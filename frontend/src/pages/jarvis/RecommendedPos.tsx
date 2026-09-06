// RecommendedPos - moved verbatim out of JarvisPage.tsx (Wave 3 file diet).

import { Package, Check, X } from 'lucide-react';
import type { JarvisPageState } from './useJarvisPage';

// ── #7 Recommended POs section ──────────────────────────────────────
// Burn-rate reorder suggestions. Visible to ADMIN + SUPERADMIN. Each card
// surfaces the full reasoning (on-hand, burn rate, days left, projected
// stockout, recommended qty) so the reviewer acts with the same signal
// ORACLE used. Approve ("Act On It") creates a DRAFT PO only - it never
// sends a PO or commits money. SKUs with no vendor are flagged and their
// Act-On-It button is disabled until a vendor is assigned.
const renderPoNumber = (v: unknown): string =>
  typeof v === 'number' && isFinite(v) ? String(Math.round(v * 100) / 100) : '-';

export function RecommendedPos({ page }: { page: JarvisPageState }) {
  const {
    poPending, loadPoProposals, poProposals, proposalBusyId,
    handleApprovePo, handleIgnorePo,
  } = page;
  return (
    <>
      <div className="eyebrow" style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Package className="w-3 h-3" /> Recommended POs · {poPending} pending
        </span>
        <button
          type="button"
          onClick={loadPoProposals}
          className="btn sm ghost"
          style={{ marginLeft: 'auto', fontSize: 10, height: 22, padding: '0 10px' }}
        >
          Refresh
        </button>
      </div>
      <div
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r-lg)',
          marginBottom: 24,
          overflow: 'hidden',
        }}
      >
        {poProposals === null && (
          <div style={{ padding: 20, color: 'var(--ink-4)', fontSize: 12.5, textAlign: 'center' }}>
            Loading reorder suggestions…
          </div>
        )}
        {poProposals !== null && poProposals.length === 0 && (
          <div style={{ padding: 20, color: 'var(--ink-4)', fontSize: 12.5, textAlign: 'center' }}>
            No reorder recommendations. ORACLE runs hourly and will surface SKUs approaching stockout.
          </div>
        )}
        {poProposals !== null && poProposals.length > 0 && poProposals.map((p, i) => {
          const busy = proposalBusyId === p.proposal_id;
          const pl = (p.payload ?? {}) as Record<string, unknown>;
          const vendorMissing = pl.vendor_missing === true;
          const daysRemaining = typeof pl.days_remaining === 'number' ? pl.days_remaining : null;
          const stockoutIso = typeof pl.projected_stockout_date === 'string' ? pl.projected_stockout_date : null;
          const storeId = typeof pl.store_id === 'string' ? pl.store_id : '—';
          const qty = typeof pl.quantity === 'number' ? pl.quantity : null;
          // Urgency: semantic colour ONLY (red <7d critical, amber <14d watch).
          const urgent = daysRemaining !== null && daysRemaining < 7;
          const watch = daysRemaining !== null && daysRemaining >= 7 && daysRemaining < 14;
          return (
            <div
              key={p.proposal_id}
              style={{
                padding: '14px 16px',
                borderBottom: i === poProposals.length - 1 ? 'none' : '1px solid var(--line-soft)',
                display: 'grid',
                gridTemplateColumns: '1fr auto',
                gap: 16,
                alignItems: 'flex-start',
              }}
            >
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
                  {/* Store badge */}
                  <span style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 10,
                    color: '#fff',
                    background: 'var(--ink)',
                    padding: '3px 6px',
                    borderRadius: 3,
                    textTransform: 'uppercase',
                    letterSpacing: '.06em',
                  }}>
                    {storeId}
                  </span>
                  {/* Urgency chip — semantic colour only */}
                  {urgent && (
                    <span style={{ fontSize: 9.5, fontFamily: 'var(--font-mono)', color: '#fff', background: '#cd201a', padding: '3px 6px', borderRadius: 3, textTransform: 'uppercase', letterSpacing: '.06em' }}>
                      Critical · under 7 days
                    </span>
                  )}
                  {watch && (
                    <span style={{ fontSize: 9.5, fontFamily: 'var(--font-mono)', color: '#7a4f00', background: '#fbe6b4', padding: '3px 6px', borderRadius: 3, textTransform: 'uppercase', letterSpacing: '.06em' }}>
                      Watch · under 14 days
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 3 }}>
                  {p.title}
                </div>
                {/* Signal row (mono) */}
                <div style={{ fontSize: 11.5, color: 'var(--ink-3)', fontFamily: 'var(--font-mono)', lineHeight: 1.6 }}>
                  On hand: {renderPoNumber(pl.on_hand)} · Burn 7d: {renderPoNumber(pl.burn_rate_7d)}/day · Days left: {renderPoNumber(pl.days_remaining)}
                  {stockoutIso && (
                    <> · Stockout: {new Date(stockoutIso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}</>
                  )}
                </div>
                <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)', marginTop: 4 }}>
                  Order {qty ?? '—'} unit{qty === 1 ? '' : 's'}
                </div>
                {/* Vendor row */}
                {vendorMissing ? (
                  <span style={{ display: 'inline-block', marginTop: 6, fontSize: 10.5, color: '#cd201a', border: '1px solid #cd201a', padding: '3px 7px', borderRadius: 3 }}>
                    No vendor assigned — assign before ordering
                  </span>
                ) : (
                  <div style={{ fontSize: 11, color: 'var(--ink-4)', marginTop: 5 }}>
                    Vendor: {String(pl.vendor_id ?? '—')}
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                <button
                  type="button"
                  onClick={() => handleApprovePo(p)}
                  disabled={busy || vendorMissing}
                  className="btn sm primary"
                  style={{ fontSize: 11, opacity: busy || vendorMissing ? 0.5 : 1 }}
                  title={vendorMissing ? 'Assign a vendor before ordering' : 'Create a DRAFT purchase order (not sent)'}
                >
                  <Check className="w-3.5 h-3.5" /> Act On It
                </button>
                <button
                  type="button"
                  onClick={() => handleIgnorePo(p)}
                  disabled={busy}
                  className="btn sm ghost"
                  style={{ fontSize: 11, opacity: busy ? 0.5 : 1 }}
                >
                  <X className="w-3.5 h-3.5" /> Ignore
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
