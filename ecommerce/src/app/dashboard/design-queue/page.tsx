"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Loader2,
  Upload,
  CheckCircle2,
  AlertTriangle,
  Download,
  Palette,
  Search,
  RefreshCw,
} from "lucide-react";
import { uploadImages, type UploadFail, type UploadOk } from "@/lib/imageUpload";
import { UploadErrorModal } from "@/components/ImageUploadFeedback";

interface RawImage {
  id: string;
  url: string;
  originalUrl: string | null;
  position: number;
}

interface DesignQueueRow {
  id: string;
  title: string | null;
  brand: string | null;
  modelNo: string | null;
  category: string | null;
  shopifyProductId: string | null;
  createdAt: string;
  rawImages: RawImage[];
  editedImages: Array<{ id: string; url: string }>;
}

export default function DesignQueuePage() {
  const [rows, setRows] = useState<DesignQueueRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [banner, setBanner] = useState<
    { tone: "success" | "error"; text: string } | null
  >(null);

  // Per-product upload state
  const [uploadingFor, setUploadingFor] = useState<string | null>(null);
  const [publishingFor, setPublishingFor] = useState<string | null>(null);
  const [stagedEdits, setStagedEdits] = useState<Record<string, string[]>>({});
  // F1/U8 — modal for hard upload failures
  const [uploadFailures, setUploadFailures] = useState<UploadFail[]>([]);
  const [uploadSuccesses, setUploadSuccesses] = useState<UploadOk[]>([]);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);

  const fetchQueue = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page),
        limit: "20",
        ...(search && { search }),
      });
      const res = await fetch(`/api/products/design-queue?${params}`);
      const data = await res.json();
      if (!res.ok || !data.success) {
        setBanner({
          tone: "error",
          text: data.error || "Failed to load design queue",
        });
      } else {
        setRows(data.data);
        setTotal(data.pagination.total);
        setTotalPages(data.pagination.pages);
      }
    } catch (e) {
      setBanner({
        tone: "error",
        text: e instanceof Error ? e.message : "Failed to load design queue",
      });
    } finally {
      setLoading(false);
    }
  }, [page, search]);

  useEffect(() => {
    fetchQueue();
  }, [fetchQueue]);

  const handleEditedUpload = async (
    productId: string,
    files: FileList | null
  ) => {
    if (!files || files.length === 0) return;
    setUploadingFor(productId);
    setBanner(null);
    try {
      const result = await uploadImages(Array.from(files));
      const urls = result.uploaded.map((u) => u.url);
      if (urls.length > 0) {
        setStagedEdits((prev) => ({
          ...prev,
          [productId]: [...(prev[productId] || []), ...urls],
        }));
      }
      // Banner — keeps the tone-binary success/error contract this page expects.
      if (result.failed.length === 0) {
        setBanner({
          tone: "success",
          text: `Uploaded ${urls.length} edited image(s). Review and click "Publish" to push to Shopify.`,
        });
      } else {
        setBanner({
          tone: "error",
          text:
            result.failed.length === 1
              ? `Failed: ${result.failed[0].fileName} — ${result.failed[0].error}`
              : `Failed ${result.failed.length} of ${result.failed.length + urls.length} images. Open the dialog for details.`,
        });
      }
      // Modal for hard failures (F1/U8).
      const hardFailures = result.failed.filter((f) => f.level === "hard");
      if (hardFailures.length > 0) {
        setUploadFailures(result.failed);
        setUploadSuccesses(result.uploaded);
        setUploadModalOpen(true);
      }
    } finally {
      setUploadingFor(null);
    }
  };

  const handlePublish = async (productId: string) => {
    const staged = stagedEdits[productId] || [];
    if (staged.length === 0) {
      setBanner({
        tone: "error",
        text: "No edited images staged yet — upload at least one first.",
      });
      return;
    }
    setPublishingFor(productId);
    setBanner(null);
    try {
      const res = await fetch(`/api/products/${productId}/design-upload`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          images: staged.map((url) => ({ url })),
          removeRaw: true,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        setBanner({
          tone: "error",
          text: data.error || "Publish failed",
        });
        return;
      }
      setBanner({
        tone: "success",
        text: data.message || "Published.",
      });
      setStagedEdits((prev) => {
        const next = { ...prev };
        delete next[productId];
        return next;
      });
      await fetchQueue();
    } catch (e) {
      setBanner({
        tone: "error",
        text: e instanceof Error ? e.message : "Publish failed",
      });
    } finally {
      setPublishingFor(null);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 p-4 sm:p-6">
      <div className="max-w-6xl mx-auto">
        <div className="flex items-start justify-between gap-4 mb-6 flex-wrap">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold text-slate-900 flex items-center gap-2">
              <Palette className="w-6 h-6 text-purple-600" />
              Design Queue
            </h1>
            <p className="text-sm text-slate-600 mt-1">
              Products awaiting edited images before they go live on Shopify.
            </p>
          </div>
          <button
            onClick={() => fetchQueue()}
            disabled={loading}
            className="flex items-center gap-2 px-3 py-2 text-sm border border-slate-300 bg-white text-slate-700 rounded-lg hover:bg-slate-50 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>

        {banner && (
          <div
            className={`mb-4 p-3 rounded-lg border text-sm ${
              banner.tone === "success"
                ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                : "bg-red-50 border-red-200 text-red-800"
            }`}
          >
            <div className="flex items-start gap-2">
              {banner.tone === "success" ? (
                <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" />
              ) : (
                <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              )}
              <span>{banner.text}</span>
            </div>
          </div>
        )}

        {/* KPI + search */}
        <div className="flex items-center gap-3 mb-4 flex-wrap">
          <div className="bg-white border border-slate-200 rounded-lg px-4 py-2 text-sm">
            <span className="text-slate-500">Awaiting design:</span>{" "}
            <b className="text-purple-700">{total}</b>
          </div>
          <form
            className="flex-1 min-w-[220px] relative"
            onSubmit={(e) => {
              e.preventDefault();
              setPage(1);
              fetchQueue();
            }}
          >
            <Search className="absolute left-3 top-2.5 w-4 h-4 text-slate-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by title / brand / model…"
              className="w-full pl-10 pr-3 py-2 text-sm border border-slate-300 rounded-lg"
            />
          </form>
        </div>

        {/* Queue list */}
        {loading && rows.length === 0 ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-purple-600" />
          </div>
        ) : rows.length === 0 ? (
          <div className="bg-white border border-slate-200 rounded-lg p-8 text-center text-slate-500">
            <Palette className="w-10 h-10 mx-auto text-slate-300 mb-3" />
            Queue is empty. Nothing awaiting design right now.
          </div>
        ) : (
          <div className="space-y-5">
            {rows.map((row) => {
              const staged = stagedEdits[row.id] || [];
              const isUploading = uploadingFor === row.id;
              const isPublishing = publishingFor === row.id;

              return (
                <div
                  key={row.id}
                  className="bg-white border border-slate-200 rounded-lg overflow-hidden"
                >
                  {/* Header */}
                  <div className="px-4 py-3 border-b border-slate-200 bg-slate-50 flex items-start justify-between gap-4 flex-wrap">
                    <div className="min-w-0">
                      <h2 className="font-semibold text-slate-900 truncate">
                        {row.title || "(untitled)"}
                      </h2>
                      <p className="text-xs text-slate-500">
                        {row.brand || "—"} · {row.modelNo || "—"} ·{" "}
                        {row.category || "—"}
                        {row.shopifyProductId && (
                          <> · <span className="text-emerald-600">On Shopify (draft)</span></>
                        )}
                      </p>
                    </div>
                    <div className="text-xs text-slate-500">
                      Submitted{" "}
                      {new Date(row.createdAt).toLocaleDateString(undefined, {
                        year: "numeric",
                        month: "short",
                        day: "numeric",
                      })}
                    </div>
                  </div>

                  <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-5">
                    {/* Raw images (from cataloger) */}
                    <div>
                      <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wider mb-2">
                        Raw from cataloger ({row.rawImages.length})
                      </h3>
                      {row.rawImages.length === 0 ? (
                        <p className="text-sm text-slate-500">
                          No raw images uploaded yet.
                        </p>
                      ) : (
                        <div className="grid grid-cols-3 gap-2">
                          {row.rawImages.map((img) => (
                            <a
                              key={img.id}
                              href={img.originalUrl || img.url}
                              target="_blank"
                              rel="noreferrer"
                              download
                              className="relative group block aspect-square rounded overflow-hidden border border-slate-200"
                              title="Open / download raw image"
                            >
                              {/* eslint-disable-next-line @next/next/no-img-element */}
                              <img
                                src={img.url}
                                alt="raw"
                                className="w-full h-full object-cover"
                              />
                              <div className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-colors flex items-center justify-center">
                                <Download className="w-5 h-5 text-white opacity-0 group-hover:opacity-100 transition-opacity" />
                              </div>
                            </a>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Edited images (staged + existing) */}
                    <div>
                      <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wider mb-2">
                        Edited {staged.length > 0 ? `(staged ${staged.length})` : ""}
                      </h3>
                      {staged.length > 0 ? (
                        <div className="grid grid-cols-3 gap-2 mb-3">
                          {staged.map((url, i) => (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              key={`${url}-${i}`}
                              src={url}
                              alt={`edited ${i + 1}`}
                              className="aspect-square object-cover rounded border border-emerald-200"
                            />
                          ))}
                        </div>
                      ) : (
                        <p className="text-sm text-slate-500 mb-3">
                          No edited images yet. Upload them below.
                        </p>
                      )}
                      <label className="flex items-center gap-2 px-3 py-2 text-sm border border-slate-300 rounded-lg bg-white hover:bg-slate-50 cursor-pointer w-fit">
                        {isUploading ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Upload className="w-4 h-4" />
                        )}
                        {isUploading ? "Uploading…" : "Upload edited images"}
                        <input
                          type="file"
                          multiple
                          accept="image/*"
                          className="hidden"
                          disabled={isUploading || isPublishing}
                          onChange={(e) =>
                            handleEditedUpload(row.id, e.target.files)
                          }
                        />
                      </label>
                    </div>
                  </div>

                  {/* Publish bar */}
                  <div className="border-t border-slate-200 bg-slate-50 px-4 py-3 flex items-center justify-between gap-3 flex-wrap">
                    <div className="text-xs text-slate-500">
                      Publishing pushes the edited images to Shopify and
                      activates the listing.
                    </div>
                    <button
                      onClick={() => handlePublish(row.id)}
                      disabled={
                        staged.length === 0 || isPublishing || isUploading
                      }
                      className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-purple-600 text-white hover:bg-purple-700 disabled:opacity-50"
                    >
                      {isPublishing ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <CheckCircle2 className="w-4 h-4" />
                      )}
                      Publish edited images
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Pagination */}
        {rows.length > 0 && (
          <div className="flex items-center justify-between mt-4 text-sm text-slate-600">
            <span>
              Page {page} of {totalPages} · {total} item(s)
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage(Math.max(1, page - 1))}
                disabled={page <= 1 || loading}
                className="px-3 py-1.5 border border-slate-300 bg-white rounded disabled:opacity-40"
              >
                Previous
              </button>
              <button
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                disabled={page >= totalPages || loading}
                className="px-3 py-1.5 border border-slate-300 bg-white rounded disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
      {uploadModalOpen && (
        <UploadErrorModal
          failures={uploadFailures}
          successes={uploadSuccesses}
          onClose={() => setUploadModalOpen(false)}
        />
      )}
    </div>
  );
}
