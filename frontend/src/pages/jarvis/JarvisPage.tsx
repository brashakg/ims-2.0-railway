// ============================================================================
// IMS 2.0 - JARVIS AI Control Interface
// ============================================================================
// SUPERADMIN EXCLUSIVE - Like Jarvis to Iron Man
// Full business intelligence and control system

import { useState, useEffect, useRef } from 'react';
import {
  Bot,
  Send,
  Mic,
  MicOff,
  Package,
  Users,
  ShoppingCart,
  Clock,
  Zap,
  Activity,
  Target,
  Brain,
  Sparkles,
  RefreshCw,
  ChevronRight,
  ArrowUpRight,
  ArrowDownRight,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import clsx from 'clsx';

// Types
interface Message {
  id: string;
  type: 'user' | 'jarvis';
  content: string;
  timestamp: Date;
  data?: Record<string, unknown>;
}

interface QuickInsight {
  revenue_today: number;
  revenue_growth: number;
  orders_today: number;
  pending_orders: number;
  low_stock_count: number;
  staff_present: string;
  top_recommendation: {
    priority: string;
    title: string;
    description: string;
    action: string;
    impact: string;
  } | null;
  greeting: string;
}

interface Recommendation {
  priority: string;
  category: string;
  title: string;
  description: string;
  action: string;
  impact: string;
}

export function JarvisPage() {
  const { hasRole } = useAuth();
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [insights, setInsights] = useState<QuickInsight | null>(null);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [isListening, setIsListening] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // STRICT ACCESS CONTROL - SUPERADMIN ONLY
  const isSuperAdmin = hasRole(['SUPERADMIN']);

  // If not superadmin, render nothing (404-like behavior)
  if (!isSuperAdmin) {
    return null;
  }

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Load initial insights
  useEffect(() => {
    loadInsights();
    loadRecommendations();
    // Add initial greeting
    setMessages([
      {
        id: '1',
        type: 'jarvis',
        content: getGreeting(),
        timestamp: new Date(),
      },
    ]);
  }, []);

  const getGreeting = () => {
    const hour = new Date().getHours();
    const timeGreeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
    return `${timeGreeting}, Sir. I'm JARVIS, your AI business intelligence system. I have full access to all business data and I'm ready to assist you with analytics, predictions, recommendations, and operational decisions. What would you like to know?`;
  };

  const loadInsights = async () => {
    // Simulated data - would come from API
    setInsights({
      revenue_today: 145000,
      revenue_growth: 9.1,
      orders_today: 28,
      pending_orders: 12,
      low_stock_count: 23,
      staff_present: '42/45',
      top_recommendation: {
        priority: 'high',
        title: 'Urgent Reorder Required',
        description: '5 high-demand products are critically low',
        action: 'Generate purchase order',
        impact: 'Prevent ₹2.5L potential lost sales',
      },
      greeting: getGreeting(),
    });
  };

  const loadRecommendations = async () => {
    setRecommendations([
      {
        priority: 'high',
        category: 'inventory',
        title: 'Urgent Reorder Required',
        description: '5 high-demand products are critically low. Immediate reorder recommended.',
        action: 'Generate purchase order for critical items',
        impact: 'Prevent ₹2.5L potential lost sales',
      },
      {
        priority: 'high',
        category: 'staffing',
        title: 'GK Delhi Store Understaffed',
        description: 'Orders per staff ratio is 54% above optimal.',
        action: 'Transfer 2 staff from Noida to GK Delhi',
        impact: 'Improve customer service',
      },
      {
        priority: 'medium',
        category: 'marketing',
        title: 'Re-engagement Campaign Needed',
        description: '234 high-value customers haven\'t purchased in 6+ months.',
        action: 'Launch personalized WhatsApp campaign',
        impact: 'Potential ₹8L in recovered revenue',
      },
    ]);
  };

  const formatCurrency = (amount: number) => {
    if (amount >= 10000000) return `₹${(amount / 10000000).toFixed(2)} Cr`;
    if (amount >= 100000) return `₹${(amount / 100000).toFixed(2)} L`;
    if (amount >= 1000) return `₹${(amount / 1000).toFixed(1)}K`;
    return `₹${amount}`;
  };

  const handleSend = async () => {
    if (!inputValue.trim() || isLoading) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      type: 'user',
      content: inputValue,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    const queryText = inputValue;
    setInputValue('');
    setIsLoading(true);

    try {
      // Call JARVIS API (powered by Claude)
      const response = await fetch('/api/jarvis/query', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('token')}`,
        },
        body: JSON.stringify({ message: queryText }),
      });

      if (response.ok) {
        const data = await response.json();
        const jarvisMessage: Message = {
          id: (Date.now() + 1).toString(),
          type: 'jarvis',
          content: data.response,
          timestamp: new Date(),
          data: {
            ai_powered: data.ai_powered,
            model: data.model,
            intent: data.intent_detected,
          },
        };
        setMessages((prev) => [...prev, jarvisMessage]);
      } else {
        // Fallback to local response if API fails
        const fallbackResponse = generateResponse(queryText);
        const jarvisMessage: Message = {
          id: (Date.now() + 1).toString(),
          type: 'jarvis',
          content: fallbackResponse,
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, jarvisMessage]);
      }
    } catch {
      // Fallback to local response on error
      const fallbackResponse = generateResponse(queryText);
      const jarvisMessage: Message = {
        id: (Date.now() + 1).toString(),
        type: 'jarvis',
        content: fallbackResponse,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, jarvisMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const generateResponse = (query: string): string => {
    const q = query.toLowerCase();

    if (q.includes('sales') || q.includes('revenue')) {
      return `**Sales Report - Today**

📊 Revenue: ${formatCurrency(145000)}
📈 vs Yesterday: +9.1%
🛒 Orders: 28
💰 Avg Order Value: ${formatCurrency(5178)}

**Top Performing Store:** CP Delhi (111% of target)
**Top Category:** Frames (₹12.5L)

Excellent performance! We're significantly ahead of our targets. The Ray-Ban Aviator collection is driving most of the premium segment sales.`;
    }

    if (q.includes('inventory') || q.includes('stock')) {
      return `**Inventory Status Report**

📦 Total Products: 4,567
⚠️ Low Stock Items: 23
🚫 Out of Stock: 5
💎 Inventory Value: ${formatCurrency(12500000)}

**Critical Alerts:**
• 🔴 Acuvue Oasys -2.00 is OUT OF STOCK (High demand)
• 🟡 Ray-Ban Wayfarer Black - Only 3 left
• 🟠 1-Day Acuvue expires in 38 days (45 units)

I recommend generating a purchase order for the critical items immediately.`;
    }

    if (q.includes('customer') || q.includes('clients')) {
      return `**Customer Intelligence Report**

👥 Total Customers: 8,934
🔄 Repeat Purchase Rate: 42.5%
⭐ NPS Score: 72

**Customer Segments:**
• Premium Buyers: 1,234 (Avg: ₹25K)
• Regular Customers: 3,456 (Avg: ₹8K)
• Contact Lens Users: 2,100 (Avg: ₹12K)

**⚠️ Churn Risk Alert:**
• Amit Sharma - LTV: ₹45K (high risk, 8 months inactive)
• Priya Gupta - LTV: ₹32K (medium risk)

Consider launching a re-engagement campaign for dormant high-value customers.`;
    }

    if (q.includes('staff') || q.includes('employee') || q.includes('team')) {
      return `**Staff Performance Report**

✅ Present Today: 93.3% (42/45)
⏰ Late Arrivals: 2
📊 Avg Sales/Staff: ${formatCurrency(32500)}

**Top Performers:**
1. Rajesh Kumar (Sales) - ₹4.5L | ⭐4.8
2. Neha Gupta (Optometrist) - 89 tests | ⭐4.9
3. Vikram Singh (Sales) - ₹3.8L | ⭐4.5

**Alert:** GK Delhi store is understaffed. I recommend temporarily transferring 2 staff from Noida.`;
    }

    if (q.includes('predict') || q.includes('forecast')) {
      return `**AI Predictions & Forecasts**

📈 **Sales Forecast (Next Month):** ${formatCurrency(3450000)}
🎯 Confidence: 85%

**Key Factors:**
• Festive season approaching
• New collection launch
• Marketing campaign active

**Demand Trends:**
📈 Sunglasses: +25% (Summer approaching)
➡️ Contact Lenses: +5% (Consistent demand)
📈 Progressive Lenses: +15% (Aging customer base)

**⚠️ Stock Alerts:**
• SG-RB-AVI001: 10 days until stockout
• CL-BL-PUR001: 33 days until stockout`;
    }

    if (q.includes('recommend') || q.includes('suggest') || q.includes('should')) {
      return `**My Recommendations:**

1. 🔴 **Urgent Reorder Required**
   5 high-demand products are critically low.
   💡 *Action:* Generate purchase order for critical items
   📈 *Impact:* Prevent ₹2.5L potential lost sales

2. 🔴 **GK Delhi Store Understaffed**
   Orders per staff ratio is 54% above optimal.
   💡 *Action:* Transfer 2 staff from Noida to GK Delhi
   📈 *Impact:* Improve customer service, reduce wait times

3. 🟡 **Re-engagement Campaign Needed**
   234 high-value customers haven't purchased in 6+ months.
   💡 *Action:* Launch personalized WhatsApp campaign
   📈 *Impact:* Potential ₹8L in recovered revenue

Would you like me to execute any of these actions?`;
    }

    return `I understand you're asking about "${query}". Let me analyze our data...

Based on my analysis of your business data, here's what I found:

📊 **Overview:**
• Today's revenue: ${formatCurrency(145000)} (+9.1%)
• Active orders: 28 (12 pending)
• Inventory alerts: 23 items need attention

Is there a specific aspect you'd like me to dive deeper into? I can provide detailed analysis on:
• Sales & Revenue trends
• Inventory management
• Customer insights
• Staff performance
• Predictions & forecasts
• Actionable recommendations`;
  };

  const handleQuickQuery = (query: string) => {
    setInputValue(query);
  };

  const quickQueries = [
    { label: "Today's Sales", query: "What are today's sales?" },
    { label: 'Inventory Status', query: 'Show me inventory status' },
    { label: 'Customer Insights', query: 'Give me customer insights' },
    { label: 'Staff Performance', query: 'How is my team performing?' },
    { label: 'Predictions', query: 'What are your predictions?' },
    { label: 'Recommendations', query: 'What do you recommend?' },
  ];

  return (
    <div className="h-[calc(100vh-120px)] flex flex-col">
      {/* JARVIS Header */}
      <div className="bg-gradient-to-r from-gray-900 to-gray-800 text-white p-4 rounded-t-xl">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-full bg-gradient-to-br from-blue-400 to-cyan-500 flex items-center justify-center">
              <Brain className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-xl font-bold flex items-center gap-2">
                JARVIS
                <Sparkles className="w-4 h-4 text-yellow-400" />
              </h1>
              <p className="text-sm text-gray-400">Powered by Claude AI • Business Intelligence System</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 text-sm bg-gray-800/50 px-3 py-1 rounded-full">
              <Activity className="w-4 h-4 text-green-400 animate-pulse" />
              <span className="text-green-400">Online</span>
              <span className="text-gray-500">•</span>
              <span className="text-cyan-400 text-xs">Claude</span>
            </div>
            <button
              onClick={() => {
                loadInsights();
                loadRecommendations();
              }}
              className="p-2 hover:bg-gray-700 rounded-lg transition-colors"
            >
              <RefreshCw className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden bg-gray-50">
        {/* Chat Panel */}
        <div className="flex-1 flex flex-col">
          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.map((message) => (
              <div
                key={message.id}
                className={clsx(
                  'flex',
                  message.type === 'user' ? 'justify-end' : 'justify-start'
                )}
              >
                <div
                  className={clsx(
                    'max-w-[80%] rounded-2xl px-4 py-3',
                    message.type === 'user'
                      ? 'bg-bv-gold-500 text-white'
                      : 'bg-white shadow-md'
                  )}
                >
                  {message.type === 'jarvis' && (
                    <div className="flex items-center gap-2 mb-2">
                      <Bot className="w-4 h-4 text-blue-500" />
                      <span className="text-xs font-medium text-blue-500">JARVIS</span>
                      {(message.data as { ai_powered?: boolean })?.ai_powered && (
                        <span className="text-xs px-1.5 py-0.5 bg-gradient-to-r from-cyan-500 to-blue-500 text-white rounded-full">
                          Claude AI
                        </span>
                      )}
                    </div>
                  )}
                  <div
                    className={clsx(
                      'text-sm whitespace-pre-wrap',
                      message.type === 'jarvis' && 'prose prose-sm max-w-none'
                    )}
                    dangerouslySetInnerHTML={{
                      __html: message.content
                        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                        .replace(/\n/g, '<br />')
                    }}
                  />
                  <div
                    className={clsx(
                      'text-xs mt-2',
                      message.type === 'user' ? 'text-white/70' : 'text-gray-400'
                    )}
                  >
                    {message.timestamp.toLocaleTimeString([], {
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </div>
                </div>
              </div>
            ))}

            {isLoading && (
              <div className="flex justify-start">
                <div className="bg-white shadow-md rounded-2xl px-4 py-3">
                  <div className="flex items-center gap-2">
                    <Bot className="w-4 h-4 text-blue-500" />
                    <span className="text-xs font-medium text-blue-500">JARVIS</span>
                  </div>
                  <div className="flex items-center gap-1 mt-2">
                    <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                    <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                    <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                  </div>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Quick Queries */}
          <div className="px-4 py-2 border-t bg-white">
            <div className="flex flex-wrap gap-2">
              {quickQueries.map((q) => (
                <button
                  key={q.label}
                  onClick={() => handleQuickQuery(q.query)}
                  className="px-3 py-1.5 text-xs font-medium bg-gray-100 hover:bg-gray-200 rounded-full transition-colors"
                >
                  {q.label}
                </button>
              ))}
            </div>
          </div>

          {/* Input */}
          <div className="p-4 bg-white border-t">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setIsListening(!isListening)}
                className={clsx(
                  'p-3 rounded-full transition-colors',
                  isListening
                    ? 'bg-red-500 text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                )}
              >
                {isListening ? <MicOff className="w-5 h-5" /> : <Mic className="w-5 h-5" />}
              </button>
              <input
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSend()}
                placeholder="Ask JARVIS anything..."
                className="flex-1 px-4 py-3 bg-gray-100 rounded-full focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <button
                onClick={handleSend}
                disabled={!inputValue.trim() || isLoading}
                className="p-3 bg-blue-500 text-white rounded-full hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <Send className="w-5 h-5" />
              </button>
            </div>
          </div>
        </div>

        {/* Insights Panel */}
        <div className="w-80 border-l bg-white overflow-y-auto hidden laptop:block">
          <div className="p-4 space-y-4">
            <h2 className="font-semibold text-gray-900 flex items-center gap-2">
              <Zap className="w-4 h-4 text-yellow-500" />
              Live Insights
            </h2>

            {insights && (
              <>
                {/* Revenue Card */}
                <div className="bg-gradient-to-br from-green-50 to-emerald-50 rounded-xl p-4">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm text-gray-600">Today's Revenue</span>
                    {insights.revenue_growth >= 0 ? (
                      <ArrowUpRight className="w-4 h-4 text-green-600" />
                    ) : (
                      <ArrowDownRight className="w-4 h-4 text-red-600" />
                    )}
                  </div>
                  <p className="text-2xl font-bold text-gray-900">
                    {formatCurrency(insights.revenue_today)}
                  </p>
                  <p
                    className={clsx(
                      'text-sm',
                      insights.revenue_growth >= 0 ? 'text-green-600' : 'text-red-600'
                    )}
                  >
                    {insights.revenue_growth >= 0 ? '+' : ''}
                    {insights.revenue_growth}% vs yesterday
                  </p>
                </div>

                {/* Stats Grid */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="bg-blue-50 rounded-lg p-3">
                    <ShoppingCart className="w-4 h-4 text-blue-600 mb-1" />
                    <p className="text-lg font-bold">{insights.orders_today}</p>
                    <p className="text-xs text-gray-600">Orders Today</p>
                  </div>
                  <div className="bg-orange-50 rounded-lg p-3">
                    <Clock className="w-4 h-4 text-orange-600 mb-1" />
                    <p className="text-lg font-bold">{insights.pending_orders}</p>
                    <p className="text-xs text-gray-600">Pending</p>
                  </div>
                  <div className="bg-red-50 rounded-lg p-3">
                    <Package className="w-4 h-4 text-red-600 mb-1" />
                    <p className="text-lg font-bold">{insights.low_stock_count}</p>
                    <p className="text-xs text-gray-600">Low Stock</p>
                  </div>
                  <div className="bg-purple-50 rounded-lg p-3">
                    <Users className="w-4 h-4 text-purple-600 mb-1" />
                    <p className="text-lg font-bold">{insights.staff_present}</p>
                    <p className="text-xs text-gray-600">Staff Present</p>
                  </div>
                </div>
              </>
            )}

            {/* Recommendations */}
            <div className="pt-4 border-t">
              <h3 className="font-semibold text-gray-900 flex items-center gap-2 mb-3">
                <Target className="w-4 h-4 text-bv-gold-500" />
                Recommendations
              </h3>
              <div className="space-y-3">
                {recommendations.map((rec, index) => (
                  <div
                    key={index}
                    className={clsx(
                      'p-3 rounded-lg border-l-4',
                      rec.priority === 'high'
                        ? 'bg-red-50 border-red-500'
                        : rec.priority === 'medium'
                        ? 'bg-yellow-50 border-yellow-500'
                        : 'bg-green-50 border-green-500'
                    )}
                  >
                    <p className="text-sm font-medium text-gray-900">{rec.title}</p>
                    <p className="text-xs text-gray-600 mt-1">{rec.description}</p>
                    <button className="text-xs text-blue-600 font-medium mt-2 flex items-center gap-1">
                      Take Action
                      <ChevronRight className="w-3 h-3" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default JarvisPage;
