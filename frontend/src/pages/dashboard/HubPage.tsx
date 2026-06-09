// ============================================================================
// IMS 2.0 — Hub (landing page / launcher)
// Operational, not marketing: greeting + live snapshot + priority work
// (tasks + SOP checklists) + notifications + handoff inbox + module grid.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Send, AlertTriangle, ListChecks, Clock, CheckCircle2, ChevronRight } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { Icon } from '../../components/shell';
import { analyticsApi, tasksApi, clinicalApi } from '../../services/api';
import HandoffInboxCard from '../../components/handoffs/HandoffInboxCard';
import ClinicalHandoverCard from '../../components/handoffs/ClinicalHandoverCard';
import HandoffUploadModal from '../../components/handoffs/HandoffUploadModal';
import DashboardNotifications from '../../components/notifications/DashboardNotifications';
import OwnerDigestCard from '../../components/dashboard/OwnerDigestCard';
import TickerCard from '../../components/hub/TickerCard';
import './HubPage.css';

interface HeroMeta {
  salesToday: string;
  salesDelta: string;
  openTasks: string;
  tasksDetail: string;
  queue: string;
  queueDetail: string;
}

interface PriorityTask {
  id: string;
  title: string;
  priority: string;
  due_at?: string | null;
}

interface SopTemplate {
  template_id: string;
  title: string;
  frequency?: string;
  estimated_time?: number;
}

const INR = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

// Priority rank for sorting (P0 = most urgent).
const PRIORITY_RANK: Record<string, number> = { P0: 0, P1: 1, P2: 2, P3: 3, P4: 4 };

function formatINR(amount: number | undefined | null): string {
  if (!amount && amount !== 0) return '—';
  if (amount >= 100000) return `₹ ${(amount / 100000).toFixed(1)}L`;
  return `₹ ${INR.format(Math.round(amount))}`;
}

function humanDate(d: Date = new Date()): string {
  return d.toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
}

// Human label for a due timestamp. Guards against absurd/missing values
// (bad stored due_at can otherwise render "4y left") -> returns null so the
// chip is simply hidden rather than showing a nonsense countdown.
function dueLabel(dueAt?: string | null): { text: string; overdue: boolean } | null {
  if (!dueAt) return null;
  const due = new Date(dueAt).getTime();
  if (Number.isNaN(due)) return null;
  const diffMin = Math.round((due - Date.now()) / 60000);
  // Clamp out clearly-bad data: anything more than ~120 days out is treated
  // as unset (SLA windows top out at 1 week; months/years = corrupt data).
  if (diffMin > 120 * 24 * 60) return null;
  if (diffMin < 0) {
    const od = -diffMin;
    if (od < 60) return { text: `${od}m overdue`, overdue: true };
    if (od < 24 * 60) return { text: `${Math.round(od / 60)}h overdue`, overdue: true };
    return { text: `${Math.round(od / (60 * 24))}d overdue`, overdue: true };
  }
  if (diffMin < 60) return { text: `${diffMin}m left`, overdue: false };
  if (diffMin < 24 * 60) return { text: `${Math.round(diffMin / 60)}h left`, overdue: false };
  return { text: `${Math.round(diffMin / (60 * 24))}d left`, overdue: false };
}

interface ModuleCard {
  id: string;
  to: string;
  title: string;
  eyebrow: string;
  desc: string;
  iconName: keyof typeof Icon;
  meta: Array<[string, string]>;
  feature?: boolean;
  badge?: string;
  requireRoles?: string[]; // hide card unless user holds one of these
}

