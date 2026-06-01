// ============================================================================
// IMS 2.0 - Campaign Manager
// ============================================================================
// Marketing campaigns wired to the real backend (/api/v1/marketing/campaigns):
//   - List real campaigns + a live summary (active / sent / delivery / conversion)
//   - Builder: type, segment (with LIVE audience count), channels, template,
//     schedule (one-time / recurring / triggered), Save Draft / Schedule / Send
//   - Per-campaign: analytics view + lifecycle (pause/resume/edit/duplicate/delete)
//
// Sending REUSES the shared marketing send infra (DISPATCH_MODE safety gate +
// consent + DLT audit). When DISPATCH_MODE is not "live", a Test-mode notice is
// shown on Send (messages simulated / only the test number is contacted).
//
// Fail-soft: every read tolerates an empty/failed endpoint so the page never
// white-screens.

import { useState, useEffect, useCallback } from 'react';
import {
  Plus, Edit, BarChart3, Pause, Play, Copy, Trash2, Send, CalendarClock,
  Users, RefreshCw, X, Megaphone, FlaskConical,
} from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { settingsApi } from '../../services/api/settings';
import {
  campaignsApi,
  type Campaign,
  type CampaignType,
  type CampaignChannel,
  type CampaignSegment,
  type CampaignSummary,
  type CampaignAnalytics,
  type ScheduleKind,
} from '../../services/api/marketing';

// Bulk-send roles (mirror backend _CAMPAIGN_ROLES). Only these (+ SUPERADMIN)
// may create / send / manage campaigns; everyone else gets a read-only view.
const MANAGE_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'];

const CHANNELS: CampaignChannel[] = ['WHATSAPP', 'SMS', 'EMAIL'];

const TYPE_META: Record<CampaignType, { label: string; icon: string; hint: string }> = {
  rx_renewal: { label: 'Rx Renewal', icon: 'Rx', hint: 'Prescription expiring soon' },
  birthday: { label: 'Birthday', icon: 'Bd', hint: "Today's birthdays" },
  winback: { label: 'Win-back', icon: 'Wb', hint: 'Lapsed buyers' },
  custom: { label: 'Custom', icon: 'Cu', hint: 'Any segment' },
};

// The known default templates (notification_service.TEMPLATES). The settings
// endpoint only returns OWNER-EDITED overrides, so we seed the picker with these
// defaults and merge in any saved ones on top.
const DEFAULT_TEMPLATE_IDS = [
  'PRESCRIPTION_EXPIRY',
  'BIRTHDAY_WISH',
  'ANNUAL_CHECKUP_REMINDER',
  'WALKOUT_RECOVERY',
  'REFERRAL_INVITE',
  'GOOGLE_REVIEW_REQUEST',
  'NPS_SURVEY',
  'ORDER_DELIVERED',
];

const STATUS_STYLE: Record<string, string> = {
  ACTIVE: 'bg-green-100 text-green-700',
  SCHEDULED: 'bg-blue-100 text-blue-700',
  COMPLETED: 'bg-gray-100 text-gray-600',
  PAUSED: 'bg-amber-100 text-amber-700',
  DRAFT: 'bg-yellow-100 text-yellow-700',
};

function titleCase(s: string) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1).toLowerCase() : s;
}

interface BuilderState {
  name: string;
  campaign_type: CampaignType;
  segment_id: string;
  channels: CampaignChannel[];
  template_id: string;
  schedule_kind: ScheduleKind;
  send_at: string;
  frequency: 'daily' | 'weekly' | 'monthly';
  trigger_event: string;
  notes: string;
}

const EMPTY_BUILDER: BuilderState = {
  name: '',
  campaign_type: 'custom',
  segment_id: '',
  channels: ['WHATSAPP'],
  template_id: '',
  schedule_kind: 'one_time',
  send_at: '',
  frequency: 'weekly',
  trigger_event: 'rx_expiry',
  notes: '',
};

