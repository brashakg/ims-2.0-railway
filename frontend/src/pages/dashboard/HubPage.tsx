// ============================================================================
// IMS 2.0 — Hub (landing page / launcher)
// Ported from docs/design/hub.html. Operational tone, not marketing.
// Hero + meta grid (real data where available) + news strip + module grid.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { Icon } from '../../components/shell';
import { analyticsApi, tasksApi, clinicalApi } from '../../services/api';

interface HeroMeta {
  salesToday: string;
  salesDelta: string;
  openTasks: string;
  tasksDetail: string;
  queue: string;
  queueDetail: string;
}

const INR = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

function formatINR(amount: number | undefined | null): string {
  if (!amount && amount !== 0) return '—';
  if (amount >= 100000) return `₹ ${(amount / 100000).toFixed(1)}L`;
  return `₹ ${INR.format(Math.round(amount))}`;
}

function humanDate(d: Date = new Date()): string {
  return d.toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
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
  const [meta, setMeta] = useState<HeroMeta>({
    salesToday: '—',
    salesDelta: '',
    openTasks: '—',
    tasksDetail: '',
    queue: '—',
    queueDetail: '',
  });

  // Fire-and-forget — resilient to 404/500; show placeholders on failure.
  useEffect(() => {
    let cancelled = false;

    analyticsApi
      .getDashboardSummary('today')
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
      desc: '6-step guided checkout with Rx intake, split payments, hold & recall, and printable invoice + job card.',
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
      meta: [['Agents', '2/8 live'], ['Expand', 'Phase 3']],
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
          <h1 className="hub-h1">
            Run the floor.<br />
            Close the <em>day</em>.
          </h1>
          <p className="hub-sub">
            Every Better Vision store is one shift, one queue, one ledger. IMS 2.0 is the single surface your team opens at 10 AM and closes at 9 PM — checkout, exam, stock, tasks, and the agents that quietly keep everything in line.
            {firstName && <> Welcome back, {firstName}.</>}
          </p>
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
    </div>
  );
}

export { HubPage };
