// ============================================================================
// IMS 2.0 - Campaign Manager
// ============================================================================
// Marketing campaigns: real backend (routers/campaigns.py under /api/v1/marketing).
// Segment builder with LIVE audience counts, scheduling, send (reuses the
// existing send-bulk / DISPATCH_MODE infra), lifecycle controls + analytics.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Plus, BarChart3, Send, Pause, Play, Copy, Trash2, RefreshCw, X } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  campaignsApi,
  type Campaign,
  type CampaignSummary,
  type CampaignAnalytics,
  type Segment,
  type ScheduleKind,
} from '../../services/api/marketing';

// Templates the backend sender knows how to populate (notification_service.TEMPLATES).
const TEMPLATE_OPTIONS: { id: string; label: string }[] = [
  { id: 'PRESCRIPTION_EXPIRY', label: 'Prescription expiry reminder' },
  { id: 'BIRTHDAY_WISH', label: 'Birthday wish' },
  { id: 'ANNUAL_CHECKUP_REMINDER', label: 'Annual check-up reminder' },
  { id: 'WALKOUT_RECOVERY', label: 'Win-back / walkout recovery' },
  { id: 'REFERRAL_INVITE', label: 'Referral invite' },
  { id: 'GOOGLE_REVIEW_REQUEST', label: 'Google review request' },
  { id: 'NPS_SURVEY', label: 'NPS survey' },
];

const CHANNEL_OPTIONS = ['WHATSAPP', 'SMS', 'EMAIL'];
const CAMPAIGN_TYPES: { id: Campaign['type']; name: string; icon: string }[] = [
  { id: 'rx_renewal', name: 'Rx Renewal', icon: 'Rx' },
  { id: 'birthday', name: 'Birthday', icon: 'BD' },
  { id: 'winback', name: 'Win-back', icon: 'WB' },
  { id: 'custom', name: 'Custom', icon: '+' },
];

function statusClasses(status: Campaign['status']): string {
  switch (status) {
    case 'ACTIVE':
      return 'bg-green-100 text-green-700';
    case 'SCHEDULED':
      return 'bg-blue-100 text-blue-700';
    case 'COMPLETED':
      return 'bg-gray-100 text-gray-600';
    case 'PAUSED':
      return 'bg-orange-100 text-orange-700';
    default:
      return 'bg-yellow-100 text-yellow-700';
  }
}

