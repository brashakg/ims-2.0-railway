// ============================================================================
// IMS 2.0 — F50 Clinical Handover inbox strip (Hub-page section)
// ============================================================================
// "Clinical Handovers · N from optometry" — a horizontal strip of CLINICAL_RX
// cards the sales floor received. Each card shows the patient, the sending
// optometrist, an Rx one-liner, the doctor's product recommendations, and
// Acknowledge / Mark-Served actions. Renders NOTHING when the caller has no
// active handovers (no empty section header). Restrained light UI: neutral
// throughout, a single bv-red left accent only while UNACKNOWLEDGED.
//
// The only delivery channel is the in-app bell — there is no message send here.

import { useCallback, useEffect, useState } from 'react';
import { useNow } from '../../hooks/useNow';
import { handoffsApi, type ClinicalHandover } from '../../services/api/handoffs';

const TICK_MS = 30 * 1000;

function timeAgo(then: string | null, now: Date): string {
  if (!then) return '';
  const date = new Date(then);
  if (Number.isNaN(date.getTime())) return '';
  const seconds = Math.max(0, Math.floor((now.getTime() - date.getTime()) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function rxLine(h: ClinicalHandover): string | null {
  const s = h.rx_summary;
  if (!s) return null;
  const eye = (e?: Record<string, unknown> | null): string => {
    if (!e) return '—';
    const sph = e.sph ?? '';
    const cyl = e.cyl ?? '';
    const axis = e.axis ?? '';
    const parts = [sph, cyl ? `${cyl}` : '', axis ? `x${axis}` : ''].filter(Boolean);
    return parts.length ? parts.join(' ') : '—';
  };
  return `R: ${eye(s.right_eye)}  L: ${eye(s.left_eye)}`;
}

interface ClinicalHandoverCardProps {
  /** Bumped by the parent to force a refetch. */
  refreshKey?: number;
}

export function ClinicalHandoverCard({ refreshKey }: ClinicalHandoverCardProps) {
  const [items, setItems] = useState<ClinicalHandover[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const now = useNow(TICK_MS);

  const refetch = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const r = await handoffsApi.listClinicalInbox();
      setItems(r.handoffs || []);
    } catch {
      setError(true);
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch, refreshKey]);

  const acknowledge = async (id: string) => {
    setBusy(id);
    try {
      await handoffsApi.acknowledgeClinical(id);
      await refetch();
    } catch {
      // fail-soft: leave the card as-is; the user can retry.
    } finally {
      setBusy(null);
    }
  };

  const markServed = async (id: string) => {
    setBusy(id);
    try {
      await handoffsApi.markServedClinical(id);
      await refetch();
    } catch {
      await refetch();
    } finally {
      setBusy(null);
    }
  };

  // Hide entirely when there's nothing (and during the silent load / error).
  if (loading || error || items.length === 0) return null;

  return (
    <section style={sectionStyle}>
      <div className="section-head">
        <span className="eyebrow">Clinical Handovers</span>
        <span className="line" />
        <span className="sub">
          {items.length} from optometry
        </span>
      </div>

      <div style={stripStyle}>
        {items.map((h) => {
          const acked = Boolean(h.acknowledged_by);
          const served = h.mark_served;
          const recs = h.product_recommendations || [];
          const shown = recs.slice(0, 3);
          const extra = recs.length - shown.length;
          const line = rxLine(h);
          return (
            <div
              key={h.handoff_id}
              style={{
                ...cardStyle,
                borderLeft: acked ? '1px solid var(--line, #e3e3dc)' : '4px solid var(--color-bv-red-600, #c0392b)',
              }}
            >
              <div style={topRowStyle}>
                <span style={patientStyle}>{h.patient_name || 'Patient'}</span>
                <span style={timeStyle}>{timeAgo(h.created_at, now)}</span>
              </div>
              <p style={optoStyle}>Dr. {h.optometrist_name || 'Optometry'}</p>
              {line && <p style={rxStyle}>{line}</p>}

              {shown.length > 0 && (
                <div style={pillRowStyle}>
                  {shown.map((r, i) => (
                    <span key={i} style={pillStyle}>
                      {[r.category, r.brand_preference].filter(Boolean).join(' · ') || r.notes || 'Note'}
                    </span>
                  ))}
                  {extra > 0 && <span style={pillStyle}>+{extra} more</span>}
                </div>
              )}

              {h.clinical_summary && <p style={summaryStyle}>{h.clinical_summary}</p>}

              <div style={ctaRowStyle}>
                {!acked && (
                  <button
                    type="button"
                    onClick={() => acknowledge(h.handoff_id)}
                    disabled={busy === h.handoff_id}
                    style={btnStyle}
                  >
                    Acknowledge
                  </button>
                )}
                {acked && !served && (
                  <button
                    type="button"
                    onClick={() => markServed(h.handoff_id)}
                    disabled={busy === h.handoff_id}
                    style={btnStyle}
                  >
                    Mark Served
                  </button>
                )}
                {served && <span style={servedStyle}>Served</span>}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default ClinicalHandoverCard;

// ============================================================================
// Inline styles (mirror HandoffInboxCard's strip for visual consistency)
// ============================================================================

const sectionStyle: React.CSSProperties = {
  padding: '20px 24px 0 24px',
  maxWidth: 1280,
  margin: '0 auto',
};

const stripStyle: React.CSSProperties = {
  display: 'grid',
  gridAutoFlow: 'column',
  gridAutoColumns: 'minmax(260px, 280px)',
  gap: 12,
  overflowX: 'auto',
  paddingBottom: 6,
  marginTop: 12,
};

const cardStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 6,
  padding: 14,
  borderRadius: 10,
  border: '1px solid var(--line, #e3e3dc)',
  background: 'var(--bg-card, #fff)',
  minHeight: 156,
};

const topRowStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'baseline',
  justifyContent: 'space-between',
  gap: 8,
};

const patientStyle: React.CSSProperties = {
  fontSize: 14,
  fontWeight: 600,
  color: 'var(--ink, #1f1f1d)',
};

const timeStyle: React.CSSProperties = {
  fontSize: 11,
  color: 'var(--ink-4, #86867d)',
  flexShrink: 0,
};

const optoStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 12,
  color: 'var(--ink-3, #65655e)',
};

const rxStyle: React.CSSProperties = {
  margin: 0,
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  fontSize: 11,
  color: 'var(--ink-2, #45453f)',
};

const pillRowStyle: React.CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  gap: 4,
};

const pillStyle: React.CSSProperties = {
  fontSize: 10,
  padding: '2px 6px',
  borderRadius: 999,
  background: 'var(--bg-sunk, #f6f6f1)',
  color: 'var(--ink-2, #45453f)',
};

const summaryStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 11,
  fontStyle: 'italic',
  color: 'var(--ink-4, #86867d)',
};

const ctaRowStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  marginTop: 'auto',
  paddingTop: 6,
};

const btnStyle: React.CSSProperties = {
  fontSize: 12,
  padding: '4px 10px',
  borderRadius: 6,
  border: '1px solid var(--line, #d6d6cf)',
  background: '#fff',
  color: 'var(--ink-2, #45453f)',
  cursor: 'pointer',
};

const servedStyle: React.CSSProperties = {
  fontSize: 11,
  color: 'var(--ink-4, #86867d)',
};
