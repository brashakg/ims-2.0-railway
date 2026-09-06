// ============================================================================
// IMS 2.0 - JARVIS AI Control Interface
// ============================================================================
// SUPERADMIN EXCLUSIVE - Like Jarvis to Iron Man
// Full business intelligence and control system
//
// Wave 3 file diet: this file is the COMPOSITION only. Every block lives in
// a sibling file in this folder; all state, polling and API glue is in
// useJarvisPage. Nothing below changed behaviour - it was moved.

import { IntegrationStatusCard } from '../../components/integrations/IntegrationStatusCard';
import { useJarvisPage } from './useJarvisPage';
import { JarvisHero } from './JarvisHero';
import { JarvisChatPane } from './JarvisChatPane';
import { JarvisInsightsRail } from './JarvisInsightsRail';
import { JarvisAgentGrid } from './JarvisAgentGrid';
import { RecommendedPos } from './RecommendedPos';
import { ChangeProposals } from './ChangeProposals';
import { ActivityFeed } from './ActivityFeed';
import { SentinelHealth } from './SentinelHealth';
import { PixelAudits } from './PixelAudits';

export function JarvisPage() {
  const page = useJarvisPage();
  const { isSuperAdmin, isStrictSuperAdmin } = page;

  // STRICT ACCESS CONTROL — render nothing for non-SUPERADMIN.
  // This guard MUST live after every hook declaration above; an
  // early-return before useState/useEffect would skip them on a
  // later render (e.g. if role changes), throwing
  // "rendered fewer hooks than expected".
  if (!isSuperAdmin) {
    return null;
  }

  // ADMIN (not SUPERADMIN) sees ONLY the #7 Recommended-POs surface — the rest
  // of Jarvis (agent toggles, ORACLE config, chat) is SUPERADMIN-only.
  if (!isStrictSuperAdmin) {
    return (
      <div style={{ padding: '24px 28px 60px', background: 'var(--bg)', minHeight: 'calc(100vh - 52px)', overflowY: 'auto' }}>
        <div className="eyebrow" style={{ marginBottom: 6 }}>Predictive purchasing</div>
        <h1 style={{ margin: '0 0 18px', fontFamily: 'var(--font-display)', fontSize: 28, letterSpacing: '-0.02em', color: 'var(--ink)', fontWeight: 500 }}>
          Recommended purchase orders
        </h1>
        <p style={{ margin: '0 0 20px', color: 'var(--ink-3)', maxWidth: 560, fontSize: 13, lineHeight: 1.55 }}>
          ORACLE projects which SKUs will run out within the reorder horizon from recent sales velocity. Act On It drafts a purchase order for review — nothing is sent to a vendor automatically.
        </p>
        <RecommendedPos page={page} />
      </div>
    );
  }

  return (
    <div style={{ padding: '24px 28px 60px', background: 'var(--bg)', minHeight: 'calc(100vh - 52px)', overflowY: 'auto' }}>
      <JarvisHero page={page} />

      {/* ── Jarvis conversation + Live insights/recommendations ──
          Moved to TOP of /jarvis (per user direction) so the chat +
          recommendations are the first thing the operator sees after
          the hero. Agent toggles, activity feed, SENTINEL/PIXEL cards
          render below. */}
      <div className="eyebrow" style={{ marginBottom: 10 }}>Ask intelligence</div>
      <div
        className="jarvis-ask-grid"
        style={{
          gap: 14,
          background: 'var(--surface)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r-lg)',
          overflow: 'hidden',
          minHeight: 480,
          marginBottom: 24,
        }}
      >
        <JarvisChatPane page={page} />
        <JarvisInsightsRail page={page} />
      </div>

      <JarvisAgentGrid page={page} />

      {/* ── #7 Recommended POs (burn-rate reorder suggestions) ── */}
      <RecommendedPos page={page} />

      <ChangeProposals page={page} />

      <ActivityFeed page={page} />

      <SentinelHealth page={page} />

      <PixelAudits page={page} />

      {/* ── Integration status (read-only, NEXUS boundary) ── */}
      <div className="eyebrow" style={{ marginBottom: 10, marginTop: 24 }}>
        NEXUS · integration status
      </div>
      <IntegrationStatusCard />

      {/* Chat + insights/recommendations moved to top of page (see above). */}
    </div>
  );
}

export default JarvisPage;
