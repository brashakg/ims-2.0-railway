"use client";

// Round 2 mapping C6 — admin UI for running the tag-casing migration.
// Shows the dry-run plan first; commit requires explicit confirmation.

import { useState } from "react";
import { Loader2, AlertTriangle, CheckCircle2, RefreshCw, AlertCircle } from "lucide-react";

interface RuleEntry {
  column: string;
  relation: string;
  condition: string;
}

interface CollectionRulesEntry {
  shopifyCollectionId: string;
  title: string;
  handle: string;
  productsCount: number;
  appliedDisjunctively: boolean;
  oldRules: RuleEntry[];
  newRules: RuleEntry[];
  changedConditions: number;
}

interface MigrationResult {
  success: boolean;
  dryRun: boolean;
  generatedAt: string;
  totalCollections: number;
  smartCollections: number;
  collectionsWithChanges: number;
  totalRuleChanges: number;
  entries: CollectionRulesEntry[];
  applied?: number;
  failed?: Array<{ shopifyCollectionId: string; title: string; error: string }>;
  committed?: boolean;
}

export default function TagCasingMigrationPage() {
  const [loading, setLoading] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [result, setResult] = useState<MigrationResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runDryRun = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/admin/tag-casing-migration", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dryRun: true }),
      });
      const j = (await res.json()) as MigrationResult & { error?: string };
      if (!res.ok || !j.success) throw new Error(j.error || `HTTP ${res.status}`);
      setResult(j);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const commit = async () => {
    if (!result) return;
    if (
      !confirm(
        `This will update ${result.collectionsWithChanges} Shopify collections (${result.totalRuleChanges} rule changes total). Continue?`
      )
    )
      return;
    setCommitting(true);
    setError(null);
    try {
      const res = await fetch("/api/admin/tag-casing-migration", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ commit: true }),
      });
      const j = (await res.json()) as MigrationResult & { error?: string };
      if (!res.ok) throw new Error(j.error || `HTTP ${res.status}`);
      setResult(j);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setCommitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <div className="max-w-5xl mx-auto">
        <h1 className="text-2xl font-bold text-slate-900 mb-2">
          Tag-Casing Migration
        </h1>
        <p className="text-sm text-slate-600 mb-6">
          Round 2 mapping C6 — fixes the live Shopify smart-collection rules to use
          lowercase + hyphenated tag conditions (matching what new app-pushed
          products emit). Dry-run first, review the plan, then commit.
        </p>

        {/* Run controls */}
        <div className="flex gap-3 mb-6">
          <button
            onClick={runDryRun}
            disabled={loading || committing}
            className="inline-flex items-center gap-2 px-4 py-2 bg-slate-900 text-white rounded-lg hover:bg-slate-800 disabled:opacity-50"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            {result ? "Refresh dry-run" : "Run dry-run"}
          </button>
          {result && !result.committed && result.collectionsWithChanges > 0 && (
            <button
              onClick={commit}
              disabled={loading || committing}
              className="inline-flex items-center gap-2 px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50"
            >
              {committing ? <Loader2 className="w-4 h-4 animate-spin" /> : <AlertTriangle className="w-4 h-4" />}
              Commit migration to Shopify
            </button>
          )}
        </div>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-800 flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
            <div>{error}</div>
          </div>
        )}

        {result && (
          <>
            {/* Summary */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
              <Stat label="Collections scanned" value={result.totalCollections} />
              <Stat label="Smart collections" value={result.smartCollections} />
              <Stat label="Need migration" value={result.collectionsWithChanges} tone={result.collectionsWithChanges > 0 ? "warn" : "ok"} />
              <Stat label="Rule conditions changing" value={result.totalRuleChanges} />
            </div>

            {result.committed && (
              <div className="mb-4 p-3 rounded-lg bg-emerald-50 border border-emerald-200 text-sm text-emerald-800 flex items-start gap-2">
                <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
                <div>
                  Migration committed. Applied {result.applied}/{result.collectionsWithChanges} collections.
                  {(result.failed?.length ?? 0) > 0 && (
                    <span className="block mt-1 text-red-700">
                      {result.failed?.length} failures (see below).
                    </span>
                  )}
                </div>
              </div>
            )}

            {/* Failures table */}
            {(result.failed?.length ?? 0) > 0 && (
              <div className="mb-6 bg-white border border-red-200 rounded-lg overflow-hidden">
                <div className="px-4 py-2 bg-red-50 border-b border-red-200 text-sm font-semibold text-red-800">
                  Failures ({result.failed?.length})
                </div>
                <table className="w-full text-sm">
                  <thead className="bg-red-50 text-xs uppercase text-red-700">
                    <tr>
                      <th className="text-left px-4 py-2">Collection</th>
                      <th className="text-left px-4 py-2">Error</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.failed?.map((f) => (
                      <tr key={f.shopifyCollectionId} className="border-t border-red-100">
                        <td className="px-4 py-2 font-medium">{f.title}</td>
                        <td className="px-4 py-2 text-red-700">{f.error}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Plan table */}
            {result.entries.length > 0 ? (
              <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
                <div className="px-4 py-2 bg-slate-100 border-b border-slate-200 text-sm font-semibold text-slate-800">
                  Plan ({result.entries.length} collections)
                </div>
                <div className="max-h-[600px] overflow-y-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-50 text-xs uppercase text-slate-600 sticky top-0">
                      <tr>
                        <th className="text-left px-4 py-2">Collection</th>
                        <th className="text-left px-4 py-2">Products</th>
                        <th className="text-left px-4 py-2">Rule changes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.entries.map((e) => (
                        <tr key={e.shopifyCollectionId} className="border-t border-slate-100 align-top">
                          <td className="px-4 py-2">
                            <div className="font-medium text-slate-900">{e.title}</div>
                            <div className="text-xs text-slate-500 font-mono">{e.handle}</div>
                          </td>
                          <td className="px-4 py-2 text-slate-700 tabular-nums">{e.productsCount.toLocaleString()}</td>
                          <td className="px-4 py-2">
                            <div className="space-y-1.5">
                              {e.oldRules.map((oldR, i) => {
                                const newR = e.newRules[i];
                                const changed = oldR.condition !== newR?.condition;
                                if (!changed) {
                                  return (
                                    <div key={i} className="text-xs text-slate-400 font-mono">
                                      {oldR.column} {oldR.relation} <span>{oldR.condition}</span> <span className="text-slate-400">(no change)</span>
                                    </div>
                                  );
                                }
                                return (
                                  <div key={i} className="text-xs font-mono">
                                    <span className="text-slate-500">{oldR.column} {oldR.relation}</span>{" "}
                                    <span className="line-through text-red-700">{oldR.condition}</span>{" "}
                                    <span className="text-slate-400">→</span>{" "}
                                    <span className="text-emerald-700 font-semibold">{newR?.condition}</span>
                                  </div>
                                );
                              })}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <div className="p-6 bg-emerald-50 border border-emerald-200 rounded-lg text-sm text-emerald-800 flex items-start gap-2">
                <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
                <div>No collections need migration — all rule conditions are already in the canonical lowercase + hyphenated form.</div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "ok" | "warn" }) {
  const ringClass = tone === "warn" ? "border-amber-300 bg-amber-50" : tone === "ok" ? "border-emerald-300 bg-emerald-50" : "border-slate-200 bg-white";
  const valClass = tone === "warn" ? "text-amber-700" : tone === "ok" ? "text-emerald-700" : "text-slate-900";
  return (
    <div className={`rounded-lg border ${ringClass} p-4`}>
      <div className="text-xs uppercase tracking-wide text-slate-500 font-semibold">{label}</div>
      <div className={`text-2xl font-bold tabular-nums mt-1 ${valClass}`}>{value.toLocaleString()}</div>
    </div>
  );
}
