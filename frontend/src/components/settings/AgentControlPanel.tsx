// ============================================================================
// IMS 2.0 - Agent Control Panel (SUPERADMIN Only)
// ============================================================================
// Manages JARVIS AI agents — toggle ON/OFF, view health, force-run, view logs.
// Accessible from Settings > Agents tab.

import { useState, useEffect, useCallback } from 'react';
import {
  Activity, PlayCircle, RefreshCw, AlertTriangle,
  CheckCircle2, XCircle, Clock, Zap, ChevronDown, ChevronUp,
  Loader2, Shield, Bot, Heart,
} from 'lucide-react';
import clsx from 'clsx';
import api from '../../services/api/client';

// ============================================================================
// Types
// ============================================================================

interface AgentData {
  agent_id: string;
  agent_name: string;
  agent_type: string;
  description: string;
  enabled: boolean;
  toggleable: boolean;
  status: string;
  health: string;
  schedule_type: string;
  schedule_value: string;
  last_run: string | null;
  last_status: string | null;
  last_error: string | null;
  run_count: number;
  error_count: number;
  avg_run_time_ms: number;
  hero: string;
  capabilities?: string[];
}

interface AgentLogEntry {
  agent_id: string;
  action: string;
  details: Record<string, unknown>;
  timestamp: string;
}

interface TimelineEntry {
  agent_id: string;
  action: string;
  details: Record<string, unknown>;
  timestamp: string;
}

// ============================================================================
// Hero identity mapping
// ============================================================================

const HERO_META: Record<string, { color: string; gradient: string; emoji: string }> = {
  cortex:     { color: 'purple', gradient: 'from-purple-500 to-indigo-600', emoji: '🧠' },
  sentinel:   { color: 'red',    gradient: 'from-red-500 to-orange-600',    emoji: '🛡️' },
  oracle:     { color: 'blue',   gradient: 'from-blue-500 to-cyan-600',     emoji: '🔮' },
  taskmaster: { color: 'amber',  gradient: 'from-amber-500 to-yellow-600',  emoji: '📋' },
  megaphone:  { color: 'green',  gradient: 'from-green-500 to-emerald-600', emoji: '📢' },
  nexus:      { color: 'slate',  gradient: 'from-slate-500 to-gray-600',    emoji: '🔗' },
  pixel:      { color: 'sky',    gradient: 'from-sky-500 to-blue-600',      emoji: '🖥️' },
};

const getHeroMeta = (agentId: string) =>
  HERO_META[agentId] || { color: 'gray', gradient: 'from-gray-500 to-gray-600', emoji: '🤖' };

// ============================================================================
// Helpers
// ============================================================================

function formatTimestamp(ts: string | null): string {
  if (!ts) return 'Never';
  try {
    const d = new Date(ts);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'Just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  } catch {
    return ts;
  }
}

function HealthBadge({ health }: { health: string }) {
  const styles: Record<string, { bg: string; text: string; icon: typeof CheckCircle2 }> = {
    healthy:   { bg: 'bg-green-100', text: 'text-green-700', icon: CheckCircle2 },
    degraded:  { bg: 'bg-amber-100', text: 'text-amber-700', icon: AlertTriangle },
    unhealthy: { bg: 'bg-red-100',   text: 'text-red-700',   icon: XCircle },
    unknown:   { bg: 'bg-gray-100',  text: 'text-gray-500',  icon: Clock },
  };
  const s = styles[health] || styles.unknown;
  const Icon = s.icon;
  return (
    <span className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium', s.bg, s.text)}>
      <Icon className="w-3 h-3" />
      {health}
    </span>
  );
}

function StatusDot({ status }: { status: string }) {
  const color = status === 'running' ? 'bg-green-400 animate-pulse' :
                status === 'idle' ? 'bg-blue-400' :
                status === 'error' ? 'bg-red-500' : 'bg-gray-400';
  return <span className={clsx('inline-block w-2 h-2 rounded-full', color)} />;
}

// ============================================================================
// Agent Card Component
// ============================================================================

