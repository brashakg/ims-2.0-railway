// JarvisHero - moved verbatim out of JarvisPage.tsx (Wave 3 file diet).

import { Settings2, RefreshCw } from 'lucide-react';
import type { JarvisPageState } from './useJarvisPage';

export function JarvisHero({ page }: { page: JarvisPageState }) {
  const {
    enabledCount, totalActs24h, awaitingApproval,
    loadInsights, loadRecommendations, loadAgents, loadActivity, loadProposals,
  } = page;
  return (
    <>
      {/* ── Hero: editorial title, pulse stats. L1-F10: a light surface with
          dark text -- the app is light-only, and the global main-h1 rule
          forces headings dark, which made the old white-on-ink title vanish. ── */}
      <section
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--line)',
          color: 'var(--ink)',
          borderRadius: 'var(--r-xl)',
          padding: '32px 32px 28px',
          marginBottom: 20,
          display: 'grid',
          gridTemplateColumns: '1fr auto',
          gap: 32,
          alignItems: 'end',
          position: 'relative',
          overflow: 'hidden',
          backgroundImage: 'radial-gradient(400px 180px at 85% 0%, rgba(205,32,26,.08), transparent 60%)',
        }}
      >
        <div>
          <div className="eyebrow" style={{ marginBottom: 12 }}>
            Jarvis · Always-on automation · Superadmin only
          </div>
          <h1 style={{ margin: '0 0 10px', fontFamily: 'var(--font-display)', fontSize: 44, lineHeight: 1.02, letterSpacing: '-0.02em', maxWidth: 600, color: 'var(--ink)', fontWeight: 400 }}>
            Eight agents. One shift.<br />
            Quietly keeping things in line.
          </h1>
          <p style={{ margin: 0, color: 'var(--ink-3)', maxWidth: 520, fontSize: 13.5, lineHeight: 1.55 }}>
            Jarvis watches the feeds every store ignores — stock, pricing, Rx alignment, task SLAs — and takes the smallest action it can, then asks for approval when the stakes matter.
          </p>
          <div style={{ display: 'flex', gap: 18, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-4)', marginTop: 16, textTransform: 'uppercase', letterSpacing: '.08em' }}>
            <div style={{ paddingRight: 18, borderRight: '1px solid var(--line)' }}>
              <div className="figure" style={{ fontSize: 26, color: 'var(--ink)', textTransform: 'none', letterSpacing: '-.02em' }}>{enabledCount}/8</div>
              agents enabled
            </div>
            <div style={{ paddingRight: 18, borderRight: '1px solid var(--line)' }}>
              <div className="figure" style={{ fontSize: 26, color: 'var(--ink)', textTransform: 'none', letterSpacing: '-.02em' }}>{totalActs24h}</div>
              runs · lifetime
            </div>
            <div>
              <div className="figure" style={{ fontSize: 26, color: 'var(--ink)', textTransform: 'none', letterSpacing: '-.02em' }}>{awaitingApproval}</div>
              awaiting approval
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="btn sm"
            onClick={() => {
              loadInsights();
              loadRecommendations();
              loadAgents();
              loadActivity();
              loadProposals();
            }}
          >
            <RefreshCw className="w-3.5 h-3.5" /> Refresh
          </button>
          {/* L1-F10: "Deploy agent" did nothing -- Jarvis has a fixed set of
              eight agents and no way to add one. This takes the superadmin to
              the agent switches below, which is what turning one on means. */}
          <button
            type="button"
            className="btn sm accent"
            onClick={() => document.getElementById('jarvis-agents')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
          >
            <Settings2 className="w-3.5 h-3.5" /> Manage agents
          </button>
        </div>
      </section>
    </>
  );
}
