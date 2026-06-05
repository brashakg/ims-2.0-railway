// ============================================================================
// IMS 2.0 - Ad Performance Dashboard (CRM-16)
// ============================================================================
// Agency oversight dashboard: Google Ads + Meta Ads spend / ROAS / leads.
// Gated to SUPERADMIN / ADMIN. Fail-soft: when credentials are not
// configured the page shows a clear "connect your ad account" empty-state
// instead of an error. Light theme only; bv-red accent.

import { useState, useEffect, useCallback } from 'react';
import {
  BarChart2,
  TrendingUp,
  Users,
  DollarSign,
  RefreshCw,
  AlertCircle,
  ExternalLink,
  Filter,
  ChevronDown,
} from 'lucide-react';
import { marketingApi, type AdPerformanceResponse, type CampaignRowOut } from '../../services/api/marketing';

// ---- date helpers ----------------------------------------------------------

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

// ---- formatting helpers ----------------------------------------------------

function fmtINR(v: number): string {
  if (v >= 100_000) return `${(v / 100_000).toFixed(1)}L`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return v.toFixed(0);
}

function fmtNum(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return v.toLocaleString('en-IN');
}

function fmtPct(v: number): string {
  return `${v.toFixed(2)}%`;
}

// ---- channel badge ---------------------------------------------------------

