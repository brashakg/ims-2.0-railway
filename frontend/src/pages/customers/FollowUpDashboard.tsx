// ============================================================================
// IMS 2.0 - Customer Follow-up Dashboard
// ============================================================================
// Automated customer follow-ups for optical retail:
// - Eye test reminders (yearly)
// - Frame replacement reminders (2 years)
// - Order delivery notifications
// - Prescription expiry reminders

import { useState, useEffect } from 'react';
import { useAuth } from '../../context/AuthContext';
import { Phone, Clock, AlertCircle, CheckCircle2, RotateCcw, Calendar } from 'lucide-react';
import clsx from 'clsx';

interface FollowUp {
  follow_up_id: string;
  customer_id: string;
  customer_name: string;
  customer_phone: string;
  store_id: string;
  type: 'eye_test_reminder' | 'frame_replacement' | 'order_delivery' | 'prescription_expiry' | 'general';
  scheduled_date: string;
  status: 'pending' | 'completed' | 'skipped';
  outcome?: string | null;
  notes: string;
  created_at: string;
  completed_at?: string | null;
  completed_by?: string | null;
}

interface SummaryStats {
  due_today: number;
  this_week: number;
  overdue: number;
  completed_this_month: number;
  pending_total: number;
}

interface Outcome {
  value: string;
  label: string;
  color: string;
}

const FOLLOW_UP_TYPES = [
  { id: 'all', label: 'All', color: 'bg-gray-700', icon: '📋' },
  { id: 'eye_test_reminder', label: 'Eye Test', color: 'bg-purple-700', icon: '👁️' },
  { id: 'frame_replacement', label: 'Frame Replacement', color: 'bg-blue-700', icon: '👓' },
  { id: 'order_delivery', label: 'Order Delivery', color: 'bg-green-700', icon: '📦' },
  { id: 'prescription_expiry', label: 'Prescription', color: 'bg-red-700', icon: '📄' },
];

const OUTCOMES: Outcome[] = [
  { value: 'called_interested', label: 'Called - Interested', color: 'bg-green-900 text-green-200' },
  { value: 'called_not_interested', label: 'Called - Not Interested', color: 'bg-gray-900 text-gray-300' },
  { value: 'no_answer', label: 'No Answer', color: 'bg-orange-900 text-orange-200' },
  { value: 'rescheduled', label: 'Rescheduled', color: 'bg-yellow-900 text-yellow-200' },
  { value: 'completed', label: 'Completed', color: 'bg-blue-900 text-blue-200' },
];

