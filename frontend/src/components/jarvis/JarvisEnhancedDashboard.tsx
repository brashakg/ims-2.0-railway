// ============================================================================
// IMS 2.0 - JARVIS AI Enhanced Dashboard
// ============================================================================
// Advanced AI control system with real-time analytics and recommendations

import { useState, useEffect, useRef } from 'react';
import {
  Bot,
  Send,
  TrendingUp,
  AlertCircle,
  Zap,
  Brain,
  BarChart3,
  LineChart,
  PieChart,
  Activity,
  Download,
  Settings,
  Sparkles,
  AlertTriangle,
} from 'lucide-react';
import clsx from 'clsx';

interface Insight {
  id: string;
  title: string;
  description: string;
  metric: string;
  value: number | string;
  change?: number;
  severity: 'info' | 'warning' | 'critical';
  timestamp: Date;
}

interface Recommendation {
  id: string;
  priority: 'critical' | 'high' | 'medium' | 'low';
  category: string;
  title: string;
  description: string;
  impact: string;
  action: string;
  confidence: number;
}

interface Analytics {
  sales: {
    today: number;
    change: number;
    forecast: number;
  };
  orders: {
    pending: number;
    completed: number;
    rate: number;
  };
  inventory: {
    lowStock: number;
    turnover: number;
    health: string;
  };
  staff: {
    utilization: number;
    productivity: number;
    attendance: string;
  };
}

interface QueryHistory {
  id: string;
  query: string;
  response: string;
  timestamp: Date;
  type: string;
}

