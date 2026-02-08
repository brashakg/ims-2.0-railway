// ============================================================================
// IMS 2.0 - Campaign Manager
// ============================================================================
// Marketing campaigns: Rx Renewal, Birthday, Winback with builder & analytics

import { useState } from 'react';
import { Mail, MessageSquare, Plus, Edit, BarChart3 } from 'lucide-react';
import clsx from 'clsx';

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

const CAMPAIGNS: Campaign[] = [
  {
    id: '1',
    name: 'Valentine Renewal',
    type: 'rx_renewal',
    icon: <Mail className="w-5 h-5" />,
    status: 'active',
    audience: 1245,
    channels: ['email', 'sms'],
    startDate: '2024-02-01',
    endDate: '2024-02-14',
    template: 'Prescription Renewal Reminder',
    stats: {
      sent: 1245,
      opened: 687,
      clicked: 342,
      converted: 89,
    },
  },
  {
    id: '2',
    name: 'Birthday Specials',
    type: 'birthday',
    icon: <Mail className="w-5 h-5" />,
    status: 'active',
    audience: 450,
    channels: ['email', 'whatsapp'],
    startDate: '2024-01-01',
    endDate: '2024-12-31',
    template: 'Birthday Special',
    stats: {
      sent: 892,
      opened: 521,
      clicked: 198,
      converted: 67,
    },
  },
  {
    id: '3',
    name: 'Win Back Campaign',
    type: 'winback',
    icon: <MessageSquare className="w-5 h-5" />,
    status: 'scheduled',
    audience: 2100,
    channels: ['email', 'sms'],
    startDate: '2024-02-15',
    endDate: '2024-02-28',
    template: 'Exclusive Comeback Offer',
    stats: {
      sent: 0,
      opened: 0,
      clicked: 0,
      converted: 0,
    },
  },
];

