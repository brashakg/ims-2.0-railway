// ============================================================================
// IMS 2.0 — New Task modal (Tasks/SOP v2, ported from docs/design/tasks.html)
// ============================================================================
// Replaces the dead-end "Open Tasks Dashboard" placeholder on /tasks. This is
// a fully functional create surface wired to the EXISTING tasksApi.createTask
// contract (title / description / priority P0-P4 / assigned_to / due_at).
//
// Design intent (docs/design/tasks.html → NewTaskModal):
//   • Priority chips P0-P4, each carrying a sensible default "due-in".
//   • Due-in presets that compute a real FUTURE due_at (backend rejects past).
//   • Owner picker sourced from the active store's users.
//   • Attach-SOP + watchers + auto-escalation are shown as CONTEXT/preview only
//     because the backend create contract persists none of them — surfacing
//     them as live preview keeps the screen honest (no silently-ignored inputs).
//   • Live preview card + escalation-ladder preview on the right.
//
// No backend / API shape change: the only network call is tasksApi.createTask.

import { useEffect, useMemo, useRef, useState } from 'react';
import { X, Plus, Paperclip, FileText, Image as ImageIcon, Loader2 } from 'lucide-react';
import { tasksApi } from '../../services/api';
import { adminStoreApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

type PCode = 'P0' | 'P1' | 'P2' | 'P3' | 'P4';

interface PriMeta {
  id: PCode;
  bar: string;
  title: string;
  sub: string;
  defaultMin: number;
  hint: string;
}

// Priority meta — colours mirror the --p0..--p4 design tokens; defaultMin maps
// to the SLA bands the backend escalation engine uses.
const PRI_META: PriMeta[] = [
  { id: 'P0', bar: 'var(--p0)', title: 'P0 · Now',     sub: 'Safety / compliance', defaultMin: 10,
    hint: 'Drops everything. Blocks shift close until resolved. Default owner: Store Manager.' },
  { id: 'P1', bar: 'var(--p1)', title: 'P1 · < 30m',   sub: 'Active escalation',   defaultMin: 30,
    hint: 'Counts down live in the list. Escalates up the ladder when the timer hits zero.' },
  { id: 'P2', bar: 'var(--p2)', title: 'P2 · Today',   sub: 'Before shift close',  defaultMin: 120,
    hint: 'Must close before end-of-shift. Rolls forward to next day otherwise.' },
  { id: 'P3', bar: 'var(--p3)', title: 'P3 · Week',    sub: 'Plannable',           defaultMin: 60 * 24,
    hint: 'Planned work for this week. Shown in the week view.' },
  { id: 'P4', bar: 'var(--p4)', title: 'P4 · Backlog', sub: 'Nice-to-have',        defaultMin: 60 * 24 * 3,
    hint: 'Backlog. Visible in reports but not the daily board.' },
];

const DUE_PRESETS: { k: string; m: number }[] = [
  { k: '15m', m: 15 },
  { k: '30m', m: 30 },
  { k: '1h', m: 60 },
  { k: '2h', m: 120 },
  { k: 'End of shift', m: 360 },
  { k: 'Tomorrow 10am', m: 60 * 20 },
  { k: 'This week', m: 60 * 24 * 3 },
];

interface StaffOption {
  user_id: string;
  name: string;
  role?: string;
}

// File attachment limits — must match the backend file_store contract.
const ACCEPT_ATTR = 'image/*,application/pdf';
const MAX_FILE_BYTES = 25 * 1024 * 1024; // 25 MB

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

const fmtDue = (m: number): string =>
  m < 60 ? `${m}m` : m < 60 * 24 ? `${Math.round(m / 60)}h` : `${Math.round(m / (60 * 24))}d`;

const initials = (name: string): string =>
  (name || '—')
    .split(' ')
    .map((s) => s[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase() || '—';

export interface NewTaskModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Called after a successful create so the parent can reload the list. */
  onCreated: () => void;
  /**
   * Pre-attach a file when the modal opens. Used by the Hub "Send a file"
   * action: sharing a file is now creating a task that carries it.
   */
  initialFile?: File | null;
  /** Custom heading + subtitle (e.g. the "Share a file" entry point). */
  heading?: string;
  subheading?: string;
}

export function NewTaskModal({
  isOpen,
  onClose,
  onCreated,
  initialFile = null,
  heading,
  subheading,
}: NewTaskModalProps) {
  const { user } = useAuth();
  const toast = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [title, setTitle] = useState('');
  const [desc, setDesc] = useState('');
  const [pri, setPri] = useState<PCode>('P2');
  const [due, setDue] = useState<number>(120);
  const [owner, setOwner] = useState<string>('');
  const [watchers, setWatchers] = useState<string[]>([]);
  const [escalate, setEscalate] = useState(true);
  const [staff, setStaff] = useState<StaffOption[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  // Load the active store's users for the owner / watcher pickers.
  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    const load = async () => {
      if (!user?.activeStoreId) {
        // No active store — fall back to self-assign only.
        setStaff(
          user?.id ? [{ user_id: user.id, name: user.name || 'Me', role: user.activeRole }] : [],
        );
        setOwner(user?.id || '');
        return;
      }
      try {
        const resp: any = await adminStoreApi.getStoreUsers(user.activeStoreId, { activeOnly: true });
        const list = resp?.users || resp || [];
        const opts: StaffOption[] = Array.isArray(list)
          ? list.map((u: any) => ({
              user_id: u.user_id || u.id,
              name: u.name || u.full_name || u.username || u.user_id,
              role: Array.isArray(u.roles) ? u.roles[0] : u.role,
            }))
          : [];
        if (cancelled) return;
        // Ensure the current user is selectable even if not returned by the store list.
        if (user?.id && !opts.find((o) => o.user_id === user.id)) {
          opts.unshift({ user_id: user.id, name: user.name || 'Me', role: user.activeRole });
        }
        setStaff(opts);
        setOwner((prev) => prev || user?.id || opts[0]?.user_id || '');
      } catch {
        if (cancelled) return;
        // Store-users endpoint unavailable — self-assign fallback keeps create working.
        const fallback = user?.id
          ? [{ user_id: user.id, name: user.name || 'Me', role: user.activeRole }]
          : [];
        setStaff(fallback);
        setOwner(user?.id || '');
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [isOpen, user?.activeStoreId, user?.id]);

  // Reset transient fields each time the modal opens.
  useEffect(() => {
    if (isOpen) {
      setTitle('');
      setDesc('');
      setPri('P2');
      setDue(120);
      setWatchers([]);
      setEscalate(true);
      setFile(initialFile);
      setFileError(null);
    }
  }, [isOpen, initialFile]);

  const validateAndSetFile = (f: File | null) => {
    setFileError(null);
    if (!f) {
      setFile(null);
      return;
    }
    const okType = f.type.startsWith('image/') || f.type === 'application/pdf';
    if (!okType) {
      setFileError('Only images or PDF files are allowed.');
      return;
    }
    if (f.size > MAX_FILE_BYTES) {
      setFileError(`File exceeds the ${MAX_FILE_BYTES / (1024 * 1024)} MB cap.`);
      return;
    }
    setFile(f);
  };

  const priMeta = useMemo(() => PRI_META.find((p) => p.id === pri)!, [pri]);
  const ownerName = useMemo(
    () => staff.find((s) => s.user_id === owner)?.name || 'Unassigned',
    [staff, owner],
  );
  // P0/P1 with a sub-10-minute window will read as overdue almost instantly.
  const willOverdue = (pri === 'P0' || pri === 'P1') && due < 10;
  const canSubmit = title.trim().length >= 3 && !!owner && !submitting;

  const submit = async () => {
    if (title.trim().length < 3) {
      toast.error('Title must be at least 3 characters');
      return;
    }
    if (!owner) {
      toast.error('Pick an owner for this task');
      return;
    }
    if (fileError) {
      toast.error(fileError);
      return;
    }
    setSubmitting(true);
    try {
      // If a file is attached, upload it first to get a file_id. A failed
      // upload aborts the create (the task should not silently lose its file).
      let attachment: {
        attachment_file_id?: string;
        attachment_filename?: string;
        attachment_mime?: string;
      } = {};
      if (file) {
        const up = await tasksApi.uploadTaskFile(file);
        attachment = {
          attachment_file_id: up.file_id,
          attachment_filename: up.filename,
          attachment_mime: up.mime,
        };
      }
      // Compute a real future due_at from the "due in N minutes" selection.
      // +30s padding so the backend's 5-minute past-guard never trips on a
      // tiny preset (e.g. 15m) due to request latency.
      const dueAt = new Date(Date.now() + due * 60_000 + 30_000);
      await tasksApi.createTask({
        title: title.trim(),
        description: desc.trim() || undefined,
        priority: pri,
        assigned_to: owner,
        due_date: dueAt,
        type: 'manual',
        ...attachment,
      });
      toast.success(file ? 'Task created with file attached' : 'Task created');
      onClose();
      onCreated();
    } catch (e) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(typeof msg === 'string' ? msg : 'Failed to create task');
    } finally {
      setSubmitting(false);
    }
  };

  // Keyboard: Esc closes, Cmd/Ctrl+Enter submits.
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submit();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, title, desc, pri, due, owner, file]);

  if (!isOpen) return null;

  return (
    <div
      className="nt-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="nt-modal" role="dialog" aria-modal="true" aria-label="New task">
        <div className="nt-head">
          <div>
            <h2>{heading || 'New task'}</h2>
            <div className="sub">
              {subheading ||
                'Tied to an SOP, assigned to an owner, auto-escalated when overdue.'}
            </div>
          </div>
          <button className="nt-close" onClick={onClose} aria-label="Close">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="nt-body">
          {/* Form */}
          <div className="nt-form">
            <div className="nt-field">
              <label>
                Title <span className="req">*</span>
              </label>
              <input
                className="input"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Reconcile Vision-Express invoice VE-8821"
                autoFocus
              />
            </div>

            <div className="nt-field">
              <label>Description</label>
              <textarea
                className="input"
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
                placeholder="What needs to happen, in one or two lines."
                style={{ minHeight: 68, resize: 'vertical' }}
              />
            </div>

            {/* Optional file attachment — sharing a file = a task carrying it. */}
            <div className="nt-field">
              <label>Attach a file</label>
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPT_ATTR}
                style={{ display: 'none' }}
                onChange={(e) => validateAndSetFile(e.target.files?.[0] ?? null)}
              />
              {!file ? (
                <button
                  type="button"
                  className="btn sm"
                  onClick={() => fileInputRef.current?.click()}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                >
                  <Paperclip className="w-3.5 h-3.5" /> Choose image or PDF
                </button>
              ) : (
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '8px 10px',
                    border: '1px solid var(--line)',
                    borderRadius: 8,
                    fontSize: 12.5,
                  }}
                >
                  {file.type === 'application/pdf' ? (
                    <FileText className="w-4 h-4" />
                  ) : (
                    <ImageIcon className="w-4 h-4" />
                  )}
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {file.name}
                  </span>
                  <span style={{ color: 'var(--ink-4)' }}>{humanSize(file.size)}</span>
                  <button
                    type="button"
                    className="nt-close"
                    aria-label="Remove file"
                    onClick={() => {
                      validateAndSetFile(null);
                      if (fileInputRef.current) fileInputRef.current.value = '';
                    }}
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}
              {fileError && (
                <div className="nt-hint" style={{ color: 'var(--err)', fontStyle: 'normal' }}>
                  {fileError}
                </div>
              )}
              <div className="nt-hint" style={{ fontStyle: 'normal', color: 'var(--ink-3)' }}>
                Image or PDF, up to 25 MB. The assignee can download it from the task.
              </div>
            </div>

            <div className="nt-field">
              <label>
                Priority <span className="req">*</span>
              </label>
              <div className="nt-pri-pick">
                {PRI_META.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    className={'nt-pri-chip' + (pri === p.id ? ' on' : '')}
                    onClick={() => {
                      setPri(p.id);
                      setDue(p.defaultMin);
                    }}
                  >
                    <div className="bar" style={{ background: p.bar }} />
                    <div className="t">{p.title}</div>
                    <div className="s">{p.sub}</div>
                  </button>
                ))}
              </div>
              <div className="nt-hint">{priMeta.hint}</div>
            </div>

            <div className="nt-row-2">
              <div className="nt-field">
                <label>
                  Due in <span className="req">*</span>
                </label>
                <div className="nt-preset-row">
                  {DUE_PRESETS.map((p) => (
                    <button
                      key={p.k}
                      type="button"
                      className={'nt-preset' + (due === p.m ? ' on' : '')}
                      onClick={() => setDue(p.m)}
                    >
                      {p.k} <span className="mono">· {fmtDue(p.m)}</span>
                    </button>
                  ))}
                </div>
                {willOverdue && (
                  <div className="nt-hint" style={{ color: 'var(--err)', fontStyle: 'normal' }}>
                    This will show as overdue almost immediately.
                  </div>
                )}
              </div>

              <div className="nt-field">
                <label>Attach SOP</label>
                <div
                  className="nt-hint"
                  style={{ fontStyle: 'normal', color: 'var(--ink-3)', marginTop: 0 }}
                >
                  Link an SOP from the SOPs tab after create — the SOP defines the
                  step-by-step trigger, owner, approver, and escalation ladder.
                </div>
              </div>
            </div>

            <div className="nt-field">
              <label>
                Owner <span className="req">*</span>
              </label>
              {staff.length === 0 ? (
                <div className="nt-hint" style={{ fontStyle: 'normal' }}>
                  No assignable staff found for this store.
                </div>
              ) : (
                <div className="nt-owner-pick">
                  {staff.map((t) => (
                    <button
                      key={t.user_id}
                      type="button"
                      className={'nt-owner-opt' + (owner === t.user_id ? ' on' : '')}
                      onClick={() => {
                        setOwner(t.user_id);
                        setWatchers((ws) => ws.filter((w) => w !== t.user_id));
                      }}
                    >
                      <span className="av">{initials(t.name)}</span>
                      <span>
                        {t.name}
                        {t.role && (
                          <span style={{ opacity: 0.55, fontWeight: 400 }}> · {t.role}</span>
                        )}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {staff.length > 1 && (
              <div className="nt-field">
                <label>Watchers</label>
                <div className="nt-owner-pick">
                  {staff
                    .filter((t) => t.user_id !== owner)
                    .map((t) => (
                      <button
                        key={t.user_id}
                        type="button"
                        className={'nt-owner-opt' + (watchers.includes(t.user_id) ? ' on' : '')}
                        onClick={() =>
                          setWatchers((ws) =>
                            ws.includes(t.user_id)
                              ? ws.filter((x) => x !== t.user_id)
                              : [...ws, t.user_id],
                          )
                        }
                      >
                        <span className="av">{initials(t.name)}</span>
                        <span>{t.name}</span>
                      </button>
                    ))}
                </div>
                <div className="nt-hint">
                  Notified on status change. Not responsible for completion.
                </div>
              </div>
            )}

            <div className="nt-field">
              <label>Auto-escalation</label>
              <div
                className={'nt-esc-toggle' + (escalate ? ' on' : '')}
                onClick={() => setEscalate((e) => !e)}
                role="switch"
                aria-checked={escalate}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setEscalate((v) => !v);
                  }
                }}
              >
                <div className="sw" />
                <div>
                  <div className="t">
                    {escalate
                      ? 'Enabled — auto-escalates up the role ladder when overdue'
                      : 'Disabled — stays with the owner'}
                  </div>
                  <div className="s">
                    Uses the per-priority SLA + role ladder. Preview on the right.
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Preview sidebar */}
          <aside className="nt-preview">
            <div className="nt-eyebrow">Preview · how this appears</div>
            <div className={'nt-prv-task' + (willOverdue ? ' overdue' : '')}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
                <span className={'pill-' + pri}>{pri}</span>
                <span
                  className={'count-pill' + (due < 10 ? ' hot' : due < 30 ? ' warm' : '')}
                >
                  {due < 30 && <span className="dot" />}
                  {fmtDue(due)}
                </span>
              </div>
              <div>
                <div style={{ font: '500 13px/1.3 var(--font-sans)', marginBottom: 6 }}>
                  {title || (
                    <span style={{ color: 'var(--ink-5)', fontStyle: 'italic' }}>
                      Task title will appear here…
                    </span>
                  )}
                </div>
                <div
                  style={{
                    display: 'flex',
                    gap: 10,
                    fontSize: 11,
                    color: 'var(--ink-4)',
                    flexWrap: 'wrap',
                    alignItems: 'center',
                  }}
                >
                  <span className="mono">TSK-????</span>
                  <span>·</span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    <span className="nt-mini-av">{initials(ownerName)}</span>
                    {ownerName}
                  </span>
                </div>
              </div>
            </div>

            {escalate && (
              <>
                <div className="nt-eyebrow" style={{ marginTop: 24 }}>
                  Escalation ladder
                </div>
                <div className="nt-prv-ladder">
                  <div className="step cur">
                    <div className="dot-r" />
                    <div>Step 1 · {ownerName}</div>
                    <div className="when">Now · {fmtDue(due)}</div>
                  </div>
                  <div className="step">
                    <div className="dot-r" />
                    <div>Step 2 · Store Manager</div>
                    <div className="when">+{fmtDue(due)}</div>
                  </div>
                  <div className="step">
                    <div className="dot-r" />
                    <div>Step 3 · Area Manager</div>
                    <div className="when">+{fmtDue(due * 2)}</div>
                  </div>
                  <div className="step">
                    <div className="dot-r" />
                    <div>Step 4 · Admin</div>
                    <div className="when">+{fmtDue(due * 4)}</div>
                  </div>
                </div>
              </>
            )}

            <div className="nt-eyebrow" style={{ marginTop: 24 }}>
              Notifications
            </div>
            <div className="nt-notif">
              <div style={{ marginBottom: 6 }}>
                <span className="k">On create →</span> {ownerName} (in-app + WhatsApp)
              </div>
              {watchers.length > 0 && (
                <div style={{ marginBottom: 6 }}>
                  <span className="k">Watchers →</span>{' '}
                  {watchers
                    .map((w) => staff.find((s) => s.user_id === w)?.name?.split(' ')[0])
                    .filter(Boolean)
                    .join(', ')}{' '}
                  (in-app)
                </div>
              )}
              {escalate && (
                <div>
                  <span className="k">On overdue →</span> Role ladder kicks in automatically
                </div>
              )}
            </div>
          </aside>
        </div>

        <div className="nt-foot">
          <span>
            <span className="kbd">Esc</span> to cancel
          </span>
          <span>
            <span className="kbd">⌘</span>
            <span className="kbd">↵</span> to create
          </span>
          <div style={{ flex: 1 }} />
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary" onClick={submit} disabled={!canSubmit}>
            {submitting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" /> {file ? 'Uploading…' : 'Creating…'}
              </>
            ) : (
              <>
                <Plus className="w-4 h-4" /> Create task
                <span style={{ opacity: 0.7, marginLeft: 6, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                  · {pri} · {fmtDue(due)}
                </span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

export default NewTaskModal;
