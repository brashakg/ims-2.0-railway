// ChangeProposals - moved verbatim out of JarvisPage.tsx (Wave 3 file diet).

import { Check, X, ShieldCheck } from 'lucide-react';
import type { JarvisPageState } from './useJarvisPage';

export function ChangeProposals({ page }: { page: JarvisPageState }) {
  const {
    awaitingApproval, loadProposals, proposals, proposalBusyId,
    handleApproveProposal, handleRejectProposal,
  } = page;
  return (
    <>
      {/* ── Change proposals (SYSTEM_INTENT section 8 review loop) ── */}
      <div className="eyebrow" style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <ShieldCheck className="w-3 h-3" /> Change proposals · {awaitingApproval} pending
        </span>
        <button
          type="button"
          onClick={loadProposals}
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
        {proposals === null && (
          <div style={{ padding: 20, color: 'var(--ink-4)', fontSize: 12.5, textAlign: 'center' }}>
            Loading proposals…
          </div>
        )}
        {proposals !== null && proposals.length === 0 && (
          <div style={{ padding: 20, color: 'var(--ink-4)', fontSize: 12.5, textAlign: 'center' }}>
            No proposals awaiting review. Agents enqueue suggestions here for your approval.
          </div>
        )}
        {proposals !== null && proposals.length > 0 && proposals.map((p, i) => {
          const busy = proposalBusyId === p.proposal_id;
          return (
            <div
              key={p.proposal_id}
              style={{
                padding: '14px 16px',
                borderBottom: i === proposals.length - 1 ? 'none' : '1px solid var(--line-soft)',
                display: 'grid',
                gridTemplateColumns: '1fr auto',
                gap: 16,
                alignItems: 'flex-start',
              }}
            >
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
                  {/* Reversible -> will auto-execute on approve; Advisory -> records only */}
                  <span
                    className={'chip ' + (p.reversible ? 'ok' : 'neutral')}
                    style={{ height: 18, fontSize: 9.5, fontFamily: 'var(--font-mono)' }}
                    title={
                      p.reversible
                        ? 'Reversible Tier-1 — approving will auto-execute the change'
                        : 'Advisory — approving records your decision; a human still makes the change'
                    }
                  >
                    {p.reversible ? 'reversible · auto-executes' : 'advisory · manual'}
                  </span>
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
                    {p.created_by_agent} · {p.type}
                  </span>
                </div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)', marginBottom: 3 }}>
                  {p.title}
                </div>
                <div style={{ fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5 }}>
                  {p.rationale}
                </div>
                {p.created_at && (
                  <div style={{ fontSize: 10.5, color: 'var(--ink-5)', marginTop: 4, fontFamily: 'var(--font-mono)' }}>
                    {new Date(p.created_at).toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                <button
                  type="button"
                  onClick={() => handleApproveProposal(p)}
                  disabled={busy}
                  className="btn sm primary"
                  style={{ fontSize: 11, opacity: busy ? 0.5 : 1 }}
                  title={p.reversible ? 'Approve and execute' : 'Approve (advisory)'}
                >
                  <Check className="w-3.5 h-3.5" /> Approve
                </button>
                <button
                  type="button"
                  onClick={() => handleRejectProposal(p)}
                  disabled={busy}
                  className="btn sm ghost"
                  style={{ fontSize: 11, opacity: busy ? 0.5 : 1 }}
                >
                  <X className="w-3.5 h-3.5" /> Reject
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
