// ============================================================================
// IMS 2.0 - Configurable Reminders (F46 / Engine E6) settings surface
// ============================================================================
// CONFIG ONLY. This screen never performs a live send. Each card edits a
// reminder_rules document (which event, offset/cadence, segment, channel,
// template, trigger, active on/off). The underlying rail rides
// send_notification (PENDING, DISPATCH_MODE-gated; Railway default `off`), so
// activating a rule here only flips config -- nothing leaves the building.
//
// Design: neutral / restrained. A single accent (bv-red) marks the enabled
// state; colour is reserved for semantic meaning (red = failed count). No
// emoji, no dark surfaces. Mirrors the existing SettingsPage card density.

import { useEffect, useState, useCallback } from 'react';
import { Plus, X, Eye, Play, Pencil, Trash2, RefreshCw, AlertCircle } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { remindersApi, settingsApi } from '../../services/api';
import type {
  ReminderRule,
  ReminderRuleCreate,
  ReminderChannel,
  ReminderScope,
  ReminderTriggerKind,
  ReminderPreviewResult,
} from '../../services/api';

// Static segment catalog (key + label) -- mirrors campaign_segments.SEGMENT_DEFS.
// Kept client-side so the picker renders even if the live count endpoint is slow.
const SEGMENTS: Array<{ key: string; label: string; paramHint?: string }> = [
  { key: 'rx_expiry', label: 'Prescription expiring', paramHint: 'window_days' },
  { key: 'birthday', label: 'Birthday this week' },
  { key: 'winback', label: 'Win-back (lapsed)', paramHint: 'inactive_months' },
  { key: 'cl_reorder', label: 'Contact-lens reorder due', paramHint: 'window_days' },
  { key: 'churn_risk', label: 'Churn risk (lapse)', paramHint: 'window_days' },
  { key: 'fu_due_today', label: 'Follow-ups due today' },
  { key: 'recent_buyers', label: 'Recent buyers' },
  { key: 'by_store', label: 'All store customers' },
  { key: 'by_customer_type', label: 'By customer type (B2B / B2C)' },
];

const RULE_TYPES: string[] = [
  'rx_expiry',
  'birthday',
  'winback',
  'cl_reorder',
  'churn_risk',
  'feedback',
  'fu_due_today',
  'custom',
];

const CHANNELS: ReminderChannel[] = ['WHATSAPP', 'SMS', 'EMAIL'];

function triggerLabel(rule: ReminderRule): string {
  const t = rule.trigger || { kind: 'CRON' };
  if (t.kind === 'EVENT') return `On event: ${t.event_key || 'n/a'}`;
  return `${t.cron || 'CRON'} IST`;
}

function emptyDraft(): ReminderRuleCreate {
  return {
    name: '',
    rule_type: 'custom',
    segment_key: 'rx_expiry',
    segment_params: {},
    channel: 'WHATSAPP',
    template_id: '',
    trigger: { kind: 'CRON', cron: 'DAILY 09:00', event_key: null },
    scope: 'GLOBAL',
    store_id: null,
    entity_id: null,
    is_transactional: false,
    freq_cap_exempt: false,
    voucher_template: null,
    active: false,
  };
}

