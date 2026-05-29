import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { analyticsApi, clinicalApi } from '../../services/api';
import { tasksApi } from '../../services/api/hr';
import './HubPage.css';
import {
  ShoppingCart, Package, Users, BarChart3, Stethoscope, Crown,
  ClipboardList, DollarSign, Megaphone, ChevronRight,
  Settings as SettingsIcon, FileText, Wrench, Send,
  AlertTriangle, ListChecks, Clock, CheckCircle2
} from 'lucide-react';
import { HandoffInboxCard } from '../../components/handoffs/HandoffInboxCard';
import { HandoffUploadModal } from '../../components/handoffs/HandoffUploadModal';
import { DashboardNotifications } from '../../components/notifications/DashboardNotifications';

interface ModuleCard {
  title: string;
  description: string;
  icon: React.ReactNode;
  path: string;
  roles?: string[];
  accent?: string;
}

interface PriorityTask {
  id: string;
  title: string;
  priority: string;
  due_at?: string | null;
  status?: string;
}

interface SopTemplate {
  template_id: string;
  title: string;
  category?: string;
  frequency?: string;
  estimated_time?: number;
}

// Priority rank for sorting (P0 = most urgent).
const PRIORITY_RANK: Record<string, number> = { P0: 0, P1: 1, P2: 2, P3: 3, P4: 4 };

// Human label for a due timestamp. Guards against absurd/missing values
// (bad stored due_at can otherwise render "4y left") -> returns '' so the
// chip is simply hidden rather than showing a nonsense countdown.
function dueLabel(dueAt?: string | null): { text: string; overdue: boolean } | null {
  if (!dueAt) return null;
  const due = new Date(dueAt).getTime();
  if (Number.isNaN(due)) return null;
  const now = Date.now();
  const diffMin = Math.round((due - now) / 60000);
  // Clamp out clearly-bad data: anything more than ~120 days out is treated
  // as unset (SLA windows top out at 1 week; months/years = corrupt data).
  if (diffMin > 120 * 24 * 60) return null;
  if (diffMin < 0) {
    const overdueMin = -diffMin;
    if (overdueMin < 60) return { text: `${overdueMin}m overdue`, overdue: true };
    if (overdueMin < 24 * 60) return { text: `${Math.round(overdueMin / 60)}h overdue`, overdue: true };
    return { text: `${Math.round(overdueMin / (60 * 24))}d overdue`, overdue: true };
  }
  if (diffMin < 60) return { text: `${diffMin}m left`, overdue: false };
  if (diffMin < 24 * 60) return { text: `${Math.round(diffMin / 60)}h left`, overdue: false };
  return { text: `${Math.round(diffMin / (60 * 24))}d left`, overdue: false };
}

