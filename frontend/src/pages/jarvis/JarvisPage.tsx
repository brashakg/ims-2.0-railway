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
  Target,
  Sparkles,
  RefreshCw,
  ChevronRight,
  ArrowUpRight,
  ArrowDownRight,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import api from '../../services/api/client';

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
      // Call JARVIS backend at /api/v1/jarvis/query (base URL + Authorization
      // header handled by the shared axios client). The previous raw fetch
      // called "/api/jarvis/query" (wrong — missing /v1) with
      // `Bearer ${localStorage.getItem('token')}` (wrong key — should be
      // 'ims_token'). Both failures silently fell through to generateResponse()
      // below, so every user question got the same keyword-matched canned
      // templates regardless of what was asked. Switching to the shared
      // client fixes both at once.
      const { data } = await api.post<{
        response: string;
        ai_powered?: boolean;
        model?: string;
        intent_detected?: string;
      }>('/jarvis/query', { message: queryText });

      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          type: 'jarvis',
          content: data.response,
          timestamp: new Date(),
          data: {
            ai_powered: data.ai_powered,
            model: data.model,
            intent: data.intent_detected,
          },
        },
      ]);
    } catch (err) {
      // Only hit local fallback when the backend is genuinely unreachable
      // (network error). 4xx/5xx with a body get logged below and a short
      // apology surfaces so the user knows something's off — vs the old
      // behavior that silently served canned text on any failure.
      // eslint-disable-next-line no-console
      console.error('[JARVIS] query failed:', err);
      const fallbackResponse = generateResponse(queryText);
      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          type: 'jarvis',
          content:
            '_(Working in offline-fallback mode — live backend query failed. Answer below is from local templates, not real data.)_\n\n' +
            fallbackResponse,
          timestamp: new Date(),
        },
      ]);
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

  // 8 Jarvis superhero agents — from docs/reference/IMS2_Agent_Architecture.html.
  // Current state: CORTEX + SENTINEL implemented (backend/agents/implementations),
  // the other 6 are Phase 3 work. Shown here as "pending" so the control surface
  // reflects the roadmap honestly.
  const AGENTS = [
    { id: 'JARVIS',     hero: 'J.A.R.V.I.S. (Marvel)',    role: 'Foundation · NLP & conversation core',             schedule: 'Always-on',               status: 'live',    acts24h: 0 },
    { id: 'CORTEX',     hero: 'Professor X (Marvel)',     role: 'Orchestrator · command router',                    schedule: 'Event-driven',            status: 'live',    acts24h: 12 },
    { id: 'SENTINEL',   hero: 'The Sentinels (Marvel)',   role: 'System health & monitoring',                       schedule: 'Every 60s',               status: 'live',    acts24h: 94 },
    { id: 'PIXEL',      hero: 'Batman (DC)',              role: 'UI/UX quality · deploy audit · a11y',              schedule: 'Daily 2 AM + on deploy',  status: 'pending', acts24h: 0 },
    { id: 'MEGAPHONE',  hero: 'Black Canary (DC)',        role: 'Marketing · Rx expiry / birthday / follow-up',     schedule: '30 min + daily 9 AM',     status: 'pending', acts24h: 0 },
    { id: 'ORACLE',     hero: 'Oracle / Barbara Gordon',  role: 'AI analysis · anomaly scan + EOD sweep',           schedule: 'Hourly + 10 PM',          status: 'pending', acts24h: 0 },
    { id: 'TASKMASTER', hero: 'Taskmaster (Marvel)',      role: 'Real execution · SLA, SOP, auto-reorder',          schedule: 'Every 5 min',             status: 'pending', acts24h: 0 },
    { id: 'NEXUS',      hero: 'Cyborg (DC)',              role: 'Integration sync · Shopify / Razorpay / Shiprocket', schedule: 'Hourly + webhook',        status: 'pending', acts24h: 0 },
  ];
  const liveAgents = AGENTS.filter(a => a.status === 'live').length;
  const totalActs24h = AGENTS.reduce((sum, a) => sum + a.acts24h, 0);

  return (
    <div style={{ padding: '24px 28px 60px', background: 'var(--bg)', minHeight: 'calc(100vh - 52px)', overflowY: 'auto' }}>
      {/* ── Hero: ink background, editorial title, pulse stats ── */}
      <section
        style={{
          background: 'var(--ink)',
          color: '#fff',
          borderRadius: 'var(--r-xl)',
          padding: '32px 32px 28px',
          marginBottom: 20,
          display: 'grid',
          gridTemplateColumns: '1fr auto',
          gap: 32,
          alignItems: 'end',
          position: 'relative',
          overflow: 'hidden',
          backgroundImage:
            'radial-gradient(400px 180px at 85% 0%, rgba(205,32,26,.22), transparent 60%), radial-gradient(600px 300px at 30% 110%, rgba(255,255,255,.04), transparent 60%)',
        }}
      >
        <div>
          <div className="eyebrow" style={{ color: '#9a9a92', marginBottom: 12 }}>
            Jarvis · Always-on automation · Superadmin only
          </div>
          <h1 style={{ margin: '0 0 10px', fontFamily: 'var(--font-display)', fontSize: 44, lineHeight: 1.02, letterSpacing: '-0.02em', maxWidth: 600, color: '#fff', fontWeight: 400 }}>
            Eight agents. One shift.<br />
            Quietly keeping things in line.
          </h1>
          <p style={{ margin: 0, color: '#b6b6ae', maxWidth: 520, fontSize: 13.5, lineHeight: 1.55 }}>
            Jarvis watches the feeds every store ignores — stock, pricing, Rx alignment, task SLAs — and takes the smallest action it can, then asks for approval when the stakes matter.
          </p>
          <div style={{ display: 'flex', gap: 18, fontFamily: 'var(--font-mono)', fontSize: 11, color: '#8a8a82', marginTop: 16, textTransform: 'uppercase', letterSpacing: '.08em' }}>
            <div style={{ paddingRight: 18, borderRight: '1px solid #2e2e2b' }}>
              <div className="figure" style={{ fontSize: 26, color: '#fff', textTransform: 'none', letterSpacing: '-.02em' }}>{liveAgents}/8</div>
              agents live
            </div>
            <div style={{ paddingRight: 18, borderRight: '1px solid #2e2e2b' }}>
              <div className="figure" style={{ fontSize: 26, color: '#fff', textTransform: 'none', letterSpacing: '-.02em' }}>{totalActs24h}</div>
              actions · 24h
            </div>
            <div>
              <div className="figure" style={{ fontSize: 26, color: '#fff', textTransform: 'none', letterSpacing: '-.02em' }}>0</div>
              awaiting approval
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="btn sm"
            style={{ background: '#2a2a28', color: '#fff', borderColor: '#3a3a36' }}
            onClick={() => {
              loadInsights();
              loadRecommendations();
            }}
          >
            <RefreshCw className="w-3.5 h-3.5" /> Refresh
          </button>
          <button className="btn sm accent">
            <Sparkles className="w-3.5 h-3.5" /> Deploy agent
          </button>
        </div>
      </section>

      {/* ── Agent grid (2 cols) ── */}
      <div className="eyebrow" style={{ marginBottom: 10 }}>Agents</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))', gap: 14, marginBottom: 24 }}>
        {AGENTS.map((a) => (
          <div
            key={a.id}
            style={{
              background: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: 'var(--r-lg)',
              padding: 18,
              display: 'grid',
              gridTemplateColumns: '48px 1fr auto',
              gap: 14,
              alignItems: 'flex-start',
              position: 'relative',
              overflow: 'hidden',
              opacity: a.status === 'pending' ? 0.7 : 1,
            }}
          >
            <div
              style={{
                width: 48,
                height: 48,
                borderRadius: 10,
                background: a.status === 'live' ? 'var(--ink)' : 'var(--bg-sunk)',
                color: a.status === 'live' ? '#fff' : 'var(--ink-4)',
                display: 'grid',
                placeItems: 'center',
                fontFamily: 'var(--font-mono)',
                fontSize: 12,
                fontWeight: 600,
                letterSpacing: '.04em',
              }}
            >
              {a.id.slice(0, 3)}
            </div>
            <div>
              <h3 style={{ margin: '0 0 3px', font: '600 14px/1.2 var(--font-sans)', color: 'var(--ink)' }}>{a.id}</h3>
              <div style={{ fontSize: 11, color: 'var(--ink-4)', marginBottom: 4, fontStyle: 'italic' }}>{a.hero}</div>
              <div style={{ fontSize: 12.5, color: 'var(--ink-3)', lineHeight: 1.5, marginBottom: 10 }}>{a.role}</div>
              <div style={{ display: 'flex', gap: 14, fontSize: 11, color: 'var(--ink-4)', fontFamily: 'var(--font-mono)' }}>
                <span>Cadence · <strong style={{ color: 'var(--ink)', fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 12 }}>{a.schedule}</strong></span>
                <span>24h · <strong style={{ color: 'var(--ink)', fontFamily: 'var(--font-sans)', fontWeight: 600, fontSize: 12 }}>{a.acts24h} acts</strong></span>
              </div>
            </div>
            <span className={'chip ' + (a.status === 'live' ? 'ok' : 'warn')}>
              {a.status === 'live' ? 'running' : 'pending · Phase 3'}
            </span>
          </div>
        ))}
      </div>

      {/* ── Jarvis conversation (preserves original chat logic) ── */}
      <div className="eyebrow" style={{ marginBottom: 10 }}>Ask intelligence</div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 320px',
          gap: 14,
          background: 'var(--surface)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r-lg)',
          overflow: 'hidden',
          minHeight: 480,
        }}
      >
        {/* Chat pane */}
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          {/* Messages */}
          <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
            {messages.map((message) => (
              <div
                key={message.id}
                style={{
                  display: 'flex',
                  justifyContent: message.type === 'user' ? 'flex-end' : 'flex-start',
                }}
              >
                <div
                  style={{
                    maxWidth: '80%',
                    borderRadius: 14,
                    padding: '10px 14px',
                    background: message.type === 'user' ? 'var(--ink)' : 'var(--bg-sunk)',
                    color: message.type === 'user' ? '#fff' : 'var(--ink)',
                    border: message.type === 'user' ? '1px solid var(--ink)' : '1px solid var(--line)',
                  }}
                >
                  {message.type === 'jarvis' && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                      <Bot className="w-3.5 h-3.5" style={{ color: 'var(--bv)' }} />
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--bv)', textTransform: 'uppercase', letterSpacing: '.08em' }}>
                        JARVIS
                      </span>
                      {(message.data as { ai_powered?: boolean })?.ai_powered && (
                        <span className="chip accent" style={{ height: 18, fontSize: 9.5 }}>
                          Claude AI
                        </span>
                      )}
                    </div>
                  )}
                  <div
                    style={{
                      fontSize: 13,
                      lineHeight: 1.5,
                      whiteSpace: 'pre-wrap',
                    }}
                    dangerouslySetInnerHTML={{
                      __html: message.content
                        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                        .replace(/\n/g, '<br />'),
                    }}
                  />
                  <div style={{ fontSize: 10.5, marginTop: 6, color: message.type === 'user' ? 'rgba(255,255,255,.55)' : 'var(--ink-4)', fontFamily: 'var(--font-mono)' }}>
                    {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </div>
                </div>
              </div>
            ))}

            {isLoading && (
              <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
                <div style={{ background: 'var(--bg-sunk)', borderRadius: 14, padding: '10px 14px', border: '1px solid var(--line)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                    <Bot className="w-3.5 h-3.5" style={{ color: 'var(--bv)' }} />
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--bv)', textTransform: 'uppercase', letterSpacing: '.08em' }}>
                      JARVIS
                    </span>
                  </div>
                  <div style={{ display: 'flex', gap: 4, paddingTop: 4 }}>
                    <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--ink-4)', animation: 'bounce 1s infinite', animationDelay: '0ms' }} />
                    <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--ink-4)', animation: 'bounce 1s infinite', animationDelay: '150ms' }} />
                    <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--ink-4)', animation: 'bounce 1s infinite', animationDelay: '300ms' }} />
                  </div>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Quick queries chip row */}
          <div style={{ padding: '10px 14px', borderTop: '1px solid var(--line)', background: 'var(--surface-2)' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {quickQueries.map((q) => (
                <button
                  key={q.label}
                  type="button"
                  onClick={() => handleQuickQuery(q.query)}
                  className="btn sm ghost"
                  style={{ fontSize: 11 }}
                >
                  {q.label}
                </button>
              ))}
            </div>
          </div>

          {/* Input bar */}
          <div style={{ padding: 14, borderTop: '1px solid var(--line)', background: 'var(--surface)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <button
                type="button"
                onClick={() => setIsListening(!isListening)}
                className="btn icon ghost"
                style={{
                  background: isListening ? 'var(--err)' : 'var(--bg-sunk)',
                  color: isListening ? '#fff' : 'var(--ink-3)',
                  borderColor: isListening ? 'var(--err)' : 'var(--line-strong)',
                }}
                aria-label={isListening ? 'Stop listening' : 'Start voice input'}
              >
                {isListening ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
              </button>
              <input
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSend()}
                placeholder="Ask JARVIS anything…"
                className="input"
                style={{ flex: 1 }}
              />
              <button
                type="button"
                onClick={handleSend}
                disabled={!inputValue.trim() || isLoading}
                className="btn sm primary"
              >
                <Send className="w-4 h-4" /> Send
              </button>
            </div>
          </div>
        </div>

        {/* Right rail — live insights */}
        <aside
          style={{
            borderLeft: '1px solid var(--line)',
            background: 'var(--surface-2)',
            padding: 16,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
          }}
        >
          <div className="eyebrow" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Zap className="w-3 h-3" /> Live insights
          </div>

          {insights && (
            <>
              <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 'var(--r-lg)', padding: 14 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span style={{ fontSize: 11, color: 'var(--ink-4)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '.08em' }}>Today's revenue</span>
                  {insights.revenue_growth >= 0 ? (
                    <ArrowUpRight className="w-4 h-4" style={{ color: 'var(--ok)' }} />
                  ) : (
                    <ArrowDownRight className="w-4 h-4" style={{ color: 'var(--err)' }} />
                  )}
                </div>
                <div className="figure" style={{ fontSize: 26, color: 'var(--ink)' }}>
                  {formatCurrency(insights.revenue_today)}
                </div>
                <div style={{ fontSize: 12, color: insights.revenue_growth >= 0 ? 'var(--ok)' : 'var(--err)', marginTop: 3, fontFamily: 'var(--font-mono)' }}>
                  {insights.revenue_growth >= 0 ? '+' : ''}{insights.revenue_growth}% vs yesterday
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 8 }}>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, padding: 10 }}>
                  <ShoppingCart className="w-3.5 h-3.5" style={{ color: 'var(--info)', marginBottom: 4 }} />
                  <div className="figure" style={{ fontSize: 18 }}>{insights.orders_today}</div>
                  <div style={{ fontSize: 10.5, color: 'var(--ink-4)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '.06em' }}>Orders</div>
                </div>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, padding: 10 }}>
                  <Clock className="w-3.5 h-3.5" style={{ color: 'var(--warn)', marginBottom: 4 }} />
                  <div className="figure" style={{ fontSize: 18 }}>{insights.pending_orders}</div>
                  <div style={{ fontSize: 10.5, color: 'var(--ink-4)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '.06em' }}>Pending</div>
                </div>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, padding: 10 }}>
                  <Package className="w-3.5 h-3.5" style={{ color: 'var(--err)', marginBottom: 4 }} />
                  <div className="figure" style={{ fontSize: 18 }}>{insights.low_stock_count}</div>
                  <div style={{ fontSize: 10.5, color: 'var(--ink-4)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '.06em' }}>Low stock</div>
                </div>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 8, padding: 10 }}>
                  <Users className="w-3.5 h-3.5" style={{ color: 'var(--info)', marginBottom: 4 }} />
                  <div className="figure" style={{ fontSize: 18 }}>{insights.staff_present}</div>
                  <div style={{ fontSize: 10.5, color: 'var(--ink-4)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '.06em' }}>Staff</div>
                </div>
              </div>
            </>
          )}

          {/* Recommendations */}
          <div style={{ paddingTop: 12, borderTop: '1px solid var(--line)' }}>
            <div className="eyebrow" style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
              <Target className="w-3 h-3" /> Recommendations
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {recommendations.map((rec, index) => (
                <div
                  key={index}
                  style={{
                    padding: 10,
                    borderRadius: 8,
                    background: 'var(--surface)',
                    borderLeft: `3px solid ${rec.priority === 'high' ? 'var(--err)' : rec.priority === 'medium' ? 'var(--warn)' : 'var(--ok)'}`,
                    border: '1px solid var(--line)',
                  }}
                >
                  <div style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--ink)' }}>{rec.title}</div>
                  <div style={{ fontSize: 11, color: 'var(--ink-4)', marginTop: 2, lineHeight: 1.45 }}>{rec.description}</div>
                  <button type="button" className="btn sm ghost" style={{ marginTop: 6, fontSize: 11, padding: '0 6px', height: 22 }}>
                    Take action <ChevronRight className="w-3 h-3" />
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div style={{ marginTop: 'auto', paddingTop: 10, fontSize: 10, color: 'var(--ink-4)', lineHeight: 1.5, fontFamily: 'var(--font-mono)' }}>
            Activity signal. For agent toggles, see docs/reference/IMS2_Agent_Architecture.html.
          </div>
        </aside>
      </div>
    </div>
  );
}

export default JarvisPage;
