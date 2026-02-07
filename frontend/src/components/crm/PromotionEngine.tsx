// ============================================================================
// IMS 2.0 - Dynamic Promotion & Campaign Engine
// ============================================================================
// Create and manage promotional campaigns, coupons, and targeted offers
// Designed for Indian optical retail (festival offers, eye care awareness)

import { useState } from 'react';
import {
  Gift, Plus, Search, Calendar, Tag, Percent,
  IndianRupee, Eye, X, CheckCircle, Clock, Pause,
  Copy, Download, TrendingUp, Target, Filter,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { exportToCSV } from '../../utils/exportUtils';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type CampaignType = 'PERCENTAGE' | 'FIXED_AMOUNT' | 'BOGO' | 'BUNDLE' | 'LOYALTY_BONUS';
type CampaignStatus = 'DRAFT' | 'ACTIVE' | 'PAUSED' | 'ENDED' | 'SCHEDULED';
type TargetSegment = 'ALL' | 'NEW_CUSTOMERS' | 'LOYAL' | 'AT_RISK' | 'LAPSED' | 'PREMIUM' | 'CUSTOM';

interface Campaign {
  id: string;
  name: string;
  description: string;
  type: CampaignType;
  status: CampaignStatus;
  discountValue: number; // percent or fixed amount
  minPurchase: number;
  maxDiscount?: number; // cap for percentage discounts
  startDate: string;
  endDate: string;
  targetSegment: TargetSegment;
  targetCategories: string[]; // product categories
  couponCode?: string;
  totalCouponsGenerated: number;
  totalRedemptions: number;
  totalRevenue: number;
  totalDiscount: number;
  createdAt: string;
  isAutoApply: boolean;
}

interface CouponCode {
  code: string;
  campaignId: string;
  isUsed: boolean;
  usedBy?: string;
  usedAt?: string;
  amount?: number;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CAMPAIGN_TYPES: { type: CampaignType; label: string; icon: typeof Percent; description: string }[] = [
  { type: 'PERCENTAGE', label: 'Percentage Off', icon: Percent, description: 'Flat % discount on qualifying purchases' },
  { type: 'FIXED_AMOUNT', label: 'Fixed Amount Off', icon: IndianRupee, description: 'Fixed ₹ discount on purchases' },
  { type: 'BOGO', label: 'Buy One Get One', icon: Gift, description: 'Free item with qualifying purchase' },
  { type: 'BUNDLE', label: 'Bundle Deal', icon: Tag, description: 'Discount on frame + lens combo' },
  { type: 'LOYALTY_BONUS', label: 'Loyalty Points Bonus', icon: TrendingUp, description: 'Extra loyalty points on purchase' },
];

const TARGET_SEGMENTS: { value: TargetSegment; label: string; description: string }[] = [
  { value: 'ALL', label: 'All Customers', description: 'Every customer sees this offer' },
  { value: 'NEW_CUSTOMERS', label: 'New Customers', description: 'First-time buyers only' },
  { value: 'LOYAL', label: 'Loyal Customers', description: 'Champions and loyal segment (RFM)' },
  { value: 'AT_RISK', label: 'At Risk', description: 'Customers who haven\'t visited recently' },
  { value: 'LAPSED', label: 'Lapsed Customers', description: 'Inactive for 6+ months' },
  { value: 'PREMIUM', label: 'Premium Buyers', description: 'Top 20% by spend' },
];

const OPTICAL_CATEGORIES = [
  'Frames', 'Sunglasses', 'Lenses', 'Progressive Lenses', 'Contact Lenses',
  'Lens Coatings', 'Accessories', 'Eye Drops', 'Lens Solution',
];

const STATUS_CONFIG: Record<CampaignStatus, { label: string; color: string; bgColor: string; icon: typeof CheckCircle }> = {
  DRAFT: { label: 'Draft', color: 'text-gray-600', bgColor: 'bg-gray-100', icon: Clock },
  ACTIVE: { label: 'Active', color: 'text-green-700', bgColor: 'bg-green-100', icon: CheckCircle },
  PAUSED: { label: 'Paused', color: 'text-yellow-700', bgColor: 'bg-yellow-100', icon: Pause },
  ENDED: { label: 'Ended', color: 'text-gray-500', bgColor: 'bg-gray-100', icon: X },
  SCHEDULED: { label: 'Scheduled', color: 'text-blue-700', bgColor: 'bg-blue-100', icon: Calendar },
};

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const INITIAL_CAMPAIGNS: Campaign[] = [
  {
    id: '1',
    name: 'Republic Day Frame Sale',
    description: '20% off on all premium frames. Celebrate with a new look!',
    type: 'PERCENTAGE',
    status: 'ACTIVE',
    discountValue: 20,
    minPurchase: 3000,
    maxDiscount: 2000,
    startDate: '2026-01-20',
    endDate: '2026-02-10',
    targetSegment: 'ALL',
    targetCategories: ['Frames', 'Sunglasses'],
    couponCode: 'REPUBLIC20',
    totalCouponsGenerated: 500,
    totalRedemptions: 87,
    totalRevenue: 435000,
    totalDiscount: 87000,
    createdAt: '2026-01-15',
    isAutoApply: false,
  },
  {
    id: '2',
    name: 'Lapsed Customer Win-Back',
    description: '₹500 off for customers who haven\'t visited in 6 months',
    type: 'FIXED_AMOUNT',
    status: 'ACTIVE',
    discountValue: 500,
    minPurchase: 2000,
    startDate: '2026-01-01',
    endDate: '2026-03-31',
    targetSegment: 'LAPSED',
    targetCategories: [],
    couponCode: 'COMEBACK500',
    totalCouponsGenerated: 200,
    totalRedemptions: 28,
    totalRevenue: 84000,
    totalDiscount: 14000,
    createdAt: '2025-12-28',
    isAutoApply: false,
  },
  {
    id: '3',
    name: 'Frame + Lens Combo',
    description: 'Get lens at 50% off when buying any frame above ₹5000',
    type: 'BUNDLE',
    status: 'ACTIVE',
    discountValue: 50,
    minPurchase: 5000,
    startDate: '2026-01-01',
    endDate: '2026-06-30',
    targetSegment: 'ALL',
    targetCategories: ['Frames', 'Lenses'],
    totalCouponsGenerated: 0,
    totalRedemptions: 145,
    totalRevenue: 1450000,
    totalDiscount: 362500,
    createdAt: '2025-12-20',
    isAutoApply: true,
  },
  {
    id: '4',
    name: 'Loyalty Double Points Week',
    description: 'Earn 2x loyalty points on all purchases this week',
    type: 'LOYALTY_BONUS',
    status: 'ENDED',
    discountValue: 2,
    minPurchase: 0,
    startDate: '2026-01-27',
    endDate: '2026-02-02',
    targetSegment: 'LOYAL',
    targetCategories: [],
    totalCouponsGenerated: 0,
    totalRedemptions: 62,
    totalRevenue: 310000,
    totalDiscount: 0,
    createdAt: '2026-01-25',
    isAutoApply: true,
  },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PromotionEngine() {
  const toast = useToast();

  const [campaigns, setCampaigns] = useState<Campaign[]>(INITIAL_CAMPAIGNS);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<CampaignStatus | 'ALL'>('ALL');
  const [selectedCampaign, setSelectedCampaign] = useState<Campaign | null>(null);

  // Create form state
  const [form, setForm] = useState({
    name: '',
    description: '',
    type: 'PERCENTAGE' as CampaignType,
    discountValue: '',
    minPurchase: '',
    maxDiscount: '',
    startDate: '',
    endDate: '',
    targetSegment: 'ALL' as TargetSegment,
    targetCategories: [] as string[],
    couponCode: '',
    isAutoApply: false,
  });

  // Filter campaigns
  const filtered = campaigns.filter(c => {
    const matchesSearch = !searchQuery ||
      c.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      c.couponCode?.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = statusFilter === 'ALL' || c.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  // Stats
  const activeCampaigns = campaigns.filter(c => c.status === 'ACTIVE').length;
  const totalRedemptions = campaigns.reduce((sum, c) => sum + c.totalRedemptions, 0);
  const totalRevenue = campaigns.reduce((sum, c) => sum + c.totalRevenue, 0);
  const totalDiscountGiven = campaigns.reduce((sum, c) => sum + c.totalDiscount, 0);

  const handleCreateCampaign = () => {
    if (!form.name || !form.discountValue || !form.startDate || !form.endDate) {
      toast.error('Please fill in all required fields');
      return;
    }

    const newCampaign: Campaign = {
      id: `camp-${Date.now()}`,
      name: form.name,
      description: form.description,
      type: form.type,
      status: new Date(form.startDate) > new Date() ? 'SCHEDULED' : 'ACTIVE',
      discountValue: parseFloat(form.discountValue),
      minPurchase: parseFloat(form.minPurchase) || 0,
      maxDiscount: form.maxDiscount ? parseFloat(form.maxDiscount) : undefined,
      startDate: form.startDate,
      endDate: form.endDate,
      targetSegment: form.targetSegment,
      targetCategories: form.targetCategories,
      couponCode: form.couponCode || undefined,
      totalCouponsGenerated: 0,
      totalRedemptions: 0,
      totalRevenue: 0,
      totalDiscount: 0,
      createdAt: new Date().toISOString().split('T')[0],
      isAutoApply: form.isAutoApply,
    };

    setCampaigns(prev => [newCampaign, ...prev]);
    setShowCreateModal(false);
    resetForm();
    toast.success(`Campaign "${form.name}" created successfully`);
  };

  const resetForm = () => {
    setForm({
      name: '', description: '', type: 'PERCENTAGE', discountValue: '', minPurchase: '',
      maxDiscount: '', startDate: '', endDate: '', targetSegment: 'ALL',
      targetCategories: [], couponCode: '', isAutoApply: false,
    });
  };

  const handleToggleStatus = (campaign: Campaign) => {
    const newStatus = campaign.status === 'ACTIVE' ? 'PAUSED' : 'ACTIVE';
    setCampaigns(prev => prev.map(c => c.id === campaign.id ? { ...c, status: newStatus } : c));
    toast.success(`Campaign "${campaign.name}" ${newStatus === 'ACTIVE' ? 'activated' : 'paused'}`);
  };

  const handleGenerateCoupons = (campaign: Campaign, count: number) => {
    const coupons: CouponCode[] = Array.from({ length: count }, (_, i) => ({
      code: `${campaign.couponCode || campaign.name.slice(0, 4).toUpperCase()}-${String(campaign.totalCouponsGenerated + i + 1).padStart(4, '0')}`,
      campaignId: campaign.id,
      isUsed: false,
    }));

    setCampaigns(prev =>
      prev.map(c => c.id === campaign.id ? { ...c, totalCouponsGenerated: c.totalCouponsGenerated + count } : c)
    );

    // Export coupons as CSV
    exportToCSV(
      coupons.map(c => ({ code: c.code, campaign: campaign.name, status: 'Available' })),
      `coupons_${campaign.name.replace(/\s+/g, '_')}`,
      [
        { key: 'code', label: 'Coupon Code' },
        { key: 'campaign', label: 'Campaign' },
        { key: 'status', label: 'Status' },
      ]
    );
    toast.success(`${count} coupons generated and downloaded`);
  };

  const handleCopyCode = (code: string) => {
    navigator.clipboard.writeText(code).then(() => {
      toast.success(`Copied "${code}" to clipboard`);
    }).catch(() => {
      toast.error('Failed to copy');
    });
  };

  const formatCurrency = (amount: number) =>
    new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(amount);

  const formatDate = (dateStr: string) =>
    new Date(dateStr).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <Gift className="w-6 h-6 text-pink-600" />
            Promotions & Campaigns
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            Create targeted offers, generate coupon codes, and track campaign performance
          </p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="btn-primary flex items-center gap-2"
        >
          <Plus className="w-4 h-4" />
          New Campaign
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
        <div className="bg-white rounded-lg border border-gray-200 p-3">
          <p className="text-2xl font-bold text-green-600">{activeCampaigns}</p>
          <p className="text-xs text-gray-500">Active Campaigns</p>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-3">
          <p className="text-2xl font-bold text-blue-600">{totalRedemptions}</p>
          <p className="text-xs text-gray-500">Total Redemptions</p>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-3">
          <p className="text-2xl font-bold text-gray-900">{formatCurrency(totalRevenue)}</p>
          <p className="text-xs text-gray-500">Revenue from Campaigns</p>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-3">
          <p className="text-2xl font-bold text-red-600">{formatCurrency(totalDiscountGiven)}</p>
          <p className="text-xs text-gray-500">Total Discount Given</p>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="Search campaigns or coupon codes..."
            className="input-field pl-10 text-sm"
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-gray-400" />
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value as CampaignStatus | 'ALL')}
            className="input-field text-sm w-auto"
          >
            <option value="ALL">All Status</option>
            <option value="ACTIVE">Active</option>
            <option value="SCHEDULED">Scheduled</option>
            <option value="PAUSED">Paused</option>
            <option value="ENDED">Ended</option>
            <option value="DRAFT">Draft</option>
          </select>
        </div>
      </div>

      {/* Campaign Cards */}
      <div className="space-y-3">
        {filtered.map(campaign => {
          const statusConf = STATUS_CONFIG[campaign.status];
          const StatusIcon = statusConf.icon;
          const roi = campaign.totalDiscount > 0
            ? ((campaign.totalRevenue - campaign.totalDiscount) / campaign.totalDiscount * 100).toFixed(0)
            : '0';

          return (
            <div key={campaign.id} className="bg-white rounded-lg border border-gray-200 p-4 hover:shadow-md transition-shadow">
              <div className="flex items-start justify-between mb-3">
                <div className="flex-1">
                  <div className="flex items-center gap-3 mb-1">
                    <h3 className="font-semibold text-gray-900">{campaign.name}</h3>
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${statusConf.bgColor} ${statusConf.color}`}>
                      <StatusIcon className="w-3 h-3" />
                      {statusConf.label}
                    </span>
                    {campaign.isAutoApply && (
                      <span className="px-2 py-0.5 bg-purple-100 text-purple-700 rounded-full text-xs">
                        Auto-Apply
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-gray-500">{campaign.description}</p>
                </div>
                <div className="flex items-center gap-2">
                  {campaign.status === 'ACTIVE' || campaign.status === 'PAUSED' ? (
                    <button
                      onClick={() => handleToggleStatus(campaign)}
                      className="btn-outline text-xs"
                    >
                      {campaign.status === 'ACTIVE' ? 'Pause' : 'Activate'}
                    </button>
                  ) : null}
                  <button
                    onClick={() => setSelectedCampaign(campaign)}
                    className="p-2 hover:bg-gray-100 rounded-lg"
                  >
                    <Eye className="w-4 h-4 text-gray-600" />
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 tablet:grid-cols-5 gap-3 text-sm">
                <div>
                  <p className="text-xs text-gray-500">Discount</p>
                  <p className="font-semibold text-gray-900">
                    {campaign.type === 'PERCENTAGE' ? `${campaign.discountValue}%` :
                     campaign.type === 'FIXED_AMOUNT' ? formatCurrency(campaign.discountValue) :
                     campaign.type === 'LOYALTY_BONUS' ? `${campaign.discountValue}x Points` :
                     `${campaign.discountValue}% off 2nd item`}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Duration</p>
                  <p className="font-medium text-gray-700">{formatDate(campaign.startDate)} - {formatDate(campaign.endDate)}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Redemptions</p>
                  <p className="font-semibold text-blue-600">{campaign.totalRedemptions}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Revenue</p>
                  <p className="font-semibold text-green-600">{formatCurrency(campaign.totalRevenue)}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">ROI</p>
                  <p className="font-semibold text-gray-900">{roi}%</p>
                </div>
              </div>

              {/* Coupon Code & Quick Actions */}
              {campaign.couponCode && (
                <div className="mt-3 pt-3 border-t border-gray-100 flex items-center gap-3">
                  <span className="text-xs text-gray-500">Coupon:</span>
                  <code className="px-2 py-1 bg-gray-100 rounded text-sm font-mono font-semibold text-gray-800">
                    {campaign.couponCode}
                  </code>
                  <button
                    onClick={() => handleCopyCode(campaign.couponCode!)}
                    className="p-1 hover:bg-gray-100 rounded text-gray-400"
                    title="Copy code"
                  >
                    <Copy className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => handleGenerateCoupons(campaign, 50)}
                    className="text-xs text-blue-600 hover:text-blue-700 flex items-center gap-1 ml-auto"
                  >
                    <Download className="w-3 h-3" />
                    Generate 50 Codes
                  </button>
                </div>
              )}

              {/* Target Segment */}
              <div className="mt-2 flex items-center gap-2 flex-wrap">
                <span className="inline-flex items-center gap-1 text-xs text-gray-500">
                  <Target className="w-3 h-3" />
                  {TARGET_SEGMENTS.find(s => s.value === campaign.targetSegment)?.label}
                </span>
                {campaign.targetCategories.map(cat => (
                  <span key={cat} className="px-2 py-0.5 bg-gray-100 rounded text-xs text-gray-600">{cat}</span>
                ))}
                {campaign.minPurchase > 0 && (
                  <span className="text-xs text-gray-400">Min: {formatCurrency(campaign.minPurchase)}</span>
                )}
              </div>
            </div>
          );
        })}

        {filtered.length === 0 && (
          <div className="text-center py-16 bg-white rounded-lg border border-gray-200">
            <Gift className="w-12 h-12 text-gray-300 mx-auto mb-3" />
            <p className="text-gray-500">No campaigns found</p>
          </div>
        )}
      </div>

      {/* Create Campaign Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-bold text-gray-900">Create New Campaign</h2>
                <button onClick={() => { setShowCreateModal(false); resetForm(); }} className="p-2 hover:bg-gray-100 rounded-lg">
                  <X className="w-5 h-5" />
                </button>
              </div>

              <div className="space-y-4">
                {/* Campaign Name & Description */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Campaign Name *</label>
                  <input
                    type="text"
                    value={form.name}
                    onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                    className="input-field"
                    placeholder="e.g., Summer Sunglasses Sale"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                  <textarea
                    value={form.description}
                    onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                    className="input-field"
                    rows={2}
                    placeholder="Campaign description visible to staff"
                  />
                </div>

                {/* Campaign Type */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Campaign Type *</label>
                  <div className="grid grid-cols-1 tablet:grid-cols-2 gap-2">
                    {CAMPAIGN_TYPES.map(ct => (
                      <button
                        key={ct.type}
                        onClick={() => setForm(f => ({ ...f, type: ct.type }))}
                        className={`p-3 rounded-lg border text-left transition-all ${
                          form.type === ct.type
                            ? 'border-pink-500 bg-pink-50'
                            : 'border-gray-200 hover:border-gray-300'
                        }`}
                      >
                        <div className="flex items-center gap-2 mb-1">
                          <ct.icon className={`w-4 h-4 ${form.type === ct.type ? 'text-pink-600' : 'text-gray-400'}`} />
                          <span className="text-sm font-medium">{ct.label}</span>
                        </div>
                        <p className="text-xs text-gray-500">{ct.description}</p>
                      </button>
                    ))}
                  </div>
                </div>

                {/* Discount Value & Constraints */}
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      {form.type === 'PERCENTAGE' ? 'Discount %' :
                       form.type === 'FIXED_AMOUNT' ? 'Amount (₹)' :
                       form.type === 'LOYALTY_BONUS' ? 'Points Multiplier' : 'Discount %'} *
                    </label>
                    <input
                      type="number"
                      value={form.discountValue}
                      onChange={e => setForm(f => ({ ...f, discountValue: e.target.value }))}
                      className="input-field"
                      placeholder={form.type === 'PERCENTAGE' ? '20' : '500'}
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Min Purchase (₹)</label>
                    <input
                      type="number"
                      value={form.minPurchase}
                      onChange={e => setForm(f => ({ ...f, minPurchase: e.target.value }))}
                      className="input-field"
                      placeholder="0"
                    />
                  </div>
                  {form.type === 'PERCENTAGE' && (
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Max Discount (₹)</label>
                      <input
                        type="number"
                        value={form.maxDiscount}
                        onChange={e => setForm(f => ({ ...f, maxDiscount: e.target.value }))}
                        className="input-field"
                        placeholder="2000"
                      />
                    </div>
                  )}
                </div>

                {/* Dates */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Start Date *</label>
                    <input
                      type="date"
                      value={form.startDate}
                      onChange={e => setForm(f => ({ ...f, startDate: e.target.value }))}
                      className="input-field"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">End Date *</label>
                    <input
                      type="date"
                      value={form.endDate}
                      onChange={e => setForm(f => ({ ...f, endDate: e.target.value }))}
                      className="input-field"
                    />
                  </div>
                </div>

                {/* Target Segment */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Target Segment</label>
                  <div className="grid grid-cols-2 tablet:grid-cols-3 gap-2">
                    {TARGET_SEGMENTS.map(seg => (
                      <button
                        key={seg.value}
                        onClick={() => setForm(f => ({ ...f, targetSegment: seg.value }))}
                        className={`p-2 rounded-lg border text-left text-sm transition-all ${
                          form.targetSegment === seg.value
                            ? 'border-pink-500 bg-pink-50'
                            : 'border-gray-200 hover:border-gray-300'
                        }`}
                      >
                        <p className="font-medium">{seg.label}</p>
                        <p className="text-xs text-gray-500">{seg.description}</p>
                      </button>
                    ))}
                  </div>
                </div>

                {/* Target Categories */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Product Categories (optional)</label>
                  <div className="flex flex-wrap gap-2">
                    {OPTICAL_CATEGORIES.map(cat => (
                      <button
                        key={cat}
                        onClick={() => {
                          setForm(f => ({
                            ...f,
                            targetCategories: f.targetCategories.includes(cat)
                              ? f.targetCategories.filter(c => c !== cat)
                              : [...f.targetCategories, cat],
                          }));
                        }}
                        className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
                          form.targetCategories.includes(cat)
                            ? 'bg-pink-600 text-white'
                            : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                        }`}
                      >
                        {cat}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Coupon Code & Auto Apply */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Coupon Code</label>
                    <input
                      type="text"
                      value={form.couponCode}
                      onChange={e => setForm(f => ({ ...f, couponCode: e.target.value.toUpperCase() }))}
                      className="input-field font-mono"
                      placeholder="SUMMER20"
                      maxLength={20}
                    />
                  </div>
                  <div className="flex items-end">
                    <label className="flex items-center gap-2 p-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={form.isAutoApply}
                        onChange={e => setForm(f => ({ ...f, isAutoApply: e.target.checked }))}
                        className="rounded border-gray-300"
                      />
                      <span className="text-sm text-gray-700">Auto-apply at POS</span>
                    </label>
                  </div>
                </div>

                {/* Submit */}
                <div className="flex gap-3 pt-4 border-t border-gray-200">
                  <button
                    onClick={handleCreateCampaign}
                    className="btn-primary flex-1"
                  >
                    Create Campaign
                  </button>
                  <button
                    onClick={() => { setShowCreateModal(false); resetForm(); }}
                    className="btn-outline flex-1"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Campaign Detail Modal */}
      {selectedCampaign && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-lg w-full">
            <div className="p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-xl font-bold text-gray-900">{selectedCampaign.name}</h2>
                <button onClick={() => setSelectedCampaign(null)} className="p-2 hover:bg-gray-100 rounded-lg">
                  <X className="w-5 h-5" />
                </button>
              </div>

              <p className="text-sm text-gray-500 mb-4">{selectedCampaign.description}</p>

              <div className="space-y-3 text-sm">
                <div className="flex justify-between py-2 border-b border-gray-100">
                  <span className="text-gray-500">Type</span>
                  <span className="font-medium">{CAMPAIGN_TYPES.find(t => t.type === selectedCampaign.type)?.label}</span>
                </div>
                <div className="flex justify-between py-2 border-b border-gray-100">
                  <span className="text-gray-500">Status</span>
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_CONFIG[selectedCampaign.status].bgColor} ${STATUS_CONFIG[selectedCampaign.status].color}`}>
                    {STATUS_CONFIG[selectedCampaign.status].label}
                  </span>
                </div>
                <div className="flex justify-between py-2 border-b border-gray-100">
                  <span className="text-gray-500">Duration</span>
                  <span className="font-medium">{formatDate(selectedCampaign.startDate)} - {formatDate(selectedCampaign.endDate)}</span>
                </div>
                <div className="flex justify-between py-2 border-b border-gray-100">
                  <span className="text-gray-500">Target</span>
                  <span className="font-medium">{TARGET_SEGMENTS.find(s => s.value === selectedCampaign.targetSegment)?.label}</span>
                </div>
                <div className="flex justify-between py-2 border-b border-gray-100">
                  <span className="text-gray-500">Redemptions</span>
                  <span className="font-bold text-blue-600">{selectedCampaign.totalRedemptions}</span>
                </div>
                <div className="flex justify-between py-2 border-b border-gray-100">
                  <span className="text-gray-500">Revenue Generated</span>
                  <span className="font-bold text-green-600">{formatCurrency(selectedCampaign.totalRevenue)}</span>
                </div>
                <div className="flex justify-between py-2">
                  <span className="text-gray-500">Total Discount Given</span>
                  <span className="font-bold text-red-600">{formatCurrency(selectedCampaign.totalDiscount)}</span>
                </div>
              </div>

              {selectedCampaign.couponCode && (
                <div className="mt-4 p-3 bg-gray-50 rounded-lg">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-xs text-gray-500 mb-1">Coupon Code</p>
                      <code className="text-lg font-mono font-bold text-gray-900">{selectedCampaign.couponCode}</code>
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleCopyCode(selectedCampaign.couponCode!)}
                        className="btn-outline text-xs flex items-center gap-1"
                      >
                        <Copy className="w-3 h-3" />
                        Copy
                      </button>
                      <button
                        onClick={() => handleGenerateCoupons(selectedCampaign, 100)}
                        className="btn-primary text-xs flex items-center gap-1"
                      >
                        <Download className="w-3 h-3" />
                        Generate 100
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-gray-400 mt-2">
                    {selectedCampaign.totalCouponsGenerated} codes generated
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default PromotionEngine;
