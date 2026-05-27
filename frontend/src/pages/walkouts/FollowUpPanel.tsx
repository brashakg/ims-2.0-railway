// ============================================================================
// IMS 2.0 — Follow-up Panel (Pune Incentive Module i, 3-round + approval)
// ============================================================================
// Renders the embedded follow-up sub-docs for a walkout. Owner spec
// (verbatim): "walkouts also need to be tied with a follow up section,
// making sure each walkout has been followed up properly with notes,
// 3 times follow up, call/text/visit notes with date and time, manager
// approval that the follow up actually happened etc".
//
//   - Round 1 / Round 2 / Round 3 cards showing scheduled date+time,
//     mode, supervisor, status, notes, completed_at if DONE
//   - "Schedule round N" CTAs (3 rounds max, scheduled independently)
//   - Inline status update (PENDING -> DONE / NOT REACHABLE / NOT REQUIRED)
//   - Per-card approval chips + manager Approve / Reject controls when
//     status is DONE. Salespeople see chips but no buttons.
//
// RBAC mirrors backend: any role allowed to edit the walkout can edit
// follow-ups. Approval is role-gated to managers (STORE_MANAGER /
// AREA_MANAGER / ADMIN / SUPERADMIN) — anti-fake-closure: salespeople
// cannot rubber-stamp the rounds they marked DONE themselves.

import { useMemo, useState } from 'react';
import {
  Plus, Phone, MessageSquare, Mail, Users, Loader2, Check, X,
  ShieldCheck, ShieldAlert, Clock,
} from 'lucide-react';
import { walkoutsApi } from '../../services/api';
import { adminUserApi } from '../../services/api/stores';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  FOLLOWUP_MODES,
  FOLLOWUP_STATUSES,
  type WalkoutFollowUp,
  type Walkout,
  type CreateFollowUpRequest,
  type FollowUpMode,
  type FollowUpStatus,
  type FollowUpApprovalDecision,
} from '../../types';

type RoundNum = 1 | 2 | 3;

const ROUND_VALUES: RoundNum[] = [1, 2, 3];

// Same set the backend uses for approval (_APPROVE_FOLLOWUP_ROLES). If
// this drifts the worst case is the FE shows buttons that the server
// 403s — but the chips and status all still render correctly.
const APPROVER_ROLES = new Set([
  'SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER',
]);

function hasApproverRole(roles: readonly string[] | undefined, activeRole: string | undefined): boolean {
  if (activeRole && APPROVER_ROLES.has(activeRole)) return true;
  if (!roles) return false;
  return roles.some(r => APPROVER_ROLES.has(r));
}

const MODE_ICON: Record<FollowUpMode, React.ComponentType<{ className?: string }>> = {
  CALL: Phone,
  WHATSAPP: MessageSquare,
  SMS: MessageSquare,
  EMAIL: Mail,
  'IN-PERSON': Users,
};

interface FollowUpPanelProps {
  walkout: Walkout;
  canEdit: boolean;
  onChanged: (w: Walkout) => void;
  storeId?: string;
}

interface StaffOption { user_id: string; name: string; }

