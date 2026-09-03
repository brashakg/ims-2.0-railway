// ============================================================================
// IMS 2.0 - Tasks: the priority strip + list + detail panel
// ============================================================================
// ONE implementation, used by both /tasks/mine and /tasks/team. They differ by
// a single server parameter (`assigned_to`), so they are one component with a
// scope prop rather than two pages that will drift.
//
// NO 30-DAY BROWSE HORIZON HERE (owner ruling 2026-09-03). A task queue is
// work IN HAND, not a browse-by-date list: an open P0 raised 45 days ago must
// stay visible to everyone entitled to see it. This module is explicitly
// exempt from thirty_day_data_horizon. Do not add a date window.

import { useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  CheckSquare,
  Download,
  Edit,
  Loader2,
  Paperclip,
  Search,
  User,
  Zap,
  AlertTriangle,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { tasksApi } from '../../services/api';
import {
  PRIORITY_META,
  STATUS_LABEL,
  flattenTasks,
  useTaskList,
  type PCode,
  type Task,
  type TaskStatus,
} from './tasksQueries';

const PRI_VAR: Record<PCode, string> = {
  P0: 'var(--p0)',
  P1: 'var(--p1)',
  P2: 'var(--p2)',
  P3: 'var(--p3)',
  P4: 'var(--p4)',
};

const STATUS_OPTIONS: TaskStatus[] = ['OPEN', 'IN_PROGRESS', 'ESCALATED', 'COMPLETED'];

export interface TasksSplitViewProps {
  /** 'mine' adds the server-side assigned_to filter; 'team' does not. */
  scope: 'mine' | 'team';
  /** Copy for the empty state, which differs meaningfully between the two. */
  emptyLine: string;
}

export function TasksSplitView({ scope, emptyLine }: TasksSplitViewProps) {
  const { user } = useAuth();
  const queryClient = useQueryClient();

  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<TaskStatus | 'ALL'>('ALL');
  const [priority, setPriority] = useState<PCode | 'ALL'>('ALL');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // THE FIX: the assignee filter is a SERVER filter. The old pages fetched the
  // store's first 50 tasks and narrowed them in the browser, so past 50 tasks
  // "Mine" could show none of your own work. See tasksQueries.ts.
  const params = useMemo(
    () => ({
      ...(scope === 'mine' ? { assigned_to: user?.id || '' } : {}),
      ...(status !== 'ALL' ? { status } : {}),
      ...(priority !== 'ALL' ? { priority } : {}),
    }),
    [scope, user?.id, status, priority],
  );

  // A "mine" query with no user id would silently become a whole-store query.
  const ready = scope === 'team' || !!user?.id;
  const q = useTaskList(params, ready);

  const tasks = flattenTasks(q.data);
  const total = q.data?.pages[0]?.total ?? 0;

  // Title/description search is the only filter the endpoint cannot do, so it
  // stays client-side over the rows loaded. Everything that decides whether a
  // task is YOURS is on the server.
  const needle = search.trim().toLowerCase();
  const visible = needle
    ? tasks.filter(
        (t) =>
          t.title.toLowerCase().includes(needle) ||
          t.description.toLowerCase().includes(needle),
      )
    : tasks;

  const selected = visible.find((t) => t.id === selectedId) ?? null;
  useEffect(() => {
    if (selected) return;
    // Land on the most urgent open row so the detail panel is never blank
    // while something is on fire.
    const next =
      visible.find((t) => (t.pCode === 'P0' || t.pCode === 'P1') && t.status !== 'COMPLETED') ??
      visible[0];
    setSelectedId(next?.id ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible.length, scope]);

  const counts = useMemo(() => {
    const c: Record<PCode, number> = { P0: 0, P1: 0, P2: 0, P3: 0, P4: 0 };
    for (const t of visible) {
      if (t.status === 'COMPLETED' || t.status === 'CANCELLED') continue;
      c[t.pCode] += 1;
    }
    return c;
  }, [visible]);

  const overdue = visible.filter(
    (t) => t.status !== 'COMPLETED' && t.status !== 'CANCELLED' && t.dueInMin < 0 && isSaneDue(t.dueInMin),
  ).length;
  const escalated = visible.filter((t) => t.status === 'ESCALATED').length;

  const reload = () => queryClient.invalidateQueries({ queryKey: ['tasks'] });

  return (
    <>
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap mb-3.5">
        <div className="flex-1 relative" style={{ minWidth: 200 }}>
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
          <input
            type="text"
            placeholder="Search loaded tasks..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="input-field pl-10"
          />
        </div>
        <select
          title="Filter by status"
          value={status}
          onChange={(e) => setStatus(e.target.value as TaskStatus | 'ALL')}
          className="input-field w-auto"
        >
          <option value="ALL">All status</option>
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>{STATUS_LABEL[s]}</option>
          ))}
        </select>
        <select
          title="Filter by priority"
          value={priority}
          onChange={(e) => setPriority(e.target.value as PCode | 'ALL')}
          className="input-field w-auto"
        >
          <option value="ALL">All priorities</option>
          {(Object.keys(PRIORITY_META) as PCode[]).map((p) => (
            <option key={p} value={p}>{PRIORITY_META[p].label}</option>
          ))}
        </select>
      </div>

      {(overdue > 0 || escalated > 0) && (
        <div
          className="s-section flex items-center gap-2 mb-3.5"
          style={{ padding: 12, borderColor: 'var(--err-50)', background: 'var(--err-50)' }}
        >
          <AlertTriangle className="w-5 h-5" style={{ color: 'var(--err)' }} />
          <span style={{ color: 'var(--err)' }}>
            {overdue} overdue{escalated > 0 ? ` · ${escalated} escalated` : ''} — action required.
          </span>
        </div>
      )}

      {q.isError && (
        <div
          className="s-section flex items-center gap-2 mb-3.5"
          style={{ padding: 12, borderColor: 'var(--err-50)', background: 'var(--err-50)' }}
        >
          <AlertTriangle className="w-5 h-5" style={{ color: 'var(--err)' }} />
          <span style={{ color: 'var(--err)' }}>Failed to load tasks.</span>
          <button onClick={() => q.refetch()} className="btn sm ml-auto">Retry</button>
        </div>
      )}

      <div className={'t-body' + (selected ? ' detail-open' : '')}>
        <div className="t-list">
          <div className="pri-strip">
            {(Object.keys(PRIORITY_META) as PCode[]).map((p) => (
              <div key={p}>
                <div className="bar" style={{ background: PRI_VAR[p] }} />
                <div className="l">{PRIORITY_META[p].label}</div>
                <div className="v">{counts[p]}</div>
                <div className="d">{PRIORITY_META[p].sub}</div>
              </div>
            ))}
          </div>

          {q.isPending ? (
            <div className="flex items-center justify-center h-64">
              <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
            </div>
          ) : visible.length === 0 ? (
            <div className="text-center py-12">
              <CheckSquare className="w-12 h-12 text-gray-400 mx-auto mb-3" />
              <p className="text-gray-700 font-medium">No tasks here</p>
              <p className="text-sm text-gray-500 mt-1">{emptyLine}</p>
            </div>
          ) : (
            visible.map((t, i) => (
              <TaskRow
                key={t.id || `task-${i}`}
                task={t}
                selected={selectedId === t.id}
                onSelect={() => setSelectedId(t.id)}
              />
            ))
          )}

          {/* Honest footer: how many rows are in hand vs how many the server
              holds for these filters. The old pages showed neither. */}
          {!q.isPending && (
            <div className="flex items-center gap-3 mt-4 text-xs text-gray-500">
              <span>
                Showing {visible.length}
                {needle ? ` of ${tasks.length} loaded` : ''} · {total} match{total === 1 ? 'es' : ''} on the server
              </span>
              {q.hasNextPage && (
                <button
                  className="btn sm"
                  onClick={() => q.fetchNextPage()}
                  disabled={q.isFetchingNextPage}
                >
                  {q.isFetchingNextPage ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                  Load more
                </button>
              )}
            </div>
          )}
        </div>

        <aside className="t-detail">
          {selected ? (
            <TaskDetailPanel
              task={selected}
              onClose={() => setSelectedId(null)}
              onChanged={reload}
            />
          ) : (
            <div className="d-body">
              <p className="text-sm text-gray-500">
                Pick a task on the left to see its escalation ladder and SOP.
              </p>
            </div>
          )}
        </aside>
      </div>
    </>
  );
}

