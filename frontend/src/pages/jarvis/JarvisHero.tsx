// JarvisHero - moved verbatim out of JarvisPage.tsx (Wave 3 file diet).

import { Sparkles, RefreshCw } from 'lucide-react';
import type { JarvisPageState } from './useJarvisPage';

export function JarvisHero({ page }: { page: JarvisPageState }) {
  const {
    enabledCount, totalActs24h, awaitingApproval,
    loadInsights, loadRecommendations, loadAgents, loadActivity, loadProposals,
  } = page;
  return (
    <>
      {/* ── Hero: ink background, editorial title, pulse stats ── */}
      <section
        style={{
          background: 'var(--ink)',
          color: '#fff',
          borderRadius: 'var(--r-xl)',
          padding: '32px 32px 28px',
          marginBottom: 20,
          display: 'grid',
          gridTemplateColumns: '1fr auto',
          gap: 32,
          alignItems: 'end',
          position: 'relative',
          overflow: 'hidden',
          backgroundImage:
            'radial-gradient(400px 180px at 85% 0%, rgba(205,32,26,.22), transparent 60%), radial-gradient(600px 300px at 30% 110%, rgba(255,255,255,.04), transparent 60%)',
        }}
      >
        <div>
          <div className="eyebrow" style={{ color: '#9a9a92', marginBottom: 12 }}>
            Jarvis · Always-on automation · Superadmin only
          </div>
          <h1 style={{ margin: '0 0 10px', fontFamily: 'var(--font-display)', fontSize: 44, lineHeight: 1.02, letterSpacing: '-0.02em', maxWidth: 600, color: '#fff', fontWeight: 400 }}>
            Eight agents. One shift.<br />
            Quietly keeping things in line.
          </h1>
          <p style={{ margin: 0, color: '#b6b6ae', maxWidth: 520, fontSize: 13.5, lineHeight: 1.55 }}>
            Jarvis watches the feeds every store ignores — stock, pricing, Rx alignment, task SLAs — and takes the smallest action it can, then asks for approval when the stakes matter.
          </p>
          <div style={{ display: 'flex', gap: 18, fontFamily: 'var(--font-mono)', fontSize: 11, color: '#8a8a82', marginTop: 16, textTransform: 'uppercase', letterSpacing: '.08em' }}>
            <div style={{ paddingRight: 18, borderRight: '1px solid #2e2e2b' }}>
              <div className="figure" style={{ fontSize: 26, color: '#fff', textTransform: 'none', letterSpacing: '-.02em' }}>{enabledCount}/8</div>
              agents enabled
            </div>
            <div style={{ paddingRight: 18, borderRight: '1px solid #2e2e2b' }}>
              <div className="figure" style={{ fontSize: 26, color: '#fff', textTransform: 'none', letterSpacing: '-.02em' }}>{totalActs24h}</div>
              runs · lifetime
            </div>
            <div>
              <div className="figure" style={{ fontSize: 26, color: '#fff', textTransform: 'none', letterSpacing: '-.02em' }}>{awaitingApproval}</div>
              awaiting approval
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="btn sm"
            style={{ background: '#2a2a28', color: '#fff', borderColor: '#3a3a36' }}
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
          <button className="btn sm accent">
            <Sparkles className="w-3.5 h-3.5" /> Deploy agent
          </button>
        </div>
      </section>
    </>
  );
}
