// ============================================================================
// IMS 2.0 - WhatsApp Inbox (CRM-14)
// Inbound customer message threads from the Meta Business API.
// Read-only v1: lists conversations + messages; no reply composition.
// Role gate: SUPERADMIN / ADMIN / STORE_MANAGER (mirrors backend).
// ============================================================================

import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import api from '../../services/api/client';

// ── Types ──────────────────────────────────────────────────────────────────

interface WaMessage {
  wa_message_id?: string;
  from_phone?: string;
  sender_name?: string;
  type?: string;
  text?: string;
  button_payload?: string;
  received_at?: string;
  direction?: 'inbound' | 'outbound';
}

interface WaConversation {
  phone: string;
  phone_e164?: string;
  customer_id?: string;
  customer_name?: string;
  last_message_at?: string;
  needs_human?: boolean;
  messages?: WaMessage[];
}

interface ConversationsResponse {
  conversations: WaConversation[];
  total: number;
  limit: number;
  offset: number;
}

// ── API call ───────────────────────────────────────────────────────────────

async function fetchConversations(
  needsHuman?: boolean,
  offset = 0,
  limit = 50
): Promise<ConversationsResponse> {
  const params: Record<string, string | number | boolean> = { limit, offset };
  if (needsHuman !== undefined) params['needs_human'] = needsHuman;
  const resp = await api.get('/webhooks/whatsapp/conversations', { params });
  return resp.data as ConversationsResponse;
}

// ── Helpers ────────────────────────────────────────────────────────────────

function formatTs(ts?: string): string {
  if (!ts) return '';
  try {
    return new Date(ts).toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return ts;
  }
}

function intentBadge(text: string): string {
  const t = text.toLowerCase();
  if (/\b(book|eyetest|appointment)\b/.test(t)) return 'book';
  if (/\b(reorder|lens|contact)\b/.test(t)) return 'reorder';
  if (/\b(agent|help|human)\b/.test(t)) return 'agent';
  if (/\b(stop|optout|unsubscribe)\b/.test(t)) return 'opt-out';
  return '';
}

