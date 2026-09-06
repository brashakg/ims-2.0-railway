// JarvisChatPane - moved verbatim out of JarvisPage.tsx (Wave 3 file diet).

import { Bot, Send, Mic, MicOff } from 'lucide-react';
import type { JarvisPageState } from './useJarvisPage';

export function JarvisChatPane({ page }: { page: JarvisPageState }) {
  const {
    chatScrollRef, autoFollowRef, messages, isLoading, messagesEndRef,
    quickQueries, handleQuickQuery, isListening, setIsListening,
    inputValue, setInputValue, handleSend, llmModels, selectedModel, handleModelChange,
  } = page;
  return (
    <>
      {/* Chat pane */}
      <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {/* Messages */}
        <div
          ref={chatScrollRef}
          onScroll={() => {
            const el = chatScrollRef.current;
            if (el) autoFollowRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
          }}
          style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}
        >
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
                      .replace(/&/g, '&amp;')
                      .replace(/</g, '&lt;')
                      .replace(/>/g, '&gt;')
                      .replace(/"/g, '&quot;')
                      .replace(/'/g, '&#x27;')
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
            {llmModels.length > 1 && (
              <select
                value={selectedModel}
                onChange={(e) => handleModelChange(e.target.value)}
                className="input sm"
                style={{ maxWidth: 200 }}
                title="Choose which model answers — premium models prompt before switching"
                aria-label="LLM model"
              >
                {llmModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.tier === 'premium' ? `$$ ${m.label}` : m.label}
                  </option>
                ))}
              </select>
            )}
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
    </>
  );
}
