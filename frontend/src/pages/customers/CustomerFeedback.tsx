// ============================================================================
// IMS 2.0 - Customer Feedback & NPS
// ============================================================================
// NPS/CSAT surveys, sentiment dashboard, complaints workflow, store comparison

import { useState } from 'react';
import { MessageSquare, AlertCircle, Store, Heart, Filter } from 'lucide-react';
import clsx from 'clsx';

interface Feedback {
  id: string;
  customer: string;
  date: string;
  type: 'nps' | 'complaint' | 'suggestion';
  score?: number;
  sentiment: 'positive' | 'neutral' | 'negative';
  message: string;
  status: 'open' | 'acknowledged' | 'resolved';
  store: string;
}

const FEEDBACK_DATA: Feedback[] = [
  {
    id: '1',
    customer: 'Rajesh Kumar',
    date: '2024-02-01',
    type: 'nps',
    score: 9,
    sentiment: 'positive',
    message: 'Excellent service and product quality. Highly recommend!',
    status: 'acknowledged',
    store: 'Main Store',
  },
  {
    id: '2',
    customer: 'Priya Sharma',
    date: '2024-01-30',
    type: 'complaint',
    sentiment: 'negative',
    message: 'Long wait time at checkout. Need more staff during peak hours.',
    status: 'open',
    store: 'Downtown',
  },
  {
    id: '3',
    customer: 'Amit Patel',
    date: '2024-01-28',
    type: 'suggestion',
    sentiment: 'neutral',
    message: 'Consider offering online prescription verification service.',
    status: 'acknowledged',
    store: 'Mall Location',
  },
  {
    id: '4',
    customer: 'Sunita Singh',
    date: '2024-01-25',
    type: 'nps',
    score: 10,
    sentiment: 'positive',
    message: 'Best opticians in the city. Very professional staff.',
    status: 'resolved',
    store: 'Main Store',
  },
  {
    id: '5',
    customer: 'Vikram Desai',
    date: '2024-01-22',
    type: 'complaint',
    sentiment: 'negative',
    message: 'Product quality not as advertised. Disappointed with frames.',
    status: 'resolved',
    store: 'Downtown',
  },
];

const STORES = ['All Stores', 'Main Store', 'Downtown', 'Mall Location'];

