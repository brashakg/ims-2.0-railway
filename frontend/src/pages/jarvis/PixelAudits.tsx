// PixelAudits - moved verbatim out of JarvisPage.tsx (Wave 3 file diet).

import type { JarvisPageState } from './useJarvisPage';

export function PixelAudits({ page }: { page: JarvisPageState }) {
  const {
    pixelAudits, pixelLoading, handleRunNow, loadPixelAudits,
  } = page;
  return (
    <>
      {/* ── PIXEL UI/UX audit history ── */}
      <div className="eyebrow" style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span>PIXEL · UI/UX audits</span>
        {pixelAudits && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-4)' }}>
            {pixelAudits.audits_total} run{pixelAudits.audits_total === 1 ? '' : 's'} on record · {
              pixelAudits.last_outcome?.outcome && pixelAudits.last_outcome.outcome !== 'ok'
                ? `last run: ${pixelAudits.last_outcome.outcome.replace(/_/g, ' ')}`
                : pixelAudits.pagespeed_ready ? 'PageSpeed key present' : 'PageSpeed key not set'
            }
          </span>
        )}
        <button
          type="button"
          onClick={() => handleRunNow('pixel')}
          className="btn sm ghost"
          disabled={pixelLoading}
          style={{ marginLeft: 'auto', fontSize: 10, height: 22, padding: '0 10px' }}
        >
          Run audit now
        </button>
        <button
          type="button"
          onClick={loadPixelAudits}
          disabled={pixelLoading}
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
        {pixelLoading && (
          <div style={{ color: 'var(--ink-4)', fontSize: 12.5, textAlign: 'center', padding: 12 }}>
            Loading PIXEL audits…
          </div>
        )}
        {!pixelLoading && (!pixelAudits || pixelAudits.audits.length === 0) && (
          <div style={{ color: 'var(--ink-4)', fontSize: 12.5, padding: 12 }}>
            <div style={{ marginBottom: 8 }}>No audits on record yet.</div>
            {/* A failed run is a RECORDED run — say what went wrong and what
                to do about it. `pagespeed_ready` only means "a key string
                exists", so it must never be the last word. */}
            {pixelAudits?.last_outcome && pixelAudits.last_outcome.outcome !== 'ok' && (
              <div style={{ fontSize: 11, color: 'var(--err)' }}>
                <strong>
                  {pixelAudits.last_outcome.outcome === 'credentials_rejected'
                    ? 'Google rejected the PageSpeed API key.'
                    : pixelAudits.last_outcome.outcome === 'no_credentials'
                      ? 'No PageSpeed API key is set.'
                      : 'Every PageSpeed call failed.'}
                </strong>
                {pixelAudits.last_outcome.notes && (
                  <span style={{ color: 'var(--ink-3)' }}> {pixelAudits.last_outcome.notes}</span>
                )}
                {pixelAudits.last_outcome.next_step && (
                  <div style={{ marginTop: 4, color: 'var(--ink-3)' }}>
                    {pixelAudits.last_outcome.next_step}
                  </div>
                )}
                {pixelAudits.last_outcome.ran_at && (
                  <div style={{ marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ink-4)' }}>
                    last attempt {new Date(pixelAudits.last_outcome.ran_at).toLocaleString('en-IN')}
                  </div>
                )}
              </div>
            )}
            {pixelAudits && !pixelAudits.last_outcome && !pixelAudits.pagespeed_ready && (
              <div style={{ fontSize: 11, color: 'var(--warn)' }}>
                PIXEL needs a Google PageSpeed API key - add it under Settings &rarr; Integrations &rarr; Google PageSpeed - to run real Lighthouse audits.
                Once set, hit "Run audit now" or wait for the daily 2 AM cron.
              </div>
            )}
            {pixelAudits && !pixelAudits.last_outcome && pixelAudits.pagespeed_ready && (
              <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                A PageSpeed key is saved, but PIXEL has never recorded a run - so the key has never actually been tested.
                Hit "Run audit now" to find out whether Google accepts it, or wait for the daily 2 AM cron.
              </div>
            )}
          </div>
        )}
        {!pixelLoading && pixelAudits && pixelAudits.latest && (
          <div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
              {[
                { label: 'Min Performance', value: pixelAudits.latest.summary?.overall_min_perf, delta: pixelAudits.deltas_vs_previous?.overall_min_perf, max: 1 },
                { label: 'Min Accessibility', value: pixelAudits.latest.summary?.overall_min_a11y, delta: pixelAudits.deltas_vs_previous?.overall_min_a11y, max: 1 },
                { label: 'A11y Violations', value: pixelAudits.latest.summary?.total_a11y_violations, delta: pixelAudits.deltas_vs_previous?.total_a11y_violations, lower_better: true },
                { label: 'Pages Audited', value: pixelAudits.latest.summary?.pages_audited },
              ].map((kpi) => {
                const display = kpi.value === null || kpi.value === undefined
                  ? '—'
                  : (kpi.max === 1 ? Math.round(kpi.value * 100) : kpi.value);
                const deltaSign = kpi.delta !== undefined && kpi.delta !== null && kpi.delta !== 0
                  ? (kpi.delta > 0 ? '+' : '')
                  : '';
                const deltaGood = kpi.delta === undefined || kpi.delta === null || kpi.delta === 0
                  ? null
                  : (kpi.lower_better ? kpi.delta < 0 : kpi.delta > 0);
                return (
                  <div key={kpi.label} style={{ padding: 12, background: 'var(--bg)', borderRadius: 'var(--r-md)', border: '1px solid var(--line)' }}>
                    <div style={{ fontSize: 10.5, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 4 }}>
                      {kpi.label}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                      <span style={{ fontSize: 22, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{display}</span>
                      {kpi.max === 1 && kpi.value !== null && kpi.value !== undefined && <span style={{ fontSize: 12, color: 'var(--ink-4)' }}>/100</span>}
                      {deltaSign !== '' && kpi.delta !== undefined && (
                        <span style={{
                          fontSize: 11,
                          color: deltaGood ? 'var(--ok)' : 'var(--err)',
                          fontFamily: 'var(--font-mono)',
                        }}>
                          {deltaSign}{kpi.max === 1 && typeof kpi.delta === 'number' ? Math.round(kpi.delta * 100) : kpi.delta}
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {pixelAudits.latest.regressions?.length > 0 && (
              <div style={{ marginBottom: 14, padding: 10, background: 'rgba(220,38,38,.05)', border: '1px solid rgba(220,38,38,.2)', borderRadius: 'var(--r-md)' }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--err)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '.06em' }}>
                  {pixelAudits.latest.regressions.length} regression{pixelAudits.latest.regressions.length === 1 ? '' : 's'} vs last week
                </div>
                {pixelAudits.latest.regressions.slice(0, 5).map((r: any, i: number) => (
                  <div key={i} style={{ fontSize: 11.5, color: 'var(--ink-2)', display: 'flex', gap: 8, marginBottom: 2 }}>
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--ink-4)' }}>{r.url}</span>
                    <span>{r.metric}</span>
                    <span style={{ marginLeft: 'auto', color: 'var(--err)' }}>
                      {Math.round(r.baseline * 100)} → {Math.round(r.current * 100)} ({Math.round(r.delta * 100)})
                    </span>
                  </div>
                ))}
              </div>
            )}

            <div style={{ fontSize: 11, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 8 }}>
              Latest per-page · {new Date(pixelAudits.latest.ran_at).toLocaleString('en-IN')}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--ink-4)', textTransform: 'uppercase', fontSize: 10, letterSpacing: '.06em' }}>
                    <th style={{ textAlign: 'left', padding: '6px 8px' }}>Page</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px' }}>Perf</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px' }}>A11y</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px' }}>Best</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px' }}>SEO</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px' }}>LCP</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px' }}>CLS</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px' }}>A11y issues</th>
                  </tr>
                </thead>
                <tbody>
                  {(pixelAudits.latest.pages || []).map((p: any, idx: number) => {
                    const cellColor = (score: number | null) => {
                      if (score === null || score === undefined) return 'var(--ink-4)';
                      if (score >= 0.9) return 'var(--ok)';
                      if (score >= 0.5) return 'var(--warn)';
                      return 'var(--err)';
                    };
                    return (
                      <tr key={idx} style={{ borderBottom: '1px solid var(--line)' }}>
                        <td style={{ padding: '6px 8px', fontFamily: 'var(--font-mono)', fontSize: 11 }}>{p.url?.replace(pixelAudits.frontend_url, '') || p.url}</td>
                        <td style={{ textAlign: 'right', padding: '6px 8px', color: cellColor(p.scores?.performance), fontFamily: 'var(--font-mono)' }}>
                          {p.scores?.performance != null ? Math.round(p.scores.performance * 100) : '—'}
                        </td>
                        <td style={{ textAlign: 'right', padding: '6px 8px', color: cellColor(p.scores?.accessibility), fontFamily: 'var(--font-mono)' }}>
                          {p.scores?.accessibility != null ? Math.round(p.scores.accessibility * 100) : '—'}
                        </td>
                        <td style={{ textAlign: 'right', padding: '6px 8px', color: cellColor(p.scores?.best_practices), fontFamily: 'var(--font-mono)' }}>
                          {p.scores?.best_practices != null ? Math.round(p.scores.best_practices * 100) : '—'}
                        </td>
                        <td style={{ textAlign: 'right', padding: '6px 8px', color: cellColor(p.scores?.seo), fontFamily: 'var(--font-mono)' }}>
                          {p.scores?.seo != null ? Math.round(p.scores.seo * 100) : '—'}
                        </td>
                        <td style={{ textAlign: 'right', padding: '6px 8px', fontFamily: 'var(--font-mono)' }}>
                          {p.core_web_vitals?.lcp_ms != null ? `${Math.round(p.core_web_vitals.lcp_ms)}ms` : '—'}
                        </td>
                        <td style={{ textAlign: 'right', padding: '6px 8px', fontFamily: 'var(--font-mono)' }}>
                          {p.core_web_vitals?.cls != null ? p.core_web_vitals.cls.toFixed(3) : '—'}
                        </td>
                        <td style={{ textAlign: 'right', padding: '6px 8px', color: p.a11y_violations_count > 0 ? 'var(--warn)' : 'var(--ink-3)', fontFamily: 'var(--font-mono)' }}>
                          {p.a11y_violations_count ?? '—'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {pixelAudits.audits.length > 1 && (
              <div style={{ marginTop: 12, fontSize: 10.5, color: 'var(--ink-4)' }}>
                {pixelAudits.audits.length} most-recent audits loaded · trend deltas above are vs the previous run
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
