// ============================================================================
// IMS 2.0 - /tasks/performance
// ============================================================================
// Store-wide task completion. Gated to TEAM_TASK_ROLES (taskRoles.ts) - it is
// the whole store's numbers, so it follows the same ruling as Team tasks.
//
// The four headline counts come from GET /tasks/summary, which counts in the
// database. They used to be derived from whatever tasks the page happened to
// have fetched (the first 50), so a busy store's "Total" was simply 50.
//
// The per-owner ranking is still computed from the rows loaded here, because
// the API has no per-assignee aggregate. That is stated on the card rather
// than hidden - see the caption and the Load more button.

import { AlertTriangle, CheckSquare, Loader2, TrendingUp, Users } from 'lucide-react';
import { useMemo } from 'react';
import { useAuth } from '../../context/AuthContext';
import SystemIntegrityPanel from '../../components/tasks/SystemIntegrityPanel';
import { flattenTasks, useTaskList, useTaskSummary } from './tasksQueries';

interface OwnerRow {
  employeeId: string;
  employeeName: string;
  assigned: number;
  completed: number;
  escalated: number;
  completionRate: number;
}

export function TasksPerformancePage() {
  const { user } = useAuth();
  const storeId = user?.activeStoreId;

  const summaryQ = useTaskSummary(storeId);
  const listQ = useTaskList({});
  const tasks = flattenTasks(listQ.data);

  const s = summaryQ.data?.summary ?? {};
  const total = Number(summaryQ.data?.total ?? 0)
    || Number(s.OPEN ?? 0) + Number(s.IN_PROGRESS ?? 0) + Number(s.COMPLETED ?? 0) + Number(s.ESCALATED ?? 0);
  const completed = Number(s.COMPLETED ?? 0);
  const overdue = Number(summaryQ.data?.overdue_count ?? summaryQ.data?.overdue ?? 0);
  const completionRate = total > 0 ? (completed / total) * 100 : 0;

  const owners = useMemo<OwnerRow[]>(() => {
    const byOwner = new Map<string, OwnerRow>();
    for (const t of tasks) {
      const id = t.assignedTo || '__unassigned__';
      const row = byOwner.get(id) ?? {
        employeeId: id,
        employeeName: t.assignedToName || (id === '__unassigned__' ? 'Unassigned' : id),
        assigned: 0,
        completed: 0,
        escalated: 0,
        completionRate: 0,
      };
      row.assigned += 1;
      if (t.status === 'COMPLETED') row.completed += 1;
      if (t.status === 'ESCALATED') row.escalated += 1;
      byOwner.set(id, row);
    }
    return Array.from(byOwner.values())
      .map((r) => ({ ...r, completionRate: r.assigned > 0 ? (r.completed / r.assigned) * 100 : 0 }))
      .sort((a, b) => b.completionRate - a.completionRate);
  }, [tasks]);

  const loading = summaryQ.isPending;

  return (
    <div className="space-y-6">
      {/* Manager-only variance / integrity controls (self-hides for staff).
          Lifted off the old TasksDashboard, which put it above three rival
          tabs where floor staff also landed. */}
      <SystemIntegrityPanel storeId={storeId} />

      {summaryQ.isError && (
        <div
          className="s-section flex items-center gap-2"
          style={{ padding: 12, borderColor: 'var(--err-50)', background: 'var(--err-50)' }}
        >
          <AlertTriangle className="w-5 h-5" style={{ color: 'var(--err)' }} />
          <span style={{ color: 'var(--err)' }}>Failed to load task totals.</span>
          <button onClick={() => summaryQ.refetch()} className="btn sm ml-auto">Retry</button>
        </div>
      )}

      <div className="grid grid-cols-1 tablet:grid-cols-4 gap-4">
        <Kpi icon={<CheckSquare className="w-5 h-5 text-gray-500" />} label="Total tasks" value={loading ? '—' : String(total)} />
        <Kpi icon={<CheckSquare className="w-5 h-5 text-gray-500" />} label="Completed" value={loading ? '—' : `${completed}/${total}`} />
        <Kpi icon={<TrendingUp className="w-5 h-5 text-gray-500" />} label="Completion rate" value={loading || total === 0 ? '—' : `${completionRate.toFixed(1)}%`} />
        <Kpi
          icon={<AlertTriangle className="w-5 h-5 text-red-600" />}
          label="Overdue"
          value={loading ? '—' : String(overdue)}
          danger={overdue > 0}
        />
      </div>

      <div className="card">
        <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
          <h3 className="text-lg font-semibold text-gray-900">Owner performance ranking</h3>
          {listQ.hasNextPage && (
            <button className="btn sm" onClick={() => listQ.fetchNextPage()} disabled={listQ.isFetchingNextPage}>
              {listQ.isFetchingNextPage ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
              Load more tasks
            </button>
          )}
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Computed from the {tasks.length} task{tasks.length === 1 ? '' : 's'} loaded on this page
          {listQ.hasNextPage ? ' — load more for the full picture.' : '.'}
        </p>

        {listQ.isPending ? (
          <div className="flex items-center justify-center h-40">
            <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
          </div>
        ) : owners.length === 0 ? (
          <div className="text-center py-12">
            <Users className="w-12 h-12 text-gray-400 mx-auto mb-3" />
            <p className="text-gray-700 font-medium">No tasks yet</p>
            <p className="text-sm text-gray-500 mt-1">
              This ranking is computed from the live tasks collection. No placeholder people are shown.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {owners.map((o, index) => {
              const band =
                o.completionRate >= 90
                  ? 'border-green-200 bg-green-50'
                  : o.completionRate >= 70
                    ? 'border-blue-200 bg-blue-50'
                    : 'border-red-200 bg-red-50';
              return (
                <div key={o.employeeId} className={`p-4 rounded-lg border-2 ${band}`}>
                  <div className="flex items-center gap-4 flex-wrap">
                    <div
                      className={`w-10 h-10 rounded-full flex items-center justify-center font-bold text-lg ${
                        index === 0
                          ? 'bg-amber-100 text-amber-800'
                          : index === 1
                            ? 'bg-gray-200 text-gray-700'
                            : 'bg-gray-100 text-gray-600'
                      }`}
                    >
                      {index + 1}
                    </div>
                    <div className="flex-1" style={{ minWidth: 140 }}>
                      <h4 className="font-semibold text-gray-900">{o.employeeName}</h4>
                      <p className="text-xs text-gray-500 font-mono mt-0.5">{o.employeeId}</p>
                    </div>
                    <div className="grid grid-cols-3 gap-6 text-center">
                      <Stat label="Assigned" value={o.assigned} />
                      <Stat label="Completed" value={o.completed} tone="text-green-600" />
                      <Stat label="Escalated" value={o.escalated} tone={o.escalated > 0 ? 'text-red-600' : undefined} />
                    </div>
                    <div className="text-right">
                      <p className="text-2xl font-bold text-gray-900">{o.completionRate.toFixed(1)}%</p>
                      <p className="text-xs text-gray-600">Completion</p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function Kpi({
  icon,
  label,
  value,
  danger,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  danger?: boolean;
}) {
  return (
    <div className="card">
      <div className="flex items-center gap-3">
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${danger ? 'bg-red-50' : 'bg-gray-100'}`}>
          {icon}
        </div>
        <div>
          <p className="text-sm text-gray-600">{label}</p>
          <p className={`text-2xl font-bold ${danger ? 'text-red-900' : 'text-gray-900'}`}>{value}</p>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div>
      <p className="text-xs text-gray-600">{label}</p>
      <p className={`text-lg font-semibold ${tone ?? 'text-gray-900'}`}>{value}</p>
    </div>
  );
}

export default TasksPerformancePage;