function AgentCard({
  agent,
  onToggle,
  onRunNow,
  onExpand,
  isExpanded,
  logs,
  logsLoading,
}: {
  agent: AgentData;
  onToggle: (id: string, enabled: boolean) => void;
  onRunNow: (id: string) => void;
  onExpand: (id: string) => void;
  isExpanded: boolean;
  logs: AgentLogEntry[];
  logsLoading: boolean;
}) {
  const meta = getHeroMeta(agent.agent_id);
  const [toggling, setToggling] = useState(false);
  const [running, setRunning] = useState(false);

  const handleToggle = async () => {
    if (!agent.toggleable || toggling) return;
    setToggling(true);
    await onToggle(agent.agent_id, !agent.enabled);
    setToggling(false);
  };

  const handleRun = async () => {
    if (running) return;
    setRunning(true);
    await onRunNow(agent.agent_id);
    setTimeout(() => setRunning(false), 2000);
  };

  return (
    <div className={clsx(
      'border rounded-xl overflow-hidden transition-all',
      agent.enabled ? 'border-gray-200 bg-white shadow-sm' : 'border-gray-100 bg-gray-50 opacity-75',
    )}>
      {/* Header bar */}
      <div className={clsx('h-1 bg-gradient-to-r', meta.gradient)} />

      <div className="p-4">
        {/* Top row: name + controls */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-2xl flex-shrink-0">{meta.emoji}</span>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="font-semibold text-gray-900 truncate">{agent.agent_name}</h3>
                <StatusDot status={agent.status} />
                <HealthBadge health={agent.health} />
              </div>
              <p className="text-xs text-gray-500 mt-0.5 truncate">{agent.description}</p>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            {/* Run Now */}
            <button
              onClick={handleRun}
              disabled={!agent.enabled || running}
              title="Force run now"
              className={clsx(
                'p-1.5 rounded-lg transition',
                agent.enabled
                  ? 'hover:bg-blue-50 text-blue-600 cursor-pointer'
                  : 'text-gray-700 cursor-not-allowed',
              )}
            >
              {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <PlayCircle className="w-4 h-4" />}
            </button>

            {/* Toggle */}
            <button
              onClick={handleToggle}
              disabled={!agent.toggleable || toggling}
              title={agent.toggleable ? (agent.enabled ? 'Disable agent' : 'Enable agent') : 'Core agent — always on'}
              className={clsx(
                'relative w-10 h-5 rounded-full transition-colors',
                agent.toggleable ? 'cursor-pointer' : 'cursor-not-allowed opacity-60',
                agent.enabled ? 'bg-green-500' : 'bg-gray-300',
              )}
            >
              <span className={clsx(
                'absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform',
                agent.enabled ? 'translate-x-5' : 'translate-x-0.5',
              )} />
              {!agent.toggleable && (
                <Shield className="absolute -top-1 -right-1 w-3 h-3 text-purple-500" />
              )}
            </button>
          </div>
        </div>

        {/* Stats row */}
        <div className="mt-3 flex items-center gap-4 text-xs text-gray-500">
          <span className="flex items-center gap-1">
            <Zap className="w-3 h-3" />
            {agent.run_count} runs
          </span>
          {agent.error_count > 0 && (
            <span className="flex items-center gap-1 text-red-500">
              <XCircle className="w-3 h-3" />
              {agent.error_count} errors
            </span>
          )}
          <span className="flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {agent.schedule_type === 'interval'
              ? `Every ${agent.schedule_value}s`
              : agent.schedule_type === 'cron'
              ? `Cron: ${agent.schedule_value}`
              : 'Event-driven'}
          </span>
          <span className="flex items-center gap-1">
            Last: {formatTimestamp(agent.last_run)}
          </span>
          {agent.avg_run_time_ms > 0 && (
            <span>{Math.round(agent.avg_run_time_ms)}ms avg</span>
          )}
        </div>

        {/* Last error */}
        {agent.last_error && (
          <div className="mt-2 px-2 py-1 bg-red-50 border border-red-100 rounded text-xs text-red-600 truncate">
            {agent.last_error}
          </div>
        )}

        {/* Expand/collapse */}
        <button
          onClick={() => onExpand(agent.agent_id)}
          className="mt-2 flex items-center gap-1 text-xs text-gray-500 hover:text-gray-600 transition"
        >
          {isExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          {isExpanded ? 'Hide logs' : 'Show logs'}
        </button>

        {/* Expanded: recent logs */}
        {isExpanded && (
          <div className="mt-3 border-t pt-3">
            <h4 className="text-xs font-medium text-gray-600 mb-2">Recent Activity</h4>
            {logsLoading ? (
              <div className="flex justify-center py-4"><Loader2 className="w-5 h-5 animate-spin text-gray-500" /></div>
            ) : logs.length === 0 ? (
              <p className="text-xs text-gray-500 text-center py-3">No activity yet</p>
            ) : (
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {logs.map((log, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs">
                    <span className="text-gray-500 flex-shrink-0 w-20">{formatTimestamp(log.timestamp)}</span>
                    <span className="font-medium text-gray-700">{log.action}</span>
                    {log.details && (
                      <span className="text-gray-500 truncate">
                        {typeof log.details === 'object' ? JSON.stringify(log.details).slice(0, 80) : String(log.details)}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export function AgentControlPanel() {
  const [agents, setAgents] = useState<AgentData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const [agentLogs, setAgentLogs] = useState<Record<string, AgentLogEntry[]>>({});
  const [logsLoading, setLogsLoading] = useState<Record<string, boolean>>({});
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [showTimeline, setShowTimeline] = useState(false);
  const [healthScore, setHealthScore] = useState<number | null>(null);

  // Fetch all agents
  const fetchAgents = useCallback(async () => {
    try {
      setError(null);
      const res = await api.get('/jarvis/agents');
      setAgents(res.data.agents || []);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to load agents';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch timeline
  const fetchTimeline = useCallback(async () => {
    try {
      const res = await api.get('/jarvis/agents/timeline', { params: { limit: 30 } });
      setTimeline(res.data.timeline || []);
    } catch {
      // Non-critical
    }
  }, []);

  // Fetch health history for score
  const fetchHealthScore = useCallback(async () => {
    try {
      const res = await api.get('/jarvis/agents/health-history', { params: { hours: 1 } });
      const history = res.data.history || [];
      if (history.length > 0) {
        setHealthScore(history[0].score);
      }
    } catch {
      // Non-critical
    }
  }, []);

  useEffect(() => {
    fetchAgents();
    fetchTimeline();
    fetchHealthScore();
    // Auto-refresh every 30 seconds
    const interval = setInterval(() => {
      fetchAgents();
      fetchHealthScore();
    }, 30000);
    return () => clearInterval(interval);
  }, [fetchAgents, fetchTimeline, fetchHealthScore]);

  // Toggle agent
  const handleToggle = async (agentId: string, enabled: boolean) => {
    try {
      await api.patch(`/jarvis/agents/${agentId}/toggle`, { enabled });
      setAgents(prev => prev.map(a =>
        a.agent_id === agentId ? { ...a, enabled } : a
      ));
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Toggle failed';
      setError(msg);
    }
  };

  // Force run agent
  const handleRunNow = async (agentId: string) => {
    try {
      await api.post(`/jarvis/agents/${agentId}/run-now`);
      // Refresh after a brief delay
      setTimeout(fetchAgents, 1500);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Run failed';
      setError(msg);
    }
  };

  // Expand agent → fetch logs
  const handleExpand = async (agentId: string) => {
    if (expandedAgent === agentId) {
      setExpandedAgent(null);
      return;
    }
    setExpandedAgent(agentId);

    if (!agentLogs[agentId]) {
      setLogsLoading(prev => ({ ...prev, [agentId]: true }));
      try {
        const res = await api.get(`/jarvis/agents/${agentId}/logs`, { params: { limit: 15 } });
        setAgentLogs(prev => ({ ...prev, [agentId]: res.data.logs || [] }));
      } catch {
        setAgentLogs(prev => ({ ...prev, [agentId]: [] }));
      } finally {
        setLogsLoading(prev => ({ ...prev, [agentId]: false }));
      }
    }
  };

  // Summary stats
  const enabledCount = agents.filter(a => a.enabled).length;
  const healthyCount = agents.filter(a => a.health === 'healthy').length;
  const totalRuns = agents.reduce((sum, a) => sum + a.run_count, 0);
  const totalErrors = agents.reduce((sum, a) => sum + a.error_count, 0);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-gray-500">
        <Loader2 className="w-8 h-8 animate-spin mb-3" />
        <p className="text-sm">Loading agent control panel...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-gradient-to-br from-purple-500 to-indigo-600 rounded-xl shadow-sm">
            <Bot className="w-5 h-5 text-gray-900" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-gray-900">JARVIS Agent Control</h2>
            <p className="text-xs text-gray-500">Manage AI agents — toggle, monitor health, force-run</p>
          </div>
        </div>
        <button
          onClick={() => { setLoading(true); fetchAgents(); fetchTimeline(); fetchHealthScore(); }}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gray-600 hover:text-gray-900 border rounded-lg hover:bg-gray-50 transition"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          {error}
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="px-4 py-3 bg-white border rounded-xl">
          <p className="text-xs text-gray-500">Agents</p>
          <p className="text-xl font-bold text-gray-900">{enabledCount}<span className="text-sm font-normal text-gray-500">/{agents.length}</span></p>
          <p className="text-xs text-gray-500">active</p>
        </div>
        <div className="px-4 py-3 bg-white border rounded-xl">
          <p className="text-xs text-gray-500">Health</p>
          <div className="flex items-center gap-2">
            <Heart className={clsx('w-5 h-5', healthScore !== null && healthScore >= 80 ? 'text-green-500' : healthScore !== null && healthScore >= 50 ? 'text-amber-500' : 'text-gray-700')} />
            <p className="text-xl font-bold text-gray-900">{healthScore !== null ? `${healthScore}` : '—'}</p>
          </div>
          <p className="text-xs text-gray-500">{healthyCount} healthy</p>
        </div>
        <div className="px-4 py-3 bg-white border rounded-xl">
          <p className="text-xs text-gray-500">Total Runs</p>
          <p className="text-xl font-bold text-gray-900">{totalRuns.toLocaleString()}</p>
          <p className="text-xs text-gray-500">background ticks</p>
        </div>
        <div className="px-4 py-3 bg-white border rounded-xl">
          <p className="text-xs text-gray-500">Errors</p>
          <p className={clsx('text-xl font-bold', totalErrors > 0 ? 'text-red-600' : 'text-gray-900')}>{totalErrors}</p>
          <p className="text-xs text-gray-500">{totalErrors === 0 ? 'clean' : 'needs attention'}</p>
        </div>
      </div>

      {/* Agent cards */}
      <div className="space-y-3">
        {agents.map(agent => (
          <AgentCard
            key={agent.agent_id}
            agent={agent}
            onToggle={handleToggle}
            onRunNow={handleRunNow}
            onExpand={handleExpand}
            isExpanded={expandedAgent === agent.agent_id}
            logs={agentLogs[agent.agent_id] || []}
            logsLoading={logsLoading[agent.agent_id] || false}
          />
        ))}
        {agents.length === 0 && !error && (
          <div className="text-center py-12 text-gray-500">
            <Bot className="w-10 h-10 mx-auto mb-3 opacity-40" />
            <p className="text-sm">No agents registered yet</p>
            <p className="text-xs mt-1">Agents will appear here once the system initializes</p>
          </div>
        )}
      </div>

      {/* Timeline toggle */}
      <div className="border rounded-xl bg-white overflow-hidden">
        <button
          onClick={() => setShowTimeline(!showTimeline)}
          className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-gray-700 hover:bg-gray-50 transition"
        >
          <span className="flex items-center gap-2">
            <Activity className="w-4 h-4" />
            Activity Timeline
            <span className="text-xs text-gray-500">({timeline.length} events)</span>
          </span>
          {showTimeline ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>

        {showTimeline && (
          <div className="border-t px-4 py-3 max-h-64 overflow-y-auto">
            {timeline.length === 0 ? (
              <p className="text-xs text-gray-500 text-center py-4">No activity recorded yet</p>
            ) : (
              <div className="space-y-2">
                {timeline.map((entry, i) => {
                  const meta = getHeroMeta(entry.agent_id);
                  return (
                    <div key={i} className="flex items-start gap-3 text-xs">
                      <span className="text-gray-500 flex-shrink-0 w-20">{formatTimestamp(entry.timestamp)}</span>
                      <span>{meta.emoji}</span>
                      <span className="font-medium text-gray-700">{entry.agent_id}</span>
                      <span className="text-gray-500">{entry.action}</span>
                      {entry.details && (
                        <span className="text-gray-500 truncate max-w-xs">
                          {JSON.stringify(entry.details).slice(0, 60)}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
