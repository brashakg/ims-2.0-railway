'use client';

import { useState, useEffect } from 'react';
import { Users, TrendingUp, Target, ShoppingCart, AlertCircle } from 'lucide-react';
import Topbar from '@/components/Topbar';

interface MarketingData {
  totalCustomers: number;
  acceptsMarketing: number;
  topRevenueCustomers: number;
  avgOrderValue: number;
  segments: {
    highValue: number;
    regular: number;
    oneTime: number;
    marketingOptIn: number;
    marketingOptOut: number;
  };
  revenueBySegment: {
    segment: string;
    revenue: number;
    count: number;
  }[];
}

interface StatCardProps {
  label: string;
  value: string;
  subtext?: string;
  icon: React.ReactNode;
  color: string;
}

export default function MarketingPage() {
  const [data, setData] = useState<MarketingData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true);
        const [statsRes, reportsRes] = await Promise.all([
          fetch('/api/customers/stats'),
          fetch('/api/reports?type=sales'),
        ]);

        const statsJson = statsRes.ok ? await statsRes.json() : {};
        const reportsJson = reportsRes.ok ? await reportsRes.json() : {};
        const stats = statsJson.data || statsJson;
        const reports = reportsJson.data || reportsJson;

        setData({
          totalCustomers: stats.totalCustomers || 0,
          acceptsMarketing: stats.acceptsMarketing || 0,
          topRevenueCustomers: stats.topRevenueCustomers || 0,
          avgOrderValue: reports.avgOrderValue || 0,
          segments: stats.segments || {},
          revenueBySegment: reports.revenueBySegment || [],
        });
      } catch (error) {
        console.error('Error fetching marketing data:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  const StatCard: React.FC<StatCardProps> = ({ label, value, subtext, icon, color }) => (
    <div className={`bg-gradient-to-br ${color} rounded-lg shadow-lg p-6 text-white`}>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium opacity-90">{label}</p>
          <p className="text-3xl font-bold mt-2">{value}</p>
          {subtext && <p className="text-xs opacity-75 mt-1">{subtext}</p>}
        </div>
        <div className="opacity-20">{icon}</div>
      </div>
    </div>
  );

  const marketingOptInPercent = data
    ? ((data.segments?.marketingOptIn || 0) / (data.totalCustomers || 1)) * 100
    : 0;

  const campaignIdeas = [
    {
      title: 'Re-engage One-Time Buyers',
      description: `Target ${data?.segments?.oneTime || 0} customers who made a single purchase. Send personalized win-back campaign.`,
      icon: '🎯',
    },
    {
      title: 'VIP Loyalty Program',
      description: `Create exclusive offers for ${data?.topRevenueCustomers || 0} high-value customers (₹10,000+ spent).`,
      icon: '👑',
    },
    {
      title: 'Regular Customer Retention',
      description: `Develop targeted retention plan for ${data?.segments?.regular || 0} repeat customers with loyalty rewards.`,
      icon: '🔄',
    },
    {
      title: 'Email List Growth',
      description: `${marketingOptInPercent.toFixed(1)}% of customers opted in to marketing. Run opt-in campaign to increase engagement.`,
      icon: '📧',
    },
    {
      title: 'Cross-sell Campaign',
      description: `Based on average order value of ₹${data?.avgOrderValue?.toLocaleString('en-IN')}, create bundled offers.`,
      icon: '📦',
    },
    {
      title: 'Seasonal Promotions',
      description: 'Plan quarterly promotions targeting high-value and regular segments with seasonal themes.',
      icon: '🎉',
    },
  ];

  return (
    <>
      <Topbar
        title="Marketing"
        subtitle="Customer segments, engagement, growth"
        breadcrumb={[{ label: 'Home', href: '/dashboard' }, { label: 'Marketing' }]}
        primaryAction={null}
      />
      <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>

        {/* Stat Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <StatCard
            label="Total Customers"
            value={data?.totalCustomers?.toLocaleString() || '0'}
            icon={<Users size={32} />}
            color="from-blue-400 to-blue-600"
          />
          <StatCard
            label="Marketing Opt-In"
            value={`${marketingOptInPercent.toFixed(1)}%`}
            subtext={`${data?.segments?.marketingOptIn || 0} customers`}
            icon={<TrendingUp size={32} />}
            color="from-green-400 to-green-600"
          />
          <StatCard
            label="High-Value Customers"
            value={data?.topRevenueCustomers?.toLocaleString() || '0'}
            subtext="(Spent ₹10,000+)"
            icon={<Target size={32} />}
            color="from-purple-400 to-purple-600"
          />
          <StatCard
            label="Avg Order Value"
            value={`₹${data?.avgOrderValue?.toLocaleString('en-IN') || '0'}`}
            icon={<ShoppingCart size={32} />}
            color="from-amber-400 to-amber-600"
          />
        </div>

        {/* Customer Segments */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
          <div className="bg-white rounded-lg shadow-md p-6">
            <h2 className="text-xl font-semibold mb-4">Customer Segments</h2>
            <div className="space-y-4">
              <div className="flex items-center justify-between p-4 bg-gradient-to-r from-purple-50 to-purple-100 rounded-lg">
                <div>
                  <p className="font-semibold text-gray-900">High-Value Customers</p>
                  <p className="text-sm text-gray-600">Spent over ₹10,000</p>
                </div>
                <div className="text-right">
                  <p className="text-2xl font-bold text-purple-600">{data?.segments?.highValue || 0}</p>
                  <p className="text-xs text-gray-600">
                    {data ? (((data.segments?.highValue || 0) / (data.totalCustomers || 1)) * 100).toFixed(1) : 0}%
                  </p>
                </div>
              </div>

              <div className="flex items-center justify-between p-4 bg-gradient-to-r from-blue-50 to-blue-100 rounded-lg">
                <div>
                  <p className="font-semibold text-gray-900">Regular Customers</p>
                  <p className="text-sm text-gray-600">2+ orders made</p>
                </div>
                <div className="text-right">
                  <p className="text-2xl font-bold text-blue-600">{data?.segments?.regular || 0}</p>
                  <p className="text-xs text-gray-600">
                    {data ? (((data.segments?.regular || 0) / (data.totalCustomers || 1)) * 100).toFixed(1) : 0}%
                  </p>
                </div>
              </div>

              <div className="flex items-center justify-between p-4 bg-gradient-to-r from-amber-50 to-amber-100 rounded-lg">
                <div>
                  <p className="font-semibold text-gray-900">One-Time Buyers</p>
                  <p className="text-sm text-gray-600">Single purchase only</p>
                </div>
                <div className="text-right">
                  <p className="text-2xl font-bold text-amber-600">{data?.segments?.oneTime || 0}</p>
                  <p className="text-xs text-gray-600">
                    {data ? (((data.segments?.oneTime || 0) / (data.totalCustomers || 1)) * 100).toFixed(1) : 0}%
                  </p>
                </div>
              </div>
            </div>
          </div>

          <div className="bg-white rounded-lg shadow-md p-6">
            <h2 className="text-xl font-semibold mb-4">Email Preference</h2>
            <div className="space-y-4">
              <div>
                <div className="flex justify-between text-sm mb-2">
                  <span className="font-medium">Opted In (Marketing)</span>
                  <span className="text-gray-600">{data?.segments?.marketingOptIn || 0}</span>
                </div>
                <div className="bg-gray-200 rounded-full h-8 overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-green-400 to-green-600 flex items-center justify-end pr-3"
                    style={{ width: `${marketingOptInPercent}%` }}
                  >
                    {marketingOptInPercent > 20 && (
                      <span className="text-xs font-bold text-white">{marketingOptInPercent.toFixed(0)}%</span>
                    )}
                  </div>
                </div>
              </div>

              <div>
                <div className="flex justify-between text-sm mb-2">
                  <span className="font-medium">Opted Out</span>
                  <span className="text-gray-600">{data?.segments?.marketingOptOut || 0}</span>
                </div>
                <div className="bg-gray-200 rounded-full h-8 overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-red-400 to-red-600"
                    style={{ width: `${100 - marketingOptInPercent}%` }}
                  />
                </div>
              </div>

              <div className="mt-6 p-4 bg-blue-50 rounded-lg border border-blue-200">
                <p className="text-sm text-blue-800">
                  <span className="font-semibold">Tip:</span> Focus on re-engagement campaigns to increase the opt-in rate. Even
                  a 5% increase can significantly boost your reach.
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Revenue by Segment */}
        {data?.revenueBySegment && data.revenueBySegment.length > 0 && (
          <div className="bg-white rounded-lg shadow-md p-6 mb-8">
            <h2 className="text-xl font-semibold mb-4">Revenue by Segment</h2>
            <div className="space-y-3">
              {data.revenueBySegment.map((seg, idx) => {
                const maxRevenue = Math.max(...(data.revenueBySegment?.map((s) => s.revenue) || [1]));
                const percentage = (seg.revenue / maxRevenue) * 100;
                return (
                  <div key={idx}>
                    <div className="flex justify-between text-sm mb-1">
                      <span className="font-medium">{seg.segment}</span>
                      <span className="text-gray-600">
                        ₹{seg.revenue.toLocaleString('en-IN')} ({seg.count} customers)
                      </span>
                    </div>
                    <div className="bg-gray-200 rounded-full h-6 overflow-hidden">
                      <div
                        className="h-full bg-gradient-to-r from-indigo-500 to-purple-500 transition-all"
                        style={{ width: `${percentage}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Campaign Ideas */}
        <div className="bg-white rounded-lg shadow-md p-6 mb-8">
          <h2 className="text-xl font-semibold mb-4">Campaign Ideas</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {campaignIdeas.map((campaign, idx) => (
              <div key={idx} className="p-4 border rounded-lg hover:shadow-md transition-shadow">
                <div className="flex items-start gap-3">
                  <span className="text-2xl">{campaign.icon}</span>
                  <div className="flex-1">
                    <h3 className="font-semibold text-gray-900 mb-1">{campaign.title}</h3>
                    <p className="text-sm text-gray-600">{campaign.description}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Google Analytics Banner */}
        <div className="bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 rounded-lg p-6 flex items-start gap-4">
          <div className="mt-1">
            <AlertCircle className="text-blue-600" size={24} />
          </div>
          <div className="flex-1">
            <h3 className="font-semibold text-gray-900 mb-1">Deeper Insights Available</h3>
            <p className="text-sm text-gray-700">
              Connect Google Analytics to track customer behavior, traffic sources, conversion funnels, and device analytics.
              This will help you optimize campaigns and identify new growth opportunities.
            </p>
          </div>
        </div>
      </div>
    </>
  );
}
