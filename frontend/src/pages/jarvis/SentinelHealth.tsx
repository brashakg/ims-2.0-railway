// SentinelHealth - moved verbatim out of JarvisPage.tsx (Wave 3 file diet).

import type { JarvisPageState } from './useJarvisPage';

export function SentinelHealth({ page }: { page: JarvisPageState }) {
  const {
    sentinelHealth, sentinelLoading, handleRunNow, loadSentinelHealth,
  } = page;
  return (
    <>
      {/* ── SENTINEL system health ── */}
      <div className="eyebrow" style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span>SENTINEL · system health</span>
        {sentinelHealth?.latest && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-4)' }}>
            last check {new Date(sentinelHealth.latest.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })} · {sentinelHealth.history_count} samples on record
          </span>
        )}
        <button
          type="button"
          onClick={() => handleRunNow('sentinel')}
          className="btn sm ghost"
          disabled={sentinelLoading}
          style={{ marginLeft: 'auto', fontSize: 10, height: 22, padding: '0 10px' }}
        >
          Run check now
        </button>
        <button
          type="button"
          onClick={loadSentinelHealth}
          disabled={sentinelLoading}
          className="btn sm ghost"
          style={{ fontSize: 10, height: 22, padding: '0 10px' }}
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
          padding: 16,
        }}
      >
        {sentinelLoading && (
          <div style={{ color: 'var(--ink-4)', fontSize: 12.5, textAlign: 'center', padding: 12 }}>
            Loading SENTINEL health…
          </div>
        )}
        {!sentinelLoading && !sentinelHealth?.latest && (
          <div style={{ color: 'var(--ink-4)', fontSize: 12.5, padding: 12 }}>
            <div style={{ marginBottom: 8 }}>No health checks on record yet.</div>
            <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>
              SENTINEL ticks every 60 seconds when enabled. First record appears within a minute of the scheduler picking it up — hit "Run check now" to trigger immediately.
            </div>
          </div>
        )}
        {!sentinelLoading && sentinelHealth?.latest && (() => {
          const latest = sentinelHealth.latest;
          const score = latest.score ?? 0;
          const scoreColor = score >= 90 ? 'var(--ok)' : score >= 70 ? 'var(--warn)' : 'var(--err)';
          const statusBadge = (status: string | undefined) => {
            const s = (status || 'unknown').toLowerCase();
            const bg = s === 'healthy' ? 'rgba(16,185,129,.12)' : s === 'degraded' ? 'rgba(245,158,11,.15)' : s === 'unhealthy' ? 'rgba(220,38,38,.15)' : 'rgba(120,120,120,.12)';
            const fg = s === 'healthy' ? 'var(--ok)' : s === 'degraded' ? 'var(--warn)' : s === 'unhealthy' ? 'var(--err)' : 'var(--ink-4)';
            return (
              <span style={{ fontSize: 9.5, fontWeight: 600, padding: '2px 8px', borderRadius: 10, background: bg, color: fg, textTransform: 'uppercase', letterSpacing: '.06em' }}>
                {s}
              </span>
            );
          };
          const r = latest.results || {};
          return (
            <div>
              {/* Score gauge + component grid */}
              <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: 16, marginBottom: 16 }}>
                <div style={{ padding: 16, background: 'var(--bg)', borderRadius: 'var(--r-md)', border: '1px solid var(--line)', textAlign: 'center' }}>
                  <div style={{ fontSize: 10.5, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 4 }}>
                    Health Score
                  </div>
                  <div style={{ fontSize: 42, fontWeight: 700, fontFamily: 'var(--font-mono)', color: scoreColor, lineHeight: 1 }}>
                    {score}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--ink-4)', marginTop: 2 }}>/ 100</div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 8 }}>
                  {[
                    { label: 'Database', status: r.database?.status, detail: r.database?.response_time_ms ? `${r.database.response_time_ms}ms` : '' },
                    { label: 'API', status: r.api?.status, detail: r.api?.response_time_ms ? `${r.api.response_time_ms}ms` : '' },
                    { label: 'Frontend', status: r.frontend?.status, detail: r.frontend?.status_code ? `HTTP ${r.frontend.status_code}` : '' },
                    { label: 'Agents', status: r.agents?.status, detail: r.agents ? `${r.agents.healthy}/${r.agents.total} healthy` : '' },
                    { label: 'Integrity', status: r.data_integrity?.status, detail: r.data_integrity?.issues?.length ? `${r.data_integrity.issues.length} issue${r.data_integrity.issues.length === 1 ? '' : 's'}` : 'clean' },
                  ].map((c) => (
                    <div key={c.label} style={{ padding: 10, background: 'var(--bg)', borderRadius: 'var(--r-md)', border: '1px solid var(--line)' }}>
                      <div style={{ fontSize: 10, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 4 }}>
                        {c.label}
                      </div>
                      <div style={{ marginBottom: 4 }}>{statusBadge(c.status)}</div>
                      <div style={{ fontSize: 10.5, color: 'var(--ink-3)', fontFamily: 'var(--font-mono)' }}>
                        {c.detail || '—'}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Score history sparkline (text-based) */}
              {sentinelHealth.history.length > 1 && (() => {
                const scores = sentinelHealth.history.map((h) => h.score);
                const min = Math.min(...scores);
                const max = Math.max(...scores);
                const range = Math.max(max - min, 1);
                const bars = '▁▂▃▄▅▆▇█';
                const spark = scores.map((s) => bars[Math.min(7, Math.floor(((s - min) / range) * 7))]).join('');
                return (
                  <div style={{ fontSize: 11, color: 'var(--ink-3)', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.06em' }}>Score history</span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 14, letterSpacing: 1 }}>{spark}</span>
                    <span style={{ color: 'var(--ink-4)', fontFamily: 'var(--font-mono)' }}>min {min} · max {max}</span>
                  </div>
                );
              })()}

              {/* Recent alerts */}
              {sentinelHealth.alerts.length > 0 && (
                <div>
                  <div style={{ fontSize: 11, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 6 }}>
                    Recent alerts · last {sentinelHealth.alerts.length}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {sentinelHealth.alerts.map((a, i) => {
                      const sev = (a.severity || 'LOW').toUpperCase();
                      const sevColor = sev === 'CRITICAL' ? 'var(--err)' : sev === 'HIGH' ? 'var(--err)' : sev === 'MEDIUM' ? 'var(--warn)' : 'var(--ink-4)';
                      return (
                        <div key={i} style={{ display: 'flex', gap: 8, fontSize: 11.5, padding: 6, borderLeft: `3px solid ${sevColor}`, paddingLeft: 10 }}>
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: sevColor, textTransform: 'uppercase', minWidth: 56, fontWeight: 600 }}>{sev}</span>
                          <span style={{ color: 'var(--ink-4)', fontFamily: 'var(--font-mono)', fontSize: 9.5, minWidth: 60 }}>{a.domain}</span>
                          <span style={{ flex: 1, color: 'var(--ink-2)' }}>{a.message}</span>
                          <span style={{ color: 'var(--ink-4)', fontSize: 10, whiteSpace: 'nowrap' }}>
                            {new Date(a.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          );
        })()}
      </div>
    </>
  );
}