export default function HubPage() {
  const { user, hasRole } = useAuth();
  const navigate = useNavigate();
  const [meta, setMeta] = useState<HeroMeta>({
    salesToday: '—',
    salesDelta: '',
    openTasks: '—',
    tasksDetail: '',
    queue: '—',
    queueDetail: '',
  });

  // Priority work surfaced above the fold.
  const [priorityTasks, setPriorityTasks] = useState<PriorityTask[]>([]);
  const [sopTemplates, setSopTemplates] = useState<SopTemplate[]>([]);
  const [loadingWork, setLoadingWork] = useState(true);

  // Handoff feature state — modal visibility + a tick that the inbox
  // card listens to for a fresh refetch after a successful send.
  const [showSendFile, setShowSendFile] = useState(false);
  const [inboxRefreshKey, setInboxRefreshKey] = useState(0);

  // Fire-and-forget — resilient to 404/500; show placeholders on failure.
  useEffect(() => {
    let cancelled = false;

    analyticsApi
      .getDashboardSummary('today', user?.activeStoreId)
      .then((r: any) => {
        if (cancelled) return;
        const rev = r?.revenue?.total ?? r?.total_revenue ?? r?.revenue;
        const change = r?.revenue?.change_percent ?? r?.change_percent;
        setMeta((m) => ({
          ...m,
          salesToday: rev != null ? formatINR(rev) : '—',
          salesDelta:
            typeof change === 'number'
              ? `${change > 0 ? '+' : ''}${change.toFixed(1)}% vs 4-wk avg`
              : '',
        }));
      })
      .catch(() => {});

    tasksApi
      .getTaskSummary(user?.activeStoreId)
      .then((r: any) => {
        if (cancelled) return;
        const open = r?.open ?? r?.total_open ?? r?.counts?.open;
        const overdue = r?.overdue ?? r?.counts?.overdue ?? 0;
        const p1 = r?.p1 ?? r?.counts?.p1 ?? r?.high_priority ?? 0;
        setMeta((m) => ({
          ...m,
          openTasks: open != null ? String(open) : '—',
          tasksDetail:
            overdue || p1
              ? `${overdue || 0} overdue · ${p1 || 0} P1`
              : '',
        }));
      })
      .catch(() => {});

    if (user?.activeStoreId) {
      clinicalApi
        .getQueue(user.activeStoreId)
        .then((r: any) => {
          if (cancelled) return;
          const list: any[] = Array.isArray(r) ? r : r?.queue ?? [];
          const waiting = list.filter((q) => q?.status === 'Waiting' || q?.status === 'waiting').length;
          const inExam = list.filter((q) => q?.status === 'In exam' || q?.status === 'in_exam').length;
          setMeta((m) => ({
            ...m,
            queue: String(waiting || list.length || 0),
            queueDetail: inExam ? `${inExam} in exam` : '',
          }));
        })
        .catch(() => {});
    }

    // Priority work: top open tasks (most-urgent first) + SOP checklists.
    setLoadingWork(true);
    Promise.allSettled([
      tasksApi.getTasks(
        user?.activeStoreId
          ? { store_id: user.activeStoreId, status: 'OPEN' }
          : { status: 'OPEN' }
      ),
      tasksApi.getSopTemplates(
        user?.activeStoreId
          ? { storeId: user.activeStoreId, activeOnly: true }
          : { activeOnly: true }
      ),
    ]).then(([tRes, sRes]) => {
      if (cancelled) return;
      if (tRes.status === 'fulfilled') {
        const data: any = tRes.value;
        const list: any[] = Array.isArray(data) ? data : data?.tasks ?? [];
        const mapped: PriorityTask[] = list.map((t) => ({
          id: t.id ?? t.task_id ?? '',
          title: t.title ?? 'Untitled task',
          priority: String(t.priority ?? 'P3').toUpperCase(),
          due_at: t.due_at ?? null,
        }));
        mapped.sort((a, b) => {
          const ra = PRIORITY_RANK[a.priority] ?? 9;
          const rb = PRIORITY_RANK[b.priority] ?? 9;
          if (ra !== rb) return ra - rb;
          const da = a.due_at ? new Date(a.due_at).getTime() : Infinity;
          const db = b.due_at ? new Date(b.due_at).getTime() : Infinity;
          return da - db;
        });
        setPriorityTasks(mapped.slice(0, 5));
      }
      if (sRes.status === 'fulfilled') {
        const data: any = sRes.value;
        const list: any[] = data?.templates ?? (Array.isArray(data) ? data : []);
        setSopTemplates(
          list.slice(0, 5).map((s: any) => ({
            template_id: s.template_id ?? s.id ?? '',
            title: s.title ?? 'Checklist',
            frequency: s.frequency,
            estimated_time: s.estimated_time,
          }))
        );
      }
      setLoadingWork(false);
    });

    return () => {
      cancelled = true;
    };
  }, [user?.activeStoreId]);

  const today = useMemo(() => humanDate(), []);
  const firstName = (user?.name ?? '').split(/\s+/)[0] ?? '';

  const modules: ModuleCard[] = [
    {
      id: 'pos',
      to: '/pos',
      title: 'POS',
      eyebrow: 'Checkout',
      desc: 'Guided checkout with Rx intake, split payments, hold & recall, overall discount, and printable invoice + workshop handoff.',
      iconName: 'cart',
      meta: [['Today', meta.salesToday], ['Queue', meta.queue], ['Tasks', meta.openTasks]],
      feature: true,
      badge: 'Most used',
    },
    {
      id: 'clinical',
      to: '/clinical',
      title: 'Clinical',
      eyebrow: 'Eye exam',
      desc: 'Optometrist queue, A5 Rx card, refraction form, handoff to POS with family & external-doctor flags.',
      iconName: 'eye',
      meta: [['Queue', meta.queue], ['Detail', meta.queueDetail || '—']],
      feature: true,
    },
    {
      id: 'inventory',
      to: '/inventory',
      title: 'Inventory',
      eyebrow: 'Stock',
      desc: 'Live stock by SKU, lens power matrix, cycle count, non-moving flags, inter-store transfer.',
      iconName: 'box',
      meta: [['Browse', 'SKU + stock']],
    },
    {
      id: 'tasks',
      to: '/tasks',
      title: 'Tasks & SOPs',
      eyebrow: 'Ops',
      desc: 'P0–P4 priorities with countdown timers and auto-escalation tied to SOPs.',
      iconName: 'check',
      meta: [['Open', meta.openTasks], ['Status', meta.tasksDetail || 'See board']],
    },
    {
      id: 'reports',
      to: '/reports',
      title: 'Reports',
      eyebrow: 'Analytics',
      desc: 'Day-end close, MoM & YoY trends, sell-through by category, aging cohorts.',
      iconName: 'chart',
      meta: [['Today', meta.salesToday], ['Trend', meta.salesDelta || '—']],
    },
    {
      id: 'customers',
      to: '/customers',
      title: 'Customers',
      eyebrow: 'CRM',
      desc: 'Customer 360, segmentation, loyalty tiers, referrals, NPS, and prescription family view.',
      iconName: 'users',
      meta: [['360', 'profile view'], ['Follow-ups', 'Rx reminders']],
    },
    {
      id: 'jarvis',
      to: '/jarvis',
      title: 'Jarvis',
      eyebrow: 'Automation · Superadmin',
      desc: '8 agents (JARVIS, CORTEX, SENTINEL, PIXEL, MEGAPHONE, ORACLE, TASKMASTER, NEXUS) watching stock, pricing, escalations, Rx.',
      iconName: 'cpu',
      // Canonical 8 agents — the live count is fetched by the Jarvis
      // page itself (not here) so the Hub card just states the roster
      // size. If the Hub ever wires a live count, prefer "N/8 live"
      // from /api/v1/jarvis/agents/diagnostic (single source of truth).
      meta: [['Agents', '8 total'], ['Scope', 'Superadmin only']],
      badge: 'Super-admin',
      requireRoles: ['SUPERADMIN'],
    },
    {
      id: 'setup',
      to: '/settings',
      title: 'Store Setup',
      eyebrow: 'Configuration',
      desc: 'Deep feature toggles, GST & billing, print templates, role matrix, audit log.',
      iconName: 'settings',
      meta: [['Toggles', 'Feature gates'], ['Store', user?.activeStoreId ?? '—']],
    },
  ];

  const visibleModules = modules.filter((m) => {
    if (!m.requireRoles) return true;
    return m.requireRoles.some((r) => hasRole([r as any]));
  });

  return (
    <div className="hub-bg">
      <section className="hub-hero">
        <div>
          <div className="eyebrow">Better Vision · IMS 2.0 · {today}</div>
          <h1 className="hub-h1 hub-h1-compact">
            {firstName ? <>Welcome back, <em>{firstName}</em>.</> : 'Welcome back.'}
          </h1>
          <p className="hub-sub">Here's what needs you today.</p>
          <div style={{ marginTop: 14 }}>
            <button
              type="button"
              onClick={() => setShowSendFile(true)}
              className="btn sm"
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
            >
              <Send className="w-3.5 h-3.5" />
              Send a file
            </button>
          </div>
        </div>
        <div className="hub-meta-grid">
          <div>
            <div className="k">Sales · Today</div>
            <div className="v figure">{meta.salesToday}</div>
            {meta.salesDelta && <div className="sm">{meta.salesDelta}</div>}
          </div>
          <div>
            <div className="k">Open Tasks</div>
            <div className="v figure">{meta.openTasks}</div>
            {meta.tasksDetail && <div className="sm">{meta.tasksDetail}</div>}
          </div>
          <div>
            <div className="k">Queue</div>
            <div className="v figure">
              {meta.queue}
              {meta.queueDetail && <span className="hub-meta-sub"> / {meta.queueDetail}</span>}
            </div>
            <div className="sm">Clinical</div>
          </div>
        </div>
      </section>

      {/* F34 monthly-target ticker — shown to EVERY role; the view is
          privacy-stratified server-side (floor staff see % only, no rupees). */}
      <section style={{ marginBottom: 18 }}>
        <TickerCard />
      </section>

      {/* Owner digest — day-close snapshot, SUPERADMIN / ADMIN only. */}
      {hasRole(['SUPERADMIN', 'ADMIN']) && (
        <section style={{ marginBottom: 18 }}>
          <OwnerDigestCard storeId={user?.activeStoreId} />
        </section>
      )}

      {/* Priority work: tasks + SOP checklists */}
      <section className="hub-work-row">
        <div className="hub-work-card">
          <div className="hub-work-head">
            <span className="hub-work-title">
              <AlertTriangle size={16} /> Priority tasks
            </span>
            <button className="hub-work-link" onClick={() => navigate('/tasks')}>
              View all <ChevronRight size={14} />
            </button>
          </div>
          {loadingWork ? (
            <p className="hub-work-empty">Loading…</p>
          ) : priorityTasks.length === 0 ? (
            <p className="hub-work-empty">
              <CheckCircle2 size={16} /> Nothing urgent — you're all caught up.
            </p>
          ) : (
            <ul className="hub-task-list">
              {priorityTasks.map((t) => {
                const due = dueLabel(t.due_at);
                return (
                  <li key={t.id || t.title}>
                    <button className="hub-task-row" onClick={() => navigate('/tasks')}>
                      <span className={`hub-prio hub-prio-${t.priority.toLowerCase()}`}>
                        {t.priority}
                      </span>
                      <span className="hub-task-name">{t.title}</span>
                      {due && (
                        <span className={`hub-task-due${due.overdue ? ' is-overdue' : ''}`}>
                          <Clock size={12} /> {due.text}
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="hub-work-card">
          <div className="hub-work-head">
            <span className="hub-work-title">
              <ListChecks size={16} /> Today's checklists
            </span>
            <button className="hub-work-link" onClick={() => navigate('/tasks/checklists')}>
              Open <ChevronRight size={14} />
            </button>
          </div>
          {loadingWork ? (
            <p className="hub-work-empty">Loading…</p>
          ) : sopTemplates.length === 0 ? (
            <p className="hub-work-empty">No checklists assigned to this store yet.</p>
          ) : (
            <ul className="hub-sop-list">
              {sopTemplates.map((s) => (
                <li key={s.template_id || s.title}>
                  <button className="hub-sop-row" onClick={() => navigate('/tasks/checklists')}>
                    <span className="hub-sop-name">{s.title}</span>
                    <span className="hub-sop-meta">
                      {s.frequency ? s.frequency.toLowerCase() : ''}
                      {s.estimated_time ? ` · ${s.estimated_time}m` : ''}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {/* Notifications snapshot — links to the full Notifications screen. */}
      <section style={{ marginBottom: 18 }}>
        <DashboardNotifications />
      </section>

      {/* Handoff inbox — only renders when the user has at least one card. */}
      <HandoffInboxCard refreshKey={inboxRefreshKey} />

      {/* F50: clinical -> retail handovers — only renders when the sales floor
          has at least one active CLINICAL_RX from optometry. */}
      <ClinicalHandoverCard refreshKey={inboxRefreshKey} />

      <section className="modules">
        <div className="section-head">
          <span className="eyebrow">Workspaces</span>
          <span className="line" />
          <span className="sub">{visibleModules.length} modules · role-gated</span>
        </div>

        {visibleModules.map((m) => {
          const IconCmp = Icon[m.iconName];
          return (
            <Link
              key={m.id}
              to={m.to}
              className={'mod-card' + (m.feature ? ' feature' : '')}
            >
              {m.badge && <span className="mod-badge">{m.badge}</span>}
              <div className="mod-head">
                <div className="mod-icon">
                  <IconCmp />
                </div>
              </div>
              <div className="eyebrow">{m.eyebrow}</div>
              <h3 className="mod-title">{m.title}</h3>
              <p className="mod-desc">{m.desc}</p>
              <div className="mod-meta">
                {m.meta.map(([k, v]) => (
                  <div key={k}>
                    <span>{k}</span>
                    <strong>{v}</strong>
                  </div>
                ))}
              </div>
            </Link>
          );
        })}
      </section>

      {/* Send-file (handoff upload) modal — owned by the Hub so it
          remains accessible from a single, predictable entry point. */}
      <HandoffUploadModal
        isOpen={showSendFile}
        onClose={() => setShowSendFile(false)}
        onSent={() => {
          // Bumping the refresh key triggers a refetch in the inbox card.
          setInboxRefreshKey((k) => k + 1);
        }}
      />
    </div>
  );
}

export { HubPage };