export function CampaignManager() {
  const { user } = useAuth();
  const toast = useToast();
  const canManage = (user?.roles || []).some((r: string) => MANAGE_ROLES.includes(r));

  const [activeTab, setActiveTab] = useState<'campaigns' | 'builder'>('campaigns');
  const [loading, setLoading] = useState(true);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [summary, setSummary] = useState<CampaignSummary | null>(null);
  const [dispatchMode, setDispatchMode] = useState<string>('off');
  const [segments, setSegments] = useState<CampaignSegment[]>([]);
  const [templateIds, setTemplateIds] = useState<string[]>(DEFAULT_TEMPLATE_IDS);

  const [builder, setBuilder] = useState<BuilderState>(EMPTY_BUILDER);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [audienceCount, setAudienceCount] = useState<number | null>(null);
  const [audienceLoading, setAudienceLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Analytics drawer
  const [analyticsFor, setAnalyticsFor] = useState<string | null>(null);
  const [analytics, setAnalytics] = useState<CampaignAnalytics | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);

  const isLive = dispatchMode === 'live';

  // ----- Loaders (fail-soft) -----------------------------------------------

  const loadCampaigns = useCallback(async () => {
    setLoading(true);
    try {
      const data = await campaignsApi.list();
      setCampaigns(Array.isArray(data?.campaigns) ? data.campaigns : []);
      setSummary(data?.summary || null);
      if (data?.dispatch_mode) setDispatchMode(data.dispatch_mode);
    } catch {
      setCampaigns([]);
      setSummary(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadSegments = useCallback(async () => {
    try {
      const data = await campaignsApi.listSegments({ with_counts: true });
      setSegments(Array.isArray(data?.segments) ? data.segments : []);
      if (data?.dispatch_mode) setDispatchMode(data.dispatch_mode);
    } catch {
      setSegments([]);
    }
  }, []);

  const loadTemplates = useCallback(async () => {
    try {
      const saved = await settingsApi.getNotificationTemplates();
      const savedIds: string[] = Array.isArray(saved)
        ? saved.map((t: { template_id?: string }) => t?.template_id).filter(Boolean) as string[]
        : [];
      // Union of defaults + any saved override ids, de-duplicated, defaults first.
      const merged = [...DEFAULT_TEMPLATE_IDS];
      savedIds.forEach((id) => { if (!merged.includes(id)) merged.push(id); });
      setTemplateIds(merged);
    } catch {
      setTemplateIds(DEFAULT_TEMPLATE_IDS);
    }
  }, []);

  useEffect(() => {
    loadCampaigns();
    loadSegments();
    loadTemplates();
  }, [loadCampaigns, loadSegments, loadTemplates]);

  // ----- Builder: live audience count when segment changes -----------------

  useEffect(() => {
    let cancelled = false;
    if (!builder.segment_id) { setAudienceCount(null); return; }
    // If the segments list already carries a count, use it immediately...
    const seg = segments.find((s) => s.id === builder.segment_id);
    if (seg && typeof seg.audience_count === 'number') setAudienceCount(seg.audience_count);
    // ...and refine with a live preview (authoritative).
    setAudienceLoading(true);
    campaignsApi
      .previewSegment({ segment_id: builder.segment_id, sample_size: 0 })
      .then((res) => { if (!cancelled) setAudienceCount(res?.audience_count ?? 0); })
      .catch(() => { if (!cancelled && audienceCount == null) setAudienceCount(0); })
      .finally(() => { if (!cancelled) setAudienceLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [builder.segment_id, segments]);

  // ----- Builder helpers ----------------------------------------------------

  const setBuilderField = <K extends keyof BuilderState>(k: K, v: BuilderState[K]) =>
    setBuilder((b) => ({ ...b, [k]: v }));

  const pickType = (t: CampaignType) => {
    // Selecting a type pre-fills the matching segment + its default template.
    const seg = segments.find((s) => s.campaign_type === t);
    setBuilder((b) => ({
      ...b,
      campaign_type: t,
      segment_id: seg ? seg.id : b.segment_id,
      template_id: seg?.default_template || b.template_id,
    }));
  };

  const toggleChannel = (ch: CampaignChannel) =>
    setBuilder((b) => ({
      ...b,
      channels: b.channels.includes(ch)
        ? b.channels.filter((c) => c !== ch)
        : [...b.channels, ch],
    }));

  const resetBuilder = () => {
    setBuilder(EMPTY_BUILDER);
    setEditingId(null);
    setAudienceCount(null);
  };

  const openBuilderForNew = () => {
    resetBuilder();
    setActiveTab('builder');
  };

  const openBuilderForEdit = (c: Campaign) => {
    setEditingId(c.campaign_id);
    setBuilder({
      name: c.name || '',
      campaign_type: c.campaign_type || 'custom',
      segment_id: c.segment_id || '',
      channels: c.channels?.length ? c.channels : ['WHATSAPP'],
      template_id: c.template_id || '',
      schedule_kind: c.schedule?.kind || 'one_time',
      send_at: c.schedule?.send_at || '',
      frequency: (c.schedule?.frequency as BuilderState['frequency']) || 'weekly',
      trigger_event: c.schedule?.trigger_event || 'rx_expiry',
      notes: c.notes || '',
    });
    setActiveTab('builder');
  };

  const buildSchedulePayload = () => ({
    kind: builder.schedule_kind,
    send_at: builder.schedule_kind !== 'triggered' ? builder.send_at || null : null,
    frequency: builder.schedule_kind === 'recurring' ? builder.frequency : null,
    trigger_event: builder.schedule_kind === 'triggered' ? builder.trigger_event : null,
  });

  const validateBuilder = (): string | null => {
    if (!builder.name.trim()) return 'Give the campaign a name.';
    if (!builder.segment_id) return 'Pick a target segment.';
    if (!builder.channels.length) return 'Pick at least one channel.';
    if (!builder.template_id) return 'Pick a message template.';
    return null;
  };

  // Persist the builder (create or update). Returns the saved campaign id.
  const persist = async (): Promise<string | null> => {
    const err = validateBuilder();
    if (err) { toast.warning(err); return null; }
    setSaving(true);
    try {
      const base = {
        name: builder.name.trim(),
        campaign_type: builder.campaign_type,
        segment_id: builder.segment_id,
        channels: builder.channels,
        template_id: builder.template_id,
        schedule: buildSchedulePayload(),
        notes: builder.notes || undefined,
      };
      if (editingId) {
        const res = await campaignsApi.update(editingId, base);
        return res?.campaign?.campaign_id || editingId;
      }
      const res = await campaignsApi.create(base);
      return res?.campaign?.campaign_id || null;
    } catch (e) {
      toast.error(e);
      return null;
    } finally {
      setSaving(false);
    }
  };

  const onSaveDraft = async () => {
    const id = await persist();
    if (id) {
      toast.success(editingId ? 'Campaign updated' : 'Draft saved');
      resetBuilder();
      await loadCampaigns();
      setActiveTab('campaigns');
    }
  };

  const onSchedule = async () => {
    if (builder.schedule_kind !== 'triggered' && !builder.send_at) {
      toast.warning('Set a date/time to schedule, or choose a triggered life-event.');
      return;
    }
    const id = await persist();
    if (!id) return;
    try {
      await campaignsApi.schedule(id, buildSchedulePayload());
      toast.success('Campaign scheduled');
      resetBuilder();
      await loadCampaigns();
      setActiveTab('campaigns');
    } catch (e) {
      toast.error(e);
    }
  };

  const onSendNow = async () => {
    const id = await persist();
    if (!id) return;
    try {
      const res = await campaignsApi.send(id);
      if (res?.dispatch_mode !== 'live') {
        toast.info(res?.message || 'Queued in test mode (messages simulated)');
      } else {
        toast.success(res?.message || `${res?.queued ?? 0} messages queued`);
      }
      resetBuilder();
      await loadCampaigns();
      setActiveTab('campaigns');
    } catch (e) {
      toast.error(e);
    }
  };

  // ----- Lifecycle actions on a listed campaign ----------------------------

  const withBusy = async (id: string, fn: () => Promise<void>) => {
    setBusyId(id);
    try { await fn(); } finally { setBusyId(null); }
  };

  const onPauseResume = (c: Campaign) =>
    withBusy(c.campaign_id, async () => {
      try {
        if (c.status === 'PAUSED') {
          await campaignsApi.resume(c.campaign_id);
          toast.success('Campaign resumed');
        } else {
          await campaignsApi.pause(c.campaign_id);
          toast.success('Campaign paused');
        }
        await loadCampaigns();
      } catch (e) { toast.error(e); }
    });

  const onDuplicate = (c: Campaign) =>
    withBusy(c.campaign_id, async () => {
      try {
        await campaignsApi.duplicate(c.campaign_id);
        toast.success('Campaign duplicated');
        await loadCampaigns();
      } catch (e) { toast.error(e); }
    });

  const onDelete = (c: Campaign) =>
    withBusy(c.campaign_id, async () => {
      if (!window.confirm(`Delete campaign "${c.name}"? This cannot be undone.`)) return;
      try {
        await campaignsApi.remove(c.campaign_id);
        toast.success('Campaign deleted');
        await loadCampaigns();
      } catch (e) { toast.error(e); }
    });

  const onSendExisting = (c: Campaign) =>
    withBusy(c.campaign_id, async () => {
      try {
        const res = await campaignsApi.send(c.campaign_id);
        if (res?.dispatch_mode !== 'live') toast.info(res?.message || 'Queued in test mode');
        else toast.success(res?.message || `${res?.queued ?? 0} queued`);
        await loadCampaigns();
      } catch (e) { toast.error(e); }
    });

  const openAnalytics = async (c: Campaign) => {
    setAnalyticsFor(c.campaign_id);
    setAnalytics(null);
    setAnalyticsLoading(true);
    try {
      const data = await campaignsApi.analytics(c.campaign_id);
      setAnalytics(data);
    } catch {
      setAnalytics(null);
    } finally {
      setAnalyticsLoading(false);
    }
  };

  // ----- Render -------------------------------------------------------------

  const selectedSegment = segments.find((s) => s.id === builder.segment_id);

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Marketing · Campaigns</div>
          <h1>Reach, on cue.</h1>
          <div className="hint">
            WhatsApp · SMS · Email campaigns against customer segments, triggered by life events
            (Rx expiry, birthday, win-back) or scheduled.
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn sm" onClick={() => { loadCampaigns(); loadSegments(); }} title="Refresh">
            <RefreshCw className="w-4 h-4" />
          </button>
          {canManage && (
            <button className="btn sm primary" onClick={openBuilderForNew}>
              <Plus className="w-4 h-4" /> New campaign
            </button>
          )}
        </div>
      </div>

      {/* Dispatch-mode banner: test vs live */}
      {!isLive && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          <FlaskConical className="w-4 h-4 mt-0.5 shrink-0" />
          <div>
            <span className="font-semibold">Test mode.</span>{' '}
            Sending is currently simulated{dispatchMode === 'test' ? ' — only the configured test number is contacted' : ' (no messages leave the system)'}.
            Campaigns are saved and queued, but customers are not messaged until an admin sets dispatch to live.
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200">
        {(['campaigns', 'builder'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            )}
          >
            {tab === 'campaigns' ? 'Campaigns' : editingId ? 'Edit Campaign' : 'Campaign Builder'}
          </button>
        ))}
      </div>

      {activeTab === 'campaigns' && (
        <div className="space-y-4">
          {/* Summary */}
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
            <SummaryCard label="Active" value={summary ? String(summary.active) : '0'} tone="blue" />
            <SummaryCard label="Total Sent" value={summary ? String(summary.total_sent) : '0'} tone="ink" />
            <SummaryCard label="Delivery Rate" value={`${summary ? summary.open_rate : 0}%`} tone="green" />
            <SummaryCard label="Conversion" value={`${summary ? summary.conversion_rate : 0}%`} tone="purple" />
          </div>

          {/* Campaign List */}
          <div className="space-y-3">
            {loading && (
              <div className="text-center text-gray-500 py-10 text-sm">Loading campaigns…</div>
            )}
            {!loading && campaigns.length === 0 && (
              <div className="bg-white border border-dashed border-gray-300 rounded-lg p-10 text-center">
                <Megaphone className="w-8 h-8 text-gray-300 mx-auto mb-3" />
                <p className="text-gray-700 font-medium">No campaigns yet</p>
                <p className="text-gray-500 text-sm mt-1">
                  Build one to reach a customer segment by WhatsApp, SMS or Email.
                </p>
                {canManage && (
                  <button className="btn sm primary mt-4 inline-flex" onClick={openBuilderForNew}>
                    <Plus className="w-4 h-4" /> New campaign
                  </button>
                )}
              </div>
            )}

            {!loading && campaigns.map((c) => {
              const sent = c.stats?.sent || 0;
              const delivered = c.stats?.delivered || 0;
              const deliveryPct = sent > 0 ? Math.round((delivered / sent) * 100) : null;
              const busy = busyId === c.campaign_id;
              return (
                <div
                  key={c.campaign_id}
                  className="bg-white border border-gray-200 rounded-lg p-4 hover:border-gray-300 transition-colors"
                >
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex items-start gap-3">
                      <div className="mt-0.5 w-9 h-9 rounded-md bg-gray-100 text-gray-500 flex items-center justify-center text-xs font-semibold">
                        {TYPE_META[c.campaign_type]?.icon || 'Cu'}
                      </div>
                      <div>
                        <h3 className="text-gray-900 font-semibold">{c.name}</h3>
                        <p className="text-gray-500 text-sm">
                          {TYPE_META[c.campaign_type]?.label || 'Custom'} ·{' '}
                          {(c.channels || []).join(', ') || '—'} · Template: {c.template_id}
                        </p>
                      </div>
                    </div>
                    <span className={clsx('px-3 py-1 rounded-full text-xs font-semibold', STATUS_STYLE[c.status] || STATUS_STYLE.DRAFT)}>
                      {titleCase(c.status)}
                    </span>
                  </div>

                  <div className="grid grid-cols-2 tablet:grid-cols-4 gap-2 mb-3 pb-3 border-b border-gray-200">
                    <Stat label="Sent" value={String(sent)} />
                    <Stat label="Delivered" value={String(delivered)} />
                    <Stat label="Delivery" value={deliveryPct == null ? '—' : `${deliveryPct}%`} tone="blue" />
                    <Stat label="Converted" value={String(c.stats?.converted || 0)} tone="green" />
                  </div>

                  <div className="flex flex-wrap gap-2 pt-1">
                    <button
                      className="btn sm ghost"
                      onClick={() => openAnalytics(c)}
                    >
                      <BarChart3 className="w-4 h-4" /> Analytics
                    </button>
                    {canManage && (
                      <>
                        <button className="btn sm" disabled={busy} onClick={() => openBuilderForEdit(c)}>
                          <Edit className="w-4 h-4" /> Edit
                        </button>
                        <button
                          className="btn sm primary"
                          disabled={busy || c.status === 'PAUSED'}
                          title={c.status === 'PAUSED' ? 'Resume before sending' : 'Send now'}
                          onClick={() => onSendExisting(c)}
                        >
                          <Send className="w-4 h-4" /> Send now
                        </button>
                        <button className="btn sm" disabled={busy} onClick={() => onPauseResume(c)}>
                          {c.status === 'PAUSED'
                            ? (<><Play className="w-4 h-4" /> Resume</>)
                            : (<><Pause className="w-4 h-4" /> Pause</>)}
                        </button>
                        <button className="btn sm" disabled={busy} onClick={() => onDuplicate(c)}>
                          <Copy className="w-4 h-4" /> Duplicate
                        </button>
                        <button className="btn sm danger" disabled={busy} onClick={() => onDelete(c)}>
                          <Trash2 className="w-4 h-4" /> Delete
                        </button>
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {activeTab === 'builder' && (
        <div className="space-y-6">
          {!canManage ? (
            <div className="bg-white border border-gray-200 rounded-lg p-6 text-sm text-gray-600">
              You have read-only access to campaigns. Ask a store manager or admin to create or send campaigns.
            </div>
          ) : (
            <div className="bg-white border border-gray-200 rounded-lg p-6">
              <div className="flex items-center justify-between mb-6">
                <h3 className="text-lg font-semibold text-gray-900">
                  {editingId ? 'Edit Campaign' : 'Campaign Builder'}
                </h3>
                {editingId && (
                  <button className="btn sm ghost" onClick={resetBuilder}>
                    <X className="w-4 h-4" /> Cancel edit
                  </button>
                )}
              </div>

              <div className="space-y-6">
                {/* Name */}
                <div>
                  <label className="block text-gray-900 font-semibold mb-2">Campaign Name</label>
                  <input
                    type="text"
                    value={builder.name}
                    onChange={(e) => setBuilderField('name', e.target.value)}
                    placeholder="e.g. June Rx renewal push"
                    className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900 placeholder-gray-400"
                  />
                </div>

                {/* Campaign Type */}
                <div>
                  <label className="block text-gray-900 font-semibold mb-3">Campaign Type</label>
                  <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
                    {(Object.keys(TYPE_META) as CampaignType[]).map((t) => (
                      <button
                        key={t}
                        type="button"
                        onClick={() => pickType(t)}
                        className={clsx(
                          'p-4 border-2 rounded-lg transition-colors text-center',
                          builder.campaign_type === t
                            ? 'border-blue-500 bg-blue-50'
                            : 'border-gray-200 hover:border-blue-300'
                        )}
                      >
                        <div className="w-8 h-8 mx-auto mb-2 rounded-md bg-gray-100 text-gray-600 flex items-center justify-center text-xs font-semibold">
                          {TYPE_META[t].icon}
                        </div>
                        <p className="text-gray-900 font-semibold text-sm">{TYPE_META[t].label}</p>
                        <p className="text-gray-500 text-xs mt-0.5">{TYPE_META[t].hint}</p>
                      </button>
                    ))}
                  </div>
                </div>

                {/* Audience / Segment */}
                <div>
                  <label className="block text-gray-900 font-semibold mb-2">Target Audience</label>
                  <select
                    value={builder.segment_id}
                    onChange={(e) => setBuilderField('segment_id', e.target.value)}
                    className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900"
                  >
                    <option value="">Select a customer segment…</option>
                    {segments.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.label}{typeof s.audience_count === 'number' ? ` (${s.audience_count})` : ''}
                      </option>
                    ))}
                  </select>
                  <div className="flex items-center gap-2 mt-2 text-xs">
                    <Users className="w-3.5 h-3.5 text-gray-400" />
                    <span className="text-gray-600">
                      Estimated audience:{' '}
                      <span className="font-semibold text-gray-900">
                        {audienceLoading ? '…' : (audienceCount ?? 0)}
                      </span>{' '}
                      customers
                    </span>
                    {selectedSegment && (
                      <span className="text-gray-400">· {selectedSegment.description}</span>
                    )}
                  </div>
                </div>

                {/* Channels */}
                <div>
                  <label className="block text-gray-900 font-semibold mb-3">Channels</label>
                  <div className="flex flex-wrap gap-4">
                    {CHANNELS.map((ch) => (
                      <label key={ch} className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={builder.channels.includes(ch)}
                          onChange={() => toggleChannel(ch)}
                          className="rounded"
                        />
                        <span className="text-gray-700 text-sm">{titleCase(ch)}</span>
                      </label>
                    ))}
                  </div>
                  <p className="text-gray-400 text-xs mt-2">
                    The first channel is the primary; recipients without that contact are skipped at send.
                  </p>
                </div>

                {/* Template */}
                <div>
                  <label className="block text-gray-900 font-semibold mb-2">Message Template</label>
                  <select
                    value={builder.template_id}
                    onChange={(e) => setBuilderField('template_id', e.target.value)}
                    className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900"
                  >
                    <option value="">Select a template…</option>
                    {templateIds.map((id) => (
                      <option key={id} value={id}>{id}</option>
                    ))}
                  </select>
                  <p className="text-gray-400 text-xs mt-2">
                    Templates are managed under Settings · Notification Templates.
                  </p>
                </div>

                {/* Schedule */}
                <div>
                  <label className="block text-gray-900 font-semibold mb-3">Schedule</label>
                  <div className="flex flex-wrap gap-2 mb-3">
                    {([
                      { k: 'one_time', label: 'One-time' },
                      { k: 'recurring', label: 'Recurring' },
                      { k: 'triggered', label: 'Triggered (life-event)' },
                    ] as { k: ScheduleKind; label: string }[]).map((opt) => (
                      <button
                        key={opt.k}
                        type="button"
                        onClick={() => setBuilderField('schedule_kind', opt.k)}
                        className={clsx(
                          'px-3 py-1.5 rounded-md text-sm border transition-colors',
                          builder.schedule_kind === opt.k
                            ? 'border-blue-500 bg-blue-50 text-blue-700'
                            : 'border-gray-200 text-gray-600 hover:border-blue-300'
                        )}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>

                  {builder.schedule_kind !== 'triggered' && (
                    <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
                      <div>
                        <label className="block text-gray-600 text-sm mb-1">
                          {builder.schedule_kind === 'recurring' ? 'First run (date & time)' : 'Send at (date & time)'}
                        </label>
                        <input
                          type="datetime-local"
                          value={builder.send_at}
                          onChange={(e) => setBuilderField('send_at', e.target.value)}
                          className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900"
                        />
                      </div>
                      {builder.schedule_kind === 'recurring' && (
                        <div>
                          <label className="block text-gray-600 text-sm mb-1">Repeat</label>
                          <select
                            value={builder.frequency}
                            onChange={(e) => setBuilderField('frequency', e.target.value as BuilderState['frequency'])}
                            className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900"
                          >
                            <option value="daily">Daily</option>
                            <option value="weekly">Weekly</option>
                            <option value="monthly">Monthly</option>
                          </select>
                        </div>
                      )}
                    </div>
                  )}

                  {builder.schedule_kind === 'triggered' && (
                    <div>
                      <label className="block text-gray-600 text-sm mb-1">Life-event trigger</label>
                      <select
                        value={builder.trigger_event}
                        onChange={(e) => setBuilderField('trigger_event', e.target.value)}
                        className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900"
                      >
                        <option value="rx_expiry">Rx expiry approaching</option>
                        <option value="birthday">Birthday</option>
                        <option value="walkout">Walkout / no purchase</option>
                      </select>
                      <p className="text-gray-400 text-xs mt-2">
                        Triggered campaigns fire automatically per matching customer (driven by the MEGAPHONE agent).
                      </p>
                    </div>
                  )}
                </div>

                {/* Notes */}
                <div>
                  <label className="block text-gray-900 font-semibold mb-2">Notes (optional)</label>
                  <textarea
                    value={builder.notes}
                    onChange={(e) => setBuilderField('notes', e.target.value)}
                    rows={2}
                    placeholder="Internal note about this campaign…"
                    className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900 placeholder-gray-400"
                  />
                </div>

                {/* Send-mode notice */}
                {!isLive && (
                  <div className="rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">
                    <FlaskConical className="w-3.5 h-3.5 inline mr-1 -mt-0.5" />
                    Test mode: <b>Send Now</b> will queue {audienceCount ?? 0} message(s) but they are simulated
                    {dispatchMode === 'test' ? ' (only the test number is contacted)' : ' and not delivered'}.
                  </div>
                )}

                {/* Actions */}
                <div className="flex flex-wrap gap-3 pt-4 border-t border-gray-200">
                  <button className="btn primary" disabled={saving} onClick={onSendNow}>
                    <Send className="w-4 h-4" /> Send Now
                  </button>
                  <button className="btn" disabled={saving} onClick={onSchedule}>
                    <CalendarClock className="w-4 h-4" /> Schedule
                  </button>
                  <button className="btn ghost" disabled={saving} onClick={onSaveDraft}>
                    {editingId ? 'Save Changes' : 'Save as Draft'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Analytics drawer */}
      {analyticsFor && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={() => setAnalyticsFor(null)}>
          <div
            className="w-full max-w-md h-full bg-white shadow-xl overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between p-4 border-b border-gray-200 sticky top-0 bg-white">
              <h3 className="font-semibold text-gray-900">Campaign analytics</h3>
              <button className="btn sm icon ghost" onClick={() => setAnalyticsFor(null)}>
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="p-4 space-y-4">
              {analyticsLoading && <div className="text-sm text-gray-500">Loading…</div>}
              {!analyticsLoading && !analytics && (
                <div className="text-sm text-gray-500">No analytics available yet.</div>
              )}
              {!analyticsLoading && analytics && (
                <>
                  <div>
                    <p className="text-gray-900 font-semibold">{analytics.name}</p>
                    <p className="text-gray-500 text-sm">
                      {titleCase(analytics.status || '')} · {analytics.segment_id} · {(analytics.channels || []).join(', ')}
                    </p>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <Stat label="Audience messages" value={String(analytics.totals.audience_messages)} />
                    <Stat label="Queued" value={String(analytics.totals.queued)} />
                    <Stat label="Sent" value={String(analytics.totals.sent)} tone="blue" />
                    <Stat label="Delivered" value={String(analytics.totals.delivered)} tone="green" />
                    <Stat label="Failed" value={String(analytics.totals.failed)} tone="red" />
                    <Stat label="Converted" value={String(analytics.totals.converted)} tone="green" />
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    <Stat label="Delivery rate" value={`${analytics.rates.delivery_rate}%`} />
                    <Stat label="Failure rate" value={`${analytics.rates.failure_rate}%`} />
                    <Stat label="Conversion" value={`${analytics.rates.conversion_rate}%`} />
                  </div>

                  {/* Per-channel */}
                  {Object.keys(analytics.per_channel || {}).length > 0 && (
                    <div>
                      <p className="text-gray-700 font-semibold text-sm mb-2">By channel</p>
                      <div className="space-y-2">
                        {Object.entries(analytics.per_channel).map(([ch, m]) => (
                          <div key={ch} className="flex items-center justify-between border border-gray-200 rounded-md px-3 py-2 text-sm">
                            <span className="font-medium text-gray-800">{titleCase(ch)}</span>
                            <span className="text-gray-500">
                              {m.sent} sent · {m.delivered} delivered · {m.failed} failed
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {analytics.dispatch_mode !== 'live' && (
                    <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1.5">
                      Dispatch mode is <b>{analytics.dispatch_mode}</b> — queued messages are simulated, not delivered.
                    </p>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---- Small presentational helpers ----------------------------------------

function SummaryCard({ label, value, tone }: { label: string; value: string; tone: 'blue' | 'ink' | 'green' | 'purple' }) {
  const color = {
    blue: 'text-blue-600',
    ink: 'text-gray-900',
    green: 'text-green-600',
    purple: 'text-purple-600',
  }[tone];
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <p className="text-gray-500 text-sm mb-1">{label}</p>
      <p className={clsx('text-2xl font-bold', color)}>{value}</p>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'blue' | 'green' | 'red' }) {
  const color = tone === 'blue' ? 'text-blue-600' : tone === 'green' ? 'text-green-600' : tone === 'red' ? 'text-red-600' : 'text-gray-900';
  return (
    <div className="text-center">
      <p className="text-gray-500 text-xs mb-1">{label}</p>
      <p className={clsx('font-semibold', color)}>{value}</p>
    </div>
  );
}

export default CampaignManager;