function TaskRow({
  task,
  selected,
  onSelect,
}: {
  task: Task;
  selected: boolean;
  onSelect: () => void;
}) {
  const isOverdue =
    (isSaneDue(task.dueInMin) && task.dueInMin < 0) ||
    (task.pCode === 'P1' && task.dueInMin >= 0 && task.dueInMin < 10);
  const ownerLabel =
    (task.assignedToName || task.assignedTo || '-')
      .split(' ')
      .map((s) => s[0])
      .filter(Boolean)
      .slice(0, 2)
      .join('')
      .toUpperCase() || '-';

  return (
    <div
      className={'t-item' + (selected ? ' sel' : '') + (isOverdue ? ' overdue' : '')}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <div className="pri-wrap">
        <span className={'pill-' + task.pCode}>{task.pCode}</span>
        <Countdown minutes={task.dueInMin} />
      </div>
      <div>
        <div className="ttl">{task.title}</div>
        <div className="meta">
          <span className="mono">{task.id}</span>
          {task.sopId && (
            <>
              <span>·</span>
              <span className="mono">{task.sopId}</span>
            </>
          )}
          <span>·</span>
          <span>
            Stage: <strong>{STATUS_LABEL[task.status].toLowerCase()}</strong>
          </span>
        </div>
        {(task.pCode === 'P0' || task.pCode === 'P1') && isSaneDue(task.dueInMin) && task.dueInMin >= 0 && (
          <div className="esc">
            <Zap className="w-3 h-3" />
            Escalates in {humanizeMinutes(task.dueInMin)} if not closed
          </div>
        )}
      </div>
      <div className="own-av" title={'Owner: ' + (task.assignedToName || task.assignedTo || '-')}>
        {ownerLabel}
      </div>
    </div>
  );
}

