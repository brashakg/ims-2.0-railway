// ActivityFeed - moved verbatim out of JarvisPage.tsx (Wave 3 file diet).

import type { JarvisPageState } from './useJarvisPage';

export function ActivityFeed({ page }: { page: JarvisPageState }) {
  const {
    activity, activityFilter, setActivityFilter,
  } = page;
  return (
    <>
      {/* ── Activity feed (Phase 5) ── */}
      <div className="eyebrow" style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span>Recent activity · last 72h</span>
        <div style={{ display: 'flex', gap: 4, marginLeft: 'auto', flexWrap: 'wrap' }}>
          {['all', 'oracle', 'megaphone', 'pixel', 'taskmaster', 'nexus'].map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setActivityFilter(k)}
              className="btn sm ghost"
              style={{
                fontSize: 10,
                height: 22,
                padding: '0 8px',
                background: activityFilter === k ? 'var(--ink)' : 'transparent',
                color: activityFilter === k ? '#fff' : 'var(--ink-3)',
                borderColor: activityFilter === k ? 'var(--ink)' : 'var(--line)',
                textTransform: 'uppercase',
                letterSpacing: '.06em',
                fontFamily: 'var(--font-mono)',
              }}
            >
              {k}
            </button>
          ))}
        </div>
      </div>
      <div
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r-lg)',
          marginBottom: 24,
          maxHeight: 420,
          overflowY: 'auto',
        }}
      >
        {activity === null && (
          <div style={{ padding: 20, color: 'var(--ink-4)', fontSize: 12.5, textAlign: 'center' }}>
            Loading activity…
          </div>
        )}
        {activity !== null && activity.length === 0 && (
          <div style={{ padding: 20, color: 'var(--ink-4)', fontSize: 12.5, textAlign: 'center' }}>
            No activity in the last 72 hours. Agents may be newly enabled or idle.
          </div>
        )}
        {activity !== null && activity.length > 0 && (() => {
          const filtered = activityFilter === 'all'
            ? activity
            : activity.filter((e) => e.agent_id === activityFilter);
          if (filtered.length === 0) {
            return (
              <div style={{ padding: 20, color: 'var(--ink-4)', fontSize: 12.5, textAlign: 'center' }}>
                No {activityFilter.toUpperCase()} events in window.
              </div>
            );
          }
          return filtered.map((e, i) => {
            const severityColor =
              e.severity === 'CRITICAL' || e.severity === 'HIGH' ? 'var(--err)'
              : e.severity === 'MEDIUM' ? 'var(--warn)'
              : e.status === 'error' || e.status === 'failed' ? 'var(--err)'
              : e.status === 'warn' ? 'var(--warn)'
              : 'var(--ink-4)';
            const kindLabel = {
              anomaly: 'anomaly',
              sync_run: 'sync',
              task_execution: 'action',
              notification: 'message',
              ui_audit: 'audit',
            }[e.kind] || e.kind;
            return (
              <div
                key={`${e.timestamp}-${i}`}
                style={{
                  padding: '10px 16px',
                  borderBottom: i === filtered.length - 1 ? 'none' : '1px solid var(--line-soft)',
                  display: 'grid',
                  gridTemplateColumns: '80px 100px 1fr auto',
                  gap: 14,
                  alignItems: 'flex-start',
                  fontSize: 12.5,
                }}
              >
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--ink-4)' }}>
                  {e.timestamp ? new Date(e.timestamp).toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'}
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
                  alignSelf: 'flex-start',
                  textAlign: 'center',
                }}>
                  {e.agent_id} · {kindLabel}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ color: 'var(--ink)', lineHeight: 1.45 }}>
                    {e.summary}
                  </div>
                  {e.recommended_action && (
                    <div style={{ color: 'var(--ok)', marginTop: 4, fontSize: 11.5, fontStyle: 'italic' }}>
                      → {e.recommended_action}
                    </div>
                  )}
                  {e.ai_powered && (
                    <span className="chip accent" style={{ height: 16, fontSize: 9, marginTop: 4 }}>
                      Claude-enriched
                    </span>
                  )}
                </div>
                {(e.severity || e.status) && (
                  <span style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 9.5,
                    color: severityColor,
                    textTransform: 'uppercase',
                    letterSpacing: '.08em',
                    alignSelf: 'flex-start',
                  }}>
                    {e.severity || e.status}
                  </span>
                )}
              </div>
            );
          });
        })()}
      </div>
    </>
  );
}