export function CampaignManager() {
  const [activeTab, setActiveTab] = useState<'campaigns' | 'builder'>('campaigns');
  const [selectedCampaign, setSelectedCampaign] = useState<string | null>(null);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Campaign Manager</h1>
          <p className="text-gray-400">Marketing campaigns for customer engagement</p>
        </div>
        <button className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-semibold flex items-center gap-2">
          <Plus className="w-5 h-5" />
          Create Campaign
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-700">
        {(['campaigns', 'builder'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-gray-400 hover:text-gray-300'
            )}
          >
            {tab === 'campaigns' ? 'Active Campaigns' : 'Campaign Builder'}
          </button>
        ))}
      </div>

      {activeTab === 'campaigns' && (
        <div className="space-y-4">
          {/* Summary */}
          <div className="grid grid-cols-4 gap-4">
            <div className="bg-gray-800 rounded-lg p-4">
              <p className="text-gray-400 text-sm mb-1">Active</p>
              <p className="text-2xl font-bold text-blue-400">2</p>
            </div>
            <div className="bg-gray-800 rounded-lg p-4">
              <p className="text-gray-400 text-sm mb-1">Total Sent</p>
              <p className="text-2xl font-bold text-white">2,137</p>
            </div>
            <div className="bg-gray-800 rounded-lg p-4">
              <p className="text-gray-400 text-sm mb-1">Open Rate</p>
              <p className="text-2xl font-bold text-green-400">54%</p>
            </div>
            <div className="bg-gray-800 rounded-lg p-4">
              <p className="text-gray-400 text-sm mb-1">Conversion</p>
              <p className="text-2xl font-bold text-purple-400">7.2%</p>
            </div>
          </div>

          {/* Campaign List */}
          <div className="space-y-3">
            {CAMPAIGNS.map((campaign) => (
              <div
                key={campaign.id}
                onClick={() => setSelectedCampaign(selectedCampaign === campaign.id ? null : campaign.id)}
                className="bg-gray-800 rounded-lg p-4 border border-gray-700 hover:border-gray-600 cursor-pointer transition-colors"
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-start gap-3">
                    <div className="text-gray-400 mt-1">{campaign.icon}</div>
                    <div>
                      <h3 className="text-white font-semibold">{campaign.name}</h3>
                      <p className="text-gray-400 text-sm">
                        Template: {campaign.template}
                      </p>
                    </div>
                  </div>
                  <span className={clsx(
                    'px-3 py-1 rounded-full text-xs font-semibold',
                    campaign.status === 'active' ? 'bg-green-900 text-green-300' :
                    campaign.status === 'scheduled' ? 'bg-blue-900 text-blue-300' :
                    campaign.status === 'completed' ? 'bg-gray-700 text-gray-300' :
                    'bg-yellow-900 text-yellow-300'
                  )}>
                    {campaign.status.charAt(0).toUpperCase() + campaign.status.slice(1)}
                  </span>
                </div>

                <div className="grid grid-cols-4 gap-2 mb-3 pb-3 border-b border-gray-700">
                  <div className="text-center">
                    <p className="text-gray-400 text-xs mb-1">Audience</p>
                    <p className="text-white font-semibold">{campaign.audience}</p>
                  </div>
                  <div className="text-center">
                    <p className="text-gray-400 text-xs mb-1">Sent</p>
                    <p className="text-white font-semibold">{campaign.stats.sent}</p>
                  </div>
                  <div className="text-center">
                    <p className="text-gray-400 text-xs mb-1">Open Rate</p>
                    <p className="text-blue-400 font-semibold">
                      {campaign.stats.sent > 0 ? ((campaign.stats.opened / campaign.stats.sent) * 100).toFixed(0) : '—'}%
                    </p>
                  </div>
                  <div className="text-center">
                    <p className="text-gray-400 text-xs mb-1">Converted</p>
                    <p className="text-green-400 font-semibold">{campaign.stats.converted}</p>
                  </div>
                </div>

                {selectedCampaign === campaign.id && (
                  <div className="space-y-2 pt-3 border-t border-gray-700 grid grid-cols-3 gap-2">
                    <button className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm font-semibold flex items-center justify-center gap-1">
                      <Edit className="w-4 h-4" />
                      Edit
                    </button>
                    <button className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-white rounded text-sm font-semibold flex items-center justify-center gap-1">
                      <BarChart3 className="w-4 h-4" />
                      Analytics
                    </button>
                    <button className="px-3 py-1 bg-red-900 hover:bg-red-800 text-red-300 rounded text-sm font-semibold">
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
          <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
            <h3 className="text-lg font-semibold text-white mb-6">Campaign Builder</h3>

            <div className="space-y-6">
              {/* Campaign Type */}
              <div>
                <label className="block text-white font-semibold mb-3">Campaign Type</label>
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { id: 'rx_renewal', name: 'Rx Renewal', icon: '📋' },
                    { id: 'birthday', name: 'Birthday', icon: '🎂' },
                    { id: 'winback', name: 'Win-back', icon: '🎯' },
                  ].map((type) => (
                    <button
                      key={type.id}
                      className="p-4 border-2 border-gray-700 rounded-lg hover:border-blue-500 transition-colors text-center"
                    >
                      <div className="text-3xl mb-2">{type.icon}</div>
                      <p className="text-white font-semibold">{type.name}</p>
                    </button>
                  ))}
                </div>
              </div>

              {/* Audience */}
              <div>
                <label className="block text-white font-semibold mb-2">Target Audience</label>
                <input
                  type="text"
                  placeholder="Select customer segment..."
                  className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white placeholder-gray-500"
                />
                <p className="text-gray-400 text-xs mt-2">Estimated audience: 1,245 customers</p>
              </div>

              {/* Channels */}
              <div>
                <label className="block text-white font-semibold mb-3">Channels</label>
                <div className="grid grid-cols-4 gap-2">
                  {[
                    { name: 'Email', checked: true },
                    { name: 'SMS', checked: true },
                    { name: 'WhatsApp', checked: false },
                    { name: 'Push', checked: false },
                  ].map((channel) => (
                    <label key={channel.name} className="flex items-center gap-2 cursor-pointer">
                      <input type="checkbox" defaultChecked={channel.checked} className="rounded" />
                      <span className="text-gray-300 text-sm">{channel.name}</span>
                    </label>
                  ))}
                </div>
              </div>

              {/* Template */}
              <div>
                <label className="block text-white font-semibold mb-2">Template</label>
                <select className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white">
                  <option>Prescription Renewal Reminder</option>
                  <option>Vision Check-up</option>
                  <option>Lens Upgrade Offer</option>
                </select>
              </div>

              {/* Schedule */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-white font-semibold mb-2">Start Date</label>
                  <input type="date" className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white" />
                </div>
                <div>
                  <label className="block text-white font-semibold mb-2">End Date</label>
                  <input type="date" className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white" />
                </div>
              </div>

              {/* Actions */}
              <div className="flex gap-3 pt-4 border-t border-gray-700">
                <button className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-semibold">
                  Schedule Campaign
                </button>
                <button className="px-6 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg font-semibold">
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

export default CampaignManager;
