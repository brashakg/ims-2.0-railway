"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Archive,
  Loader2,
  RefreshCw,
  Trash2,
  UploadCloud,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  Edit2,
  ExternalLink,
} from "lucide-react";

type ReasonKey =
  | "missing_brand"
  | "missing_modelNo"
  | "missing_title"
  | "no_price"
  | "no_variants_no_images";

const REASON_LABELS: Record<ReasonKey, string> = {
  missing_brand: "Missing brand",
  missing_modelNo: "Missing model number",
  missing_title: "Missing title",
  no_price: "No price (MRP is 0)",
  no_variants_no_images: "No variants and no images",
};

interface OrphanRow {
  id: string;
  brand: string | null;
  modelNo: string | null;
  title: string | null;
  category: string | null;
  mrp: number;
  createdAt: string;
  variantCount: number;
  thumbnail: string | null;
  validation: { pushable: boolean; reasons: ReasonKey[] };
}

interface Summary {
  total: number;
  pushable: number;
  unpushable: number;
  reasonBreakdown: Record<ReasonKey, number>;
}

interface OrphansResponse {
  success: boolean;
  summary: Summary;
  pagination: { page: number; limit: number; total: number; pages: number };
  data: OrphanRow[];
  error?: string;
}

interface PushResultRow {
  productId: string;
  status: "SUCCESS" | "FAILED" | "SKIPPED";
  message?: string;
}

interface PushResponse {
  success: boolean;
  results: PushResultRow[];
  summary: {
    total: number;
    success: number;
    failed: number;
    skipped: number;
    aborted?: boolean;
    abortReason?: string;
  };
  remainingPushable: number;
  error?: string;
}

type FilterMode = "all" | "pushable" | "unpushable";