// Render a minute count as a human-friendly time-left string. Raw "1892319m"
// (~3.6 years) is useless to a store manager; "3y" or "44mo" is readable.
export function humanizeMinutes(min: number): string {
  if (min < 60) return `${min}m`;
  if (min < 60 * 24) return `${Math.round(min / 60)}h`;
  if (min < 60 * 24 * 30) return `${Math.round(min / 60 / 24)}d`;
  if (min < 60 * 24 * 365) return `${Math.round(min / 60 / 24 / 30)}mo`;
  return `${Math.round(min / 60 / 24 / 365)}y`;
}

// QA F15 guard: a due_at more than ~120 days out (or missing/unparseable, where
// minutesUntil returns +/-Infinity) is corrupt data, not a real SLA window.
const MAX_SANE_DUE_MIN = 120 * 24 * 60;
export function isSaneDue(min: number): boolean {
  return Number.isFinite(min) && Math.abs(min) <= MAX_SANE_DUE_MIN;
}

function TaskDetailPanel({
  task,
  onClose,
  onChanged,
}: {
  task: Task;
  onClose: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const isClosed = task.status === 'COMPLETED';

  const _errMsg = (e: unknown, fallback: string): string => {
    const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
    return typeof msg === 'string' ? msg : fallback;
  };

  const handleComplete = async () => {
    const notes = window.prompt('Completion notes (required, min 3 characters):', '');
    if (notes === null) return;
    if (notes.trim().length < 3) {
      toast.error('Completion notes must be at least 3 characters');
      return;
    }
    try {
      await tasksApi.completeTask(task.id, notes.trim());
      toast.success('Task completed');
      onChanged();
      onClose();
    } catch (e) {
      toast.error(_errMsg(e, 'Failed to complete task'));
    }
  };

  const handleAddNote = async () => {
    const note = window.prompt('Add a note to this task:', '');
    if (!note || !note.trim()) return;
    try {
      await tasksApi.updateTask(task.id, { notes: note.trim() });
      toast.success('Note added');
      onChanged();
    } catch (e) {
      toast.error(_errMsg(e, 'Failed to add note'));
    }
  };

  const handleStart = async () => {
    try {
      await tasksApi.updateTask(task.id, { status: 'IN_PROGRESS' });
      toast.success('Task marked in progress');
      onChanged();
      onClose();
    } catch (e) {
      toast.error(_errMsg(e, 'Failed to update task'));
    }
  };

  const handleReassign = async () => {
    const newAssignee = window.prompt('Reassign to (user id):', task.assignedTo || '');
    if (!newAssignee || !newAssignee.trim()) return;
    const reason = window.prompt('Reason (optional):', '') || undefined;
    try {
      await tasksApi.reassignTask(task.id, newAssignee.trim(), reason);
      toast.success('Task reassigned');
      onChanged();
      onClose();
    } catch (e) {
      toast.error(_errMsg(e, 'Failed to reassign task'));
    }
  };

  const handleDownloadAttachment = async () => {
    try {
      const blob = await tasksApi.getTaskFile(task.id);
      const url = window.URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener,noreferrer');
      window.setTimeout(() => window.URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      toast.error(_errMsg(e, 'Could not download the file'));
    }
  };

  const dueSane = isSaneDue(task.dueInMin);
  const minLeft = dueSane ? Math.max(0, task.dueInMin) : 0;
  const minLeftLabel = dueSane ? humanizeMinutes(minLeft) : '-';
  const showTimer = task.pCode === 'P0' || task.pCode === 'P1';
  const created = task.createdDate
    ? new Date(task.createdDate).toLocaleString('en-IN', {
        hour: '2-digit',
        minute: '2-digit',
        day: '2-digit',
        month: 'short',
      })
    : '-';

  return (
    <>
      <div className="d-head">
        <div className="row flex items-center gap-2 mb-2.5 flex-wrap">
          <span className={'pill-' + task.pCode}>{task.pCode}</span>
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-4)' }}>{task.id}</span>
          <span className="flex-1" />
          <button type="button" className="btn sm" onClick={onClose} aria-label="Close detail">
            <ArrowLeft className="w-3 h-3" /> Back
          </button>
          {!isClosed && (
            <>
              {(task.status === 'OPEN' || task.status === 'ESCALATED') && (
                <button type="button" className="btn sm" onClick={handleStart}>
                  <Zap className="w-3 h-3" /> Start
                </button>
              )}
              <button type="button" className="btn sm" onClick={handleAddNote}>
                <Edit className="w-3 h-3" /> Add note
              </button>
              <button type="button" className="btn sm" onClick={handleComplete}>
                <CheckSquare className="w-3 h-3" /> Complete
              </button>
            </>
          )}
          <button type="button" className="btn sm" onClick={handleReassign}>
            <User className="w-3 h-3" /> Reassign
          </button>
        </div>
        <h3>{task.title}</h3>
        <div className="hint" style={{ fontSize: 11.5, color: 'var(--ink-4)', marginTop: 4 }}>
          Created {created} · by {task.assignedByName || task.assignedBy || 'system'}
        </div>
      </div>

      <div className="d-body">
        {showTimer && (
          <div className="timer-big">
            <div>
              <div className="l">Auto-escalates in</div>
              <div className="v">{minLeftLabel}</div>
            </div>
          </div>
        )}

        {task.description && (
          <div className="d-sec">
            <h4>Brief</h4>
            <p style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--ink-2)' }}>
              {task.description}
            </p>
          </div>
        )}

        {task.completionNotes && (
          <div className="d-sec">
            <h4>Completion note</h4>
            <p style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--ink-2)' }}>
              {task.completionNotes}
            </p>
            {task.completedDate && (
              <div style={{ fontSize: 11, color: 'var(--ink-4)', marginTop: 4 }}>
                Completed {task.completedDate}
              </div>
            )}
          </div>
        )}

        {task.attachment?.file_id && (
          <div className="d-sec">
            <h4>Attachment</h4>
            <button
              type="button"
              className="btn sm"
              onClick={handleDownloadAttachment}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
            >
              <Paperclip className="w-3.5 h-3.5" />
              {task.attachment.filename || 'Download file'}
              <Download className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Escalation ladder.
            The two rungs above the owner used to be hard-coded "ASM" and
            "Ops Head" with invented +30m timings, regardless of the real
            role ladder the backend escalation engine walks. Naming the wrong
            person is worse than naming none, so the invented rungs are gone
            and the panel states plainly that the ladder is server-side.
            Rendering the REAL ladder needs the escalation config the list
            endpoint does not return - reported as a follow-up, not
            redesigned here. */}
        <div className="d-sec">
          <h4>Escalation ladder</h4>
          <div className="ladder">
            <div className="ladder-step done">
              <div className="rung">1</div>
              <div className="who">{task.assignedByName || 'Originator'}</div>
              <div className="when">Assigned</div>
            </div>
            <div className="ladder-step cur">
              <div className="rung">2</div>
              <div className="who">{task.assignedToName || 'Owner'} (current)</div>
              <div className="when">
                {!dueSane
                  ? '-'
                  : task.dueInMin < 0
                    ? `${humanizeMinutes(Math.abs(task.dueInMin))} overdue`
                    : `${minLeftLabel} left`}
              </div>
            </div>
          </div>
          <p style={{ fontSize: 11.5, color: 'var(--ink-4)', marginTop: 8 }}>
            Past the owner, the SLA engine escalates up this store's configured
            role ladder. Who that is next is decided server-side.
          </p>
        </div>

        {task.sopId && (
          <div className="d-sec">
            <h4>Attached SOP</h4>
            <div className="sop-box">
              <div className="sid">{task.sopId}</div>
              Open the SOP library for the full step-by-step breakdown.
            </div>
          </div>
        )}
      </div>
    </>
  );
}

/**
 * Live-ticking countdown pill. Shows "Nm" / "<1m" / "Xm late".
 *  • hot   if 0 <= minutes < 10 or already late
 *  • warm  if 10 <= minutes < 30
 */
function Countdown({ minutes }: { minutes: number }) {
  const [m, setM] = useState<number>(minutes);
  useEffect(() => {
    setM(minutes);
    const id = window.setInterval(() => setM((prev) => prev - 1), 60_000);
    return () => window.clearInterval(id);
  }, [minutes]);

  // QA F15: a missing/corrupt due_at is not a real countdown - show a neutral
  // dash rather than "4y left". Guard on the prop, not the ticking state.
  if (!isSaneDue(minutes)) return <span className="count-pill">-</span>;

  const late = m < 0;
  const hot = !late && m < 10;
  const warm = !late && m >= 10 && m < 30;
  const cls = 'count-pill' + (late || hot ? ' hot' : warm ? ' warm' : '');
  const label = late ? `${humanizeMinutes(Math.abs(m))} late` : m < 1 ? '<1m' : humanizeMinutes(m);

  return (
    <span className={cls}>
      {(late || hot) && <span className="dot" />}
      {label}
    </span>
  );
}

export default TasksSplitView;
