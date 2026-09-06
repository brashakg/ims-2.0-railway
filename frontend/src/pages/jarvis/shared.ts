// JARVIS shared types + heartbeat helpers - moved verbatim out of
// JarvisPage.tsx (Wave 3 file diet). No logic changed.

// Types
export interface Message {
  id: string;
  type: 'user' | 'jarvis';
  content: string;
  timestamp: Date;
  data?: Record<string, unknown>;
}

export interface QuickInsight {
  revenue_today: number;
  revenue_growth: number;
  orders_today: number;
  pending_orders: number;
  low_stock_count: number;
  staff_present: string;
  top_recommendation: {
    priority: string;
    title: string;
    description: string;
    action: string;
    impact: string;
  } | null;
  greeting: string;
}

export interface Recommendation {
  priority: string;
  category: string;
  title: string;
  description: string;
  action: string;
  impact: string;
  link?: string;
}

// Unified activity feed envelope from GET /api/v1/jarvis/agents/activity
export interface ActivityEvent {
  agent_id: string;
  kind: 'anomaly' | 'sync_run' | 'task_execution' | 'notification' | 'ui_audit';
  timestamp: string;
  summary: string;
  severity?: string;
  status?: string;
  recommended_action?: string;
  ai_powered?: boolean;
  details?: Record<string, unknown>;
}

// Live agent data shape from GET /api/v1/jarvis/agents
export interface LiveAgent {
  agent_id: string;
  agent_name: string;
  agent_type: string;
  description: string;
  version: string;
  enabled: boolean;
  toggleable: boolean;
  status: string;    // running | sleeping | stopped | error | starting
  health: string;    // healthy | degraded | unhealthy | unknown
  schedule_type: string;
  schedule_value: string;
  last_run: string | null;
  last_status?: string;
  last_error?: string | null;
  run_count: number;
  error_count: number;
  avg_run_time_ms: number;
  hero: string;
  capabilities: string[];
  /** ISO of the most recent scheduler tick (agent_heartbeats). A stale value =>
   *  no tick has landed (scheduler dead or agent never scheduled). */
  last_tick_at?: string | null;
  /** ok | success | error | skipped from the last tick. */
  heartbeat_status?: string | null;
}

// The scheduler evaluates every agent on a ~60s loop (see api/main.py), so a
// heartbeat older than ~2 ticks means no tick has landed -> the fleet may be
// silently dead (the #929 blind spot). A small grace avoids boundary flapping.
const HEARTBEAT_TICK_MS = 60_000;
const HEARTBEAT_STALE_MS = 3 * HEARTBEAT_TICK_MS;

/** Compact "Xs/Xm/Xh/Xd ago" for a heartbeat timestamp. */
function relAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (!isFinite(ms) || ms < 0) return 'just now';
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** Liveness dot colour + label + tooltip from an agent's heartbeat. Green = ok
 *  within ~2 ticks, amber = skipped/idle, red = error or stale (>2 ticks),
 *  grey = no tick recorded. */
export function heartbeatView(a: LiveAgent): { color: string; label: string; title: string } {
  const last = a.last_tick_at;
  const st = (a.heartbeat_status || '').toLowerCase();
  if (!last) {
    return {
      color: '#9ca3af',
      label: 'no tick yet',
      title: 'No heartbeat recorded — agent never scheduled, or the scheduler is not running',
    };
  }
  const ago = relAgo(last);
  const ageMs = Date.now() - new Date(last).getTime();
  const stale = !isFinite(ageMs) || ageMs > HEARTBEAT_STALE_MS;
  if (st === 'error') {
    return { color: '#dc2626', label: `error · ${ago}`, title: `Last tick errored (${ago})` };
  }
  if (stale) {
    return {
      color: '#dc2626',
      label: `stale · ${ago}`,
      title: `No tick in over 2 cycles — the scheduler may be down (last tick ${ago})`,
    };
  }
  if (st === 'skipped') {
    return {
      color: '#d97706',
      label: `idle · ${ago}`,
      title: `Last tick skipped — agent toggled off or nothing to do (${ago})`,
    };
  }
  return { color: '#16a34a', label: `ok · ${ago}`, title: `Healthy — last tick ${ago}` };
}