export function CustomerFeedback() {
  const [activeTab, setActiveTab] = useState<'nps' | 'sentiment' | 'complaints' | 'comparison'>('nps');
  const [filterStore, setFilterStore] = useState('All Stores');

  const npsScores = FEEDBACK_DATA.filter(f => f.type === 'nps');
  const avgNPS = npsScores.length > 0 ? Math.round(npsScores.reduce((sum, f) => sum + (f.score || 0), 0) / npsScores.length) : 0;
  const promoters = npsScores.filter(f => (f.score || 0) >= 9).length;
  const detractors = npsScores.filter(f => (f.score || 0) <= 6).length;
  const npsScore = ((promoters - detractors) / npsScores.length) * 100;

  const sentiments = {
    positive: FEEDBACK_DATA.filter(f => f.sentiment === 'positive').length,
    neutral: FEEDBACK_DATA.filter(f => f.sentiment === 'neutral').length,
    negative: FEEDBACK_DATA.filter(f => f.sentiment === 'negative').length,
  };

  const complaints = FEEDBACK_DATA.filter(f => f.type === 'complaint');

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>CRM · Feedback</div>
          <h1>What they actually thought.</h1>
          <div className="hint">NPS after every sale + eye test. Sentiment, complaint queue, store-vs-store comparison, Google review incentive.</div>
        </div>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">NPS Score</p>
          <p className="text-2xl font-bold text-blue-600">{npsScore.toFixed(0)}</p>
          <p className="text-xs text-gray-500">Excellent</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Avg Rating</p>
          <p className="text-2xl font-bold text-green-600">{avgNPS}/10</p>
          <p className="text-xs text-gray-500">{npsScores.length} responses</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Positive Sentiment</p>
          <p className="text-2xl font-bold text-green-600">{sentiments.positive}</p>
          <p className="text-xs text-gray-500">{((sentiments.positive / FEEDBACK_DATA.length) * 100).toFixed(0)}% of feedback</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-gray-500 text-sm mb-1">Open Complaints</p>
          <p className="text-2xl font-bold text-orange-600">{complaints.filter(c => c.status === 'open').length}</p>
          <p className="text-xs text-gray-500">Need attention</p>
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

      {activeTab === 'nps' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* NPS Distribution */}
          <div className="bg-white border border-gray-200 rounded-lg p-6 border border-gray-200">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Score Distribution</h3>
            <div className="space-y-3">
              {[10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0].map((score) => {
                const count = npsScores.filter(f => (f.score || 0) === score).length;
                const percentage = npsScores.length > 0 ? (count / npsScores.length) * 100 : 0;
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
          </div>

          {/* NPS Categories */}
          <div className="bg-white border border-gray-200 rounded-lg p-6 border border-gray-200">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Respondent Segments</h3>
            <div className="space-y-4">
              <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-green-600 font-semibold">Promoters (9-10)</span>
                  <span className="text-2xl font-bold text-green-600">{promoters}</span>
                </div>
                <p className="text-gray-500 text-xs">Loyal customers who will recommend</p>
              </div>
              <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-blue-600 font-semibold">Passives (7-8)</span>
                  <span className="text-2xl font-bold text-blue-600">
                    {npsScores.filter(f => (f.score || 0) >= 7 && (f.score || 0) <= 8).length}
                  </span>
                </div>
                <p className="text-gray-500 text-xs">Satisfied but vulnerable to competition</p>
              </div>
              <div className="p-4 bg-orange-50 border border-orange-200 rounded-lg">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-orange-600 font-semibold">Detractors (0-6)</span>
                  <span className="text-2xl font-bold text-orange-600">{detractors}</span>
                </div>
                <p className="text-gray-500 text-xs">Unhappy customers who may switch</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'sentiment' && (
        <div className="space-y-4">
          {/* Sentiment Pie-like display */}
          <div className="bg-white border border-gray-200 rounded-lg p-6 border border-gray-200">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Overall Sentiment</h3>
            <div className="grid grid-cols-3 gap-4">
              <div className="text-center p-4 bg-green-50 border border-green-200 rounded-lg">
                <Heart className="w-8 h-8 text-green-600 mx-auto mb-2" />
                <p className="text-2xl font-bold text-green-600">{sentiments.positive}</p>
                <p className="text-gray-500 text-sm">Positive</p>
                <p className="text-green-600 text-xs font-semibold">
                  {((sentiments.positive / FEEDBACK_DATA.length) * 100).toFixed(0)}%
                </p>
              </div>
              <div className="text-center p-4 bg-blue-50 border border-blue-200 rounded-lg">
                <MessageSquare className="w-8 h-8 text-blue-600 mx-auto mb-2" />
                <p className="text-2xl font-bold text-blue-600">{sentiments.neutral}</p>
                <p className="text-gray-500 text-sm">Neutral</p>
                <p className="text-blue-600 text-xs font-semibold">
                  {((sentiments.neutral / FEEDBACK_DATA.length) * 100).toFixed(0)}%
                </p>
              </div>
              <div className="text-center p-4 bg-orange-50 border border-orange-200 rounded-lg">
                <AlertCircle className="w-8 h-8 text-orange-600 mx-auto mb-2" />
                <p className="text-2xl font-bold text-orange-600">{sentiments.negative}</p>
                <p className="text-gray-500 text-sm">Negative</p>
                <p className="text-orange-600 text-xs font-semibold">
                  {((sentiments.negative / FEEDBACK_DATA.length) * 100).toFixed(0)}%
                </p>
              </div>
            </div>
          </div>

          {/* Recent Feedback */}
          <div className="bg-white border border-gray-200 rounded-lg p-6 border border-gray-200">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Recent Feedback</h3>
            <div className="space-y-3">
              {FEEDBACK_DATA.slice(0, 5).map((feedback) => (
                <div key={feedback.id} className="p-3 bg-gray-100 rounded-lg">
                  <div className="flex items-start justify-between mb-2">
                    <div>
                      <p className="text-gray-900 font-semibold text-sm">{feedback.customer}</p>
                      <p className="text-gray-500 text-xs">{new Date(feedback.date).toLocaleDateString()}</p>
                    </div>
                    <span className={clsx(
                      'px-2 py-1 rounded text-xs font-semibold',
                      feedback.sentiment === 'positive' ? 'bg-green-100 text-green-700' :
                      feedback.sentiment === 'neutral' ? 'bg-blue-100 text-blue-700' :
                      'bg-orange-100 text-orange-700'
                    )}>
                      {feedback.sentiment}
                    </span>
                  </div>
                  <p className="text-gray-600 text-sm">{feedback.message}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {activeTab === 'complaints' && (
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <Filter className="w-5 h-5 text-gray-500" />
            <select
              value={filterStore}
              onChange={(e) => setFilterStore(e.target.value)}
              className="bg-white border border-gray-300 rounded px-3 py-2 text-gray-900 text-sm"
            >
              {STORES.map((store) => (
                <option key={store} value={store}>
                  {store}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-3">
            {complaints.map((complaint) => (
              <div key={complaint.id} className="bg-white border border-gray-200 rounded-lg p-4 border border-gray-200">
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <p className="text-gray-900 font-semibold">{complaint.customer}</p>
                    <p className="text-gray-500 text-xs">{complaint.store} • {new Date(complaint.date).toLocaleDateString()}</p>
                  </div>
                  <span className={clsx(
                    'px-3 py-1 rounded-full text-xs font-semibold',
                    complaint.status === 'open' ? 'bg-red-100 text-red-700' :
                    complaint.status === 'acknowledged' ? 'bg-yellow-100 text-yellow-700' :
                    'bg-green-100 text-green-700'
                  )}>
                    {complaint.status}
                  </span>
                </div>
                <p className="text-gray-600 text-sm">{complaint.message}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === 'comparison' && (
        <div className="bg-white border border-gray-200 rounded-lg p-6 border border-gray-200">
          <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
            <Store className="w-5 h-5" />
            Store Performance Comparison
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-3 px-4 text-gray-500 font-semibold text-sm">Store</th>
                  <th className="text-right py-3 px-4 text-gray-500 font-semibold text-sm">Avg NPS</th>
                  <th className="text-right py-3 px-4 text-gray-500 font-semibold text-sm">Positive %</th>
                  <th className="text-right py-3 px-4 text-gray-500 font-semibold text-sm">Complaints</th>
                  <th className="text-right py-3 px-4 text-gray-500 font-semibold text-sm">Response Rate</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { name: 'Main Store', nps: 75, positive: 72, complaints: 2, response: 100 },
                  { name: 'Downtown', nps: 62, positive: 58, complaints: 5, response: 80 },
                  { name: 'Mall Location', nps: 68, positive: 65, complaints: 1, response: 100 },
                ].map((store) => (
                  <tr key={store.name} className="border-b border-gray-200 hover:bg-gray-100/50">
                    <td className="py-3 px-4 text-gray-900 text-sm">{store.name}</td>
                    <td className="py-3 px-4 text-right font-semibold">
                      <span className="text-blue-600">{store.nps}</span>
                    </td>
                    <td className="py-3 px-4 text-right">
                      <span className="text-green-600">{store.positive}%</span>
                    </td>
                    <td className="py-3 px-4 text-right">
                      <span className={clsx(
                        'font-semibold',
                        store.complaints <= 2 ? 'text-green-600' : 'text-orange-600'
                      )}>
                        {store.complaints}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right">
                      <span className="text-gray-600">{store.response}%</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export default CustomerFeedback;
