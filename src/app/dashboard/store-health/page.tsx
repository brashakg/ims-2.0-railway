'use client';

import { useState } from 'react';
import { Loader, CheckCircle, AlertCircle, AlertTriangle, Sparkles, X } from 'lucide-react';
import Topbar from '@/components/Topbar';

interface HealthMetric {
  field: string;
  count: number;
  total: number;
  percentage: number;
}

interface AuditData {
  dataQuality: HealthMetric[];
  seoHealth: HealthMetric[];
  contentCompletenessScore: number;
  recommendations: {
    title: string;
    description: string;
    priority: 'high' | 'medium' | 'low';
    affectedCount: number;
  }[];
}

interface SeoSample {
  productId: string;
  title?: string;
  status: 'SUCCESS' | 'FAILED' | 'SKIPPED';
  message?: string;
  generated?: { seoTitle: string; seoDescription: string };
  pushed?: boolean;
}

interface SeoBatchState {
  running: boolean;
  mode: 'idle' | 'dryrun' | 'batch';
  samples: SeoSample[];
  progress: { generated: number; failed: number; skipped: number; pushed: number };
  startTotal: number;
  remaining: number;
  tokens: { in: number; out: number; cacheRead: number };
  error: string | null;
}

export default function StoreHealthPage() {
  const [auditData, setAuditData] = useState<AuditData | null>(null);
  const [loading, setLoading] = useState(false);
  const [auditStarted, setAuditStarted] = useState(false);

  const [seoState, setSeoState] = useState<SeoBatchState>({
    running: false,
    mode: 'idle',
    samples: [],
    progress: { generated: 0, failed: 0, skipped: 0, pushed: 0 },
    startTotal: 0,
    remaining: 0,
    tokens: { in: 0, out: 0, cacheRead: 0 },
    error: null,
  });

  const runAudit = async () => {
    try {
      setLoading(true);
      setAuditStarted(true);

      const response = await fetch('/api/store-health', {
        method: 'GET',
      });

      if (response.ok) {
        const json = await response.json();
        setAuditData(json.data || json);
      } else {
        console.error('Audit failed:', response.statusText);
      }
    } catch (error) {
      console.error('Error running audit:', error);
    } finally {
      setLoading(false);
    }
  };

  const getHealthColor = (percentage: number) => {
    if (percentage >= 90) return 'from-green-400 to-green-600';
    if (percentage >= 70) return 'from-yellow-400 to-yellow-600';
    return 'from-red-400 to-red-600';
  };

  const getHealthIcon = (percentage: number) => {
    if (percentage >= 90) return <CheckCircle className="text-green-600" size={20} />;
    if (percentage >= 70) return <AlertTriangle className="text-yellow-600" size={20} />;
    return <AlertCircle className="text-red-600" size={20} />;
  };

  const getHealthLabel = (percentage: number) => {
    if (percentage >= 90) return 'Excellent';
    if (percentage >= 70) return 'Good';
    return 'Needs Work';
  };

  // Preview AI-generated SEO for the first 3 products needing it.
  const previewSeo = async () => {
    setSeoState((s) => ({
      ...s,
      running: true,
      mode: 'dryrun',
      samples: [],
      error: null,
    }));
    try {
      const res = await fetch('/api/store-health/generate-seo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ limit: 3, dryRun: true, mode: 'missing_either' }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.error || 'Preview failed');
      }
      setSeoState((s) => ({
        ...s,
        running: false,
        samples: data.results || [],
        remaining: data.remaining ?? 0,
        tokens: {
          in: data.summary?.tokens?.in || 0,
          out: data.summary?.tokens?.out || 0,
          cacheRead: data.summary?.tokens?.cacheRead || 0,
        },
      }));
    } catch (e) {
      setSeoState((s) => ({
        ...s,
        running: false,
        error: e instanceof Error ? e.message : 'Preview failed',
      }));
    }
  };

  // Chunked real run: repeatedly pulls 25 at a time until remaining === 0
  // or until the user cancels by navigating away.
  const runSeoBatch = async () => {
    if (!confirm('Generate SEO for all products missing titles or descriptions? This calls the Anthropic API for each product and may take several minutes.')) return;
    setSeoState((s) => ({
      ...s,
      running: true,
      mode: 'batch',
      samples: [],
      progress: { generated: 0, failed: 0, skipped: 0, pushed: 0 },
      tokens: { in: 0, out: 0, cacheRead: 0 },
      error: null,
      startTotal: 0,
    }));

    let totalIn = 0,
      totalOut = 0,
      totalCache = 0;
    let tg = 0, tf = 0, ts = 0, tp = 0;
    let loops = 0;
    let startTotal = 0;
    let remaining = 1;

    while (remaining > 0 && loops < 200) {
      try {
        const res = await fetch('/api/store-health/generate-seo', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            limit: 25,
            dryRun: false,
            pushToShopify: true,
            mode: 'missing_either',
          }),
        });
        const data = await res.json();
        if (!res.ok || !data.success) {
          setSeoState((s) => ({
            ...s,
            running: false,
            error: data.error || 'Batch call failed',
          }));
          return;
        }
        tg += data.summary?.generated || 0;
        tf += data.summary?.failed || 0;
        ts += data.summary?.skipped || 0;
        tp += data.summary?.pushed || 0;
        totalIn += data.summary?.tokens?.in || 0;
        totalOut += data.summary?.tokens?.out || 0;
        totalCache += data.summary?.tokens?.cacheRead || 0;
        remaining = data.remaining ?? 0;
        if (loops === 0) {
          startTotal = tg + tf + ts + remaining;
        }
        setSeoState((s) => ({
          ...s,
          startTotal,
          remaining,
          progress: { generated: tg, failed: tf, skipped: ts, pushed: tp },
          tokens: { in: totalIn, out: totalOut, cacheRead: totalCache },
          samples: (data.results || []).slice(0, 5),
        }));
        if ((data.summary?.generated || 0) + (data.summary?.skipped || 0) + (data.summary?.failed || 0) === 0) {
          // nothing left to do
          break;
        }
      } catch (e) {
        setSeoState((s) => ({
          ...s,
          running: false,
          error: e instanceof Error ? e.message : 'Batch failed',
        }));
        return;
      }
      loops++;
    }

    setSeoState((s) => ({
      ...s,
      running: false,
      mode: 'idle',
    }));
    // Auto-refresh the audit panel so numbers reflect the new state
    runAudit();
  };

  const cancelSeo = () => {
    setSeoState((s) => ({ ...s, running: false, mode: 'idle' }));
  };

  return (
    <>
      <Topbar
        title="Store Health"
        subtitle="AI-driven SEO + content audit"
        breadcrumb={[{ label: 'Home', href: '/dashboard' }, { label: 'Store Health' }]}
        primaryAction={null}
      />
      <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>

        {/* Run Audit Button */}
        <div className="mb-8">
          <button
            onClick={runAudit}
            disabled={loading}
            className="px-6 py-3 bg-gradient-to-r from-blue-600 to-blue-700 text-white font-semibold rounded-lg hover:shadow-lg transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {loading && <Loader className="animate-spin" size={20} />}
            {loading ? 'Running Audit...' : 'Run Audit'}
          </button>
        </div>

        {loading && !auditData && (
          <div className="flex items-center justify-center min-h-96">
            <div className="text-center">
              <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mb-4" />
              <p className="text-gray-600">Analyzing store health...</p>
            </div>
          </div>
        )}

        {auditStarted && auditData && (
          <div className="space-y-8">
            {/* Content Completeness Score */}
            <div className="bg-white rounded-lg shadow-md p-8">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-2xl font-bold text-gray-900">Content Completeness Score</h2>
                <div className="flex items-center gap-4">
                  <div className="text-right">
                    <p className="text-4xl font-bold text-gray-900">
                      {auditData.contentCompletenessScore.toFixed(1)}%
                    </p>
                    <p className="text-sm text-gray-600 mt-1">
                      {getHealthLabel(auditData.contentCompletenessScore)}
                    </p>
                  </div>
                  <div className="mb-1">{getHealthIcon(auditData.contentCompletenessScore)}</div>
                </div>
              </div>
              <div className="bg-gray-200 rounded-full h-4 overflow-hidden">
                <div
                  className={`h-full bg-gradient-to-r ${getHealthColor(auditData.contentCompletenessScore)} transition-all duration-1000`}
                  style={{ width: `${auditData.contentCompletenessScore}%` }}
                />
              </div>
              <p className="text-sm text-gray-600 mt-3">Average percentage of filled fields across all products</p>
            </div>

            {/* Product Data Quality */}
            <div className="bg-white rounded-lg shadow-md p-6">
              <h2 className="text-xl font-semibold mb-6 text-gray-900">Product Data Quality</h2>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {auditData.dataQuality?.map((metric, idx) => (
                  <div key={idx} className="p-4 border rounded-lg hover:shadow-md transition-shadow">
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="font-semibold text-gray-900">{metric.field}</h3>
                      {getHealthIcon(metric.percentage)}
                    </div>
                    <div className="bg-gray-200 rounded-full h-6 overflow-hidden mb-3">
                      <div
                        className={`h-full bg-gradient-to-r ${getHealthColor(metric.percentage)}`}
                        style={{ width: `${metric.percentage}%` }}
                      />
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-gray-600">
                        {metric.count} of {metric.total}
                      </span>
                      <span className="font-semibold text-gray-900">{metric.percentage.toFixed(1)}%</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* SEO Health */}
            <div className="bg-white rounded-lg shadow-md p-6">
              <h2 className="text-xl font-semibold mb-6 text-gray-900">SEO Health</h2>
              <div className="space-y-4">
                {auditData.seoHealth?.map((metric, idx) => {
                  const percentage = metric.percentage;
                  return (
                    <div key={idx}>
                      <div className="flex items-center justify-between mb-2">
                        <span className="font-medium text-gray-900">{metric.field}</span>
                        <div className="flex items-center gap-2">
                          <span className="text-sm text-gray-600">
                            {metric.count} of {metric.total}
                          </span>
                          {getHealthIcon(percentage)}
                        </div>
                      </div>
                      <div className="bg-gray-200 rounded-full h-6 overflow-hidden">
                        <div
                          className={`h-full bg-gradient-to-r ${getHealthColor(percentage)} transition-all`}
                          style={{ width: `${percentage}%` }}
                        />
                      </div>
                      <div className="text-right mt-1">
                        <span className="text-xs font-semibold text-gray-900">{percentage.toFixed(1)}%</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* AI SEO Generator */}
            <div className="bg-white rounded-lg shadow-md p-6 border-2 border-purple-100">
              <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
                <div>
                  <h2 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
                    <Sparkles className="w-5 h-5 text-purple-600" />
                    AI SEO Generator
                  </h2>
                  <p className="text-sm text-gray-600 mt-1">
                    Fill missing SEO titles and meta descriptions using Claude (Haiku 4.5). Gaps only — existing values are never overwritten.
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={previewSeo}
                    disabled={seoState.running}
                    className="px-3 py-2 text-sm border border-purple-300 text-purple-700 bg-white rounded-lg hover:bg-purple-50 disabled:opacity-50 flex items-center gap-2"
                  >
                    {seoState.running && seoState.mode === 'dryrun' ? (
                      <Loader className="w-4 h-4 animate-spin" />
                    ) : (
                      <Sparkles className="w-4 h-4" />
                    )}
                    Preview 3 samples
                  </button>
                  <button
                    onClick={runSeoBatch}
                    disabled={seoState.running}
                    className="px-3 py-2 text-sm rounded-lg bg-purple-600 text-white hover:bg-purple-700 disabled:opacity-50 flex items-center gap-2"
                  >
                    {seoState.running && seoState.mode === 'batch' ? (
                      <Loader className="w-4 h-4 animate-spin" />
                    ) : (
                      <Sparkles className="w-4 h-4" />
                    )}
                    Run full batch
                  </button>
                  {seoState.running && (
                    <button
                      onClick={cancelSeo}
                      className="px-2 py-2 text-sm rounded-lg border border-slate-300 text-slate-600 bg-white hover:bg-slate-50"
                      title="Stop the batch (server-side chunk in flight will finish)"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>

              {seoState.error && (
                <div className="mb-3 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-800">
                  {seoState.error}
                  {/ANTHROPIC_API_KEY/i.test(seoState.error) && (
                    <div className="mt-2 text-xs">
                      Add ANTHROPIC_API_KEY to Railway env vars, then retry.
                    </div>
                  )}
                </div>
              )}

              {seoState.mode === 'batch' && (
                <div className="mb-4">
                  <div className="flex justify-between text-xs text-slate-600 mb-1">
                    <span>
                      {seoState.progress.generated} generated · {seoState.progress.failed} failed · {seoState.progress.skipped} skipped
                      {seoState.progress.pushed > 0 && ` · ${seoState.progress.pushed} pushed to Shopify`}
                    </span>
                    <span>Remaining: {seoState.remaining}</span>
                  </div>
                  <div className="w-full bg-purple-100 rounded-full h-2">
                    <div
                      className="bg-purple-600 h-2 rounded-full transition-all"
                      style={{
                        width: `${
                          seoState.startTotal > 0
                            ? Math.min(
                                100,
                                (seoState.progress.generated /
                                  seoState.startTotal) *
                                  100
                              )
                            : 0
                        }%`,
                      }}
                    />
                  </div>
                  <div className="text-[11px] text-slate-500 mt-1">
                    Tokens in: {seoState.tokens.in.toLocaleString()} · out: {seoState.tokens.out.toLocaleString()}
                    {seoState.tokens.cacheRead > 0 && ` · cache-hit: ${seoState.tokens.cacheRead.toLocaleString()}`}
                  </div>
                </div>
              )}

              {seoState.samples.length > 0 && (
                <div className="space-y-3">
                  <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
                    {seoState.mode === 'dryrun' ? 'Preview samples (dry run)' : 'Latest results'}
                  </h3>
                  {seoState.samples.map((s) => (
                    <div
                      key={s.productId}
                      className={`border rounded-lg p-3 text-sm ${
                        s.status === 'SUCCESS'
                          ? 'border-emerald-200 bg-emerald-50/40'
                          : s.status === 'FAILED'
                            ? 'border-red-200 bg-red-50'
                            : 'border-slate-200'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2 flex-wrap mb-2">
                        <span className="font-medium text-slate-900">
                          {s.title || s.productId}
                        </span>
                        <span
                          className={`text-[11px] px-2 py-0.5 rounded-full border ${
                            s.status === 'SUCCESS'
                              ? 'bg-emerald-100 text-emerald-700 border-emerald-200'
                              : s.status === 'FAILED'
                                ? 'bg-red-100 text-red-700 border-red-200'
                                : 'bg-slate-100 text-slate-700 border-slate-200'
                          }`}
                        >
                          {s.status}
                          {s.pushed && ' · pushed'}
                        </span>
                      </div>
                      {s.generated ? (
                        <>
                          <div>
                            <span className="text-[11px] uppercase tracking-wide text-slate-500">
                              SEO Title
                            </span>
                            <div className="text-slate-800">{s.generated.seoTitle}</div>
                          </div>
                          <div className="mt-1">
                            <span className="text-[11px] uppercase tracking-wide text-slate-500">
                              Meta Description
                            </span>
                            <div className="text-slate-800">{s.generated.seoDescription}</div>
                          </div>
                        </>
                      ) : (
                        <div className="text-slate-600 text-xs">{s.message}</div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* AI Recommendations */}
            {auditData.recommendations && auditData.recommendations.length > 0 && (
              <div className="bg-white rounded-lg shadow-md p-6">
                <h2 className="text-xl font-semibold mb-6 text-gray-900">AI-Generated Recommendations</h2>
                <div className="space-y-4">
                  {auditData.recommendations.map((rec, idx) => (
                    <div
                      key={idx}
                      className={`p-4 border-l-4 rounded ${
                        rec.priority === 'high'
                          ? 'bg-red-50 border-red-500'
                          : rec.priority === 'medium'
                            ? 'bg-yellow-50 border-yellow-500'
                            : 'bg-blue-50 border-blue-500'
                      }`}
                    >
                      <div className="flex items-start justify-between">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-1">
                            <h3 className="font-semibold text-gray-900">{rec.title}</h3>
                            <span
                              className={`text-xs font-bold px-2 py-1 rounded ${
                                rec.priority === 'high'
                                  ? 'bg-red-200 text-red-800'
                                  : rec.priority === 'medium'
                                    ? 'bg-yellow-200 text-yellow-800'
                                    : 'bg-blue-200 text-blue-800'
                              }`}
                            >
                              {rec.priority.toUpperCase()}
                            </span>
                          </div>
                          <p className="text-sm text-gray-700 mb-2">{rec.description}</p>
                          <p className="text-xs text-gray-600">
                            Affects {rec.affectedCount} products
                          </p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Summary Stats */}
            <div className="bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 rounded-lg p-6">
              <h3 className="font-semibold text-gray-900 mb-3">Audit Summary</h3>
              <ul className="text-sm text-gray-700 space-y-2">
                <li>
                  Overall store health score: <span className="font-bold">{auditData.contentCompletenessScore.toFixed(1)}%</span>
                </li>
                <li>
                  Data quality metrics: <span className="font-bold">{auditData.dataQuality?.length || 0} fields checked</span>
                </li>
                <li>
                  SEO metrics: <span className="font-bold">{auditData.seoHealth?.length || 0} dimensions analyzed</span>
                </li>
                <li>
                  Recommendations: <span className="font-bold">{auditData.recommendations?.length || 0} actions suggested</span>
                </li>
              </ul>
            </div>
          </div>
        )}

        {auditStarted && !auditData && !loading && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-6 text-center">
            <AlertCircle className="mx-auto mb-3 text-red-600" size={32} />
            <p className="text-red-800 font-semibold">Unable to run audit</p>
            <p className="text-red-700 text-sm mt-1">Please check your connection and try again.</p>
          </div>
        )}

        {!auditStarted && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-8 text-center">
            <p className="text-gray-700 mb-4">Click "Run Audit" to analyze your store's product data quality and SEO optimization</p>
            <p className="text-sm text-gray-600">
              This comprehensive audit will check your product catalog for missing data, incomplete SEO information, and provide
              AI-generated recommendations for improvement.
            </p>
          </div>
        )}
      </div>
    </>
  );
}
