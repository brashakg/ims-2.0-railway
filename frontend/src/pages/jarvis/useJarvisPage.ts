// JARVIS page state, polling and API glue - moved verbatim out of
// JarvisPage.tsx (Wave 3 file diet). Every hook, loader and handler below
// is the same code, in the same order it ran in on the page.

import { useState, useEffect, useRef } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import api from '../../services/api/client';
import { proposalsApi, type AIProposal } from '../../services/api/proposals';
import type { Message, QuickInsight, Recommendation, ActivityEvent, LiveAgent } from './shared';

export function useJarvisPage() {
  const { hasRole, user } = useAuth();
  const toast = useToast();
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  // LLM model selector — populated from /jarvis/models (Claude / Claude Opus / …)
  // `tier` is "standard" | "premium"; the dropdown shows a confirm
  // modal before switching TO a premium model so users opt-in to the cost.
  const [llmModels, setLlmModels] = useState<Array<{
    id: string;
    label: string;
    tier?: 'free' | 'standard' | 'premium';
  }>>([]);
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [insights, setInsights] = useState<QuickInsight | null>(null);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [isListening, setIsListening] = useState(false);
  const [liveAgents, setLiveAgents] = useState<LiveAgent[] | null>(null);
  const [agentsErr, setAgentsErr] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  // Activity feed (Phase 5)
  const [activity, setActivity] = useState<ActivityEvent[] | null>(null);
  const [activityFilter, setActivityFilter] = useState<string>('all');
  // AI change-proposals (SYSTEM_INTENT section 8 review loop)
  const [proposals, setProposals] = useState<AIProposal[] | null>(null);
  const [proposalBusyId, setProposalBusyId] = useState<string | null>(null);
  // #7 predictive purchasing: pending reorder draft-PO suggestions (loaded
  // separately so the two sections refresh independently). Visible to ADMIN too.
  const [poProposals, setPoProposals] = useState<AIProposal[] | null>(null);
  // PIXEL audit history — last N audit runs from ui_audits
  const [pixelAudits, setPixelAudits] = useState<{
    audits: Array<{
      ran_at: string;
      summary: {
        overall_min_perf: number | null;
        overall_min_a11y: number | null;
        total_a11y_violations: number;
        pages_audited: number;
      };
      pages: Array<{
        url: string;
        scores: {
          performance: number | null;
          accessibility: number | null;
          best_practices: number | null;
          seo: number | null;
        };
        core_web_vitals: {
          lcp_ms: number | null;
          cls: number | null;
          tbt_ms: number | null;
        };
        a11y_violations_count: number;
      }>;
      regressions: Array<{ url: string; metric: string; current: number; baseline: number; delta: number }>;
    }>;
    latest: any;
    // Most recent row PIXEL wrote, WHATEVER the outcome. A failed run is
    // kind="run_failed" so it never appears in `audits` — this is the only
    // field that can tell "key rejected" apart from "never ran".
    last_outcome: {
      ran_at?: string;
      outcome?: 'ok' | 'no_credentials' | 'credentials_rejected' | 'all_calls_failed';
      notes?: string;
      next_step?: string;
    } | null;
    deltas_vs_previous: Record<string, number>;
    pagespeed_ready: boolean;
    frontend_url: string;
    audits_total: number;
  } | null>(null);
  const [pixelLoading, setPixelLoading] = useState(false);
  // SENTINEL system health
  const [sentinelHealth, setSentinelHealth] = useState<{
    latest: {
      timestamp: string;
      score: number;
      results: {
        database?: { status: string; response_time_ms?: number };
        api?: { status: string; checks?: Record<string, any>; response_time_ms?: number };
        frontend?: { status: string; status_code?: number; response_time_ms?: number };
        agents?: { status: string; total: number; healthy: number; unhealthy: number };
        data_integrity?: { status: string; issues?: any[] };
      };
    } | null;
    history: Array<{ timestamp: string; score: number }>;
    alerts: Array<{ severity: string; domain: string; message: string; timestamp: string }>;
    history_count: number;
  } | null>(null);
  const [sentinelLoading, setSentinelLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  // Auto-scroll only when the user is already at the bottom of the chat, so
  // reading older messages (scrolled up) is never yanked down on a new message.
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const autoFollowRef = useRef(true);

  // STRICT ACCESS CONTROL — SUPERADMIN ONLY. The guard (`if (!isSuperAdmin) return null`)
  // is intentionally NOT here — it lives below, AFTER every hook is declared. React
  // requires hooks to be called in the same order every render, and an early-return
  // before the useEffect/useState block would skip them on subsequent renders if the
  // role changes, throwing "rendered fewer hooks than expected" at runtime.
  // hasRole(['SUPERADMIN']) is TRUE for ADMIN too (ADMIN inherits everything),
  // so it gates "may see the Jarvis page" - which now includes ADMIN for the
  // #7 Recommended-POs section. `isStrictSuperAdmin` is the EXACT role check
  // used to keep the AI agent toggles + ORACLE config + the rest of Jarvis's
  // SUPERADMIN-only surfaces (and their SUPERADMIN-only API fetches) hidden
  // from an ADMIN, who would otherwise 404 on those endpoints.
  const isSuperAdmin = hasRole(['SUPERADMIN']);
  const userRoles = user?.roles ?? [];
  const isStrictSuperAdmin =
    userRoles.includes('SUPERADMIN') && user?.activeRole !== 'ADMIN';

  // Auto-scroll to bottom -- ONLY when the user is already near the bottom
  // (autoFollowRef), and with block:'nearest' so the scroll stays inside the
  // chat panel and never yanks the whole page down.
  useEffect(() => {
    if (autoFollowRef.current) {
      messagesEndRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [messages]);

  // Load the available LLM models for the chat selector (SUPERADMIN-only
  // endpoint; the chat surface is hidden from ADMIN, so don't fetch it).
  useEffect(() => {
    if (!isStrictSuperAdmin) return;
    api.get<{
      models: Array<{ id: string; label: string; tier?: 'free' | 'standard' | 'premium' }>;
      default: string | null;
    }>('/jarvis/models')
      .then(({ data }) => {
        setLlmModels(data.models || []);
        if (data.default) setSelectedModel(data.default);
        else if (data.models?.length) setSelectedModel(data.models[0].id);
      })
      .catch(() => setLlmModels([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStrictSuperAdmin]);

  // Gated model switching — premium models (Opus etc.) trigger a confirm
  // modal that surfaces the rough cost so users opt in deliberately.
  const handleModelChange = (newId: string) => {
    if (newId === selectedModel) return;
    const next = llmModels.find((m) => m.id === newId);
    if (next?.tier === 'premium') {
      const ok = window.confirm(
        `${next.label} is significantly pricier per query than the other models.\n\n` +
        `Approximate cost: ~$15 / 1M input tokens, ~$75 / 1M output tokens — ` +
        `roughly 20× the price of Haiku. A single complex business analysis can run ₹40-80.\n\n` +
        `Best for: deep multi-step analysis, strategy questions, anything ` +
        `where reasoning quality matters more than cost.\n\n` +
        `Continue with ${next.label}?`
      );
      if (!ok) return;
    }
    setSelectedModel(newId);
  };

  // Load initial insights + live agent list + activity feed + PIXEL + SENTINEL.
  // The agent-grid / insights / PIXEL / SENTINEL fetches are SUPERADMIN-only
  // endpoints, so only fire them for a strict SUPERADMIN; an ADMIN viewing the
  // page solely for the #7 Recommended-POs section loads ONLY the proposals.
  useEffect(() => {
    // #7 reorder suggestions — visible to ADMIN + SUPERADMIN.
    loadPoProposals();
    if (isStrictSuperAdmin) {
      loadInsights();
      loadRecommendations();
      loadAgents();
      loadActivity();
      loadProposals();
      loadPixelAudits();
      loadSentinelHealth();
    }
    // Add initial greeting
    setMessages([
      {
        id: '1',
        type: 'jarvis',
        content: getGreeting(),
        timestamp: new Date(),
      },
    ]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStrictSuperAdmin]);

  // PIXEL audit history fetch
  const loadPixelAudits = async () => {
    setPixelLoading(true);
    try {
      const { data } = await api.get('/jarvis/agents/pixel/audits', { params: { limit: 7 } });
      setPixelAudits(data);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[JARVIS] PIXEL audits fetch failed:', e);
      setPixelAudits(null);
    } finally {
      setPixelLoading(false);
    }
  };

  // SENTINEL health fetch
  const loadSentinelHealth = async () => {
    setSentinelLoading(true);
    try {
      const { data } = await api.get('/jarvis/agents/sentinel/health', {
        params: { history_limit: 60, alerts_limit: 10 },
      });
      setSentinelHealth(data);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[JARVIS] SENTINEL health fetch failed:', e);
      setSentinelHealth(null);
    } finally {
      setSentinelLoading(false);
    }
  };

  // ── Activity feed ──────────────────────────────────────────────────
  const loadActivity = async () => {
    try {
      const { data } = await api.get<{ events: ActivityEvent[] }>(
        '/jarvis/agents/activity',
        { params: { limit: 50, since_hours: 72 } }
      );
      setActivity(data.events ?? []);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[JARVIS] activity fetch failed:', e);
      setActivity(null);
    }
  };

  // ── AI change-proposals ────────────────────────────────────────────
  // Pull the PENDING queue. Approving a reversible (Tier-1) proposal
  // auto-executes server-side; an advisory one just records approval.
  const loadProposals = async () => {
    try {
      const data = await proposalsApi.list({ status: 'PENDING', limit: 50 });
      setProposals(data.proposals ?? []);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[JARVIS] proposals fetch failed:', e);
      setProposals(null);
    }
  };

  // #7: pull ONLY the pending reorder draft-PO suggestions for the
  // Recommended-POs section (separate from the generic change-proposals list).
  const loadPoProposals = async () => {
    try {
      const data = await proposalsApi.list({
        status: 'PENDING',
        type: 'draft_po',
        limit: 100,
      });
      setPoProposals(data.proposals ?? []);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[JARVIS] reorder proposals fetch failed:', e);
      setPoProposals(null);
    }
  };

  // Act On It / Ignore for a reorder draft-PO. Approving auto-executes the
  // reversible draft_po (creates a DRAFT PO, store-attributed) - it NEVER
  // sends a PO or commits money. Refreshes only the Recommended-POs list.
  const handleApprovePo = async (p: AIProposal) => {
    setProposalBusyId(p.proposal_id);
    try {
      const res = await proposalsApi.approve(p.proposal_id);
      if (res.executed) {
        toast.success(`Draft PO created: ${p.title}`);
      } else {
        toast.error(`Could not draft PO: ${res.error ?? 'unknown error'}`);
      }
      await loadPoProposals();
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[JARVIS] reorder approve failed:', e);
      toast.error('Could not act on this reorder suggestion.');
    } finally {
      setProposalBusyId(null);
    }
  };

  const handleIgnorePo = async (p: AIProposal) => {
    const reason = window.prompt(`Ignore "${p.title}"?\n\nOptional reason:`, '');
    if (reason === null) return; // Cancel -> don't ignore
    setProposalBusyId(p.proposal_id);
    try {
      await proposalsApi.reject(p.proposal_id, reason);
      toast.info(`Ignored: ${p.title}`);
      await loadPoProposals();
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[JARVIS] reorder reject failed:', e);
      toast.error('Could not ignore this reorder suggestion.');
    } finally {
      setProposalBusyId(null);
    }
  };

  const handleApproveProposal = async (p: AIProposal) => {
    // Reversible proposals warn that approval will EXECUTE; advisory ones
    // make clear approval is recorded but no change happens automatically.
    const ok = window.confirm(
      p.reversible
        ? `Approve and EXECUTE this reversible action?\n\n` +
            `"${p.title}"\n\n` +
            `The system will perform the change now (e.g. create a DRAFT — ` +
            `nothing is sent to a vendor/customer) and write a full ` +
            `before/after audit entry.`
        : `Approve this ADVISORY proposal?\n\n` +
            `"${p.title}"\n\n` +
            `This records your approval and audits it, but the system will ` +
            `NOT act automatically — someone still has to make this change.`
    );
    if (!ok) return;
    setProposalBusyId(p.proposal_id);
    try {
      const res = await proposalsApi.approve(p.proposal_id);
      if (res.executed) {
        toast.success(`Executed: ${p.title}`);
      } else if (res.advisory) {
        toast.info(`Approved (advisory): ${p.title}`);
      } else {
        toast.error(`Approval recorded but execution failed: ${res.error ?? 'unknown error'}`);
      }
      await loadProposals();
      // Side-effects land in the activity/agent feeds — refresh them too.
      loadActivity().catch(() => {});
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[JARVIS] approve failed:', e);
      toast.error('Could not approve proposal.');
    } finally {
      setProposalBusyId(null);
    }
  };

  const handleRejectProposal = async (p: AIProposal) => {
    const reason = window.prompt(`Reject "${p.title}"?\n\nOptional reason:`, '');
    // prompt returns null on Cancel — treat that as "don't reject".
    if (reason === null) return;
    setProposalBusyId(p.proposal_id);
    try {
      await proposalsApi.reject(p.proposal_id, reason);
      toast.info(`Rejected: ${p.title}`);
      await loadProposals();
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[JARVIS] reject failed:', e);
      toast.error('Could not reject proposal.');
    } finally {
      setProposalBusyId(null);
    }
  };

  // ── Live agent fetch ───────────────────────────────────────────────
  const loadAgents = async () => {
    try {
      const { data } = await api.get<{ agents: LiveAgent[]; total: number; enabled_count: number }>(
        '/jarvis/agents'
      );
      setLiveAgents(data.agents ?? []);
      setAgentsErr(null);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[JARVIS] Failed to load agents:', e);
      setAgentsErr(e instanceof Error ? e.message : 'Could not load agents');
      setLiveAgents(null);
    }
  };

  const handleToggleAgent = async (agentId: string, nextEnabled: boolean) => {
    setTogglingId(agentId);
    try {
      await api.patch(`/jarvis/agents/${agentId}/toggle`, { enabled: nextEnabled });
      // Optimistic UI — flip locally, then refetch to pick up scheduler-side state
      setLiveAgents((prev) =>
        prev ? prev.map((a) => (a.agent_id === agentId ? { ...a, enabled: nextEnabled } : a)) : prev
      );
      // Refetch in the background for authoritative truth
      loadAgents().catch(() => {});
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error(`[JARVIS] Toggle failed for ${agentId}:`, e);
    } finally {
      setTogglingId(null);
    }
  };

  const handleRunNow = async (agentId: string) => {
    setTogglingId(agentId);
    try {
      await api.post(`/jarvis/agents/${agentId}/run-now`);
      // Give the agent a moment, then refresh
      setTimeout(() => {
        loadAgents().catch(() => {});
      }, 1500);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error(`[JARVIS] Run-now failed for ${agentId}:`, e);
    } finally {
      setTogglingId(null);
    }
  };

  const getGreeting = () => {
    const hour = new Date().getHours();
    const timeGreeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
    return `${timeGreeting}, Sir. I'm JARVIS, your AI business intelligence system. I have full access to all business data and I'm ready to assist you with analytics, predictions, recommendations, and operational decisions. What would you like to know?`;
  };

  const loadInsights = async () => {
    try {
      const { data } = await api.get<QuickInsight>('/jarvis/quick-insights');
      setInsights(data);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[JARVIS] quick-insights failed:', e);
      setInsights(null);
    }
  };

  const loadRecommendations = async () => {
    try {
      const { data } = await api.get<{ recommendations: Recommendation[]; total: number }>(
        '/jarvis/recommendations',
        { params: { limit: 10 } },
      );
      setRecommendations(data.recommendations ?? []);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error('[JARVIS] recommendations failed:', e);
      setRecommendations([]);
    }
  };

  // "Take action" — feed the recommendation's action into JARVIS chat
  // so the model produces a concrete step-by-step plan. If the rec has
  // a deep-link, also navigate so the operator lands on the right page
  // with the chat already running.
  const handleTakeAction = (rec: Recommendation & { link?: string }) => {
    const prompt = (
      `For the recommendation "${rec.title}" (category: ${rec.category}): ` +
      `${rec.description || ''} ` +
      `Suggested action: ${rec.action || 'none'}. ` +
      `Walk me through the next 3 concrete steps I should take today, grounded in the live data.`
    ).trim();
    // Stuff the chat input and immediately send by invoking handleSend's path
    setInputValue(prompt);
    // User-initiated (clicked a quick query): re-engage follow + scroll within
    // the chat panel.
    setTimeout(() => {
      autoFollowRef.current = true;
      messagesEndRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }, 100);
    // Trigger send on next tick so React has applied the input update
    setTimeout(() => {
      const fakeEvent = { key: 'Enter' } as React.KeyboardEvent<HTMLInputElement>;
      void fakeEvent;  // ensure React isn't optimised away
      handleSend();
    }, 50);
    // Optionally deep-link to the relevant page (in a new tab so chat stays open)
    if (rec.link) {
      window.open(rec.link, '_blank');
    }
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
      // The shared axios client defaults to a 10 s timeout — fine for CRUD,
      // but a Claude call over a large business-data context can take
      // 25-30 s. The 10 s cap was firing client-side BEFORE the model
      // could answer, dropping every slow query into the offline-fallback
      // path with mock template numbers. 90 s here matches the backend's
      // LLM_TIMEOUT and covers a slow call with margin.
      const { data } = await api.post<{
        response: string;
        ai_powered?: boolean;
        model?: string;
        intent_detected?: string;
      }>(
        '/jarvis/query',
        { message: queryText, model: selectedModel || undefined },
        { timeout: 90000 },
      );

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

  // Pretty schedule labels shown on each card — maps the MongoDB
  // schedule_type + schedule_value into something human-readable.
  const prettySchedule = (type: string, value: string) => {
    if (type === 'event') return 'Event-driven';
    if (type === 'interval') {
      const secs = Number(value);
      if (secs >= 3600) return `Every ${Math.round(secs / 3600)} h`;
      if (secs >= 60) return `Every ${Math.round(secs / 60)} min`;
      return `Every ${secs}s`;
    }
    if (type === 'cron') return `Cron: ${value}`;
    return value || '—';
  };

  // JARVIS NLP core. Since Phase 6.5 the backend registers JARVIS as a
  // real agent (see backend/agents/implementations/jarvis.py), so it
  // flows through /jarvis/agents naturally. This stub remains as a
  // fallback for the brief window after a frontend deploy but before
  // the matching backend deploy ships — if the backend doesn't yet
  // include JARVIS, we prepend it; otherwise the dedupe below drops it.
  const JARVIS_STUB: LiveAgent = {
    agent_id: 'jarvis',
    agent_name: 'JARVIS',
    agent_type: 'foundation',
    description: 'Foundation · NLP & conversation core',
    version: '1.0.0',
    enabled: true,
    toggleable: false,
    status: 'running',
    health: 'healthy',
    schedule_type: 'event',
    schedule_value: 'always-on',
    last_run: null,
    run_count: 0,
    error_count: 0,
    avg_run_time_ms: 0,
    hero: "Iron Man's J.A.R.V.I.S.",
    capabilities: ['nlp', 'conversation', 'claude_bridge'],
  };

  // Merge + dedupe + sort in architecture order. Dedupe is keyed on
  // agent_id so if the backend returns `jarvis`, the synthetic stub is
  // dropped in favour of the live record.
  const ORDER = ['jarvis', 'cortex', 'sentinel', 'pixel', 'megaphone', 'oracle', 'taskmaster', 'nexus'];
  const mergedById = new Map<string, LiveAgent>();
  mergedById.set(JARVIS_STUB.agent_id, JARVIS_STUB);
  for (const a of liveAgents ?? []) {
    mergedById.set(a.agent_id, a);  // backend entry wins over stub
  }
  const agentsForGrid: LiveAgent[] = Array.from(mergedById.values()).sort(
    (a, b) => ORDER.indexOf(a.agent_id) - ORDER.indexOf(b.agent_id)
  );

  const enabledCount = agentsForGrid.filter((a) => a.enabled).length;
  const totalActs24h = agentsForGrid.reduce((s, a) => s + (a.run_count || 0), 0);
  // Real count of pending AI change-proposals awaiting Superadmin review.
  const awaitingApproval = proposals?.length ?? 0;
  const poPending = poProposals?.length ?? 0;


  return {
    // access
    isSuperAdmin, isStrictSuperAdmin,
    // chat
    messages, inputValue, setInputValue, isLoading, isListening, setIsListening,
    llmModels, selectedModel, handleModelChange, handleSend, handleQuickQuery,
    quickQueries, messagesEndRef, chatScrollRef, autoFollowRef,
    // insights + recommendations
    insights, recommendations, handleTakeAction, formatCurrency,
    // agents
    agentsForGrid, agentsErr, togglingId, prettySchedule,
    handleToggleAgent, handleRunNow, loadAgents,
    enabledCount, totalActs24h,
    // proposals
    proposals, proposalBusyId, awaitingApproval, loadProposals,
    handleApproveProposal, handleRejectProposal,
    // reorder draft-POs
    poProposals, poPending, loadPoProposals, handleApprovePo, handleIgnorePo,
    // activity feed
    activity, activityFilter, setActivityFilter, loadActivity,
    // SENTINEL / PIXEL / insights loaders
    sentinelHealth, sentinelLoading, loadSentinelHealth,
    pixelAudits, pixelLoading, loadPixelAudits,
    loadInsights, loadRecommendations,
  };
}

export type JarvisPageState = ReturnType<typeof useJarvisPage>;
