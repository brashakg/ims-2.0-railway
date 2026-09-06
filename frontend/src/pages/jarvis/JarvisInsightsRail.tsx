// JarvisInsightsRail - moved verbatim out of JarvisPage.tsx (Wave 3 file diet).

import { Package, Users, ShoppingCart, Clock, Zap, Target, ChevronRight, ArrowUpRight, ArrowDownRight } from 'lucide-react';
import type { JarvisPageState } from './useJarvisPage';

export function JarvisInsightsRail({ page }: { page: JarvisPageState }) {
  const {
    insights, formatCurrency, recommendations, handleTakeAction,
  } = page;
  return (
    <>
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
            {recommendations.length === 0 && (
              <div style={{ fontSize: 11, color: 'var(--ink-4)', fontStyle: 'italic' }}>
                No active recommendations — nothing needs immediate attention.
              </div>
            )}
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
                {rec.impact && (
                  <div style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 4, fontFamily: 'var(--font-mono)' }}>
                    → {rec.impact}
                  </div>
                )}
                <button
                  type="button"
                  onClick={() => handleTakeAction(rec)}
                  className="btn sm ghost"
                  style={{ marginTop: 6, fontSize: 11, padding: '0 8px', height: 22 }}
                  title={rec.action ? `Ask JARVIS: ${rec.action}` : 'Ask JARVIS to expand'}
                >
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
    </>
  );
}
