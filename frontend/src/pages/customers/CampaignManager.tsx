// ============================================================================
// IMS 2.0 - Campaign Manager
// ============================================================================
// Marketing campaigns: Rx Renewal, Birthday, Winback with builder & analytics

import { useState, useEffect } from 'react';
import { Plus, Edit, BarChart3, ShieldCheck, Save, Loader2 } from 'lucide-react';
import clsx from 'clsx';
import { customerApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

type CampaignType = 'rx_renewal' | 'birthday' | 'winback';

interface Campaign {
  id: string;
  name: string;
  type: CampaignType;
  icon: React.ReactNode;
  status: 'draft' | 'scheduled' | 'active' | 'completed';
  audience: number;
  channels: string[];
  startDate: string;
  endDate: string;
  template: string;
  stats: {
    sent: number;
    opened: number;
    clicked: number;
    converted: number;
  };
}

export function CampaignManager() {
  const [activeTab, setActiveTab] = useState<'campaigns' | 'builder' | 'consent'>('campaigns');
  const [selectedCampaign, setSelectedCampaign] = useState<string | null>(null);
  const [campaigns] = useState<Campaign[]>([]);

  return (
    <div className="inv-body">
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800">
        Preview — campaign tooling isn't connected to a backend yet. Nothing here is saved or sent.
      </div>

      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Marketing · Campaigns</div>
          <h1>Reach, on cue.</h1>
          <div className="hint">WhatsApp + SMS campaigns against customer segments, triggered by life events (Rx expiry, birthday, walkout) or scheduled.</div>
        </div>
        <button className="btn sm primary" disabled title="Coming soon">
          <Plus className="w-4 h-4" /> New campaign
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200">
        {(['campaigns', 'builder', 'consent'] as const).map((tab) => (
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
            {tab === 'campaigns' ? 'Active Campaigns' : tab === 'builder' ? 'Campaign Builder' : 'Consent Text'}
          </button>
        ))}
      </div>

      {activeTab === 'consent' && <ConsentTextEditor />}

      {activeTab === 'campaigns' && (
        <div className="space-y-4">
          {/* Summary */}
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <p className="text-gray-500 text-sm mb-1">Active</p>
              <p className="text-2xl font-bold text-blue-600">0</p>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <p className="text-gray-500 text-sm mb-1">Total Sent</p>
              <p className="text-2xl font-bold text-gray-900">0</p>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <p className="text-gray-500 text-sm mb-1">Open Rate</p>
              <p className="text-2xl font-bold text-green-600">0%</p>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <p className="text-gray-500 text-sm mb-1">Conversion</p>
              <p className="text-2xl font-bold text-purple-600">0%</p>
            </div>
          </div>

          {/* Campaign List */}
          <div className="space-y-3">
            {campaigns.length === 0 && (
              <div className="text-center text-gray-500 py-10 text-sm">No campaigns yet.</div>
            )}
            {campaigns.map((campaign) => (
              <div
                key={campaign.id}
                onClick={() => setSelectedCampaign(selectedCampaign === campaign.id ? null : campaign.id)}
                className="bg-white border border-gray-200 rounded-lg p-4 hover:border-gray-300 cursor-pointer transition-colors"
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-start gap-3">
                    <div className="text-gray-500 mt-1">{campaign.icon}</div>
                    <div>
                      <h3 className="text-gray-900 font-semibold">{campaign.name}</h3>
                      <p className="text-gray-500 text-sm">
                        Template: {campaign.template}
                      </p>
                    </div>
                  </div>
                  <span className={clsx(
                    'px-3 py-1 rounded-full text-xs font-semibold',
                    campaign.status === 'active' ? 'bg-green-100 text-green-700' :
                    campaign.status === 'scheduled' ? 'bg-blue-100 text-blue-700' :
                    campaign.status === 'completed' ? 'bg-gray-100 text-gray-600' :
                    'bg-yellow-100 text-yellow-700'
                  )}>
                    {campaign.status.charAt(0).toUpperCase() + campaign.status.slice(1)}
                  </span>
                </div>

                <div className="grid grid-cols-2 tablet:grid-cols-4 gap-2 mb-3 pb-3 border-b border-gray-200">
                  <div className="text-center">
                    <p className="text-gray-500 text-xs mb-1">Audience</p>
                    <p className="text-gray-900 font-semibold">{campaign.audience}</p>
                  </div>
                  <div className="text-center">
                    <p className="text-gray-500 text-xs mb-1">Sent</p>
                    <p className="text-gray-900 font-semibold">{campaign.stats.sent}</p>
                  </div>
                  <div className="text-center">
                    <p className="text-gray-500 text-xs mb-1">Open Rate</p>
                    <p className="text-blue-600 font-semibold">
                      {campaign.stats.sent > 0 ? ((campaign.stats.opened / campaign.stats.sent) * 100).toFixed(0) : '—'}%
                    </p>
                  </div>
                  <div className="text-center">
                    <p className="text-gray-500 text-xs mb-1">Converted</p>
                    <p className="text-green-600 font-semibold">{campaign.stats.converted}</p>
                  </div>
                </div>

                {selectedCampaign === campaign.id && (
                  <div className="space-y-2 pt-3 border-t border-gray-200 grid grid-cols-3 gap-2">
                    <button className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm font-semibold flex items-center justify-center gap-1" disabled title="Coming soon">
                      <Edit className="w-4 h-4" />
                      Edit
                    </button>
                    <button className="px-3 py-1 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded text-sm font-semibold flex items-center justify-center gap-1" disabled title="Coming soon">
                      <BarChart3 className="w-4 h-4" />
                      Analytics
                    </button>
                    <button className="px-3 py-1 bg-red-100 hover:bg-red-200 text-red-700 rounded text-sm font-semibold" disabled title="Coming soon">
                      Pause
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === 'builder' && (
        <div className="space-y-6">
          <div className="bg-white border border-gray-200 rounded-lg p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-6">Campaign Builder</h3>

            <div className="space-y-6">
              {/* Campaign Type */}
              <div>
                <label className="block text-gray-900 font-semibold mb-3">Campaign Type</label>
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { id: 'rx_renewal', name: 'Rx Renewal', icon: '📋' },
                    { id: 'birthday', name: 'Birthday', icon: '🎂' },
                    { id: 'winback', name: 'Win-back', icon: '🎯' },
                  ].map((type) => (
                    <button
                      key={type.id}
                      className="p-4 border-2 border-gray-200 rounded-lg hover:border-blue-500 transition-colors text-center"
                    >
                      <div className="text-3xl mb-2">{type.icon}</div>
                      <p className="text-gray-900 font-semibold">{type.name}</p>
                    </button>
                  ))}
                </div>
              </div>

              {/* Audience */}
              <div>
                <label className="block text-gray-900 font-semibold mb-2">Target Audience</label>
                <input
                  type="text"
                  placeholder="Select customer segment..."
                  className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900 placeholder-gray-400"
                />
                <p className="text-gray-500 text-xs mt-2">Estimated audience: 0 customers</p>
              </div>

              {/* Channels */}
              <div>
                <label className="block text-gray-900 font-semibold mb-3">Channels</label>
                <div className="grid grid-cols-4 gap-2">
                  {[
                    { name: 'Email', checked: true },
                    { name: 'SMS', checked: true },
                    { name: 'WhatsApp', checked: false },
                    { name: 'Push', checked: false },
                  ].map((channel) => (
                    <label key={channel.name} className="flex items-center gap-2 cursor-pointer">
                      <input type="checkbox" defaultChecked={channel.checked} className="rounded" />
                      <span className="text-gray-600 text-sm">{channel.name}</span>
                    </label>
                  ))}
                </div>
              </div>

              {/* Template */}
              <div>
                <label className="block text-gray-900 font-semibold mb-2">Template</label>
                <select className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900">
                  <option>Prescription Renewal Reminder</option>
                  <option>Vision Check-up</option>
                  <option>Lens Upgrade Offer</option>
                </select>
              </div>

              {/* Schedule */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-gray-900 font-semibold mb-2">Start Date</label>
                  <input type="date" className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900" />
                </div>
                <div>
                  <label className="block text-gray-900 font-semibold mb-2">End Date</label>
                  <input type="date" className="w-full bg-white border border-gray-300 rounded px-3 py-2 text-gray-900" />
                </div>
              </div>

              {/* Actions */}
              <div className="flex gap-3 pt-4 border-t border-gray-200">
                <button className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-semibold" disabled title="Coming soon">
                  Schedule Campaign
                </button>
                <button className="px-6 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-lg font-semibold" disabled title="Coming soon">
                  Save as Draft
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// DPDP data-consent wording editor (ADMIN/SUPERADMIN). The text shown to a
// customer at creation time; editing bumps the version so each stored consent
// traces to the exact wording the customer agreed to.
// ---------------------------------------------------------------------------
function ConsentTextEditor() {
  const { hasRole } = useAuth();
  const toast = useToast();
  const canEdit = hasRole?.('ADMIN') || hasRole?.('SUPERADMIN');
  const [text, setText] = useState('');
  const [version, setVersion] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    customerApi.getConsentText()
      .then((r) => { setText(r.text || ''); setVersion(r.version || ''); })
      .catch(() => { /* keep blank */ })
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    if (text.trim().length < 10) { toast.error('Consent text is too short'); return; }
    setSaving(true);
    try {
      const r = await customerApi.updateConsentText(text.trim());
      setVersion(r?.version || version);
      toast.success('Consent text updated');
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : 'Could not save consent text');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <div className="flex items-center gap-2 mb-1">
          <ShieldCheck className="w-4 h-4 text-teal-600" />
          <h3 className="font-semibold text-gray-900">Data-Storage Consent (DPDP)</h3>
          {version && <span className="text-xs text-gray-400">v{version}</span>}
        </div>
        <p className="text-sm text-gray-500 mb-3">
          The wording a customer agrees to when you store their personal data. Shown on the
          Add-Customer form; the version is recorded on each customer's consent.
        </p>
        {loading ? (
          <div className="py-8 text-center text-gray-400"><Loader2 className="w-5 h-5 animate-spin inline" /> Loading…</div>
        ) : (
          <>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={!canEdit}
              rows={5}
              maxLength={4000}
              className="input-field w-full resize-none"
              placeholder="Enter the consent wording shown to customers…"
            />
            <div className="flex items-center justify-between mt-2">
              <span className="text-xs text-gray-400">{text.length}/4000</span>
              {canEdit ? (
                <button onClick={save} disabled={saving} className="btn-primary flex items-center gap-2">
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  Save
                </button>
              ) : (
                <span className="text-xs text-gray-400">Read-only — ADMIN can edit</span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default CampaignManager;
