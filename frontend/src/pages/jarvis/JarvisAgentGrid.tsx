// JarvisAgentGrid - moved verbatim out of JarvisPage.tsx (Wave 3 file diet).

import { Zap } from 'lucide-react';
import { heartbeatView } from './shared';
import type { JarvisPageState } from './useJarvisPage';

export function JarvisAgentGrid({ page }: { page: JarvisPageState }) {
  const {
    enabledCount, agentsForGrid, agentsErr, togglingId,
    prettySchedule, handleRunNow, handleToggleAgent,
  } = page;
  return (
    <>
      {/* ── Agent grid ── */}
      <div className="eyebrow" style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 10 }}>
        <span>Agents · {enabledCount}/{agentsForGrid.length} enabled</span>
        {agentsErr && (
          <span className="chip err" style={{ marginLeft: 'auto' }}>Live fetch failed: {agentsErr}</span>
        )}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))', gap: 14, marginBottom: 24 }}>
        {agentsForGrid.map((a) => {
          const isToggling = togglingId === a.agent_id;
          const isHealthy = a.health === 'healthy' || a.health === 'unknown';
          const chipTone = !a.enabled ? 'neutral' : a.health === 'unhealthy' ? 'err' : a.health === 'degraded' ? 'warn' : 'ok';
          const statusLabel = !a.enabled ? 'paused'
            : a.health === 'unhealthy' ? 'error'
            : a.status === 'running' ? 'running'
            : a.status === 'sleeping' ? 'sleeping'
            : a.status;
          return (
            <div
              key={a.agent_id}
              style={{
                background: 'var(--surface)',
                border: '1px solid var(--line)',
                borderRadius: 'var(--r-lg)',
                padding: 18,
                display: 'grid',
                gridTemplateColumns: '48px 1fr auto',
                gap: 14,
                alignItems: 'flex-start',
                position: 'relative',
                overflow: 'hidden',
                opacity: a.enabled ? 1 : 0.7,
              }}
            >
              <div
                style={{
                  width: 48,
                  height: 48,
                  borderRadius: 10,
                  background: a.enabled && isHealthy ? 'var(--ink)' : 'var(--bg-sunk)',
                  color: a.enabled && isHealthy ? '#fff' : 'var(--ink-4)',
                  display: 'grid',
                  placeItems: 'center',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                  fontWeight: 600,
                  letterSpacing: '.04em',
                  filter: a.enabled ? 'none' : 'grayscale(1)',
                }}
              >
                {a.agent_name.slice(0, 3)}
              </div>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                  <h3 style={{ margin: 0, font: '600 14px/1.2 var(--font-sans)', color: 'var(--ink)' }}>
                    {a.agent_name}
                  </h3>
                  {!a.toggleable && (
                    <span className="chip" style={{ height: 18, fontSize: 9.5, fontFamily: 'var(--font-mono)' }}>CORE</span>
                  )}
                </div>
                <div style={{ fontSize: 11, color: 'var(--ink-4)', marginBottom: 4, fontStyle: 'italic' }}>
                  {a.hero || a.agent_type}
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--ink-3)', lineHeight: 1.5, marginBottom: 10 }}>
                  {a.description}
                </div>
                <div style={{ display: 'flex', gap: 14, fontSize: 11, color: 'var(--ink-4)', fontFamily: 'var(--font-mono)', flexWrap: 'wrap' }}>
                  <span>Cadence · <strong style={{ color: 'var(--ink)', fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 12 }}>
                    {prettySchedule(a.schedule_type, a.schedule_value)}
                  </strong></span>
                  <span>Runs · <strong style={{ color: 'var(--ink)', fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 12 }}>{a.run_count}</strong></span>
                  {a.error_count > 0 && (
                    <span style={{ color: 'var(--err)' }}>
                      Errors · <strong style={{ fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 12 }}>{a.error_count}</strong>
                    </span>
                  )}
                </div>
                {(() => {
                  const hb = heartbeatView(a);
                  return (
                    <div
                      title={hb.title}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        fontSize: 10.5,
                        color: 'var(--ink-5)',
                        marginTop: 6,
                        fontFamily: 'var(--font-mono)',
                      }}
                    >
                      <span
                        style={{
                          width: 8,
                          height: 8,
                          borderRadius: '50%',
                          background: hb.color,
                          flexShrink: 0,
                        }}
                      />
                      Heartbeat · {hb.label}
                    </div>
                  );
                })()}
                {a.last_run && (
                  <div style={{ fontSize: 10.5, color: 'var(--ink-5)', marginTop: 4, fontFamily: 'var(--font-mono)' }}>
                    Last run: {new Date(a.last_run).toLocaleString([], { hour: '2-digit', minute: '2-digit', month: 'short', day: '2-digit' })}
                  </div>
                )}
                {a.last_error && (
                  <div style={{ fontSize: 10.5, color: 'var(--err)', marginTop: 4 }}>
                    {a.last_error.slice(0, 120)}
                  </div>
                )}
                {a.toggleable && a.enabled && (
                  <button
                    type="button"
                    onClick={() => handleRunNow(a.agent_id)}
                    disabled={isToggling}
                    className="btn sm ghost"
                    style={{ marginTop: 10, fontSize: 11, padding: '0 8px', height: 24 }}
                  >
                    <Zap className="w-3 h-3" /> Run now
                  </button>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 10 }}>
                <span className={'chip ' + chipTone}>
                  <span className="dot" />{statusLabel}
                </span>
                {/* Toggle switch */}
                {a.toggleable ? (
                  <button
                    type="button"
                    role="switch"
                    aria-checked={a.enabled ? "true" : "false"}
                    aria-label={`${a.enabled ? 'Disable' : 'Enable'} ${a.agent_name}`}
                    onClick={() => handleToggleAgent(a.agent_id, !a.enabled)}
                    disabled={isToggling}
                    className={'tgl' + (a.enabled ? ' on' : '')}
                    style={{ opacity: isToggling ? 0.5 : 1 }}
                  />
                ) : (
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em' }}>
                    always on
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