export default function OrphansPage() {
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<OrphanRow[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [pagination, setPagination] = useState({
    page: 1,
    limit: 50,
    total: 0,
    pages: 1,
  });
  const [filter, setFilter] = useState<FilterMode>("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [banner, setBanner] = useState<
    { type: "success" | "error"; text: string } | null
  >(null);

  // Push-run state
  const [pushRunning, setPushRunning] = useState(false);
  const [pushProgress, setPushProgress] = useState({
    pushed: 0,
    failed: 0,
    skipped: 0,
    startTotal: 0,
    remaining: 0,
  });

  // Failed-product detail collected across all chunks of a push run.
  // We map productId → { title, brand, modelNo, message } so the
  // post-push modal can show a row per failure with the actual Shopify
  // userError text and a direct link to fix the product.
  interface PushFailure {
    productId: string;
    title: string | null;
    brand: string | null;
    modelNo: string | null;
    message: string;
  }
  const [failedDetails, setFailedDetails] = useState<PushFailure[]>([]);
  const [failureModalOpen, setFailureModalOpen] = useState(false);

  // Delete state
  const [archiveRunning, setArchiveRunning] = useState(false);
  const [hardDeleteRunning, setHardDeleteRunning] = useState(false);
  const [hardDeleteConfirm, setHardDeleteConfirm] = useState(false);
  const [confirmText, setConfirmText] = useState("");

  const fetchOrphans = useCallback(
    async (page = pagination.page) => {
      setLoading(true);
      try {
        const params = new URLSearchParams({
          page: String(page),
          limit: String(pagination.limit),
          filter,
        });
        if (search.trim()) params.set("search", search.trim());

        const res = await fetch(`/api/products/orphans?${params}`);
        const data: OrphansResponse = await res.json();
        if (data.success) {
          setRows(data.data);
          setSummary(data.summary);
          setPagination(data.pagination);
        } else {
          setBanner({ type: "error", text: data.error || "Failed to load orphans" });
        }
      } catch (e) {
        setBanner({
          type: "error",
          text: e instanceof Error ? e.message : "Failed to load orphans",
        });
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filter, pagination.limit, search]
  );

  useEffect(() => {
    fetchOrphans(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  const toggleRow = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllVisible = () => {
    setSelected((prev) => {
      const visibleIds = rows.map((r) => r.id);
      const allSelected = visibleIds.every((id) => prev.has(id));
      const next = new Set(prev);
      if (allSelected) {
        for (const id of visibleIds) next.delete(id);
      } else {
        for (const id of visibleIds) next.add(id);
      }
      return next;
    });
  };

  const runPushChunk = async (): Promise<PushResponse | null> => {
    const selectedPushable = rows
      .filter((r) => selected.has(r.id) && r.validation.pushable)
      .map((r) => r.id);

    const body: { limit?: number; productIds?: string[] } =
      selectedPushable.length > 0
        ? { productIds: selectedPushable }
        : { limit: 50 };

    const res = await fetch("/api/products/orphans/push", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data: PushResponse = await res.json();
    if (!data.success) {
      setBanner({ type: "error", text: data.error || "Push failed" });
      return null;
    }
    return data;
  };

  const startPushRun = async () => {
    if (!summary || summary.pushable === 0) {
      setBanner({ type: "error", text: "No pushable orphans." });
      return;
    }
    const startTotal = summary.pushable;
    setPushRunning(true);
    setPushProgress({
      pushed: 0,
      failed: 0,
      skipped: 0,
      startTotal,
      remaining: startTotal,
    });
    setBanner(null);
    setFailedDetails([]);

    let pushed = 0;
    let failed = 0;
    let skipped = 0;
    let remaining = startTotal;
    let loops = 0;
    // Per-product failure log accumulated across chunks. We dedupe by
    // productId — if the same product is retried and fails again, we
    // keep the most recent message rather than duplicating the row.
    const failuresByProductId = new Map<string, PushFailure>();
    // Client-side circuit breaker: stop after 5 consecutive chunks where the
    // backend reported zero successes. Pairs with the server-side breaker in
    // pushProductsToShopify so we don't hammer Shopify 60+ times.
    let consecutiveEmptyChunks = 0;
    const MAX_EMPTY_CHUNKS = 5;
    let abortReason: string | undefined;

    // Snapshot the rows table so we can resolve productId → title/brand
    // for the failure modal even if the filter changes underneath.
    const rowIndex = new Map(rows.map((r) => [r.id, r]));

    while (remaining > 0 && loops < 200) {
      const chunk = await runPushChunk();
      if (!chunk) break;
      pushed += chunk.summary.success;
      failed += chunk.summary.failed;
      skipped += chunk.summary.skipped;
      remaining = chunk.remainingPushable;

      // Capture per-product failures for the modal.
      for (const r of chunk.results) {
        if (r.status === "FAILED") {
          const meta = rowIndex.get(r.productId);
          failuresByProductId.set(r.productId, {
            productId: r.productId,
            title: meta?.title ?? null,
            brand: meta?.brand ?? null,
            modelNo: meta?.modelNo ?? null,
            message: r.message || "(no error message returned)",
          });
        }
      }

      setPushProgress({
        pushed,
        failed,
        skipped,
        startTotal,
        remaining,
      });
      if (chunk.summary.total === 0) break;

      // Honor server-side abort
      if (chunk.summary.aborted) {
        abortReason = chunk.summary.abortReason || "Server aborted push loop";
        break;
      }

      // Client-side guard: if nothing succeeded this chunk, treat it as a
      // "failed try" and count against the 5-try cap.
      if (chunk.summary.success === 0) {
        consecutiveEmptyChunks++;
        if (consecutiveEmptyChunks >= MAX_EMPTY_CHUNKS) {
          abortReason = `Stopped after ${MAX_EMPTY_CHUNKS} chunks with zero successes. Check Shopify credentials and the most recent sync log.`;
          break;
        }
      } else {
        consecutiveEmptyChunks = 0;
      }

      loops++;
      // small UI breathing room
      await new Promise((r) => setTimeout(r, 200));
    }

    const failures = Array.from(failuresByProductId.values());
    setFailedDetails(failures);
    setPushRunning(false);
    setSelected(new Set());
    setBanner({
      type: failed > 0 || abortReason ? "error" : "success",
      text: abortReason
        ? `Push halted. ${pushed} pushed, ${failed} failed, ${skipped} skipped. ${abortReason}`
        : `Push finished. ${pushed} pushed, ${failed} failed, ${skipped} skipped. Remaining pushable: ${remaining}.`,
    });
    // Auto-open the failure modal when there's anything to investigate.
    // Staff can dismiss it; "View failures" link in the banner re-opens.
    if (failures.length > 0) setFailureModalOpen(true);
    await fetchOrphans(1);
  };

  const runArchive = async () => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    if (!confirm(`Archive ${ids.length} orphan product(s)?`)) return;

    setArchiveRunning(true);
    try {
      const res = await fetch("/api/products/orphans/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ productIds: ids, mode: "archive" }),
      });
      const data = await res.json();
      if (data.success) {
        setBanner({
          type: "success",
          text: `Archived ${data.summary.succeeded} product(s).`,
        });
        setSelected(new Set());
        await fetchOrphans(pagination.page);
      } else {
        setBanner({ type: "error", text: data.error || "Archive failed" });
      }
    } catch (e) {
      setBanner({
        type: "error",
        text: e instanceof Error ? e.message : "Archive failed",
      });
    } finally {
      setArchiveRunning(false);
    }
  };

  const runHardDelete = async () => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;

    setHardDeleteRunning(true);
    try {
      const res = await fetch("/api/products/orphans/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ productIds: ids, mode: "hard" }),
      });
      const data = await res.json();
      if (data.success) {
        setBanner({
          type: "success",
          text: `Hard-deleted ${data.summary.succeeded} product(s).`,
        });
        setSelected(new Set());
        setHardDeleteConfirm(false);
        setConfirmText("");
        await fetchOrphans(1);
      } else {
        setBanner({ type: "error", text: data.error || "Hard-delete failed" });
      }
    } catch (e) {
      setBanner({
        type: "error",
        text: e instanceof Error ? e.message : "Hard-delete failed",
      });
    } finally {
      setHardDeleteRunning(false);
    }
  };

  const selectedCount = selected.size;
  const selectedPushableCount = useMemo(
    () => rows.filter((r) => selected.has(r.id) && r.validation.pushable).length,
    [rows, selected]
  );

  const allVisibleSelected =
    rows.length > 0 && rows.every((r) => selected.has(r.id));

  const reasons = (Object.keys(REASON_LABELS) as ReasonKey[]).map((k) => ({
    key: k,
    label: REASON_LABELS[k],
    count: summary?.reasonBreakdown[k] || 0,
  }));

  return (
    <div className="p-4 sm:p-6 max-w-7xl mx-auto">
      <div className="flex items-start justify-between mb-6 gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2">
            <AlertTriangle className="w-6 h-6 text-amber-500" />
            Orphan Products Audit
          </h1>
          <p className="text-sm text-slate-600 mt-1">
            Products created locally that were never pushed to Shopify
            (shopifyProductId is null).
          </p>
        </div>
        <button
          onClick={() => fetchOrphans(pagination.page)}
          disabled={loading || pushRunning}
          className="flex items-center gap-2 px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {banner && (
        <div
          className={`mb-4 px-4 py-3 rounded-lg text-sm flex items-center justify-between gap-3 flex-wrap ${
            banner.type === "success"
              ? "bg-emerald-50 text-emerald-800 border border-emerald-200"
              : "bg-red-50 text-red-800 border border-red-200"
          }`}
        >
          <span>{banner.text}</span>
          {failedDetails.length > 0 && (
            <button
              type="button"
              onClick={() => setFailureModalOpen(true)}
              className="text-red-700 underline text-xs font-medium hover:text-red-900 whitespace-nowrap"
            >
              View {failedDetails.length} failure{failedDetails.length === 1 ? "" : "s"} →
            </button>
          )}
        </div>
      )}

      {/* KPI cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
        <KpiCard
          label="Total orphans"
          value={summary?.total ?? "—"}
          loading={loading && !summary}
          tone="neutral"
        />
        <KpiCard
          label="Pushable"
          value={summary?.pushable ?? "—"}
          loading={loading && !summary}
          tone="good"
        />
        <KpiCard
          label="Unpushable"
          value={summary?.unpushable ?? "—"}
          loading={loading && !summary}
          tone="warn"
        />
      </div>

      {/* Reason breakdown */}
      <div className="bg-white border border-slate-200 rounded-lg p-4 mb-4">
        <h2 className="text-sm font-semibold text-slate-800 mb-3">
          Why they can&apos;t push (overlap — one product may have several)
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2">
          {reasons.map((r) => (
            <div
              key={r.key}
              className="flex items-baseline justify-between p-2 rounded border border-slate-100 bg-slate-50"
            >
              <span className="text-xs text-slate-600">{r.label}</span>
              <span className="text-sm font-semibold text-slate-900">
                {r.count}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Filters + search */}
      <div className="flex items-center gap-2 flex-wrap mb-3">
        {(["all", "pushable", "unpushable"] as FilterMode[]).map((f) => (
          <button
            key={f}
            onClick={() => {
              setFilter(f);
              setSelected(new Set());
            }}
            className={`px-3 py-1.5 text-sm rounded-lg border ${
              filter === f
                ? "bg-blue-600 text-white border-blue-600"
                : "bg-white text-slate-700 border-slate-300 hover:bg-slate-50"
            }`}
          >
            {f === "all" ? "All" : f === "pushable" ? "Pushable" : "Unpushable"}
          </button>
        ))}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            fetchOrphans(1);
          }}
          className="flex-1 min-w-[200px]"
        >
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search title / brand / model…"
            className="w-full px-3 py-1.5 text-sm border border-slate-300 rounded-lg"
          />
        </form>
      </div>

      {/* Push progress */}
      {pushRunning && (
        <div className="mb-4 p-4 bg-blue-50 border border-blue-200 rounded-lg">
          <div className="flex items-center gap-2 mb-2 text-sm text-blue-800">
            <Loader2 className="w-4 h-4 animate-spin" />
            Pushing orphans to Shopify… {pushProgress.pushed} /{" "}
            {pushProgress.startTotal} done (
            {pushProgress.failed} failed, {pushProgress.skipped} skipped,{" "}
            {pushProgress.remaining} remaining)
          </div>
          <div className="w-full bg-blue-100 rounded-full h-2">
            <div
              className="bg-blue-600 h-2 rounded-full transition-all"
              style={{
                width: `${
                  pushProgress.startTotal > 0
                    ? Math.min(
                        100,
                        (pushProgress.pushed / pushProgress.startTotal) * 100
                      )
                    : 0
                }%`,
              }}
            />
          </div>
        </div>
      )}

      {/* Bulk action bar */}
      <div className="mb-3 flex items-center gap-2 flex-wrap">
        <button
          onClick={startPushRun}
          disabled={
            pushRunning || !summary || summary.pushable === 0 || loading
          }
          className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
        >
          <UploadCloud className="w-4 h-4" />
          {selectedPushableCount > 0
            ? `Push ${selectedPushableCount} selected`
            : `Push all pushable (${summary?.pushable ?? 0})`}
        </button>

        <button
          onClick={runArchive}
          disabled={archiveRunning || selectedCount === 0 || pushRunning}
          className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          <Archive className="w-4 h-4" />
          {archiveRunning ? "Archiving…" : `Archive ${selectedCount || ""}`}
        </button>

        <button
          onClick={() => {
            if (selectedCount === 0) return;
            setHardDeleteConfirm(true);
          }}
          disabled={hardDeleteRunning || selectedCount === 0 || pushRunning}
          className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg border border-red-300 bg-white text-red-700 hover:bg-red-50 disabled:opacity-50"
        >
          <Trash2 className="w-4 h-4" />
          Hard-delete {selectedCount || ""}
        </button>

        <div className="ml-auto text-xs text-slate-500">
          {selectedCount} selected
        </div>
      </div>

      {/* Table */}
      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="w-10 px-3 py-2.5 text-left">
                  <input
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={toggleAllVisible}
                    className="rounded"
                  />
                </th>
                <th className="w-14 px-3 py-2.5 text-left">Image</th>
                <th className="px-3 py-2.5 text-left">Title / Brand / Model</th>
                <th className="px-3 py-2.5 text-left">Category</th>
                <th className="px-3 py-2.5 text-right">MRP</th>
                <th className="px-3 py-2.5 text-center">Variants</th>
                <th className="px-3 py-2.5 text-left">Status</th>
                <th className="w-20 px-3 py-2.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading && rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-3 py-10 text-center text-slate-500">
                    <Loader2 className="w-5 h-5 mx-auto animate-spin" />
                  </td>
                </tr>
              )}
              {!loading && rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-3 py-10 text-center text-slate-500">
                    No orphan products match this filter.
                  </td>
                </tr>
              )}
              {rows.map((r) => (
                <tr
                  key={r.id}
                  className={`border-b border-slate-100 last:border-0 ${
                    selected.has(r.id) ? "bg-blue-50/40" : ""
                  }`}
                >
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={selected.has(r.id)}
                      onChange={() => toggleRow(r.id)}
                      className="rounded"
                    />
                  </td>
                  <td className="px-3 py-2">
                    {r.thumbnail ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={r.thumbnail}
                        alt=""
                        className="w-10 h-10 rounded object-cover border border-slate-200"
                      />
                    ) : (
                      <div className="w-10 h-10 rounded bg-slate-100 border border-slate-200" />
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <Link
                      href={`/dashboard/products/edit/${r.id}`}
                      className="block group"
                      title="Open product to edit"
                    >
                      <div className="font-medium text-slate-900 truncate max-w-xs group-hover:text-blue-700 group-hover:underline">
                        {r.title || "(untitled)"}
                      </div>
                      <div className="text-xs text-slate-500">
                        {(r.brand || "—") + " · " + (r.modelNo || "—")}
                      </div>
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-600">
                    {r.category || "—"}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-700">
                    {r.mrp ? `₹${r.mrp}` : "—"}
                  </td>
                  <td className="px-3 py-2 text-center text-slate-700">
                    {r.variantCount}
                  </td>
                  <td className="px-3 py-2">
                    {r.validation.pushable ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 text-xs border border-emerald-200">
                        <CheckCircle2 className="w-3 h-3" />
                        Pushable
                      </span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {r.validation.reasons.map((reason) => (
                          <span
                            key={reason}
                            className="px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 text-[11px] border border-amber-200"
                            title={REASON_LABELS[reason]}
                          >
                            {REASON_LABELS[reason]}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Link
                      href={`/dashboard/products/edit/${r.id}`}
                      className="inline-flex items-center gap-1 px-2 py-1 text-xs text-slate-700 border border-slate-300 rounded hover:bg-slate-50"
                      title="Edit this product to fix missing fields"
                    >
                      <Edit2 className="w-3 h-3" />
                      Edit
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="flex items-center justify-between px-3 py-2 border-t border-slate-200 bg-slate-50">
          <span className="text-xs text-slate-600">
            Page {pagination.page} of {pagination.pages} · {pagination.total}{" "}
            item(s)
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => fetchOrphans(Math.max(1, pagination.page - 1))}
              disabled={pagination.page <= 1 || loading}
              className="p-1.5 rounded border border-slate-300 bg-white disabled:opacity-40"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button
              onClick={() =>
                fetchOrphans(Math.min(pagination.pages, pagination.page + 1))
              }
              disabled={pagination.page >= pagination.pages || loading}
              className="p-1.5 rounded border border-slate-300 bg-white disabled:opacity-40"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Hard-delete confirmation modal */}
      {hardDeleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5">
            <h3 className="text-lg font-bold text-red-700 flex items-center gap-2">
              <Trash2 className="w-5 h-5" />
              Permanently delete {selectedCount} product(s)?
            </h3>
            <p className="text-sm text-slate-600 mt-2">
              This cascades into their variants, images, stock, and sync logs.
              It cannot be undone. Only orphan products (never pushed to Shopify)
              will be affected.
            </p>
            <p className="text-sm text-slate-800 mt-3 mb-1">
              Type <code className="bg-slate-100 px-1 rounded">DELETE</code> to
              confirm:
            </p>
            <input
              type="text"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm"
              autoFocus
            />
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => {
                  setHardDeleteConfirm(false);
                  setConfirmText("");
                }}
                disabled={hardDeleteRunning}
                className="px-3 py-2 text-sm rounded-lg border border-slate-300 text-slate-700 bg-white hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                onClick={runHardDelete}
                disabled={confirmText !== "DELETE" || hardDeleteRunning}
                className="px-3 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 flex items-center gap-2"
              >
                {hardDeleteRunning && (
                  <Loader2 className="w-4 h-4 animate-spin" />
                )}
                Permanently delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ─── Push failure detail modal ─────────────────────
          Shows up automatically when a push run produces any FAILED
          rows. Re-openable via the "View N failures" link inside the
          banner. Each row shows the title, brand/model, the actual
          Shopify error message (verbatim from the SyncLog), and a
          direct Edit link so staff can fix the offending field. */}
      {failureModalOpen && (
        <div
          onClick={() => setFailureModalOpen(false)}
          className="fixed inset-0 z-50 bg-slate-900/40 flex items-start justify-center pt-[8vh] px-4"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="bg-white rounded-xl shadow-2xl border border-slate-200 w-full max-w-3xl max-h-[80vh] flex flex-col fade-in"
          >
            <div className="flex items-center justify-between p-4 border-b border-slate-200">
              <div>
                <h2 className="text-base font-semibold text-slate-900">
                  Push failures
                </h2>
                <p className="text-xs text-slate-500 mt-0.5">
                  {failedDetails.length} product
                  {failedDetails.length === 1 ? "" : "s"} couldn&apos;t reach
                  Shopify. Click the title or Edit to fix the underlying
                  field, then re-run the push.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setFailureModalOpen(false)}
                className="text-slate-400 hover:text-slate-700 text-xl leading-none w-8 h-8 flex items-center justify-center"
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              {failedDetails.length === 0 ? (
                <div className="p-8 text-center text-slate-500 text-sm">
                  No failures to show.
                </div>
              ) : (
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 border-b border-slate-200 sticky top-0">
                    <tr>
                      <th className="px-3 py-2 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">
                        Product
                      </th>
                      <th className="px-3 py-2 text-left text-xs font-semibold text-slate-600 uppercase tracking-wide">
                        Reason
                      </th>
                      <th className="px-3 py-2 text-right text-xs font-semibold text-slate-600 uppercase tracking-wide w-20">
                        Action
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {failedDetails.map((f) => (
                      <tr
                        key={f.productId}
                        className="border-b border-slate-100 last:border-0 align-top"
                      >
                        <td className="px-3 py-3 max-w-xs">
                          <Link
                            href={`/dashboard/products/edit/${f.productId}`}
                            className="font-medium text-slate-900 hover:text-blue-700 hover:underline block truncate"
                          >
                            {f.title || "(untitled)"}
                          </Link>
                          <div className="text-xs text-slate-500 mt-0.5">
                            {(f.brand || "—") + " · " + (f.modelNo || "—")}
                          </div>
                        </td>
                        <td className="px-3 py-3 text-xs text-red-700 bg-red-50/50 font-mono whitespace-pre-wrap break-words">
                          {f.message}
                        </td>
                        <td className="px-3 py-3 text-right">
                          <Link
                            href={`/dashboard/products/edit/${f.productId}`}
                            className="inline-flex items-center gap-1 px-2 py-1 text-xs text-slate-700 border border-slate-300 rounded hover:bg-slate-50"
                          >
                            <Edit2 className="w-3 h-3" />
                            Edit
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="p-3 border-t border-slate-200 bg-slate-50 flex items-center justify-between text-xs text-slate-500">
              <span>
                Shopify error messages are returned verbatim — see SyncLog
                for the same text.
              </span>
              <button
                type="button"
                onClick={() => setFailureModalOpen(false)}
                className="px-3 py-1.5 bg-white border border-slate-300 rounded-md text-slate-700 hover:bg-slate-50"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function KpiCard({
  label,
  value,
  loading,
  tone,
}: {
  label: string;
  value: number | string;
  loading: boolean;
  tone: "neutral" | "good" | "warn";
}) {
  const toneClasses =
    tone === "good"
      ? "text-emerald-700"
      : tone === "warn"
        ? "text-amber-700"
        : "text-slate-900";
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-4">
      <div className="text-xs font-medium text-slate-500 uppercase tracking-wider">
        {label}
      </div>
      <div className={`text-2xl font-bold mt-1 ${toneClasses}`}>
        {loading ? (
          <Loader2 className="w-5 h-5 animate-spin inline-block" />
        ) : (
          value
        )}
      </div>
    </div>
  );
}