export function JarvisEnhancedDashboard() {
  const [insights, setInsights] = useState<Insight[]>([]);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [queryHistory, setQueryHistory] = useState<QueryHistory[]>([]);
  const [currentQuery, setCurrentQuery] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [selectedTab, setSelectedTab] = useState<'dashboard' | 'analytics' | 'recommendations' | 'chat'>('dashboard');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    loadDashboardData();
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [queryHistory]);

  const loadDashboardData = async () => {
    // Load analytics data
    setAnalytics({
      sales: {
        today: 145000,
        change: 12.5,
        forecast: 152000,
      },
      orders: {
        pending: 12,
        completed: 28,
        rate: 95,
      },
      inventory: {
        lowStock: 23,
        turnover: 4.2,
        health: 'Good',
      },
      staff: {
        utilization: 87,
        productivity: 92,
        attendance: '42/45',
      },
    });

    // Load insights
    setInsights([
      {
        id: '1',
        title: 'Sales Surge',
        description: 'Sales are 12.5% above target this week',
        metric: 'Revenue',
        value: '₹145K',
        change: 12.5,
        severity: 'info',
        timestamp: new Date(),
      },
      {
        id: '2',
        title: 'Critical Stock Alert',
        description: '23 items at critical stock levels',
        metric: 'Low Stock Items',
        value: '23',
        change: -5,
        severity: 'critical',
        timestamp: new Date(),
      },
      {
        id: '3',
        title: 'High Staff Utilization',
        description: 'Staff utilization is 87%, above 80% threshold',
        metric: 'Utilization Rate',
        value: '87%',
        change: 3,
        severity: 'warning',
        timestamp: new Date(),
      },
    ]);

    // Load recommendations
    setRecommendations([
      {
        id: '1',
        priority: 'critical',
        category: 'inventory',
        title: 'Urgent: Reorder Critical Items',
        description: '5 high-demand products are critically low',
        impact: 'Prevent ₹2.5L in lost sales',
        action: 'Generate purchase order',
        confidence: 0.95,
      },
      {
        id: '2',
        priority: 'high',
        category: 'staffing',
        title: 'Staff Rebalancing',
        description: 'Delhi store is understaffed',
        impact: 'Improve service quality',
        action: 'Transfer 2 staff members',
        confidence: 0.88,
      },
      {
        id: '3',
        priority: 'medium',
        category: 'marketing',
        title: 'Promotional Campaign',
        description: '15 slow-moving products need boost',
        impact: 'Increase sales by 20%',
        action: 'Launch targeted promotion',
        confidence: 0.75,
      },
    ]);
  };

  const handleQuery = async () => {
    if (!currentQuery.trim()) return;

    setIsProcessing(true);

    // Simulate API call
    const newQuery: QueryHistory = {
      id: `query_${Date.now()}`,
      query: currentQuery,
      response: generateAIResponse(currentQuery),
      timestamp: new Date(),
      type: 'user_query',
    };

    setQueryHistory([...queryHistory, newQuery]);
    setCurrentQuery('');
    setIsProcessing(false);
  };

  const generateAIResponse = (query: string): string => {
    // Simulate AI response based on query
    const responses: Record<string, string> = {
      sales: 'Sales are currently at ₹145K today, representing a 12.5% increase from target. Trend shows consistent growth with strong performance in optical frames category.',
      inventory: 'Critical inventory alert: 23 items are at stock levels below optimal. Recommend immediate reorder for high-demand products to prevent stockouts.',
      forecast: 'Based on current trends, we forecast sales of ₹152K for tomorrow. Growth trajectory suggests 15-18% month-over-month increase.',
      staff: 'Current staff utilization is 87%, above the optimal 80% threshold. Recommend staff rebalancing across stores.',
      default: 'Processing your request... Based on current business data and analytics, here are my insights on your query.',
    };

    for (const [key, response] of Object.entries(responses)) {
      if (query.toLowerCase().includes(key)) {
        return response;
      }
    }

    return responses.default;
  };

  const exportData = (format: 'pdf' | 'excel' | 'csv') => {
    console.log(`Exporting data as ${format}`);
    // Implementation for export functionality
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="bg-gradient-to-r from-indigo-600 to-blue-600 rounded-lg p-6 text-white">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Bot className="w-8 h-8" />
            <div>
              <h1 className="text-3xl font-bold">JARVIS AI Control System</h1>
              <p className="text-blue-100">Real-time business intelligence and optimization engine</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => exportData('pdf')}
              className="p-2 hover:bg-white/20 rounded-lg transition-colors"
              title="Export as PDF"
            >
              <Download className="w-5 h-5" />
            </button>
            <button className="p-2 hover:bg-white/20 rounded-lg transition-colors">
              <Settings className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 bg-white rounded-lg p-2 border border-gray-200">
        {(['dashboard', 'analytics', 'recommendations', 'chat'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setSelectedTab(tab)}
            className={clsx(
              'px-4 py-2 rounded-lg font-medium transition-colors',
              selectedTab === tab
                ? 'bg-indigo-600 text-white'
                : 'text-gray-600 hover:bg-gray-100'
            )}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Dashboard Tab */}
      {selectedTab === 'dashboard' && (
        <div className="space-y-6">
          {/* Key Metrics */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <MetricCard
              icon={TrendingUp}
              title="Sales Today"
              value={`₹${analytics?.sales.today.toLocaleString()}`}
              change={analytics?.sales.change}
              unit="%"
            />
            <MetricCard
              icon={Activity}
              title="Orders"
              value={`${analytics?.orders.completed}`}
              subtitle={`${analytics?.orders.pending} pending`}
            />
            <MetricCard
              icon={AlertCircle}
              title="Low Stock Items"
              value={`${analytics?.inventory.lowStock}`}
              severity="warning"
            />
            <MetricCard
              icon={Zap}
              title="Staff Utilization"
              value={`${analytics?.staff.utilization}%`}
              change={3}
              unit="%"
            />
          </div>

          {/* Insights Grid */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {insights.map((insight) => (
              <InsightCard key={insight.id} insight={insight} />
            ))}
          </div>

          {/* Top Recommendations */}
          <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
            <div className="p-4 bg-gray-50 border-b border-gray-200">
              <h2 className="font-bold text-lg flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-indigo-600" />
                Top Recommendations
              </h2>
            </div>
            <div className="divide-y">
              {recommendations.slice(0, 3).map((rec) => (
                <RecommendationItem key={rec.id} recommendation={rec} />
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Analytics Tab */}
      {selectedTab === 'analytics' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <AnalyticsChart
            title="Revenue Trend"
            icon={LineChart}
            data="Last 30 days trending upward"
          />
          <AnalyticsChart
            title="Order Distribution"
            icon={PieChart}
            data="45% completed, 30% pending, 25% ready"
          />
          <AnalyticsChart
            title="Inventory Status"
            icon={BarChart3}
            data="23 items critical, 156 optimal, 12 overstock"
          />
          <AnalyticsChart
            title="Staff Performance"
            icon={Activity}
            data="87% utilization, 92% productivity"
          />
        </div>
      )}

      {/* Recommendations Tab */}
      {selectedTab === 'recommendations' && (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <div className="p-4 bg-gray-50 border-b">
            <h2 className="font-bold text-lg">AI-Generated Recommendations</h2>
          </div>
          <div className="divide-y max-h-96 overflow-y-auto">
            {recommendations.map((rec) => (
              <div key={rec.id} className="p-4 hover:bg-gray-50 transition-colors">
                <div className="flex items-start justify-between mb-2">
                  <h3 className="font-bold text-gray-900">{rec.title}</h3>
                  <PriorityBadge priority={rec.priority} />
                </div>
                <p className="text-sm text-gray-600 mb-2">{rec.description}</p>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-indigo-600">Impact: {rec.impact}</span>
                  <span className="text-gray-500">Confidence: {(rec.confidence * 100).toFixed(0)}%</span>
                </div>
                <button className="mt-2 text-sm text-indigo-600 hover:text-indigo-700 font-medium">
                  View Details →
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Chat Tab */}
      {selectedTab === 'chat' && (
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden flex flex-col h-96">
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {queryHistory.length === 0 && (
              <div className="text-center text-gray-500 py-8">
                <Bot className="w-12 h-12 mx-auto mb-2 text-gray-300" />
                <p>Ask me anything about your business...</p>
              </div>
            )}
            {queryHistory.map((msg) => (
              <div key={msg.id} className="space-y-2">
                <div className="flex justify-end">
                  <div className="bg-indigo-600 text-white rounded-lg p-3 max-w-xs">
                    <p className="text-sm">{msg.query}</p>
                  </div>
                </div>
                <div className="flex justify-start">
                  <div className="bg-gray-100 rounded-lg p-3 max-w-xs">
                    <p className="text-sm text-gray-900">{msg.response}</p>
                  </div>
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          {/* Query Input */}
          <div className="border-t border-gray-200 p-4">
            <div className="flex gap-2">
              <input
                type="text"
                value={currentQuery}
                onChange={(e) => setCurrentQuery(e.target.value)}
                onKeyPress={(e) => e.key === 'Enter' && handleQuery()}
                placeholder="Ask about sales, inventory, staff..."
                className="flex-1 px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <button
                onClick={handleQuery}
                disabled={isProcessing || !currentQuery.trim()}
                className="bg-indigo-600 text-white px-4 py-2 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >
                <Send className="w-5 h-5" />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

interface MetricCardProps {
  icon: typeof TrendingUp;
  title: string;
  value: string;
  change?: number;
  unit?: string;
  subtitle?: string;
  severity?: 'normal' | 'warning' | 'critical';
}

function MetricCard({ icon: Icon, title, value, change, unit, subtitle, severity }: MetricCardProps) {
  const bgColor =
    severity === 'critical' ? 'bg-red-50'
    : severity === 'warning' ? 'bg-yellow-50'
    : 'bg-green-50';

  return (
    <div className={clsx('rounded-lg p-4 border border-gray-200', bgColor)}>
      <div className="flex items-start justify-between mb-2">
        <Icon className="w-5 h-5 text-indigo-600" />
        {change && (
          <div className={clsx('text-sm font-medium', change > 0 ? 'text-green-600' : 'text-red-600')}>
            {change > 0 ? '↑' : '↓'} {Math.abs(change)}
            {unit}
          </div>
        )}
      </div>
      <p className="text-sm text-gray-600">{title}</p>
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      {subtitle && <p className="text-xs text-gray-500 mt-1">{subtitle}</p>}
    </div>
  );
}

function InsightCard({ insight }: { insight: Insight }) {
  const bgColor =
    insight.severity === 'critical' ? 'bg-red-50 border-red-200'
    : insight.severity === 'warning' ? 'bg-yellow-50 border-yellow-200'
    : 'bg-blue-50 border-blue-200';

  const iconColor =
    insight.severity === 'critical' ? 'text-red-600'
    : insight.severity === 'warning' ? 'text-yellow-600'
    : 'text-blue-600';

  return (
    <div className={clsx('rounded-lg p-4 border', bgColor)}>
      <div className="flex items-start gap-3">
        {insight.severity === 'critical' && <AlertTriangle className={clsx('w-5 h-5 flex-shrink-0', iconColor)} />}
        {insight.severity === 'warning' && <AlertCircle className={clsx('w-5 h-5 flex-shrink-0', iconColor)} />}
        {insight.severity === 'info' && <Brain className={clsx('w-5 h-5 flex-shrink-0', iconColor)} />}
        <div className="flex-1">
          <h3 className="font-bold text-gray-900">{insight.title}</h3>
          <p className="text-sm text-gray-600 mt-1">{insight.description}</p>
          <div className="flex items-center justify-between mt-2 text-xs text-gray-500">
            <span>{insight.metric}: {insight.value}</span>
            <span>{insight.timestamp.toLocaleTimeString()}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function RecommendationItem({ recommendation }: { recommendation: Recommendation }) {
  return (
    <div className="p-4 hover:bg-gray-50 transition-colors">
      <div className="flex items-start justify-between mb-2">
        <h3 className="font-bold text-gray-900">{recommendation.title}</h3>
        <PriorityBadge priority={recommendation.priority} />
      </div>
      <p className="text-sm text-gray-600 mb-2">{recommendation.description}</p>
      <div className="flex items-center justify-between text-sm">
        <span className="text-indigo-600">Impact: {recommendation.impact}</span>
        <span className="text-gray-500">Confidence: {(recommendation.confidence * 100).toFixed(0)}%</span>
      </div>
    </div>
  );
}

function PriorityBadge({ priority }: { priority: string }) {
  const colors = {
    critical: 'bg-red-100 text-red-700',
    high: 'bg-orange-100 text-orange-700',
    medium: 'bg-yellow-100 text-yellow-700',
    low: 'bg-green-100 text-green-700',
  };

  return (
    <span className={clsx('px-2 py-1 rounded text-xs font-medium', colors[priority as keyof typeof colors])}>
      {priority.charAt(0).toUpperCase() + priority.slice(1)}
    </span>
  );
}

function AnalyticsChart({ title, icon: Icon, data }: { title: string; icon: typeof LineChart; data: string }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6">
      <div className="flex items-center gap-2 mb-4">
        <Icon className="w-5 h-5 text-indigo-600" />
        <h3 className="font-bold text-gray-900">{title}</h3>
      </div>
      <div className="h-32 bg-gray-50 rounded flex items-center justify-center text-sm text-gray-500">
        {data}
      </div>
    </div>
  );
}

export default JarvisEnhancedDashboard;
