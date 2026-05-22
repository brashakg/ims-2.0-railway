"use client";

// Round 2 mapping C2/C3/C4 — one-click auto-generation of brand ×
// category × shape × gender smart collections per the per-category
// plan in src/lib/collections/perCategoryPlan.ts.

import { useState } from "react";
import { Loader2, Sparkles, AlertCircle, CheckCircle2 } from "lucide-react";
import { CATEGORIES } from "@/lib/categories";

interface CollectionPlan {
  handle: string;
  title: string;
  ruleCondition: string;
  ruleColumn: string;
  categoryAnchor: string;
  autoSource: string;
  sortOrder: string;
  themeSuffix: string;
}

interface AutoGenResult {
  success: boolean;
  dryRun?: boolean;
  created?: CollectionPlan[];
  updated?: CollectionPlan[];
  skipped?: CollectionPlan[];
  error?: string;
}

export default function AutoCollectionsPage() {
  const [category, setCategory] = useState<string>("");
  const [brand, setBrand] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [result, setResult] = useState<AutoGenResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (commit: boolean) => {
    if (commit) setCommitting(true);
    else setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/collections/auto-generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          category: category || undefined,
          brand: brand || undefined,
          dryRun: !commit,
        }),
      });
      const j = (await res.json()) as AutoGenResult;
      if (!res.ok) throw new Error(j.error || `HTTP ${res.status}`);
      setResult(j);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
      setCommitting(false);
    }
  };

  const renderTable = (rows: CollectionPlan[] | undefined, title: string, tone: "good" | "warn" | "muted") => {
    if (!rows || rows.length === 0) return null;
    const ringClass = tone === "good" ? "border-emerald-300 bg-emerald-50" : tone === "warn" ? "border-amber-300 bg-amber-50" : "border-slate-200 bg-white";
    return (
      <div className={`rounded-lg border ${ringClass} mb-4 overflow-hidden`}>
        <div className="px-4 py-2 text-sm font-semibold border-b border-current/10">
          {title} <span className="font-mono text-xs">({rows.length})</span>
        </div>
        <div className="max-h-[400px] overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="bg-white/50 text-xs uppercase text-slate-600 sticky top-0">
              <tr>
                <th className="text-left px-4 py-2">Handle</th>
                <th className="text-left px-4 py-2">Title</th>
                <th className="text-left px-4 py-2">Category</th>
                <th className="text-left px-4 py-2">Rule</th>
                <th className="text-left px-4 py-2">Sort</th>
                <th className="text-left px-4 py-2">Template</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.handle} className="border-t border-slate-100">
                  <td className="px-4 py-2 font-mono text-xs">{r.handle}</td>
                  <td className="px-4 py-2 font-medium">{r.title}</td>
                  <td className="px-4 py-2 text-xs text-slate-600">{r.categoryAnchor}</td>
                  <td className="px-4 py-2 font-mono text-xs">{r.ruleColumn} = {r.ruleCondition}</td>
                  <td className="px-4 py-2 text-xs">{r.sortOrder}</td>
                  <td className="px-4 py-2 text-xs">{r.themeSuffix || "default"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  const totalChanges = (result?.created?.length || 0) + (result?.updated?.length || 0);

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <div className="max-w-6xl mx-auto">
        <h1 className="text-2xl font-bold text-slate-900 mb-2 flex items-center gap-2">
          <Sparkles className="w-6 h-6 text-amber-500" />
          Auto Collections
        </h1>
        <p className="text-sm text-slate-600 mb-6">
          Round 2 mapping C2/C3/C4 — auto-generates brand × category × shape × gender smart collections per
          the per-category plan. Dry-run first to review the plan, then commit (creates local DB rows;
          Shopify push is a follow-up step).
        </p>

        <div className="bg-white border border-slate-200 rounded-lg p-4 mb-6 grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1 uppercase tracking-wide">Category (optional)</label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full h-9 px-3 border border-slate-300 rounded-md text-sm"
            >
              <option value="">All categories</option>
              {CATEGORIES.map((c) => (
                <option key={c.key} value={c.key}>{c.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1 uppercase tracking-wide">Brand (optional)</label>
            <input
              type="text"
              value={brand}
              onChange={(e) => setBrand(e.target.value)}
              placeholder="Leave empty for all"
              className="w-full h-9 px-3 border border-slate-300 rounded-md text-sm"
            />
          </div>
          <div className="flex items-end gap-2">
            <button
              onClick={() => run(false)}
              disabled={loading || committing}
              className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2 bg-slate-900 text-white rounded-lg hover:bg-slate-800 disabled:opacity-50"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              Dry-run
            </button>
            {result?.dryRun && totalChanges > 0 && (
              <button
                onClick={() => run(true)}
                disabled={loading || committing}
                className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50"
              >
                {committing ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
                Commit ({totalChanges})
              </button>
            )}
          </div>
        </div>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-800 flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
            <div>{error}</div>
          </div>
        )}

        {result && (
          <>
            {!result.dryRun && (
              <div className="mb-4 p-3 rounded-lg bg-emerald-50 border border-emerald-200 text-sm text-emerald-800 flex items-start gap-2">
                <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
                <div>
                  Committed.{" "}
                  Created {result.created?.length ?? 0},
                  updated {result.updated?.length ?? 0},
                  skipped {result.skipped?.length ?? 0}.
                </div>
              </div>
            )}
            {renderTable(result.created, "Will create (new collections)", "good")}
            {renderTable(result.updated, "Will update (existing auto-collections)", "warn")}
            {renderTable(result.skipped, "Skipped (locally-modified or no change)", "muted")}
            {totalChanges === 0 && (
              <div className="p-6 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-600">
                Nothing to do — all collections are up-to-date with the per-category plan.
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