export function RemindersSettings() {
  const { user } = useAuth();
  const toast = useToast();

  const isAdmin = !!user && (user.roles || [user.activeRole]).some((r) =>
    ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'].includes(r as string),
  );

  const [rules, setRules] = useState<ReminderRule[]>([]);
  const [templates, setTemplates] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Drawer editor state.
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ReminderRuleCreate>(emptyDraft());
  const [saving, setSaving] = useState(false);
  const [showVoucher, setShowVoucher] = useState(false);

  // Preview modal state.
  const [previewFor, setPreviewFor] = useState<ReminderRule | null>(null);
  const [preview, setPreview] = useState<ReminderPreviewResult | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await remindersApi.list();
      setRules(res.rules || []);
    } catch {
      setError('Failed to load reminder rules');
      setRules([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    settingsApi
      .getNotificationTemplates()
      .then((res: { templates?: Array<{ template_id?: string }> }) => {
        const ids = (res.templates || [])
          .map((t) => t.template_id)
          .filter((x): x is string => !!x);
        setTemplates(ids);
      })
      .catch(() => setTemplates([]));
  }, [load]);

  const openCreate = () => {
    setEditingId(null);
    setDraft(emptyDraft());
    setShowVoucher(false);
    setDrawerOpen(true);
  };

  const openEdit = (rule: ReminderRule) => {
    setEditingId(rule.rule_id);
    setDraft({
      name: rule.name,
      rule_type: rule.rule_type,
      segment_key: rule.segment_key,
      segment_params: rule.segment_params || {},
      channel: rule.channel,
      template_id: rule.template_id,
      trigger: rule.trigger || { kind: 'CRON', cron: 'DAILY 09:00', event_key: null },
      scope: rule.scope,
      store_id: rule.store_id ?? null,
      entity_id: rule.entity_id ?? null,
      is_transactional: rule.is_transactional,
      freq_cap_exempt: rule.freq_cap_exempt,
      voucher_template: rule.voucher_template ?? null,
      active: rule.active,
    });
    setShowVoucher(!!rule.voucher_template);
    setDrawerOpen(true);
  };

  const saveDraft = async () => {
    if (!draft.name || draft.name.trim().length < 2) {
      toast.error('Rule name must be at least 2 characters');
      return;
    }
    if (!draft.template_id) {
      toast.error('Pick a notification template');
      return;
    }
    if (draft.scope === 'STORE' && !draft.store_id) {
      toast.error('A store-scoped rule needs a store');
      return;
    }
    const payload: ReminderRuleCreate = {
      ...draft,
      voucher_template: showVoucher ? draft.voucher_template : null,
    };
    setSaving(true);
    try {
      if (editingId) {
        await remindersApi.update(editingId, payload);
        toast.success('Reminder rule updated');
      } else {
        await remindersApi.create(payload);
        toast.success('Reminder rule created (inactive until you turn it on)');
      }
      setDrawerOpen(false);
      await load();
    } catch {
      toast.error('Could not save the rule');
    } finally {
      setSaving(false);
    }
  };

  const onToggle = async (rule: ReminderRule) => {
    try {
      const res = await remindersApi.toggle(rule.rule_id);
      setRules((prev) =>
        prev.map((r) => (r.rule_id === rule.rule_id ? { ...r, active: res.active } : r)),
      );
      toast.success(res.active ? 'Reminder enabled' : 'Reminder paused');
    } catch {
      toast.error('Could not change the rule');
    }
  };

  const onDelete = async (rule: ReminderRule) => {
    if (!window.confirm(`Delete reminder "${rule.name}"? It stops running immediately.`)) return;
    try {
      await remindersApi.remove(rule.rule_id);
      setRules((prev) => prev.filter((r) => r.rule_id !== rule.rule_id));
      toast.success('Reminder deleted');
    } catch {
      toast.error('Could not delete the rule');
    }
  };

  const openPreview = async (rule: ReminderRule) => {
    setPreviewFor(rule);
    setPreview(null);
    setPreviewLoading(true);
    try {
      const res = await remindersApi.preview(rule.rule_id);
      setPreview(res);
    } catch {
      toast.error('Preview failed');
    } finally {
      setPreviewLoading(false);
    }
  };

  const onRunNow = async (rule: ReminderRule) => {
    if (
      !window.confirm(
        `Run "${rule.name}" now? Messages are queued only — nothing is sent live (channel is off).`,
      )
    )
      return;
    try {
      const res = await remindersApi.runNow(rule.rule_id);
      toast.success(`Queued ${res.queued} message(s) — held (send is off)`);
      await load();
    } catch {
      toast.error('Run failed (you may have hit the rate limit)');
    }
  };

  const setDraftField = <K extends keyof ReminderRuleCreate>(k: K, v: ReminderRuleCreate[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));

  return (
    <div className="space-y-4">
      {/* Header / send-dark notice */}
      <div className="card">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Configurable Reminders</h2>
            <p className="text-sm text-gray-500 mt-1 max-w-2xl">
              Rules decide who is reminded, when, on which channel, and with which template.
              Saving or enabling a rule changes <span className="font-medium">configuration only</span> —
              the outbound channel is currently off, so no message is sent live.
            </p>
          </div>
          {isAdmin && (
            <button type="button" onClick={openCreate} className="btn-primary shrink-0">
              <Plus className="w-4 h-4 mr-2" />
              New rule
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="card flex items-center gap-2 border-red-200 bg-red-50">
          <AlertCircle className="w-5 h-5 text-red-500" />
          <span className="text-sm text-red-700">{error}</span>
          <button onClick={load} className="btn-outline ml-auto text-sm">
            Retry
          </button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-40">
          <RefreshCw className="w-7 h-7 animate-spin text-gray-400" />
        </div>
      ) : rules.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">
          <p className="font-medium">No reminder rules yet</p>
          <p className="text-sm mt-1">Create one to schedule a recurring or triggered reminder.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {rules.map((rule) => {
            const skipped =
              rule.skipped_count ?? 0;
            return (
              <div key={rule.rule_id} className="card">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-gray-900">{rule.name}</span>
                      <span className="text-xs px-2 py-0.5 rounded-full border border-gray-300 text-gray-600">
                        {rule.channel}
                      </span>
                      <span className="text-xs px-2 py-0.5 rounded-full border border-gray-300 text-gray-500">
                        {rule.scope}
                      </span>
                      {rule.is_transactional && (
                        <span className="text-xs px-2 py-0.5 rounded-full border border-gray-300 text-gray-500">
                          transactional
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-gray-500 mt-1">
                      {triggerLabel(rule)} · {rule.segment_key} segment
                    </p>
                    <p className="text-xs text-gray-400 mt-0.5">
                      Last resolved: {rule.last_resolved ?? '—'} customers
                    </p>
                    <p className="text-xs mt-1">
                      <span className="text-gray-500">
                        Sent {rule.sent_count ?? 0} · Skipped {skipped}
                      </span>
                      {' · '}
                      <span className={rule.failed_count > 0 ? 'text-red-600' : 'text-gray-500'}>
                        Failed {rule.failed_count ?? 0}
                      </span>
                    </p>
                  </div>

                  {/* Enabled toggle: single accent when active. */}
                  <button
                    type="button"
                    onClick={() => onToggle(rule)}
                    role="switch"
                    aria-checked={rule.active}
                    aria-label={rule.active ? 'Disable reminder' : 'Enable reminder'}
                    className={
                      'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ' +
                      (rule.active ? 'bg-bv-red-600' : 'bg-gray-200')
                    }
                  >
                    <span
                      className={
                        'inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ' +
                        (rule.active ? 'translate-x-5' : 'translate-x-0.5')
                      }
                    />
                  </button>
                </div>

                <div className="flex items-center gap-2 mt-3 pt-3 border-t border-gray-100">
                  <button
                    type="button"
                    onClick={() => openPreview(rule)}
                    className="btn-outline text-sm flex items-center gap-1"
                  >
                    <Eye className="w-4 h-4" /> Preview
                  </button>
                  {isAdmin && (
                    <button
                      type="button"
                      onClick={() => onRunNow(rule)}
                      className="btn-outline text-sm flex items-center gap-1"
                    >
                      <Play className="w-4 h-4" /> Run now
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => openEdit(rule)}
                    className="btn-outline text-sm flex items-center gap-1"
                  >
                    <Pencil className="w-4 h-4" /> Edit
                  </button>
                  {isAdmin && (
                    <button
                      type="button"
                      onClick={() => onDelete(rule)}
                      className="btn-outline text-sm flex items-center gap-1 text-red-600 ml-auto"
                    >
                      <Trash2 className="w-4 h-4" /> Delete
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ---- Drawer editor ---- */}
      {drawerOpen && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
          <div className="relative h-full w-full max-w-md bg-white shadow-xl overflow-y-auto">
            <div className="sticky top-0 z-10 flex items-center justify-between border-b border-gray-200 bg-white px-5 py-3">
              <h3 className="font-semibold text-gray-900">
                {editingId ? 'Edit reminder rule' : 'New reminder rule'}
              </h3>
              <button type="button" onClick={() => setDrawerOpen(false)} aria-label="Close">
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>

            <div className="px-5 py-4 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-600 mb-1">Name</label>
                <input
                  type="text"
                  className="input-field"
                  value={draft.name}
                  onChange={(e) => setDraftField('name', e.target.value)}
                  placeholder="e.g. Prescription expiry reminder"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">Rule type</label>
                  <select
                    className="input-field"
                    value={draft.rule_type}
                    onChange={(e) => setDraftField('rule_type', e.target.value as ReminderRuleCreate['rule_type'])}
                  >
                    {RULE_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">Segment</label>
                  <select
                    className="input-field"
                    value={draft.segment_key}
                    onChange={(e) => setDraftField('segment_key', e.target.value)}
                  >
                    {SEGMENTS.map((s) => (
                      <option key={s.key} value={s.key}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Conditional segment-params: a single window/cadence days field. */}
              {(() => {
                const segDef = SEGMENTS.find((s) => s.key === draft.segment_key);
                if (!segDef?.paramHint) return null;
                const key = segDef.paramHint;
                const current = (draft.segment_params as Record<string, unknown>)[key];
                return (
                  <div>
                    <label className="block text-sm font-medium text-gray-600 mb-1">
                      {key === 'inactive_months' ? 'Inactive months' : 'Window / cadence (days)'}
                    </label>
                    <input
                      type="number"
                      min={1}
                      className="input-field"
                      value={typeof current === 'number' ? current : ''}
                      onChange={(e) =>
                        setDraftField('segment_params', {
                          ...(draft.segment_params as Record<string, unknown>),
                          [key]: parseInt(e.target.value, 10) || 0,
                        })
                      }
                      placeholder={key === 'inactive_months' ? '6' : '30'}
                    />
                  </div>
                );
              })()}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">Channel</label>
                  <select
                    className="input-field"
                    value={draft.channel}
                    onChange={(e) => setDraftField('channel', e.target.value as ReminderChannel)}
                  >
                    {CHANNELS.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">Template</label>
                  <select
                    className="input-field"
                    value={draft.template_id}
                    onChange={(e) => setDraftField('template_id', e.target.value)}
                  >
                    <option value="">Select…</option>
                    {templates.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                    {/* allow keeping an existing template_id not in the list */}
                    {draft.template_id && !templates.includes(draft.template_id) && (
                      <option value={draft.template_id}>{draft.template_id}</option>
                    )}
                  </select>
                </div>
              </div>

              {/* Trigger */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">Trigger</label>
                  <select
                    className="input-field"
                    value={draft.trigger?.kind || 'CRON'}
                    onChange={(e) =>
                      setDraftField('trigger', {
                        kind: e.target.value as ReminderTriggerKind,
                        cron: e.target.value === 'CRON' ? draft.trigger?.cron || 'DAILY 09:00' : null,
                        event_key: e.target.value === 'EVENT' ? draft.trigger?.event_key || '' : null,
                      })
                    }
                  >
                    <option value="CRON">Schedule (CRON)</option>
                    <option value="EVENT">On event</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">
                    {draft.trigger?.kind === 'EVENT' ? 'Event key' : 'Schedule'}
                  </label>
                  <input
                    type="text"
                    className="input-field"
                    value={
                      draft.trigger?.kind === 'EVENT'
                        ? draft.trigger?.event_key || ''
                        : draft.trigger?.cron || ''
                    }
                    onChange={(e) =>
                      setDraftField('trigger', {
                        kind: draft.trigger?.kind || 'CRON',
                        cron: draft.trigger?.kind === 'EVENT' ? null : e.target.value,
                        event_key: draft.trigger?.kind === 'EVENT' ? e.target.value : null,
                      })
                    }
                    placeholder={draft.trigger?.kind === 'EVENT' ? 'churn.detected' : 'DAILY 09:00'}
                  />
                </div>
              </div>

              {/* Scope */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1">Scope</label>
                  <select
                    className="input-field"
                    value={draft.scope}
                    onChange={(e) => setDraftField('scope', e.target.value as ReminderScope)}
                  >
                    <option value="GLOBAL">All stores (GLOBAL)</option>
                    <option value="ENTITY">Entity</option>
                    <option value="STORE">One store</option>
                  </select>
                </div>
                {draft.scope === 'STORE' && (
                  <div>
                    <label className="block text-sm font-medium text-gray-600 mb-1">Store ID</label>
                    <input
                      type="text"
                      className="input-field"
                      value={draft.store_id || ''}
                      onChange={(e) => setDraftField('store_id', e.target.value)}
                      placeholder="BV-PUN-01"
                    />
                  </div>
                )}
                {draft.scope === 'ENTITY' && (
                  <div>
                    <label className="block text-sm font-medium text-gray-600 mb-1">Entity ID</label>
                    <input
                      type="text"
                      className="input-field"
                      value={draft.entity_id || ''}
                      onChange={(e) => setDraftField('entity_id', e.target.value)}
                    />
                  </div>
                )}
              </div>

              {/* Advanced: voucher + freq-cap exempt */}
              <div className="border-t border-gray-100 pt-3 space-y-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={showVoucher}
                    onChange={(e) => {
                      setShowVoucher(e.target.checked);
                      if (e.target.checked && !draft.voucher_template) {
                        setDraftField('voucher_template', {
                          type: 'DISCOUNT',
                          amount: 200,
                          validity_days: 30,
                        });
                      }
                    }}
                    className="rounded border-gray-300"
                  />
                  <span className="text-sm text-gray-700">Attach a voucher</span>
                </label>
                {showVoucher && (
                  <div className="grid grid-cols-3 gap-2 pl-6">
                    <select
                      className="input-field"
                      value={draft.voucher_template?.type || 'DISCOUNT'}
                      onChange={(e) =>
                        setDraftField('voucher_template', {
                          type: e.target.value as 'GIFT_CARD' | 'DISCOUNT',
                          amount: draft.voucher_template?.amount ?? 200,
                          validity_days: draft.voucher_template?.validity_days ?? 30,
                        })
                      }
                    >
                      <option value="DISCOUNT">Discount</option>
                      <option value="GIFT_CARD">Gift card</option>
                    </select>
                    <input
                      type="number"
                      min={0}
                      className="input-field"
                      placeholder="Amount ₹"
                      value={draft.voucher_template?.amount ?? 200}
                      onChange={(e) =>
                        setDraftField('voucher_template', {
                          type: draft.voucher_template?.type || 'DISCOUNT',
                          amount: parseFloat(e.target.value) || 0,
                          validity_days: draft.voucher_template?.validity_days ?? 30,
                        })
                      }
                    />
                    <input
                      type="number"
                      min={1}
                      className="input-field"
                      placeholder="Valid days"
                      value={draft.voucher_template?.validity_days ?? 30}
                      onChange={(e) =>
                        setDraftField('voucher_template', {
                          type: draft.voucher_template?.type || 'DISCOUNT',
                          amount: draft.voucher_template?.amount ?? 200,
                          validity_days: parseInt(e.target.value, 10) || 30,
                        })
                      }
                    />
                  </div>
                )}
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={!!draft.freq_cap_exempt}
                    onChange={(e) => setDraftField('freq_cap_exempt', e.target.checked)}
                    className="rounded border-gray-300"
                  />
                  <span className="text-sm text-gray-700">
                    Exempt from the 30-day frequency cap (advanced)
                  </span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={!!draft.active}
                    onChange={(e) => setDraftField('active', e.target.checked)}
                    className="rounded border-gray-300"
                  />
                  <span className="text-sm text-gray-700">
                    Enabled (config only — no live send while the channel is off)
                  </span>
                </label>
              </div>
            </div>

            <div className="sticky bottom-0 flex items-center justify-end gap-2 border-t border-gray-200 bg-white px-5 py-3">
              <button type="button" onClick={() => setDrawerOpen(false)} className="btn-outline">
                Cancel
              </button>
              <button type="button" onClick={saveDraft} disabled={saving} className="btn-primary">
                {saving ? 'Saving…' : editingId ? 'Save changes' : 'Create rule'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ---- Preview modal ---- */}
      {previewFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => setPreviewFor(null)}
            aria-hidden
          />
          <div className="relative w-full max-w-md rounded-lg bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
              <h3 className="font-semibold text-gray-900">Preview · {previewFor.name}</h3>
              <button type="button" onClick={() => setPreviewFor(null)} aria-label="Close">
                <X className="w-5 h-5 text-gray-500" />
              </button>
            </div>
            <div className="px-5 py-4">
              <p className="text-xs text-gray-500 mb-3">
                Dry run — resolves the audience and runs every gate, but writes nothing and sends nothing.
              </p>
              {previewLoading ? (
                <div className="flex items-center justify-center h-24">
                  <RefreshCw className="w-6 h-6 animate-spin text-gray-400" />
                </div>
              ) : preview ? (
                <dl className="grid grid-cols-2 gap-y-2 text-sm">
                  <dt className="text-gray-500">Resolved</dt>
                  <dd className="text-gray-900 font-medium text-right">{preview.resolved}</dd>
                  <dt className="text-gray-500">Would queue</dt>
                  <dd className="text-gray-900 font-medium text-right">{preview.resolved - preview.skipped_consent - preview.skipped_freqcap - preview.skipped_no_phone}</dd>
                  <dt className="text-gray-500">Skipped — consent</dt>
                  <dd className="text-gray-700 text-right">{preview.skipped_consent}</dd>
                  <dt className="text-gray-500">Skipped — frequency cap</dt>
                  <dd className="text-gray-700 text-right">{preview.skipped_freqcap}</dd>
                  <dt className="text-gray-500">Deferred — quiet hours</dt>
                  <dd className="text-gray-700 text-right">{preview.skipped_quiet}</dd>
                  <dt className="text-gray-500">Skipped — no phone</dt>
                  <dd className="text-gray-700 text-right">{preview.skipped_no_phone}</dd>
                  {preview.tasks_created > 0 && (
                    <>
                      <dt className="text-gray-500">Staff tasks (call/in-person)</dt>
                      <dd className="text-gray-700 text-right">{preview.tasks_created}</dd>
                    </>
                  )}
                </dl>
              ) : (
                <p className="text-sm text-gray-500">No data.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default RemindersSettings;