export function HubPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [greeting, setGreeting] = useState('');
  const [stats, setStats] = useState({
    todaySales: 0,
    pendingTasks: 0,
    queueCount: 0,
  });
  const [priorityTasks, setPriorityTasks] = useState<PriorityTask[]>([]);
  const [sopTemplates, setSopTemplates] = useState<SopTemplate[]>([]);
  const [loadingWork, setLoadingWork] = useState(true);
  const [showSendFile, setShowSendFile] = useState(false);

  useEffect(() => {
    const hour = new Date().getHours();
    if (hour < 12) setGreeting('Good morning');
    else if (hour < 17) setGreeting('Good afternoon');
    else setGreeting('Good evening');
  }, []);

  useEffect(() => {
    loadStats();
    loadWork();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.activeStoreId]);

  const loadStats = async () => {
    try {
      const storeId = user?.activeStoreId;
      const [salesRes, tasksRes, queueRes] = await Promise.allSettled([
        analyticsApi.getDashboard(storeId ? { store_id: storeId } : undefined),
        tasksApi.getTasks(storeId ? { store_id: storeId, status: 'OPEN' } : { status: 'OPEN' }),
        clinicalApi.getQueue(storeId),
      ]);

      if (salesRes.status === 'fulfilled') {
        const data: any = salesRes.value;
        setStats((s) => ({
          ...s,
          todaySales: data?.today_sales ?? data?.todaySales ?? 0,
        }));
      }
      if (tasksRes.status === 'fulfilled') {
        const data: any = tasksRes.value;
        const list = Array.isArray(data) ? data : data?.tasks ?? [];
        setStats((s) => ({ ...s, pendingTasks: list.length }));
      }
      if (queueRes.status === 'fulfilled') {
        const data: any = queueRes.value;
        const list = Array.isArray(data) ? data : data?.queue ?? data?.patients ?? [];
        setStats((s) => ({ ...s, queueCount: list.length }));
      }
    } catch (err) {
      console.error('Failed to load stats:', err);
    }
  };

  // Priority work: top open tasks (most-urgent first) + today's SOP checklists.
  const loadWork = async () => {
    setLoadingWork(true);
    const storeId = user?.activeStoreId;
    const [tasksRes, sopRes] = await Promise.allSettled([
      tasksApi.getTasks(storeId ? { store_id: storeId, status: 'OPEN' } : { status: 'OPEN' }),
      tasksApi.getSopTemplates(storeId ? { storeId, activeOnly: true } : { activeOnly: true }),
    ]);

    if (tasksRes.status === 'fulfilled') {
      const data: any = tasksRes.value;
      const list: any[] = Array.isArray(data) ? data : data?.tasks ?? [];
      const mapped: PriorityTask[] = list.map((t) => ({
        id: t.id ?? t.task_id ?? '',
        title: t.title ?? 'Untitled task',
        priority: (t.priority ?? 'P3').toUpperCase(),
        due_at: t.due_at ?? null,
        status: t.status,
      }));
      mapped.sort((a, b) => {
        const ra = PRIORITY_RANK[a.priority] ?? 9;
        const rb = PRIORITY_RANK[b.priority] ?? 9;
        if (ra !== rb) return ra - rb;
        // within a priority, soonest due first (nulls last)
        const da = a.due_at ? new Date(a.due_at).getTime() : Infinity;
        const db = b.due_at ? new Date(b.due_at).getTime() : Infinity;
        return da - db;
      });
      setPriorityTasks(mapped.slice(0, 5));
    } else {
      setPriorityTasks([]);
    }

    if (sopRes.status === 'fulfilled') {
      const data: any = sopRes.value;
      const list: any[] = data?.templates ?? (Array.isArray(data) ? data : []);
      setSopTemplates(
        list.slice(0, 5).map((s) => ({
          template_id: s.template_id ?? s.id ?? '',
          title: s.title ?? 'Checklist',
          category: s.category,
          frequency: s.frequency,
          estimated_time: s.estimated_time,
        }))
      );
    } else {
      setSopTemplates([]);
    }

    setLoadingWork(false);
  };

  const modules: ModuleCard[] = [
    {
      title: 'Point of Sale',
      description: 'Checkout, billing, returns',
      icon: <ShoppingCart size={20} />,
      path: '/pos',
      accent: 'var(--accent-blue)',
    },
    {
      title: 'Inventory',
      description: 'Stock, products, transfers',
      icon: <Package size={20} />,
      path: '/inventory',
      accent: 'var(--accent-green)',
    },
    {
      title: 'Customers',
      description: 'CRM, history, loyalty',
      icon: <Users size={20} />,
      path: '/customers',
      accent: 'var(--accent-purple)',
    },
    {
      title: 'Analytics',
      description: 'Sales, trends, reports',
      icon: <BarChart3 size={20} />,
      path: '/analytics',
      accent: 'var(--accent-orange)',
    },
    {
      title: 'Clinical',
      description: 'Exams, queue, Rx',
      icon: <Stethoscope size={20} />,
      path: '/clinical',
      accent: 'var(--accent-teal)',
    },
    {
      title: 'Workshop',
      description: 'Lens fitting, QC, jobs',
      icon: <Wrench size={20} />,
      path: '/workshop',
      accent: 'var(--accent-amber)',
    },
    {
      title: 'Tasks',
      description: 'Assignments, SOPs',
      icon: <ClipboardList size={20} />,
      path: '/tasks',
      accent: 'var(--accent-indigo)',
    },
    {
      title: 'Finance',
      description: 'P&L, GST, AR/AP',
      icon: <DollarSign size={20} />,
      path: '/finance',
      accent: 'var(--accent-emerald)',
    },
    {
      title: 'Marketing',
      description: 'Campaigns, referrals',
      icon: <Megaphone size={20} />,
      path: '/marketing',
      accent: 'var(--accent-pink)',
    },
  ];

  const adminModules: ModuleCard[] = [
    {
      title: 'Organization',
      description: 'Entities, stores, GSTINs',
      icon: <SettingsIcon size={20} />,
      path: '/organization',
      roles: ['ADMIN', 'SUPERADMIN'],
    },
    {
      title: 'Reports',
      description: 'Exports, statements',
      icon: <FileText size={20} />,
      path: '/reports',
      roles: ['ADMIN', 'SUPERADMIN', 'ACCOUNTANT'],
    },
    {
      title: 'AI Agents',
      description: 'Jarvis suite',
      icon: <Crown size={20} />,
      path: '/ai',
      roles: ['SUPERADMIN'],
    },
  ];

  const visibleAdmin = adminModules.filter(
    (m) => !m.roles || m.roles.some((r) => user?.roles?.includes(r as any))
  );

  const displayName = user?.full_name?.split(' ')[0] || user?.username || 'there';
  const today = new Date().toLocaleDateString('en-IN', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  });

  return (
    <div className="hub-page">
      {/* Slim operational header */}
      <header className="hub-header">
        <div>
          <p className="hub-eyebrow">{greeting}, {displayName}</p>
          <p className="hub-header-date">{today}</p>
        </div>
        <button className="hub-send-btn" onClick={() => setShowSendFile(true)}>
          <Send size={16} /> Send a file
        </button>
      </header>

      {/* Live snapshot */}
      <section className="hub-meta-grid">
        <button className="hub-meta-card" onClick={() => navigate('/analytics')}>
          <span className="hub-meta-label">Today's sales</span>
          <span className="hub-meta-value">₹{stats.todaySales.toLocaleString('en-IN')}</span>
        </button>
        <button className="hub-meta-card" onClick={() => navigate('/tasks')}>
          <span className="hub-meta-label">Open tasks</span>
          <span className="hub-meta-value">{stats.pendingTasks}</span>
        </button>
        <button className="hub-meta-card" onClick={() => navigate('/clinical')}>
          <span className="hub-meta-label">In queue</span>
          <span className="hub-meta-value">{stats.queueCount}</span>
        </button>
      </section>

      {/* Priority work: tasks + SOP checklists */}
      <section className="hub-work-row">
        {/* Priority tasks */}
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

        {/* SOP checklists */}
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
                  <button
                    className="hub-sop-row"
                    onClick={() => navigate('/tasks/checklists')}
                  >
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

      {/* Notifications + handoffs */}
      <section className="hub-ops-row">
        <DashboardNotifications />
        <HandoffInboxCard />
      </section>

      {/* Module grid */}
      <section className="hub-modules">
        <div className="hub-section-head">
          <h2>Modules</h2>
        </div>
        <div className="hub-module-grid">
          {modules.map((m) => (
            <button
              key={m.path}
              className="hub-module-card"
              onClick={() => navigate(m.path)}
            >
              <span className="hub-module-icon" style={{ color: m.accent }}>
                {m.icon}
              </span>
              <span className="hub-module-text">
                <span className="hub-module-title">{m.title}</span>
                <span className="hub-module-desc">{m.description}</span>
              </span>
              <ChevronRight size={16} className="hub-module-arrow" />
            </button>
          ))}
          {visibleAdmin.map((m) => (
            <button
              key={m.path}
              className="hub-module-card"
              onClick={() => navigate(m.path)}
            >
              <span className="hub-module-icon" style={{ color: m.accent }}>
                {m.icon}
              </span>
              <span className="hub-module-text">
                <span className="hub-module-title">{m.title}</span>
                <span className="hub-module-desc">{m.description}</span>
              </span>
              <ChevronRight size={16} className="hub-module-arrow" />
            </button>
          ))}
        </div>
      </section>

      {showSendFile && (
        <HandoffUploadModal onClose={() => setShowSendFile(false)} />
      )}
    </div>
  );
}

export default HubPage;