export function FollowUpDashboard() {
  const [activeType, setActiveType] = useState('all');
  const [followUps, setFollowUps] = useState<FollowUp[]>([]);
  const [summary, setSummary] = useState<SummaryStats>({
    due_today: 0,
    this_week: 0,
    overdue: 0,
    completed_this_month: 0,
    pending_total: 0,
  });
  const [loading, setLoading] = useState(true);
  const [completing, setCompleting] = useState<string | null>(null);
  const [selectedOutcome, setSelectedOutcome] = useState<Record<string, string>>({});
  const { user } = useAuth();
  const storeId = user?.activeStoreId;

  if (!storeId) {
    return <div className="p-8 text-center text-red-600">No store selected. Please select a store from the header.</div>;
  }

  useEffect(() => {
    loadFollowUps();
    loadSummary();
  }, []);

  const loadFollowUps = async () => {
    try {
      setLoading(true);
      const response = await fetch(
        `/api/v1/follow-ups/?store_id=${storeId}`,
        { headers: { 'Content-Type': 'application/json' } }
      );
      if (response.ok) {
        const data = await response.json();
        setFollowUps(data);
      }
    } catch (error) {
      // silently handle error
    } finally {
      setLoading(false);
    }
  };

  const loadSummary = async () => {
    try {
      const response = await fetch(
        `/api/v1/follow-ups/summary?store_id=${storeId}`,
        { headers: { 'Content-Type': 'application/json' } }
      );
      if (response.ok) {
        const data = await response.json();
        setSummary(data);
      }
    } catch (error) {
      // silently handle error
    }
  };

  const handleAutoGenerate = async () => {
    try {
      const response = await fetch(
        `/api/v1/follow-ups/auto-generate?store_id=${storeId}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        }
      );
      if (response.ok) {
        await response.json();
        await loadFollowUps();
        await loadSummary();
      }
    } catch (error) {
      // silently handle error
    }
  };

  const handleComplete = async (followUpId: string) => {
    const outcome = selectedOutcome[followUpId];
    if (!outcome) return;

    try {
      setCompleting(followUpId);
      const response = await fetch(
        `/api/v1/follow-ups/${followUpId}/complete`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ outcome, notes: '' }),
        }
      );
      if (response.ok) {
        await loadFollowUps();
        await loadSummary();
        setSelectedOutcome((prev) => {
          const newState = { ...prev };
          delete newState[followUpId];
          return newState;
        });
      }
    } catch (error) {
      // silently handle error
    } finally {
      setCompleting(null);
    }
  };

  const filteredFollowUps = activeType === 'all'
    ? followUps
    : followUps.filter((fu) => fu.type === activeType);

  const typeIcon = (type: string) => {
    const typeConfig = FOLLOW_UP_TYPES.find((t) => t.id === type);
    return typeConfig?.icon || '📋';
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'pending':
        return 'text-yellow-400';
      case 'completed':
        return 'text-green-400';
      case 'skipped':
        return 'text-gray-400';
      default:
        return 'text-gray-300';
    }
  };

  const getDaysUntilDue = (scheduledDate: string): number => {
    const today = new Date();
    const due = new Date(scheduledDate);
    const diff = due.getTime() - today.getTime();
    return Math.floor(diff / (1000 * 60 * 60 * 24));
  };

  const isOverdue = (scheduledDate: string): boolean => {
    return getDaysUntilDue(scheduledDate) < 0;
  };

  const formatDate = (dateStr: string): string => {
    return new Date(dateStr).toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white">Follow-up Management</h1>
          <p className="text-gray-400 mt-1">Track and manage customer follow-ups</p>
        </div>
        <button
          onClick={handleAutoGenerate}
          className="flex items-center gap-2 bg-blue-700 hover:bg-blue-600 text-white px-4 py-2 rounded-lg transition"
        >
          <RotateCcw className="w-4 h-4" />
          Auto-Generate
        </button>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-5 gap-4">
        <div className="bg-gradient-to-br from-red-900/30 to-red-900/10 border border-red-700 rounded-lg p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-red-300 text-sm font-medium">Due Today</p>
              <p className="text-2xl font-bold text-white mt-1">{summary.due_today}</p>
            </div>
            <AlertCircle className="w-8 h-8 text-red-500" />
          </div>
        </div>

        <div className="bg-gradient-to-br from-yellow-900/30 to-yellow-900/10 border border-yellow-700 rounded-lg p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-yellow-300 text-sm font-medium">This Week</p>
              <p className="text-2xl font-bold text-white mt-1">{summary.this_week}</p>
            </div>
            <Calendar className="w-8 h-8 text-yellow-500" />
          </div>
        </div>

        <div className="bg-gradient-to-br from-orange-900/30 to-orange-900/10 border border-orange-700 rounded-lg p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-orange-300 text-sm font-medium">Overdue</p>
              <p className="text-2xl font-bold text-white mt-1">{summary.overdue}</p>
            </div>
            <Clock className="w-8 h-8 text-orange-500" />
          </div>
        </div>

        <div className="bg-gradient-to-br from-green-900/30 to-green-900/10 border border-green-700 rounded-lg p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-green-300 text-sm font-medium">Completed</p>
              <p className="text-2xl font-bold text-white mt-1">{summary.completed_this_month}</p>
            </div>
            <CheckCircle2 className="w-8 h-8 text-green-500" />
          </div>
        </div>

        <div className="bg-gradient-to-br from-blue-900/30 to-blue-900/10 border border-blue-700 rounded-lg p-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-blue-300 text-sm font-medium">Pending</p>
              <p className="text-2xl font-bold text-white mt-1">{summary.pending_total}</p>
            </div>
            <Phone className="w-8 h-8 text-blue-500" />
          </div>
        </div>
      </div>

      {/* Type Filter */}
      <div className="flex gap-2 overflow-x-auto pb-2">
        {FOLLOW_UP_TYPES.map((type) => (
          <button
            key={type.id}
            onClick={() => setActiveType(type.id)}
            className={clsx(
              'whitespace-nowrap px-4 py-2 rounded-lg text-white font-medium transition',
              activeType === type.id
                ? `${type.color} ring-2 ring-offset-2 ring-offset-gray-800`
                : 'bg-gray-700 hover:bg-gray-600'
            )}
          >
            <span className="mr-2">{type.icon}</span>
            {type.label}
          </button>
        ))}
      </div>

      {/* Follow-ups List */}
      <div className="bg-gray-800 border border-gray-700 rounded-lg overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-gray-400">Loading follow-ups...</div>
        ) : filteredFollowUps.length === 0 ? (
          <div className="p-8 text-center text-gray-400">No follow-ups found</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-gray-700/50 border-b border-gray-700">
                <tr>
                  <th className="px-4 py-3 text-left text-sm font-semibold text-gray-300">Customer</th>
                  <th className="px-4 py-3 text-left text-sm font-semibold text-gray-300">Type</th>
                  <th className="px-4 py-3 text-left text-sm font-semibold text-gray-300">Phone</th>
                  <th className="px-4 py-3 text-left text-sm font-semibold text-gray-300">Due Date</th>
                  <th className="px-4 py-3 text-left text-sm font-semibold text-gray-300">Status</th>
                  <th className="px-4 py-3 text-left text-sm font-semibold text-gray-300">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {filteredFollowUps.map((fu) => (
                  <tr key={fu.follow_up_id} className="hover:bg-gray-700/50 transition">
                    <td className="px-4 py-3 text-white font-medium">{fu.customer_name}</td>
                    <td className="px-4 py-3">
                      <span className="flex items-center gap-2">
                        <span>{typeIcon(fu.type)}</span>
                        <span className="text-gray-300 capitalize text-sm">
                          {fu.type.replace(/_/g, ' ')}
                        </span>
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-300">{fu.customer_phone}</td>
                    <td className="px-4 py-3 text-gray-300">
                      <div className="flex items-center gap-1">
                        <span>{formatDate(fu.scheduled_date)}</span>
                        {isOverdue(fu.scheduled_date) && fu.status === 'pending' && (
                          <span className="text-red-400 text-xs ml-1">
                            ({getDaysUntilDue(fu.scheduled_date)} days overdue)
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={clsx('text-sm font-medium capitalize', getStatusColor(fu.status))}>
                        {fu.status}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {fu.status === 'pending' ? (
                        <div className="flex items-center gap-2">
                          <select
                            value={selectedOutcome[fu.follow_up_id] || ''}
                            onChange={(e) =>
                              setSelectedOutcome((prev) => ({
                                ...prev,
                                [fu.follow_up_id]: e.target.value,
                              }))
                            }
                            className="bg-gray-700 border border-gray-600 text-white text-sm rounded px-2 py-1"
                          >
                            <option value="">Select outcome...</option>
                            {OUTCOMES.map((outcome) => (
                              <option key={outcome.value} value={outcome.value}>
                                {outcome.label}
                              </option>
                            ))}
                          </select>
                          <button
                            onClick={() => handleComplete(fu.follow_up_id)}
                            disabled={!selectedOutcome[fu.follow_up_id] || completing === fu.follow_up_id}
                            className="bg-green-700 hover:bg-green-600 disabled:bg-gray-700 text-white px-3 py-1 rounded text-sm transition"
                          >
                            {completing === fu.follow_up_id ? 'Saving...' : 'Complete'}
                          </button>
                        </div>
                      ) : (
                        <span className={`text-xs px-2 py-1 rounded ${OUTCOMES.find((o) => o.value === fu.outcome)?.color || 'bg-gray-700'}`}>
                          {OUTCOMES.find((o) => o.value === fu.outcome)?.label || 'N/A'}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

export default FollowUpDashboard;
