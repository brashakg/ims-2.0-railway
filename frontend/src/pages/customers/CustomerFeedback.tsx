// ============================================================================
// IMS 2.0 - Customer Feedback & NPS
// ============================================================================
// NPS dashboard wired to GET /marketing/nps-dashboard (store-scoped). Only the
// data the backend actually returns is shown — score distribution comes from
// real responses, segments from real promoter/passive/detractor counts.
// Sentiment classification, a complaint workflow, and store-vs-store
// comparison have no backend source yet, so those tabs show honest empty
// states instead of fabricated numbers.

import { useState, useEffect } from 'react';
import { MessageSquare, Store, Heart } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { marketingApi } from '../../services/api/marketing';

// One responded NPS survey as returned in nps-dashboard.responses[].
interface NpsResponse {
  nps_id?: string;
  customer_name?: string;
  score?: number | null;
  feedback?: string | null;
  responded_at?: string | null;
  created_at?: string | null;
}

// Shape of GET /marketing/nps-dashboard.
interface NpsDashboard {
  avg_score: number;
  promoters: number;
  passives: number;
  detractors: number;
  response_rate: number;
  total_surveys?: number;
  total_responses?: number;
  nps_score?: number;
  responses: NpsResponse[];
}

const EMPTY_DASHBOARD: NpsDashboard = {
  avg_score: 0,
  promoters: 0,
  passives: 0,
  detractors: 0,
  response_rate: 0,
  total_surveys: 0,
  total_responses: 0,
  nps_score: 0,
  responses: [],
};

