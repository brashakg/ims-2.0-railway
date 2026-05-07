// ============================================================================
// IMS 2.0 — Follow-up Panel (Pune Incentive Module i, Phase 3)
// ============================================================================
// Renders the embedded follow-up sub-docs for a walkout with:
//   - Round 1 / Round 2 cards showing scheduled date+time, mode, supervisor,
//     status, notes, completed_at if DONE
//   - "Schedule round 1" / "Schedule round 2" CTAs (rounds 1+2 only)
//   - Inline status update (PENDING → DONE / NOT REACHABLE / NOT REQUIRED)
//
// RBAC mirrors backend: any role allowed to edit the walkout can also
// edit follow-ups. Read-only mode hides all CTAs.

import { useState } from 'react';
import { Plus, Phone, MessageSquare, Mail, Users, Loader2, Check, X } from 'lucide-react';
import { walkoutsApi } from '../../services/api';
import { adminUserApi } from '../../services/api/stores';
import { useToast } from '../../context/ToastContext';
import {
  FOLLOWUP_MODES,
  FOLLOWUP_STATUSES,
  type WalkoutFollowUp,
  type Walkout,
  type CreateFollowUpRequest,
  type FollowUpMode,
  type FollowUpStatus,
} from '../../types';

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
  const [scheduling, setScheduling] = useState<1 | 2 | null>(null);
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

  const handleStatus = async (round: 1 | 2, status: FollowUpStatus) => {
    setBusy(true);
    try {
      const updated = await walkoutsApi.updateFollowUp(walkout.walkout_id, round, { status });
      onChanged(updated);
      toast.success(`Round ${round} → ${status}`);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Update failed';
      toast.error(typeof msg === 'string' ? msg : 'Update failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card p-5 space-y-3">
      <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Follow-ups</h2>

      {[1, 2].map((roundNum) => {
        const round = roundNum as 1 | 2;
        const fu = fuByRound.get(round);
        if (fu) {
          return <RoundCard key={round} fu={fu} canEdit={canEdit && !busy} onStatus={handleStatus} />;
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
            className="w-full px-4 py-3 border border-dashed border-gray-300 rounded text-sm text-gray-500 hover:border-bv-red-400 hover:text-bv-red-600 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center justify-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Schedule round {round}
          </button>
        );
      })}
    </section>
  );
}

function RoundCard({
  fu, canEdit, onStatus,
}: {
  fu: WalkoutFollowUp;
  canEdit: boolean;
  onStatus: (round: 1 | 2, status: FollowUpStatus) => void;
}) {
  const Icon = fu.mode ? MODE_ICON[fu.mode] : Phone;
  const statusCls =
    fu.status === 'DONE' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' :
    fu.status === 'NOT REACHABLE' ? 'bg-amber-50 text-amber-700 border-amber-200' :
    fu.status === 'NOT REQUIRED' ? 'bg-gray-50 text-gray-600 border-gray-200' :
    fu.status === 'ESCALATED' ? 'bg-rose-50 text-rose-700 border-rose-200' :
    'bg-blue-50 text-blue-700 border-blue-200';

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
      {canEdit && fu.status === 'PENDING' && (
        <div className="flex flex-wrap gap-2 mt-2">
          <StatusButton
            label="Done"
            icon={<Check className="w-3 h-3" />}
            onClick={() => onStatus(fu.round as 1 | 2, 'DONE')}
            className="bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100"
          />
          <StatusButton
            label="Not reachable"
            onClick={() => onStatus(fu.round as 1 | 2, 'NOT REACHABLE')}
            className="bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100"
          />
          <StatusButton
            label="Not required"
            icon={<X className="w-3 h-3" />}
            onClick={() => onStatus(fu.round as 1 | 2, 'NOT REQUIRED')}
            className="bg-gray-50 text-gray-700 border-gray-200 hover:bg-gray-100"
          />
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
  round: 1 | 2;
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
    <div className="border border-bv-red-200 rounded p-3 bg-rose-50/40">
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
        .fu-input:focus { outline: none; border-color: #fca5a5; }
      `}</style>
    </div>
  );
}

// Re-export for ergonomic imports
export { FOLLOWUP_STATUSES };