function ChannelBadge({ channel }: { channel: string }) {
  const isGoogle = channel === 'google';
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
        isGoogle
          ? 'bg-blue-50 text-blue-700 border border-blue-200'
          : 'bg-indigo-50 text-indigo-700 border border-indigo-200'
      }`}
    >
      {isGoogle ? 'Google' : 'Meta'}
    </span>
  );
}

// ---- summary card ----------------------------------------------------------

interface SummaryCardProps {
  label: string;
  value: string;
  sub?: string;
  icon: React.ElementType;
  accent?: boolean;
}

function SummaryCard({ label, value, sub, icon: Icon, accent }: SummaryCardProps) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4 flex items-start gap-3">
      <div
        className={`p-2 rounded-lg flex-shrink-0 ${
          accent ? 'bg-bv-red-50 text-bv-red-600' : 'bg-gray-100 text-gray-600'
        }`}
      >
        <Icon size={18} />
      </div>
      <div className="min-w-0">
        <p className="text-xs text-gray-500 font-medium uppercase tracking-wide">{label}</p>
        <p className="text-xl font-semibold text-gray-900 mt-0.5">{value}</p>
        {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

// ---- connect channel card --------------------------------------------------

function ConnectChannelCard({
  channel,
  label,
  href,
}: {
  channel: string;
  label: string;
  href?: string;
}) {
  return (
    <div className="border border-dashed border-gray-300 rounded-lg p-6 flex flex-col items-center gap-3 text-center bg-gray-50">
      <AlertCircle size={28} className="text-gray-400" />
      <div>
        <p className="font-medium text-gray-700">{label} not connected</p>
        <p className="text-sm text-gray-500 mt-1">
          Add a <code className="bg-gray-100 px-1 rounded text-xs">{channel}</code>{' '}
          integration doc to the integrations collection to pull live ad data.
        </p>
      </div>
      {href && (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-sm text-bv-red-600 hover:underline"
        >
          How to connect <ExternalLink size={12} />
        </a>
      )}
    </div>
  );
}

// ---- main page -------------------------------------------------------------

type ChannelFilter = 'all' | 'google' | 'meta';
type DatePreset = '7d' | '30d' | '90d' | 'custom';

const DATE_PRESETS: { label: string; value: DatePreset; days?: number }[] = [
  { label: 'Last 7 days', value: '7d', days: 7 },
  { label: 'Last 30 days', value: '30d', days: 30 },
  { label: 'Last 90 days', value: '90d', days: 90 },
  { label: 'Custom', value: 'custom' },
];

export function AdPerformancePage() {
  const [channelFilter, setChannelFilter] = useState<ChannelFilter>('all');
  const [datePreset, setDatePreset] = useState<DatePreset>('30d');
  const [fromDate, setFromDate] = useState(daysAgo(30));
  const [toDate, setToDate] = useState(today());
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<AdPerformanceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sortCol, setSortCol] = useState<keyof CampaignRowOut>('spend');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: { from: string; to: string; channel?: 'google' | 'meta' } = {
        from: fromDate,
        to: toDate,
      };
      if (channelFilter !== 'all') params.channel = channelFilter;
      const result = await marketingApi.getAdPerformance(params);
      setData(result);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to load ad performance data';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [fromDate, toDate, channelFilter]);

  // Initial load
  useEffect(() => {
    load();
  }, [load]);

  // Date preset handler
  function applyPreset(preset: DatePreset) {
    setDatePreset(preset);
    const p = DATE_PRESETS.find((d) => d.value === preset);
    if (p?.days) {
      setFromDate(daysAgo(p.days));
      setToDate(today());
    }
  }

  // Sort handler
  function handleSort(col: keyof CampaignRowOut) {
    if (sortCol === col) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(col);
      setSortDir('desc');
    }
  }

  const rows: CampaignRowOut[] = data?.rows ?? [];
  const sortedRows = [...rows].sort((a, b) => {
    const av = a[sortCol];
    const bv = b[sortCol];
    if (typeof av === 'number' && typeof bv === 'number') {
      return sortDir === 'asc' ? av - bv : bv - av;
    }
    return sortDir === 'asc'
      ? String(av).localeCompare(String(bv))
      : String(bv).localeCompare(String(av));
  });

  const noChannels = data && !data.google_configured && !data.meta_configured;

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <BarChart2 size={20} className="text-bv-red-600" />
            Ad Performance
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Agency oversight — Google Ads + Meta Ads spend, ROAS and leads
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-gray-300 text-sm text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          {loading ? 'Loading...' : 'Refresh'}
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        {/* Date presets */}
        <div className="flex rounded border border-gray-200 overflow-hidden text-sm">
          {DATE_PRESETS.map((p) => (
            <button
              key={p.value}
              onClick={() => applyPreset(p.value)}
              className={`px-3 py-1.5 ${
                datePreset === p.value
                  ? 'bg-bv-red-600 text-white'
                  : 'bg-white text-gray-700 hover:bg-gray-50'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* Custom date range */}
        {datePreset === 'custom' && (
          <div className="flex items-center gap-2 text-sm">
            <input
              type="date"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
              className="border border-gray-300 rounded px-2 py-1.5 text-sm"
            />
            <span className="text-gray-400">to</span>
            <input
              type="date"
              value={toDate}
              onChange={(e) => setToDate(e.target.value)}
              className="border border-gray-300 rounded px-2 py-1.5 text-sm"
            />
          </div>
        )}

        {/* Channel filter */}
        <div className="flex items-center gap-1 ml-auto">
          <Filter size={14} className="text-gray-400" />
          <select
            value={channelFilter}
            onChange={(e) => setChannelFilter(e.target.value as ChannelFilter)}
            className="border border-gray-300 rounded px-2 py-1.5 text-sm bg-white"
          >
            <option value="all">All channels</option>
            <option value="google">Google Ads only</option>
            <option value="meta">Meta Ads only</option>
          </select>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
          <AlertCircle size={16} className="text-red-500 mt-0.5 flex-shrink-0" />
          <div>
            <p className="text-sm font-medium text-red-700">Could not load data</p>
            <p className="text-xs text-red-600 mt-0.5">{error}</p>
          </div>
        </div>
      )}

      {/* Not configured: show connect cards */}
      {noChannels && (
        <div className="space-y-3">
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4 flex items-start gap-3">
            <AlertCircle size={16} className="text-yellow-600 mt-0.5 flex-shrink-0" />
            <p className="text-sm text-yellow-800">{data?.note}</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {(channelFilter === 'all' || channelFilter === 'google') && (
              <ConnectChannelCard
                channel="google_ads"
                label="Google Ads"
                href="https://developers.google.com/google-ads/api/docs/oauth/overview"
              />
            )}
            {(channelFilter === 'all' || channelFilter === 'meta') && (
              <ConnectChannelCard
                channel="meta_ads"
                label="Meta Ads"
                href="https://developers.facebook.com/docs/marketing-apis/"
              />
            )}
          </div>
        </div>
      )}

      {/* Summary cards */}
      {data && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <SummaryCard
            label="Total Spend"
            value={`Rs ${fmtINR(data.total_spend)}`}
            sub={`${data.google_configured ? 'Google' : ''}${data.google_configured && data.meta_configured ? ' + ' : ''}${data.meta_configured ? 'Meta' : ''}`}
            icon={DollarSign}
            accent
          />
          <SummaryCard
            label="Impressions"
            value={fmtNum(data.total_impressions)}
            icon={BarChart2}
          />
          <SummaryCard
            label="Total Leads"
            value={fmtNum(data.total_conversions)}
            sub={data.total_cpl > 0 ? `CPL Rs ${fmtINR(data.total_cpl)}` : undefined}
            icon={Users}
          />
          <SummaryCard
            label="Blended ROAS"
            value={data.blended_roas > 0 ? `${data.blended_roas.toFixed(2)}x` : 'N/A'}
            sub={data.blended_roas === 0 ? 'Requires revenue data' : undefined}
            icon={TrendingUp}
          />
        </div>
      )}

      {/* Partial connection notes */}
      {data && (data.google_configured || data.meta_configured) && (
        <div className="flex flex-wrap gap-2 text-xs text-gray-500">
          {!data.google_configured && channelFilter !== 'meta' && (
            <span className="inline-flex items-center gap-1 bg-blue-50 text-blue-600 border border-blue-100 px-2 py-1 rounded">
              <AlertCircle size={11} />
              Google Ads not connected
            </span>
          )}
          {!data.meta_configured && channelFilter !== 'google' && (
            <span className="inline-flex items-center gap-1 bg-indigo-50 text-indigo-600 border border-indigo-100 px-2 py-1 rounded">
              <AlertCircle size={11} />
              Meta Ads not connected
            </span>
          )}
        </div>
      )}

      {/* Campaign table */}
      {sortedRows.length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-700">
              Campaigns ({sortedRows.length})
            </h2>
            <span className="text-xs text-gray-400">
              {fromDate} to {toDate}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-100">
                <tr>
                  {[
                    { key: 'channel' as const, label: 'Channel', align: 'left' },
                    { key: 'campaign_name' as const, label: 'Campaign', align: 'left' },
                    { key: 'spend' as const, label: 'Spend (Rs)', align: 'right' },
                    { key: 'impressions' as const, label: 'Impressions', align: 'right' },
                    { key: 'clicks' as const, label: 'Clicks', align: 'right' },
                    { key: 'ctr' as const, label: 'CTR', align: 'right' },
                    { key: 'conversions' as const, label: 'Leads', align: 'right' },
                    { key: 'cpl' as const, label: 'CPL (Rs)', align: 'right' },
                  ].map(({ key, label, align }) => (
                    <th
                      key={key}
                      className={`px-3 py-2.5 text-xs font-medium text-gray-500 uppercase tracking-wide cursor-pointer select-none ${
                        align === 'right' ? 'text-right' : 'text-left'
                      } hover:text-gray-700`}
                      onClick={() => handleSort(key)}
                    >
                      <span className="inline-flex items-center gap-0.5">
                        {label}
                        {sortCol === key && (
                          <ChevronDown
                            size={12}
                            className={sortDir === 'asc' ? 'rotate-180' : ''}
                          />
                        )}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {sortedRows.map((row, idx) => (
                  <tr key={`${row.channel}-${row.campaign_id}-${idx}`} className="hover:bg-gray-50">
                    <td className="px-3 py-2.5">
                      <ChannelBadge channel={row.channel} />
                    </td>
                    <td className="px-3 py-2.5 max-w-xs">
                      <span className="text-gray-800 font-medium truncate block" title={row.campaign_name}>
                        {row.campaign_name}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right font-medium text-gray-900">
                      {fmtINR(row.spend)}
                    </td>
                    <td className="px-3 py-2.5 text-right text-gray-600">
                      {fmtNum(row.impressions)}
                    </td>
                    <td className="px-3 py-2.5 text-right text-gray-600">
                      {fmtNum(row.clicks)}
                    </td>
                    <td className="px-3 py-2.5 text-right text-gray-600">
                      {fmtPct(row.ctr)}
                    </td>
                    <td className="px-3 py-2.5 text-right text-gray-600">
                      {row.conversions > 0 ? fmtNum(row.conversions) : '—'}
                    </td>
                    <td className="px-3 py-2.5 text-right text-gray-600">
                      {row.cpl > 0 ? `${fmtINR(row.cpl)}` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Empty state when configured but no campaigns */}
      {data && !noChannels && sortedRows.length === 0 && !loading && (
        <div className="bg-white border border-gray-200 rounded-lg p-10 text-center">
          <BarChart2 size={32} className="mx-auto text-gray-300 mb-3" />
          <p className="text-gray-500 font-medium">No campaign data for this period</p>
          <p className="text-sm text-gray-400 mt-1">
            Try a wider date range or check that your ad accounts have active campaigns.
          </p>
        </div>
      )}

      {/* Footer note */}
      {data?.fetched_at && (
        <p className="text-xs text-gray-400 text-right">
          Last fetched: {new Date(data.fetched_at).toLocaleString('en-IN')}
        </p>
      )}
    </div>
  );
}

export default AdPerformancePage;