export function FollowUpPanel({ walkout, canEdit, onChanged, storeId }: FollowUpPanelProps) {
  const toast = useToast();
  const { user } = useAuth();
  const userRoles = (user as any)?.roles as string[] | undefined;
  const activeRole = (user as any)?.activeRole as string | undefined;
  const canApprove = useMemo(
    () => hasApproverRole(userRoles, activeRole),
    [userRoles, activeRole],
  );

  const [scheduling, setScheduling] = useState<RoundNum | null>(null);
  const [busy, setBusy] = useState(false);
  const [supervisors, setSupervisors] = useState<StaffOption[]>([]);

  // Lazy-load supervisors when opening the schedule form
  const ensureSupervisors = async () => {
    if (supervisors.length || !storeId) return;
    try {
      const resp: any = await (adminUserApi as any).getUsers?.({ storeId });
      const list = resp?.users || resp || [];
      setSupervisors(
        Array.isArray(list)
          ? list.map((u: any) => ({
              user_id: u.user_id || u.id,
              name: u.name || u.full_name || u.username || u.user_id,
            }))
          : [],
      );
    } catch {
      // best effort — supervisor field stays free-text fallback
    }
  };

  const fuByRound = new Map<number, WalkoutFollowUp>();
  for (const fu of walkout.followups || []) fuByRound.set(fu.round, fu);

  const handleSchedule = async (
    payload: CreateFollowUpRequest,
  ) => {
    setBusy(true);
    try {
      const updated = await walkoutsApi.appendFollowUp(walkout.walkout_id, payload);
      onChanged(updated);
      toast.success(`Round ${payload.round} scheduled`);
      setScheduling(null);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Schedule failed';
      toast.error(typeof msg === 'string' ? msg : 'Schedule failed');
    } finally {
      setBusy(false);
    }
  };

  const handleStatus = async (round: RoundNum, status: FollowUpStatus) => {
    setBusy(true);
    try {
      const updated = await walkoutsApi.updateFollowUp(walkout.walkout_id, round, { status });
      onChanged(updated);
      toast.success(`Round ${round} -> ${status}`);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Update failed';
      toast.error(typeof msg === 'string' ? msg : 'Update failed');
    } finally {
      setBusy(false);
    }
  };

  const handleApproval = async (
    round: RoundNum,
    decision: FollowUpApprovalDecision,
    managerNote: string,
  ) => {
    setBusy(true);
    try {
      const updated = await walkoutsApi.approveFollowUp(
        walkout.walkout_id, round,
        { decision, manager_note: managerNote || undefined },
      );
      onChanged(updated);
      toast.success(
        decision === 'APPROVED'
          ? `Round ${round} approved`
          : `Round ${round} rejected`,
      );
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Approval failed';
      toast.error(typeof msg === 'string' ? msg : 'Approval failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card p-5 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
          Follow-ups
        </h2>
        <span className="text-[11px] text-gray-400">
          Up to 3 rounds · manager approval required when DONE
        </span>
      </div>

      {ROUND_VALUES.map((round) => {
        const fu = fuByRound.get(round);
        if (fu) {
          return (
            <RoundCard
              key={round}
              fu={fu}
              canEdit={canEdit && !busy}
              canApprove={canApprove && !busy}
              onStatus={handleStatus}
              onApproval={handleApproval}
            />
          );
        }
        if (scheduling === round) {
          return (
            <ScheduleRow
              key={round}
              round={round}
              supervisors={supervisors}
              onCancel={() => setScheduling(null)}
              onSubmit={handleSchedule}
              busy={busy}
            />
          );
        }
        return (
          <button
            key={round}
            type="button"
            disabled={!canEdit || busy}
            onClick={() => { setScheduling(round); ensureSupervisors(); }}
            className="w-full px-4 py-3 border border-dashed border-gray-300 rounded text-sm text-gray-500 hover:border-bv hover:text-bv disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center justify-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Schedule round {round}
          </button>
        );
      })}
    </section>
  );
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return '';
  if (ms < 60_000) return 'just now';
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

// ----------------------------------------------------------------------------
// Round card — one per round 1/2/3 once a follow-up exists.
// ----------------------------------------------------------------------------

function RoundCard({
  fu, canEdit, canApprove, onStatus, onApproval,
}: {
  fu: WalkoutFollowUp;
  canEdit: boolean;
  canApprove: boolean;
  onStatus: (round: RoundNum, status: FollowUpStatus) => void;
  onApproval: (round: RoundNum, decision: FollowUpApprovalDecision, note: string) => void;
}) {
  const Icon = fu.mode ? MODE_ICON[fu.mode] : Phone;
  const roundNum = fu.round as RoundNum;

  const statusCls =
    fu.status === 'DONE' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' :
    fu.status === 'NOT REACHABLE' ? 'bg-amber-50 text-amber-700 border-amber-200' :
    fu.status === 'NOT REQUIRED' ? 'bg-gray-50 text-gray-600 border-gray-200' :
    fu.status === 'ESCALATED' ? 'bg-rose-50 text-rose-700 border-rose-200' :
    'bg-bv-50 text-bv border-bv';

  // Manager-approval UI state lives per-card so each card can have its
  // own note draft independently.
  const [approvalAction, setApprovalAction] = useState<FollowUpApprovalDecision | null>(null);
  const [note, setNote] = useState('');

  const submitApproval = () => {
    if (!approvalAction) return;
    onApproval(roundNum, approvalAction, note.trim());
    setApprovalAction(null);
    setNote('');
  };

  return (
    <div className="border border-gray-200 rounded p-3 bg-gray-50/50">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 text-sm font-medium text-gray-700">
          <Icon className="w-4 h-4 text-gray-500" />
          Round {fu.round}
          <span className="text-xs text-gray-400">
            • {fu.scheduled_date}{fu.scheduled_time ? ` · ${fu.scheduled_time}` : ''} · {fu.mode}
          </span>
        </div>
        <span className={`text-xs px-2 py-0.5 rounded-full border ${statusCls}`}>{fu.status}</span>
      </div>
      {fu.supervisor_name && (
        <div className="text-xs text-gray-500 mb-1">
          Supervisor: <span className="font-medium text-gray-700">{fu.supervisor_name}</span>
        </div>
      )}
      {fu.notes && (
        <div className="text-xs text-gray-600 mb-2 whitespace-pre-line">{fu.notes}</div>
      )}
      {fu.completed_at && (
        <div className="text-[11px] text-gray-400">
          Completed {new Date(fu.completed_at).toLocaleString()}
        </div>
      )}

      {/* Approval chips + (manager-only) Approve/Reject */}
      {fu.status === 'DONE' && (
        <ApprovalArea
          fu={fu}
          canApprove={canApprove}
          approvalAction={approvalAction}
          setApprovalAction={setApprovalAction}
          note={note}
          setNote={setNote}
          submitApproval={submitApproval}
        />
      )}

      {canEdit && fu.status === 'PENDING' && (
        <div className="flex flex-wrap gap-2 mt-2">
          <StatusButton
            label="Done"
            icon={<Check className="w-3 h-3" />}
            onClick={() => onStatus(roundNum, 'DONE')}
            className="bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100"
          />
          <StatusButton
            label="Not reachable"
            onClick={() => onStatus(roundNum, 'NOT REACHABLE')}
            className="bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100"
          />
          <StatusButton
            label="Not required"
            icon={<X className="w-3 h-3" />}
            onClick={() => onStatus(roundNum, 'NOT REQUIRED')}
            className="bg-gray-50 text-gray-700 border-gray-200 hover:bg-gray-100"
          />
        </div>
      )}
    </div>
  );
}

function ApprovalArea({
  fu, canApprove, approvalAction, setApprovalAction, note, setNote, submitApproval,
}: {
  fu: WalkoutFollowUp;
  canApprove: boolean;
  approvalAction: FollowUpApprovalDecision | null;
  setApprovalAction: (a: FollowUpApprovalDecision | null) => void;
  note: string;
  setNote: (s: string) => void;
  submitApproval: () => void;
}) {
  const ap = fu.approval_status;
  // Render the latest approval state as a chip.
  if (ap === 'APPROVED') {
    return (
      <div className="mt-2 flex items-center gap-2 text-xs px-2 py-1 rounded border bg-green-50 text-green-700 border-green-200 w-fit">
        <ShieldCheck className="w-3 h-3" />
        Approved by {fu.approved_by_name || fu.approved_by_user_id || 'manager'}
        {fu.approved_at && (
          <span className="text-green-600/80"> · {formatRelativeTime(fu.approved_at)}</span>
        )}
      </div>
    );
  }
  if (ap === 'REJECTED') {
    return (
      <div className="mt-2 space-y-1">
        <div className="flex items-center gap-2 text-xs px-2 py-1 rounded border bg-rose-50 text-rose-700 border-rose-200 w-fit">
          <ShieldAlert className="w-3 h-3" />
          Rejected by {fu.approved_by_name || fu.approved_by_user_id || 'manager'}
          {fu.approved_at && (
            <span className="text-rose-600/80"> · {formatRelativeTime(fu.approved_at)}</span>
          )}
        </div>
        {fu.manager_note && (
          <div className="text-[11px] text-rose-700 whitespace-pre-line pl-1">
            Manager note: {fu.manager_note}
          </div>
        )}
      </div>
    );
  }
  // PENDING_APPROVAL — show the amber chip + (manager-only) action buttons.
  return (
    <div className="mt-2 space-y-2">
      <div className="flex items-center gap-2 text-xs px-2 py-1 rounded border bg-amber-50 text-amber-700 border-amber-200 w-fit">
        <Clock className="w-3 h-3" />
        Awaiting manager approval
      </div>
      {canApprove && approvalAction === null && (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setApprovalAction('APPROVED')}
            className="text-xs px-2 py-1 rounded border inline-flex items-center gap-1 bg-green-50 text-green-700 border-green-200 hover:bg-green-100"
          >
            <ShieldCheck className="w-3 h-3" />
            Approve
          </button>
          <button
            type="button"
            onClick={() => setApprovalAction('REJECTED')}
            className="text-xs px-2 py-1 rounded border inline-flex items-center gap-1 bg-rose-50 text-rose-700 border-rose-200 hover:bg-rose-100"
          >
            <ShieldAlert className="w-3 h-3" />
            Reject
          </button>
        </div>
      )}
      {canApprove && approvalAction !== null && (
        <div className="border border-gray-200 rounded p-2 bg-white space-y-2">
          <div className="text-xs font-medium text-gray-700">
            {approvalAction === 'APPROVED' ? 'Approve' : 'Reject'} round {fu.round}?
          </div>
          <textarea
            rows={2}
            value={note}
            onChange={e => setNote(e.target.value)}
            placeholder={
              approvalAction === 'REJECTED'
                ? 'Why is this being rejected? (recommended)'
                : 'Optional note'
            }
            className="fu-input w-full resize-none"
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="text-xs px-3 py-1 rounded border border-gray-200 text-gray-700 hover:bg-gray-100"
              onClick={() => { setApprovalAction(null); setNote(''); }}
            >
              Cancel
            </button>
            <button
              type="button"
              className={
                approvalAction === 'APPROVED'
                  ? 'text-xs px-3 py-1 rounded bg-green-600 text-white hover:bg-green-700 inline-flex items-center gap-1'
                  : 'text-xs px-3 py-1 rounded bg-rose-600 text-white hover:bg-rose-700 inline-flex items-center gap-1'
              }
              onClick={submitApproval}
            >
              {approvalAction === 'APPROVED' ? <ShieldCheck className="w-3 h-3" /> : <ShieldAlert className="w-3 h-3" />}
              Confirm {approvalAction === 'APPROVED' ? 'approve' : 'reject'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function StatusButton({
  label, icon, onClick, className,
}: { label: string; icon?: React.ReactNode; onClick: () => void; className: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-xs px-2 py-1 rounded border inline-flex items-center gap-1 ${className}`}
    >
      {icon}{label}
    </button>
  );
}

function ScheduleRow({
  round, supervisors, onCancel, onSubmit, busy,
}: {
  round: RoundNum;
  supervisors: StaffOption[];
  onCancel: () => void;
  onSubmit: (payload: CreateFollowUpRequest) => void;
  busy: boolean;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const [scheduledDate, setScheduledDate] = useState(today);
  const [scheduledTime, setScheduledTime] = useState('10:00');
  const [mode, setMode] = useState<FollowUpMode>('WHATSAPP');
  const [supervisorId, setSupervisorId] = useState('');
  const [notes, setNotes] = useState('');

  const submit = () => {
    if (!scheduledDate) return;
    onSubmit({
      round,
      scheduled_date: scheduledDate,
      scheduled_time: scheduledTime || undefined,
      mode,
      supervisor_id: supervisorId || undefined,
      notes: notes || undefined,
    });
  };

  return (
    <div className="border border-bv rounded p-3 bg-bv-50/50">
      <div className="text-sm font-medium text-gray-700 mb-2">Schedule round {round}</div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-2">
        <input
          type="date"
          value={scheduledDate}
          onChange={e => setScheduledDate(e.target.value)}
          className="fu-input"
        />
        <input
          type="time"
          value={scheduledTime}
          onChange={e => setScheduledTime(e.target.value)}
          className="fu-input"
        />
        <select value={mode} onChange={e => setMode(e.target.value as FollowUpMode)} className="fu-input">
          {FOLLOWUP_MODES.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select
          value={supervisorId}
          onChange={e => setSupervisorId(e.target.value)}
          className="fu-input"
        >
          <option value="">— supervisor (optional) —</option>
          {supervisors.map(s => (
            <option key={s.user_id} value={s.user_id}>{s.name}</option>
          ))}
        </select>
      </div>
      <textarea
        rows={2}
        value={notes}
        onChange={e => setNotes(e.target.value)}
        placeholder="Notes / what to say…"
        className="fu-input w-full mb-2 resize-none"
      />
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-secondary text-xs px-3 py-1" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          className="btn-primary text-xs px-3 py-1 inline-flex items-center gap-1"
          onClick={submit}
          disabled={busy || !scheduledDate}
        >
          {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
          Schedule
        </button>
      </div>
      <style>{`
        .fu-input {
          padding: 6px 10px;
          border: 1px solid #e5e7eb;
          border-radius: 4px;
          font-size: 13px;
          color: #111827;
          background: #fff;
        }
        .fu-input:focus { outline: none; border-color: var(--bv, #fca5a5); }
      `}</style>
    </div>
  );
}

// Re-export for ergonomic imports
export { FOLLOWUP_STATUSES };
