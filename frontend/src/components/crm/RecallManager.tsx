// ============================================================================
// IMS 2.0 - Customer Recall & Reminder Manager
// ============================================================================
// Automated recall system for eye tests, lens replacements, prescriptions
// Uses existing notification templates from constants/notifications.ts

import { useState, useEffect } from 'react';
import {
  Bell, Phone, MessageSquare, Calendar, Clock, Send, Filter,
  CheckCircle, AlertTriangle, XCircle, Eye, RefreshCw,
  Users, Search, ChevronDown, Loader2,
} from 'lucide-react';
import { marketingApi } from '../../services/api/marketing';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type RecallType =
  | 'EYE_TEST_DUE'
  | 'PRESCRIPTION_EXPIRY'
  | 'LENS_REPLACEMENT'
  | 'ANNUAL_CHECKUP'
  | 'FOLLOW_UP'
  | 'BIRTHDAY_OFFER';

type RecallStatus = 'PENDING' | 'SENT' | 'ACKNOWLEDGED' | 'SCHEDULED' | 'DISMISSED';
type ChannelType = 'SMS' | 'WHATSAPP' | 'CALL';

interface RecallItem {
  id: string;
  customerId: string;
  customerName: string;
  customerPhone: string;
  patientName?: string;
  type: RecallType;
  reason: string;
  dueDate: string;
  daysPast: number; // negative = future, positive = overdue
  status: RecallStatus;
  lastContactDate?: string;
  channel?: ChannelType;
  notes?: string;
  priority: 'HIGH' | 'MEDIUM' | 'LOW';
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const RECALL_TYPE_CONFIG: Record<RecallType, { label: string; icon: typeof Bell; color: string; bgColor: string }> = {
  EYE_TEST_DUE: { label: 'Eye Test Due', icon: Eye, color: 'text-purple-700', bgColor: 'bg-purple-100' },
  PRESCRIPTION_EXPIRY: { label: 'Rx Expiring', icon: Clock, color: 'text-red-700', bgColor: 'bg-red-100' },
  LENS_REPLACEMENT: { label: 'Lens Replacement', icon: RefreshCw, color: 'text-blue-700', bgColor: 'bg-blue-100' },
  ANNUAL_CHECKUP: { label: 'Annual Checkup', icon: Calendar, color: 'text-green-700', bgColor: 'bg-green-100' },
  FOLLOW_UP: { label: 'Follow-up', icon: Phone, color: 'text-orange-700', bgColor: 'bg-orange-100' },
  BIRTHDAY_OFFER: { label: 'Birthday Offer', icon: Bell, color: 'text-pink-700', bgColor: 'bg-pink-100' },
};

const STATUS_CONFIG: Record<RecallStatus, { label: string; color: string; bgColor: string }> = {
  PENDING: { label: 'Pending', color: 'text-yellow-700', bgColor: 'bg-yellow-100' },
  SENT: { label: 'Sent', color: 'text-blue-700', bgColor: 'bg-blue-100' },
  ACKNOWLEDGED: { label: 'Acknowledged', color: 'text-green-700', bgColor: 'bg-green-100' },
  SCHEDULED: { label: 'Scheduled', color: 'text-purple-700', bgColor: 'bg-purple-100' },
  DISMISSED: { label: 'Dismissed', color: 'text-gray-500', bgColor: 'bg-gray-100' },
};

// ---------------------------------------------------------------------------
// Map real Rx-expiry alerts (GET /marketing/rx-expiry-alerts) -> RecallItem[]
// ---------------------------------------------------------------------------

interface RxAlert {
  prescription_id: string;
  customer_id: string;
  customer_name: string;
  customer_phone: string;
  expiry_date: string;
  days_until_expiry: number;
  prescription_date?: string;
}

function alertToRecall(a: RxAlert, priority: 'HIGH' | 'MEDIUM' | 'LOW'): RecallItem {
  const d = Number(a.days_until_expiry) || 0;
  return {
    id: `rx-${a.prescription_id || a.customer_id}`,
    customerId: a.customer_id,
    customerName: a.customer_name || 'Unknown',
    customerPhone: a.customer_phone || '',
    type: 'PRESCRIPTION_EXPIRY',
    reason: `Prescription expires in ${d} day(s) (on ${a.expiry_date}). Recommend a fresh eye test.`,
    dueDate: a.expiry_date,
    daysPast: -d, // endpoint returns days UNTIL expiry (future) => negative daysPast
    status: 'PENDING',
    priority,
  };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function RecallManager() {
  const { user } = useAuth();
  const toast = useToast();

  const [recalls, setRecalls] = useState<RecallItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState<RecallType | 'ALL'>('ALL');
  const [statusFilter, setStatusFilter] = useState<RecallStatus | 'ALL'>('PENDING');
  const [selectedRecalls, setSelectedRecalls] = useState<Set<string>>(new Set());
  const [showBulkActions, setShowBulkActions] = useState(false);

  useEffect(() => {
    // Refetch when the active store changes so recalls follow the store
    // switcher (the fetch already passes user.activeStoreId as storeId).
    loadRecalls();
  }, [user?.activeStoreId]);

  const loadRecalls = async () => {
    setIsLoading(true);
    try {
      const res = await marketingApi.getRxExpiryAlerts(user?.activeStoreId);
      const mapped: RecallItem[] = [
        ...(res?.urgent || []).map((a: RxAlert) => alertToRecall(a, 'HIGH')),
        ...(res?.soon || []).map((a: RxAlert) => alertToRecall(a, 'MEDIUM')),
        ...(res?.upcoming || []).map((a: RxAlert) => alertToRecall(a, 'LOW')),
      ].sort((x, y) => y.daysPast - x.daysPast);
      setRecalls(mapped);
    } catch {
      setRecalls([]);
      toast.error('Failed to load recall data');
    } finally {
      setIsLoading(false);
    }
  };

  // Filter recalls
  const filtered = recalls.filter(r => {
    const matchesSearch = !searchQuery ||
      r.customerName.toLowerCase().includes(searchQuery.toLowerCase()) ||
      r.customerPhone.includes(searchQuery);
    const matchesType = typeFilter === 'ALL' || r.type === typeFilter;
    const matchesStatus = statusFilter === 'ALL' || r.status === statusFilter;
    return matchesSearch && matchesType && matchesStatus;
  });

  // Stats
  const stats = {
    total: recalls.length,
    overdue: recalls.filter(r => r.daysPast > 0 && r.status === 'PENDING').length,
    dueThisWeek: recalls.filter(r => r.daysPast >= -7 && r.daysPast <= 0 && r.status === 'PENDING').length,
    sent: recalls.filter(r => r.status === 'SENT').length,
  };

  const handleToggleSelect = (id: string) => {
    setSelectedRecalls(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSelectAll = () => {
    if (selectedRecalls.size === filtered.length) {
      setSelectedRecalls(new Set());
    } else {
      setSelectedRecalls(new Set(filtered.map(r => r.id)));
    }
  };

  const sendReminder = async (recall: RecallItem, channel: ChannelType) => {
    handleMarkStatus(recall.id, 'SENT');
    try {
      await marketingApi.sendRxReminder(recall.customerId);
      toast.success(`Reminder sent to ${recall.customerName} via ${channel}`);
    } catch {
      toast.error(`Failed to send reminder to ${recall.customerName}`);
    }
  };

  const handleBulkSend = (channel: ChannelType) => {
    const targets = recalls.filter((r) => selectedRecalls.has(r.id));
    const count = targets.length;
    if (count === 0) return;

    setRecalls((prev) =>
      prev.map((r) =>
        selectedRecalls.has(r.id)
          ? { ...r, status: 'SENT' as RecallStatus, channel, lastContactDate: new Date().toISOString() }
          : r
      )
    );
    setSelectedRecalls(new Set());
    setShowBulkActions(false);
    // Fire real reminders (best-effort) for SMS/WhatsApp; CALL is mark-only.
    if (channel !== 'CALL') {
      Promise.allSettled(targets.map((r) => marketingApi.sendRxReminder(r.customerId)));
    }
    toast.success(`${count} recall${count !== 1 ? 's' : ''} sent via ${channel}`);
  };

  const handleMarkStatus = (id: string, status: RecallStatus) => {
    setRecalls(prev => prev.map(r => r.id === id ? { ...r, status } : r));
  };

  const formatDate = (dateStr: string) =>
    new Date(dateStr).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <Bell className="w-6 h-6 text-orange-600" />
            Customer Recalls & Reminders
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            Prescription renewals due in the next 90 days
          </p>
        </div>
        <button
          onClick={loadRecalls}
          className="btn-outline flex items-center gap-2 text-sm"
        >
          <RefreshCw className="w-4 h-4" />
          Refresh
        </button>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
        <div className="bg-white rounded-lg border border-gray-200 p-3">
          <p className="text-2xl font-bold text-gray-900">{stats.total}</p>
          <p className="text-xs text-gray-500">Total Recalls</p>
        </div>
        <div className="bg-red-50 rounded-lg border border-red-200 p-3">
          <p className="text-2xl font-bold text-red-600">{stats.overdue}</p>
          <p className="text-xs text-red-600">Overdue</p>
        </div>
        <div className="bg-yellow-50 rounded-lg border border-yellow-200 p-3">
          <p className="text-2xl font-bold text-yellow-600">{stats.dueThisWeek}</p>
          <p className="text-xs text-yellow-600">Due This Week</p>
        </div>
        <div className="bg-green-50 rounded-lg border border-green-200 p-3">
          <p className="text-2xl font-bold text-green-600">{stats.sent}</p>
          <p className="text-xs text-green-600">Sent</p>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-lg border border-gray-200 p-3">
        <div className="flex flex-wrap gap-3 items-center">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Search by name or phone..."
              className="input-field pl-10 text-sm"
            />
          </div>
          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4 text-gray-500" />
            <select
              value={typeFilter}
              onChange={e => setTypeFilter(e.target.value as RecallType | 'ALL')}
              className="input-field text-sm w-auto"
            >
              <option value="ALL">All Types</option>
              {Object.entries(RECALL_TYPE_CONFIG).map(([key, conf]) => (
                <option key={key} value={key}>{conf.label}</option>
              ))}
            </select>
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value as RecallStatus | 'ALL')}
              className="input-field text-sm w-auto"
            >
              <option value="ALL">All Status</option>
              <option value="PENDING">Pending</option>
              <option value="SENT">Sent</option>
              <option value="ACKNOWLEDGED">Acknowledged</option>
              <option value="SCHEDULED">Scheduled</option>
              <option value="DISMISSED">Dismissed</option>
            </select>
          </div>
        </div>
      </div>

      {/* Bulk Actions */}
      {selectedRecalls.size > 0 && (
        <div className="bg-blue-50 rounded-lg border border-blue-200 p-3 flex items-center justify-between">
          <span className="text-sm font-medium text-blue-700">
            {selectedRecalls.size} recall{selectedRecalls.size !== 1 ? 's' : ''} selected
          </span>
          <div className="flex items-center gap-2 relative">
            <button
              onClick={() => setShowBulkActions(!showBulkActions)}
              className="btn-primary text-sm flex items-center gap-2"
            >
              <Send className="w-4 h-4" />
              Send Recall
              <ChevronDown className="w-3 h-3" />
            </button>
            {showBulkActions && (
              <div className="absolute right-0 top-full mt-1 bg-white rounded-lg shadow-lg border border-gray-200 py-1 z-10 w-48">
                <button
                  onClick={() => handleBulkSend('SMS')}
                  className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50 flex items-center gap-2"
                >
                  <MessageSquare className="w-4 h-4 text-blue-600" />
                  Send via SMS
                </button>
                <button
                  onClick={() => handleBulkSend('WHATSAPP')}
                  className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50 flex items-center gap-2"
                >
                  <Phone className="w-4 h-4 text-green-600" />
                  Send via WhatsApp
                </button>
                <button
                  onClick={() => handleBulkSend('CALL')}
                  className="w-full px-4 py-2 text-left text-sm hover:bg-gray-50 flex items-center gap-2"
                >
                  <Phone className="w-4 h-4 text-purple-600" />
                  Mark for Calling
                </button>
              </div>
            )}
            <button
              onClick={() => {
                selectedRecalls.forEach(id => handleMarkStatus(id, 'DISMISSED'));
                setSelectedRecalls(new Set());
              }}
              className="btn-outline text-sm"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Recalls List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="w-8 h-8 animate-spin text-orange-600" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 bg-white rounded-lg border border-gray-200">
          <Bell className="w-12 h-12 text-gray-500 mx-auto mb-3" />
          <p className="text-gray-500">No recalls found</p>
          <p className="text-sm text-gray-500 mt-1">All customers are up to date!</p>
        </div>
      ) : (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          {/* Table Header */}
          <div className="grid grid-cols-[40px_1fr_140px_100px_100px_120px] gap-2 px-4 py-2 bg-gray-50 border-b border-gray-200 text-xs font-medium text-gray-500 uppercase">
            <div>
              <input
                type="checkbox"
                checked={selectedRecalls.size === filtered.length && filtered.length > 0}
                onChange={handleSelectAll}
                className="rounded border-gray-300"
              />
            </div>
            <div>Customer / Reason</div>
            <div>Type</div>
            <div>Due Date</div>
            <div>Status</div>
            <div>Actions</div>
          </div>

          {/* Table Body */}
          <div className="divide-y divide-gray-100 max-h-[600px] overflow-y-auto">
            {filtered.map(recall => {
              const typeConf = RECALL_TYPE_CONFIG[recall.type];
              const statusConf = STATUS_CONFIG[recall.status];
              const TypeIcon = typeConf.icon;

              return (
                <div
                  key={recall.id}
                  className="grid grid-cols-[40px_1fr_140px_100px_100px_120px] gap-2 px-4 py-3 items-center hover:bg-gray-50 text-sm"
                >
                  <div>
                    <input
                      type="checkbox"
                      checked={selectedRecalls.has(recall.id)}
                      onChange={() => handleToggleSelect(recall.id)}
                      className="rounded border-gray-300"
                    />
                  </div>
                  <div>
                    <p className="font-medium text-gray-900">{recall.customerName}</p>
                    <p className="text-xs text-gray-500">{recall.customerPhone}</p>
                    <p className="text-xs text-gray-500 mt-0.5 line-clamp-1">{recall.reason}</p>
                  </div>
                  <div>
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${typeConf.bgColor} ${typeConf.color}`}>
                      <TypeIcon className="w-3 h-3" />
                      {typeConf.label}
                    </span>
                  </div>
                  <div>
                    <p className="text-xs">{formatDate(recall.dueDate)}</p>
                    {recall.daysPast > 0 ? (
                      <p className="text-xs text-red-600 font-medium">{recall.daysPast}d overdue</p>
                    ) : recall.daysPast === 0 ? (
                      <p className="text-xs text-orange-600 font-medium">Today</p>
                    ) : (
                      <p className="text-xs text-green-600">In {Math.abs(recall.daysPast)}d</p>
                    )}
                  </div>
                  <div>
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs ${statusConf.bgColor} ${statusConf.color}`}>
                      {recall.status === 'SENT' && <CheckCircle className="w-3 h-3" />}
                      {recall.status === 'PENDING' && <AlertTriangle className="w-3 h-3" />}
                      {recall.status === 'DISMISSED' && <XCircle className="w-3 h-3" />}
                      {statusConf.label}
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    {recall.status === 'PENDING' && (
                      <>
                        <button
                          onClick={() => sendReminder(recall, 'SMS')}
                          className="p-1.5 hover:bg-blue-50 rounded text-blue-600"
                          title="Send SMS"
                        >
                          <MessageSquare className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => sendReminder(recall, 'WHATSAPP')}
                          className="p-1.5 hover:bg-green-50 rounded text-green-600"
                          title="Send WhatsApp"
                        >
                          <Phone className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleMarkStatus(recall.id, 'DISMISSED')}
                          className="p-1.5 hover:bg-gray-100 rounded text-gray-500"
                          title="Dismiss"
                        >
                          <XCircle className="w-4 h-4" />
                        </button>
                      </>
                    )}
                    {recall.status === 'SENT' && (
                      <button
                        onClick={() => handleMarkStatus(recall.id, 'ACKNOWLEDGED')}
                        className="p-1.5 hover:bg-green-50 rounded text-green-600"
                        title="Mark Acknowledged"
                      >
                        <CheckCircle className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Footer */}
          <div className="px-4 py-2 bg-gray-50 border-t border-gray-200 text-xs text-gray-500 flex items-center justify-between">
            <span className="flex items-center gap-1">
              <Users className="w-3 h-3" />
              Showing {filtered.length} of {recalls.length} recalls
            </span>
            <span>From prescriptions expiring within 90 days</span>
          </div>
        </div>
      )}
    </div>
  );
}

export default RecallManager;