function BadgePill({ label }: { label: string }) {
  const map: Record<string, string> = {
    book: 'bg-blue-100 text-blue-800',
    reorder: 'bg-green-100 text-green-700',
    agent: 'bg-orange-100 text-orange-800',
    'opt-out': 'bg-red-100 text-red-700',
  };
  if (!label) return null;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${map[label] ?? 'bg-gray-100 text-gray-700'}`}>
      {label}
    </span>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export function WhatsAppInboxPage() {
  const { user } = useAuth();
  const toast = useToast();

  const [conversations, setConversations] = useState<WaConversation[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<WaConversation | null>(null);
  const [loading, setLoading] = useState(false);
  const [filterHuman, setFilterHuman] = useState<boolean | undefined>(undefined);
  const [offset, setOffset] = useState(0);
  const LIMIT = 50;

  const allowed = ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'];
  const activeRole = (user as { activeRole?: string })?.activeRole ?? '';
  const hasAccess = allowed.includes(activeRole);

  const load = useCallback(async () => {
    if (!hasAccess) return;
    setLoading(true);
    try {
      const data = await fetchConversations(filterHuman, offset, LIMIT);
      setConversations(data.conversations ?? []);
      setTotal(data.total ?? 0);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to load inbox';
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [filterHuman, offset, hasAccess]);

  useEffect(() => {
    load();
  }, [load]);

  if (!hasAccess) {
    return (
      <div className="p-8 text-center text-gray-500">
        You do not have permission to view the WhatsApp inbox.
      </div>
    );
  }

  const lastMsg = (conv: WaConversation) => {
    const msgs = conv.messages ?? [];
    return msgs[msgs.length - 1] ?? null;
  };

  return (
    <div className="flex h-full min-h-0 bg-white">
      {/* Left panel: conversation list */}
      <aside className="w-80 flex-shrink-0 border-r border-gray-200 flex flex-col">
        {/* Header */}
        <div className="p-4 border-b border-gray-200">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-base font-semibold text-gray-900">WhatsApp Inbox</h2>
            <button
              onClick={load}
              disabled={loading}
              className="text-xs text-blue-600 hover:text-blue-800 disabled:opacity-40"
            >
              {loading ? 'Loading...' : 'Refresh'}
            </button>
          </div>
          {/* Filter row */}
          <div className="flex gap-2">
            <button
              onClick={() => { setFilterHuman(undefined); setOffset(0); }}
              className={`text-xs px-2 py-1 rounded border ${filterHuman === undefined ? 'bg-blue-600 text-white border-blue-600' : 'border-gray-300 text-gray-600 hover:bg-gray-50'}`}
            >
              All
            </button>
            <button
              onClick={() => { setFilterHuman(true); setOffset(0); }}
              className={`text-xs px-2 py-1 rounded border ${filterHuman === true ? 'bg-orange-500 text-white border-orange-500' : 'border-gray-300 text-gray-600 hover:bg-gray-50'}`}
            >
              Needs human
            </button>
          </div>
          <p className="text-xs text-gray-500 mt-1">{total} conversation{total !== 1 ? 's' : ''}</p>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto divide-y divide-gray-100">
          {conversations.length === 0 && !loading && (
            <p className="p-4 text-sm text-gray-400">No conversations yet.</p>
          )}
          {conversations.map((conv) => {
            const last = lastMsg(conv);
            const badge = last ? intentBadge(last.text ?? '') : '';
            const isSelected = selected?.phone === conv.phone;
            return (
              <button
                key={conv.phone}
                onClick={() => setSelected(conv)}
                className={`w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors ${isSelected ? 'bg-blue-50 border-l-2 border-blue-500' : ''}`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-gray-900 truncate max-w-[160px]">
                    {conv.customer_name ?? conv.phone}
                  </span>
                  <span className="text-xs text-gray-400 flex-shrink-0 ml-1">
                    {formatTs(conv.last_message_at)}
                  </span>
                </div>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-xs text-gray-500 truncate max-w-[140px]">
                    {last?.text ?? ''}
                  </span>
                  {badge && <BadgePill label={badge} />}
                  {conv.needs_human && (
                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-orange-100 text-orange-700 flex-shrink-0">
                      !
                    </span>
                  )}
                </div>
              </button>
            );
          })}
        </div>

        {/* Pagination */}
        {total > LIMIT && (
          <div className="p-3 border-t border-gray-200 flex items-center justify-between">
            <button
              onClick={() => setOffset(Math.max(0, offset - LIMIT))}
              disabled={offset === 0}
              className="text-xs text-blue-600 hover:text-blue-800 disabled:opacity-40"
            >
              Prev
            </button>
            <span className="text-xs text-gray-500">
              {offset + 1}–{Math.min(offset + LIMIT, total)} of {total}
            </span>
            <button
              onClick={() => setOffset(offset + LIMIT)}
              disabled={offset + LIMIT >= total}
              className="text-xs text-blue-600 hover:text-blue-800 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        )}
      </aside>

      {/* Right panel: message thread */}
      <main className="flex-1 flex flex-col min-w-0">
        {!selected ? (
          <div className="flex-1 flex items-center justify-center text-gray-400">
            <div className="text-center">
              <div className="text-4xl mb-2">💬</div>
              <p className="text-sm">Select a conversation to view messages</p>
            </div>
          </div>
        ) : (
          <>
            {/* Thread header */}
            <div className="px-6 py-4 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-gray-900">
                  {selected.customer_name ?? selected.phone}
                </h3>
                <p className="text-xs text-gray-500">
                  {selected.phone_e164 ?? selected.phone}
                  {selected.customer_id && (
                    <span className="ml-2 text-blue-600">customer #{selected.customer_id}</span>
                  )}
                </p>
              </div>
              {selected.needs_human && (
                <span className="inline-flex items-center px-2 py-1 rounded text-xs font-medium bg-orange-100 text-orange-700">
                  Needs human response
                </span>
              )}
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-6 space-y-3">
              {(selected.messages ?? []).length === 0 && (
                <p className="text-sm text-gray-400">No messages in this thread.</p>
              )}
              {(selected.messages ?? []).map((msg, i) => {
                const isOut = msg.direction === 'outbound';
                return (
                  <div key={msg.wa_message_id ?? i} className={`flex ${isOut ? 'justify-end' : 'justify-start'}`}>
                    <div
                      className={`max-w-sm px-4 py-2 rounded-2xl text-sm shadow-sm ${
                        isOut
                          ? 'bg-green-100 text-gray-900 rounded-br-sm'
                          : 'bg-white border border-gray-200 text-gray-900 rounded-bl-sm'
                      }`}
                    >
                      {msg.button_payload && (
                        <p className="text-xs text-gray-400 mb-0.5">
                          Button: {msg.button_payload}
                        </p>
                      )}
                      <p>{msg.text || <em className="text-gray-400">[{msg.type ?? 'unknown'} message]</em>}</p>
                      <p className="text-xs text-gray-400 mt-1 text-right">
                        {formatTs(msg.received_at)}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Read-only notice */}
            <div className="px-6 py-3 border-t border-gray-200 bg-gray-50">
              <p className="text-xs text-gray-400 text-center">
                Read-only inbox (v1). Replies are sent automatically by the intent engine.
                To send a manual reply, use the MEGAPHONE agent or contact the customer directly.
              </p>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

export default WhatsAppInboxPage;