export function CustomerFeedback() {
  const { user } = useAuth();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<'nps' | 'sentiment' | 'complaints' | 'comparison'>('nps');
  const [dashboard, setDashboard] = useState<NpsDashboard>(EMPTY_DASHBOARD);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDashboard();
    // Refetch when the active store changes so NPS follows the switcher.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.activeStoreId]);

  const loadDashboard = async () => {
    setLoading(true);
    try {
      const res = await marketingApi.getNpsDashboard(user?.activeStoreId);
      setDashboard({ ...EMPTY_DASHBOARD, ...(res || {}), responses: res?.responses || [] });
    } catch {
      setDashboard(EMPTY_DASHBOARD);
      toast.error('Failed to load NPS data');
    } finally {
      setLoading(false);
    }
  };

  const responses = dashboard.responses || [];
  const totalResponses = dashboard.total_responses ?? responses.length;
  const npsScore = dashboard.nps_score ?? 0;

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>CRM · Feedback</div>
          <h1>What they actually thought.</h1>
          <div className="hint">NPS collected after delivered orders. Score distribution, promoter/detractor segments, and verbatim responses.</div>
        </div>
      </div>

      {/* Summary Stats — real NPS figures from the backend dashboard */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">NPS Score</p>
          <p className="text-2xl font-bold text-blue-600">{npsScore.toFixed(0)}</p>
          <p className="text-xs text-gray-500">-100 to +100</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Avg Rating</p>
          <p className="text-2xl font-bold text-green-600">{dashboard.avg_score}/10</p>
          <p className="text-xs text-gray-500">{totalResponses} responses</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Response Rate</p>
          <p className="text-2xl font-bold text-purple-600">{dashboard.response_rate}%</p>
          <p className="text-xs text-gray-500">{dashboard.total_surveys ?? 0} surveys sent</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Promoters</p>
          <p className="text-2xl font-bold text-green-600">{dashboard.promoters}</p>
          <p className="text-xs text-gray-500">Score 9-10</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-200">
        {(['nps', 'sentiment', 'complaints', 'comparison'] as const).map((tab) => (
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
            {tab === 'nps' ? 'NPS' : tab === 'sentiment' ? 'Sentiment' : tab === 'complaints' ? 'Complaints' : 'Comparison'}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="text-center text-gray-500 py-12 text-sm">Loading feedback…</div>
      ) : (
        <>
          {activeTab === 'nps' && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* NPS Segments (real promoter/passive/detractor counts) */}
                <div className="bg-white border border-gray-200 rounded-lg p-6">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">Respondent Segments</h3>
                  <div className="space-y-4">
                    <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-green-600 font-semibold">Promoters (9-10)</span>
                        <span className="text-2xl font-bold text-green-600">{dashboard.promoters}</span>
                      </div>
                      <p className="text-gray-500 text-xs">Loyal customers who will recommend</p>
                    </div>
                    <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-blue-600 font-semibold">Passives (7-8)</span>
                        <span className="text-2xl font-bold text-blue-600">{dashboard.passives}</span>
                      </div>
                      <p className="text-gray-500 text-xs">Satisfied but vulnerable to competition</p>
                    </div>
                    <div className="p-4 bg-orange-50 border border-orange-200 rounded-lg">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-orange-600 font-semibold">Detractors (0-6)</span>
                        <span className="text-2xl font-bold text-orange-600">{dashboard.detractors}</span>
                      </div>
                      <p className="text-gray-500 text-xs">Unhappy customers who may switch</p>
                    </div>
                  </div>
                </div>

                {/* Score distribution built from real responses */}
                <div className="bg-white border border-gray-200 rounded-lg p-6">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">Score Distribution</h3>
                  {totalResponses === 0 ? (
                    <div className="text-center text-gray-500 py-8 text-sm">No responses yet.</div>
                  ) : (
                    <div className="space-y-3">
                      {[10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0].map((score) => {
                        const count = responses.filter((r) => (r.score ?? -1) === score).length;
                        const percentage = responses.length > 0 ? (count / responses.length) * 100 : 0;
                        return (
                          <div key={score}>
                            <div className="flex items-center justify-between mb-1">
                              <span className={clsx(
                                'text-sm font-semibold',
                                score >= 9 ? 'text-green-600' : score >= 7 ? 'text-blue-600' : 'text-orange-600'
                              )}>
                                {score}
                              </span>
                              <span className="text-gray-500 text-xs">{count} responses</span>
                            </div>
                            <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
                              <div
                                className={clsx(
                                  'h-full',
                                  score >= 9 ? 'bg-green-500' : score >= 7 ? 'bg-blue-500' : 'bg-orange-500'
                                )}
                                style={{ width: `${percentage}%` }}
                              />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>

              {/* Recent verbatim responses (real feedback) */}
              <div className="bg-white border border-gray-200 rounded-lg p-6">
                <h3 className="text-lg font-semibold text-gray-900 mb-4">Recent Responses</h3>
                {responses.length === 0 ? (
                  <div className="text-center text-gray-500 py-8 text-sm">No NPS responses collected yet.</div>
                ) : (
                  <div className="space-y-3">
                    {responses.slice(0, 10).map((r, idx) => (
                      <div key={r.nps_id || idx} className="p-3 bg-gray-100 rounded-lg">
                        <div className="flex items-start justify-between mb-2">
                          <div>
                            <p className="text-gray-900 font-semibold text-sm">{r.customer_name || 'Customer'}</p>
                            <p className="text-gray-500 text-xs">
                              {r.responded_at ? new Date(r.responded_at).toLocaleDateString() : ''}
                            </p>
                          </div>
                          <span className={clsx(
                            'px-2 py-1 rounded text-xs font-semibold',
                            (r.score ?? 0) >= 9 ? 'bg-green-100 text-green-700' :
                            (r.score ?? 0) >= 7 ? 'bg-blue-100 text-blue-700' :
                            'bg-orange-100 text-orange-700'
                          )}>
                            {r.score ?? '—'}/10
                          </span>
                        </div>
                        {r.feedback && <p className="text-gray-600 text-sm">{r.feedback}</p>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Sentiment analysis has no backend source — honest empty state. */}
          {activeTab === 'sentiment' && (
            <div className="bg-white border border-gray-200 rounded-lg p-10 text-center">
              <Heart className="w-8 h-8 text-gray-400 mx-auto mb-3" />
              <p className="text-gray-700 font-medium">Sentiment analysis isn't available yet</p>
              <p className="text-gray-500 text-sm mt-1 max-w-md mx-auto">
                Free-text responses aren't classified by sentiment in the backend yet. NPS scores and
                verbatim feedback are available on the NPS tab.
              </p>
            </div>
          )}

          {/* No complaints workflow exists in the backend — honest empty state. */}
          {activeTab === 'complaints' && (
            <div className="bg-white border border-gray-200 rounded-lg p-10 text-center">
              <MessageSquare className="w-8 h-8 text-gray-400 mx-auto mb-3" />
              <p className="text-gray-700 font-medium">No complaint queue yet</p>
              <p className="text-gray-500 text-sm mt-1 max-w-md mx-auto">
                A dedicated complaints workflow isn't wired up yet. NPS detractors automatically create
                manager follow-up tasks, which appear on the Follow-ups dashboard.
              </p>
            </div>
          )}

          {/* No store-comparison endpoint exists — honest empty state. */}
          {activeTab === 'comparison' && (
            <div className="bg-white border border-gray-200 rounded-lg p-10 text-center">
              <Store className="w-8 h-8 text-gray-400 mx-auto mb-3" />
              <p className="text-gray-700 font-medium">Store comparison isn't available yet</p>
              <p className="text-gray-500 text-sm mt-1 max-w-md mx-auto">
                Cross-store NPS comparison needs a multi-store aggregation endpoint that doesn't exist
                yet. The figures above reflect your active store only.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default CustomerFeedback;