export function CampaignManager() {
  const { user } = useAuth();
  const toast = useToast();
  const storeId = user?.activeStoreId || undefined;

  const [activeTab, setActiveTab] = useState<'campaigns' | 'builder'>('campaigns');
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [summary, setSummary] = useState<CampaignSummary | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedCampaign, setSelectedCampaign] = useState<string | null>(null);
  const [analytics, setAnalytics] = useState<CampaignAnalytics | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const loadCampaigns = useCallback(async () => {
    setLoading(true);
    try {
      const res = await campaignsApi.list({ store_id: storeId });
      setCampaigns(res.campaigns || []);
      setSummary(res.summary || null);
    } catch {
      toast.error('Could not load campaigns');
    } finally {
      setLoading(false);
    }
  }, [storeId, toast]);

  const loadSegments = useCallback(async () => {
    try {
      const res = await campaignsApi.listSegments(storeId);
      setSegments(res.segments || []);
    } catch {
      // segments are optional context; do not block the page
    }
  }, [storeId]);

  useEffect(() => {
    loadCampaigns();
    loadSegments();
  }, [loadCampaigns, loadSegments]);

  const handleSend = async (id: string) => {
    if (!window.confirm('Send this campaign now? Messages are queued through the marketing dispatcher (respects DISPATCH_MODE + opt-outs).')) return;
    setBusyId(id);
    try {
      const res = await campaignsApi.send(id);
      toast.success(`${res.queued} queued${res.skipped ? `, ${res.skipped} skipped` : ''} (status ${res.status})`);
      await loadCampaigns();
    } catch (e: unknown) {
      toast.error(extractErr(e) || 'Send failed');
    } finally {
      setBusyId(null);
    }
  };

  const handlePause = async (id: string) => {
    setBusyId(id);
    try {
      await campaignsApi.pause(id);
      toast.success('Campaign paused');
      await loadCampaigns();
    } catch (e: unknown) {
      toast.error(extractErr(e) || 'Pause failed');
    } finally {
      setBusyId(null);
    }
  };

  const handleResume = async (id: string) => {
    setBusyId(id);
    try {
      await campaignsApi.resume(id);
      toast.success('Campaign resumed');
      await loadCampaigns();
    } catch (e: unknown) {
      toast.error(extractErr(e) || 'Resume failed');
    } finally {
      setBusyId(null);
    }
  };

  const handleDuplicate = async (id: string) => {
    setBusyId(id);
    try {
      await campaignsApi.duplicate(id);
      toast.success('Campaign duplicated as a new draft');
      await loadCampaigns();
    } catch (e: unknown) {
      toast.error(extractErr(e) || 'Duplicate failed');
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm('Delete this campaign? This cannot be undone.')) return;
    setBusyId(id);
    try {
      await campaignsApi.remove(id);
      toast.success('Campaign deleted');
      setSelectedCampaign(null);
      await loadCampaigns();
    } catch (e: unknown) {
      toast.error(extractErr(e) || 'Delete failed');
    } finally {
      setBusyId(null);
    }
  };

  const openAnalytics = async (id: string) => {
    try {
      const a = await campaignsApi.analytics(id);
      setAnalytics(a);
    } catch (e: unknown) {
      toast.error(extractErr(e) || 'Could not load analytics');
    }
  };

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Marketing · Campaigns</div>
          <h1>Reach, on cue.</h1>
          <div className="hint">
            WhatsApp + SMS campaigns against live customer segments (Rx expiry, birthday, win-back), scheduled or sent on demand.
            Sends honour DISPATCH_MODE + marketing opt-outs.
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn sm" onClick={() => { loadCampaigns(); loadSegments(); }} title="Refresh">
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
          <button className="btn sm primary" onClick={() => setActiveTab('builder')}>
            <Plus className="w-4 h-4" /> New campaign
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200">
        {(['campaigns', 'builder'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === tab ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700',
            )}
          >
            {tab === 'campaigns' ? 'Active Campaigns' : 'Campaign Builder'}
          </button>
        ))}
      </div>

      {activeTab === 'campaigns' && (
        <div className="space-y-4">
          {/* Summary */}
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
            <SummaryCard label="Active" value={summary?.active ?? 0} tone="text-blue-600" />
            <SummaryCard label="Total Sent" value={summary?.total_sent ?? 0} tone="text-gray-900" />
            <SummaryCard label="Open Rate" value={`${summary?.open_rate ?? 0}%`} tone="text-green-600" />
            <SummaryCard label="Conversion" value={`${summary?.conversion ?? 0}%`} tone="text-purple-600" />
          </div>

          {/* Campaign List */}
          <div className="space-y-3">
            {loading && <div className="text-center text-gray-500 py-10 text-sm">Loading campaigns…</div>}
            {!loading && campaigns.length === 0 && (
              <div className="text-center text-gray-500 py-10 text-sm">
                No campaigns yet. Use the Campaign Builder to create one.
              </div>
            )}
            {campaigns.map((campaign) => {
              const openRate = campaign.sent_count > 0 ? ((campaign.opened_count / campaign.sent_count) * 100).toFixed(0) : '—';
              const expanded = selectedCampaign === campaign.campaign_id;
              return (
                <div
                  key={campaign.campaign_id}
                  className="bg-white border border-gray-200 rounded-lg p-4 hover:border-gray-300 transition-colors"
                >
                  <div
                    className="flex items-start justify-between mb-3 cursor-pointer"
                    onClick={() => setSelectedCampaign(expanded ? null : campaign.campaign_id)}
                  >
                    <div>
                      <h3 className="text-gray-900 font-semibold">{campaign.name}</h3>
                      <p className="text-gray-500 text-sm">
                        {campaign.type} · segment: {campaign.segment_key} · template: {campaign.template_id}
                      </p>
                    </div>
                    <span className={clsx('px-3 py-1 rounded-full text-xs font-semibold', statusClasses(campaign.status))}>
                      {campaign.status}
                    </span>
                  </div>

                  <div className="grid grid-cols-2 tablet:grid-cols-4 gap-2 mb-3 pb-3 border-b border-gray-200">
                    <Metric label="Audience" value={campaign.audience_count} />
                    <Metric label="Sent" value={campaign.sent_count} />
                    <Metric label="Open Rate" value={`${openRate}%`} tone="text-blue-600" />
                    <Metric label="Converted" value={campaign.converted_count} tone="text-green-600" />
                  </div>

                  {expanded && (
                    <div className="pt-3 border-t border-gray-200 flex flex-wrap gap-2">
                      {(campaign.status === 'DRAFT' || campaign.status === 'SCHEDULED' || campaign.status === 'ACTIVE') && (
                        <ActionButton
                          onClick={() => handleSend(campaign.campaign_id)}
                          busy={busyId === campaign.campaign_id}
                          className="bg-blue-600 hover:bg-blue-700 text-white"
                          icon={<Send className="w-4 h-4" />}
                          label="Send now"
                        />
                      )}
                      {(campaign.status === 'ACTIVE' || campaign.status === 'SCHEDULED') && (
                        <ActionButton
                          onClick={() => handlePause(campaign.campaign_id)}
                          busy={busyId === campaign.campaign_id}
                          className="bg-orange-100 hover:bg-orange-200 text-orange-700"
                          icon={<Pause className="w-4 h-4" />}
                          label="Pause"
                        />
                      )}
                      {campaign.status === 'PAUSED' && (
                        <ActionButton
                          onClick={() => handleResume(campaign.campaign_id)}
                          busy={busyId === campaign.campaign_id}
                          className="bg-green-100 hover:bg-green-200 text-green-700"
                          icon={<Play className="w-4 h-4" />}
                          label="Resume"
                        />
                      )}
                      <ActionButton
                        onClick={() => openAnalytics(campaign.campaign_id)}
                        className="bg-gray-100 hover:bg-gray-200 text-gray-700"
                        icon={<BarChart3 className="w-4 h-4" />}
                        label="Analytics"
                      />
                      <ActionButton
                        onClick={() => handleDuplicate(campaign.campaign_id)}
                        busy={busyId === campaign.campaign_id}
                        className="bg-gray-100 hover:bg-gray-200 text-gray-700"
                        icon={<Copy className="w-4 h-4" />}
                        label="Duplicate"
                      />
                      <ActionButton
                        onClick={() => handleDelete(campaign.campaign_id)}
                        busy={busyId === campaign.campaign_id}
                        className="bg-red-100 hover:bg-red-200 text-red-700"
                        icon={<Trash2 className="w-4 h-4" />}
                        label="Delete"
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {activeTab === 'builder' && (
        <CampaignBuilder
          segments={segments}
          storeId={storeId}
          onCreated={async () => {
            await loadCampaigns();
            setActiveTab('campaigns');
          }}
        />
      )}

      {analytics && <AnalyticsModal analytics={analytics} onClose={() => setAnalytics(null)} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Builder
// ---------------------------------------------------------------------------

function CampaignBuilder({
  segments,
  storeId,
  onCreated,
}: {
  segments: Segment[];
  storeId?: string;
  onCreated: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState('');
  const [type, setType] = useState<Campaign['type']>('rx_renewal');
  const [segmentKey, setSegmentKey] = useState<string>('rx_expiry');
  const [channels, setChannels] = useState<string[]>(['WHATSAPP']);
  const [templateId, setTemplateId] = useState<string>('PRESCRIPTION_EXPIRY');
  const [scheduleKind, setScheduleKind] = useState<ScheduleKind | 'NONE'>('NONE');
  const [sendAt, setSendAt] = useState('');
  const [frequency, setFrequency] = useState('WEEKLY');
  const [estimate, setEstimate] = useState<number | null>(null);
  const [estimating, setEstimating] = useState(false);
  const [saving, setSaving] = useState(false);

  const segmentOptions = useMemo(
    () =>
      segments.length > 0
        ? segments
        : [
            { key: 'rx_expiry', label: 'Prescription expiring', count: 0 },
            { key: 'birthday', label: 'Birthday this week', count: 0 },
            { key: 'winback', label: 'Win-back (lapsed)', count: 0 },
            { key: 'by_store', label: 'All store customers', count: 0 },
            { key: 'recent_buyers', label: 'Recent buyers', count: 0 },
          ] as Segment[],
    [segments],
  );

  // Live audience preview whenever the segment changes.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setEstimating(true);
      try {
        const res = await campaignsApi.previewSegment(segmentKey, { store_id: storeId });
        if (!cancelled) setEstimate(res.count);
      } catch {
        if (!cancelled) setEstimate(null);
      } finally {
        if (!cancelled) setEstimating(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [segmentKey, storeId]);

  const toggleChannel = (c: string) => {
    setChannels((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]));
  };

  const buildSchedule = () => {
    if (scheduleKind === 'NONE') return undefined;
    if (scheduleKind === 'ONE_TIME') return { kind: 'ONE_TIME' as const, send_at: sendAt ? new Date(sendAt).toISOString() : undefined };
    if (scheduleKind === 'RECURRING') return { kind: 'RECURRING' as const, frequency };
    return { kind: 'TRIGGERED' as const, trigger_event: segmentKey };
  };

  const validate = (): string | null => {
    if (name.trim().length < 2) return 'Give the campaign a name (min 2 characters).';
    if (channels.length === 0) return 'Pick at least one channel.';
    if (scheduleKind === 'ONE_TIME' && !sendAt) return 'Pick a send date/time for a one-time schedule.';
    return null;
  };

  const submit = async (alsoSchedule: boolean) => {
    const err = validate();
    if (err) {
      toast.warning(err);
      return;
    }
    setSaving(true);
    try {
      const created = await campaignsApi.create({
        name: name.trim(),
        type,
        segment_key: segmentKey,
        channels,
        template_id: templateId,
        store_id: storeId,
        schedule: alsoSchedule ? buildSchedule() : undefined,
      });
      const id = created.campaign.campaign_id;
      if (alsoSchedule && scheduleKind !== 'NONE') {
        const sched = buildSchedule();
        if (sched) {
          await campaignsApi.schedule(id, sched);
          toast.success('Campaign scheduled');
        } else {
          toast.success('Campaign saved');
        }
      } else {
        toast.success('Campaign saved as draft');
      }
      onCreated();
    } catch (e: unknown) {
      toast.error(extractErr(e) || 'Could not save campaign');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-white border border-gray-200 rounded-lg p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-6">Campaign Builder</h3>

        <div className="space-y-6">
          {/* Name */}
          <div>
            <label className="block text-gray-900 font-semibold mb-2">Campaign name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. June Rx Renewal"
              className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900 placeholder-gray-400"
            />
          </div>

          {/* Campaign Type */}
          <div>
            <label className="block text-gray-900 font-semibold mb-3">Campaign type</label>
            <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
              {CAMPAIGN_TYPES.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setType(t.id)}
                  className={clsx(
                    'p-4 border-2 rounded-lg transition-colors text-center',
                    type === t.id ? 'border-blue-500 bg-blue-50' : 'border-gray-200 hover:border-blue-300',
                  )}
                >
                  <div className="text-lg font-bold text-gray-700 mb-1">{t.icon}</div>
                  <p className="text-gray-900 font-semibold text-sm">{t.name}</p>
                </button>
              ))}
            </div>
          </div>

          {/* Audience / segment */}
          <div>
            <label className="block text-gray-900 font-semibold mb-2">Target audience (segment)</label>
            <select
              value={segmentKey}
              onChange={(e) => setSegmentKey(e.target.value)}
              className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900"
              title="Target audience segment"
            >
              {segmentOptions.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.label}
                  {typeof s.count === 'number' && segments.length > 0 ? ` (${s.count})` : ''}
                </option>
              ))}
            </select>
            <p className="text-gray-500 text-xs mt-2">
              {estimating ? 'Estimating audience…' : estimate === null ? 'Estimated audience: unavailable' : `Estimated audience: ${estimate} customer${estimate === 1 ? '' : 's'}`}
            </p>
          </div>

          {/* Channels */}
          <div>
            <label className="block text-gray-900 font-semibold mb-3">Channels</label>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              {CHANNEL_OPTIONS.map((c) => (
                <label key={c} className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={channels.includes(c)} onChange={() => toggleChannel(c)} className="rounded" />
                  <span className="text-gray-600 text-sm">{c}</span>
                </label>
              ))}
            </div>
            <p className="text-gray-400 text-xs mt-1">The first selected channel is used for sending; others are stored on the campaign.</p>
          </div>

          {/* Template */}
          <div>
            <label className="block text-gray-900 font-semibold mb-2">Message template</label>
            <select
              value={templateId}
              onChange={(e) => setTemplateId(e.target.value)}
              className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900"
              title="Message template"
            >
              {TEMPLATE_OPTIONS.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
            <p className="text-gray-400 text-xs mt-1">Wording is editable in Settings → Notification Templates.</p>
          </div>

          {/* Schedule */}
          <div>
            <label className="block text-gray-900 font-semibold mb-2">Schedule</label>
            <select
              value={scheduleKind}
              onChange={(e) => setScheduleKind(e.target.value as ScheduleKind | 'NONE')}
              className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900"
              title="Schedule kind"
            >
              <option value="NONE">No schedule (save as draft / send manually)</option>
              <option value="ONE_TIME">One-time (specific date/time)</option>
              <option value="RECURRING">Recurring</option>
              <option value="TRIGGERED">Triggered (on the segment event)</option>
            </select>
            {scheduleKind === 'ONE_TIME' && (
              <div className="mt-3">
                <label className="block text-gray-700 text-sm mb-1">Send at</label>
                <input
                  type="datetime-local"
                  value={sendAt}
                  onChange={(e) => setSendAt(e.target.value)}
                  className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900"
                  title="Send at"
                />
              </div>
            )}
            {scheduleKind === 'RECURRING' && (
              <div className="mt-3">
                <label className="block text-gray-700 text-sm mb-1">Frequency</label>
                <select
                  value={frequency}
                  onChange={(e) => setFrequency(e.target.value)}
                  className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900"
                  title="Frequency"
                >
                  <option value="DAILY">Daily</option>
                  <option value="WEEKLY">Weekly</option>
                  <option value="MONTHLY">Monthly</option>
                </select>
              </div>
            )}
            {scheduleKind === 'TRIGGERED' && (
              <p className="text-gray-500 text-xs mt-2">Will fire when a customer matches the "{segmentKey}" event (actioned by the MEGAPHONE agent).</p>
            )}
          </div>

          {/* Actions */}
          <div className="flex flex-wrap gap-3 pt-4 border-t border-gray-200">
            <button
              onClick={() => submit(true)}
              disabled={saving}
              className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-semibold disabled:opacity-50"
            >
              {scheduleKind === 'NONE' ? 'Save campaign' : 'Save & schedule'}
            </button>
            <button
              onClick={() => submit(false)}
              disabled={saving}
              className="px-6 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg font-semibold disabled:opacity-50"
            >
              Save as draft
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Analytics modal
// ---------------------------------------------------------------------------

function AnalyticsModal({ analytics, onClose }: { analytics: CampaignAnalytics; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-lg shadow-xl max-w-lg w-full p-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">{analytics.name || 'Campaign'} — analytics</h3>
            <p className="text-gray-500 text-sm">{analytics.status}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label="Close analytics modal">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
          <Metric label="Sent" value={analytics.sent} />
          <Metric label="Delivered" value={analytics.delivered} tone="text-green-600" />
          <Metric label="Failed" value={analytics.failed} tone="text-red-600" />
          <Metric label="Pending" value={analytics.pending} />
          <Metric label="Opened" value={analytics.opened} tone="text-blue-600" />
          <Metric label="Converted" value={analytics.converted} tone="text-purple-600" />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4 text-center">
          <div className="bg-gray-50 rounded p-2">
            <p className="text-gray-500 text-xs">Open rate</p>
            <p className="font-semibold text-gray-900">{analytics.open_rate}%</p>
          </div>
          <div className="bg-gray-50 rounded p-2">
            <p className="text-gray-500 text-xs">Delivery rate</p>
            <p className="font-semibold text-gray-900">{analytics.delivery_rate}%</p>
          </div>
          <div className="bg-gray-50 rounded p-2">
            <p className="text-gray-500 text-xs">Conversion</p>
            <p className="font-semibold text-gray-900">{analytics.conversion_rate}%</p>
          </div>
        </div>

        {Object.keys(analytics.by_channel || {}).length > 0 && (
          <div>
            <p className="text-gray-900 font-semibold text-sm mb-2">By channel</p>
            <div className="space-y-1">
              {Object.entries(analytics.by_channel).map(([ch, v]) => (
                <div key={ch} className="flex items-center justify-between text-sm text-gray-700 border-b border-gray-100 py-1">
                  <span>{ch}</span>
                  <span className="text-gray-500">
                    {v.sent} sent · {v.delivered} delivered · {v.failed} failed
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

function SummaryCard({ label, value, tone }: { label: string; value: string | number; tone: string }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <p className="text-gray-500 text-sm mb-1">{label}</p>
      <p className={clsx('text-2xl font-bold', tone)}>{value}</p>
    </div>
  );
}

function Metric({ label, value, tone = 'text-gray-900' }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="text-center">
      <p className="text-gray-500 text-xs mb-1">{label}</p>
      <p className={clsx('font-semibold', tone)}>{value}</p>
    </div>
  );
}

function ActionButton({
  onClick,
  label,
  icon,
  className,
  busy,
}: {
  onClick: () => void;
  label: string;
  icon: React.ReactNode;
  className: string;
  busy?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className={clsx('px-3 py-1 rounded text-sm font-semibold flex items-center justify-center gap-1 disabled:opacity-50', className)}
    >
      {icon}
      {label}
    </button>
  );
}

function extractErr(e: unknown): string | null {
  if (typeof e === 'object' && e !== null) {
    const anyE = e as { response?: { data?: { detail?: unknown } } };
    const detail = anyE.response?.data?.detail;
    if (typeof detail === 'string') return detail;
  }
  return null;
}

export default CampaignManager;
