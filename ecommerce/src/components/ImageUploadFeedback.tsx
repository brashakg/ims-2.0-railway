"use client";

// UI primitives for surfacing image-upload failures.
//
// Per F1 / U8 — every failure must be visible via THREE channels:
//   1. Modal interruption (UploadErrorModal) — shown when ≥1 hard failure
//   2. Inline banner (UploadInlineBanner) — sticky to the upload area
//   3. Activity log (server-side, see /api/images logActivity calls)
//
// Both components are presentational only. The caller manages state
// (which errors exist, when to show the modal) so the same widgets can
// be used in the new-product form, edit form, V2 shell, and design queue.

import { AlertTriangle, X, AlertCircle, CheckCircle2 } from "lucide-react";
import type { UploadFail, UploadOk } from "@/lib/imageUpload";

/* ------------------------------------------------------------------
 * Modal — shown when there is at least one HARD failure (5xx, network,
 * file too large, corrupted file, etc.). The user must dismiss it
 * deliberately so they actually see what went wrong.
 * ------------------------------------------------------------------ */
export function UploadErrorModal({
  failures,
  successes,
  onClose,
}: {
  failures: UploadFail[];
  successes?: UploadOk[];
  onClose: () => void;
}) {
  if (failures.length === 0) return null;
  const hardCount = failures.filter((f) => f.level === "hard").length;
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(15,23,42,0.55)",
        zIndex: 200,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        role="alertdialog"
        aria-modal="true"
        style={{
          background: "white",
          borderRadius: 12,
          width: "100%",
          maxWidth: 520,
          padding: 22,
          boxShadow: "0 24px 64px rgba(0,0,0,0.25)",
        }}
      >
        <div className="flex items-start gap-3 mb-3">
          <AlertTriangle className="w-6 h-6 mt-0.5 shrink-0" style={{ color: "#dc2626" }} />
          <div className="flex-1 min-w-0">
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: "#0f172a" }}>
              {failures.length === 1 ? "Image upload failed" : `${failures.length} image uploads failed`}
            </h3>
            <p style={{ margin: "2px 0 0", fontSize: 13, color: "#64748b" }}>
              {hardCount === failures.length
                ? "These files were not uploaded. The product can still be saved without them."
                : `${hardCount} hard failure${hardCount === 1 ? "" : "s"} need attention. The rest may have uploaded.`}
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{ width: 28, height: 28, border: 0, background: "transparent", cursor: "pointer", color: "#64748b" }}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <ul style={{ listStyle: "none", padding: 0, margin: "0 0 12px", maxHeight: 240, overflowY: "auto" }}>
          {failures.map((f, idx) => (
            <li
              key={idx}
              style={{
                padding: "10px 12px",
                background: "#fef2f2",
                border: "1px solid #fecaca",
                borderRadius: 8,
                marginBottom: 6,
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 600, color: "#991b1b", wordBreak: "break-word" }}>
                {f.fileName}
              </div>
              <div style={{ fontSize: 12, color: "#7f1d1d", marginTop: 2 }}>{f.error}</div>
            </li>
          ))}
        </ul>

        {successes && successes.length > 0 && (
          <div style={{ padding: "8px 12px", background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 8, marginBottom: 12, fontSize: 12, color: "#166534" }}>
            <CheckCircle2 className="w-3.5 h-3.5 inline-block mr-1.5" style={{ verticalAlign: "-2px" }} />
            {successes.length} other image{successes.length === 1 ? "" : "s"} uploaded successfully.
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button
            onClick={onClose}
            style={{
              padding: "8px 16px",
              border: 0,
              background: "#0f172a",
              color: "white",
              borderRadius: 7,
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------
 * Inline banner — a persistent strip near the upload area that
 * summarises the most recent upload outcome. Stays visible until
 * dismissed or another upload happens. Use this in tandem with the
 * modal: the modal demands attention, the banner remains as a
 * reminder.
 * ------------------------------------------------------------------ */
export type BannerTone = "error" | "warning" | "success";

export function UploadInlineBanner({
  tone,
  message,
  detail,
  onDismiss,
}: {
  tone: BannerTone;
  message: string;
  detail?: string;
  onDismiss?: () => void;
}) {
  if (!message) return null;
  const palette: Record<BannerTone, { bg: string; border: string; fg: string; Icon: typeof AlertCircle }> = {
    error: { bg: "#fef2f2", border: "#fecaca", fg: "#991b1b", Icon: AlertCircle },
    warning: { bg: "#fffbeb", border: "#fde68a", fg: "#92400e", Icon: AlertTriangle },
    success: { bg: "#f0fdf4", border: "#bbf7d0", fg: "#166534", Icon: CheckCircle2 },
  };
  const c = palette[tone];
  const Icon = c.Icon;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      style={{
        display: "flex",
        gap: 10,
        alignItems: "flex-start",
        padding: "10px 14px",
        background: c.bg,
        border: `1px solid ${c.border}`,
        borderRadius: 8,
        marginBottom: 12,
      }}
    >
      <Icon className="w-4 h-4 mt-0.5 shrink-0" style={{ color: c.fg }} />
      <div className="flex-1 min-w-0">
        <div style={{ fontSize: 13, fontWeight: 600, color: c.fg }}>{message}</div>
        {detail && (
          <div style={{ fontSize: 12, color: c.fg, opacity: 0.85, marginTop: 2, wordBreak: "break-word" }}>
            {detail}
          </div>
        )}
      </div>
      {onDismiss && (
        <button
          onClick={onDismiss}
          aria-label="Dismiss"
          style={{ width: 20, height: 20, border: 0, background: "transparent", cursor: "pointer", color: c.fg, padding: 0 }}
        >
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------
 * Convenience: build a banner state from an upload run. Use like:
 *   const result = await uploadImages(files);
 *   const banner = bannerFromUploadResult(result);
 *   setBanner(banner);
 *   if (result.failed.some(f => f.level === "hard")) setModalOpen(true);
 * ------------------------------------------------------------------ */
export function bannerFromUploadResult(result: { uploaded: UploadOk[]; failed: UploadFail[] }):
  | { tone: BannerTone; message: string; detail?: string }
  | null {
  const { uploaded, failed } = result;
  if (failed.length === 0 && uploaded.length === 0) return null;
  if (failed.length === 0) {
    return {
      tone: "success",
      message: `Uploaded ${uploaded.length} image${uploaded.length === 1 ? "" : "s"}.`,
    };
  }
  if (uploaded.length === 0) {
    return {
      tone: "error",
      message:
        failed.length === 1
          ? `Failed to upload ${failed[0].fileName}.`
          : `Failed to upload ${failed.length} images.`,
      detail: failed.length === 1 ? failed[0].error : "Open the dialog for details.",
    };
  }
  return {
    tone: "warning",
    message: `Uploaded ${uploaded.length}, ${failed.length} failed.`,
    detail: failed.length === 1 ? failed[0].error : "Open the dialog for details.",
  };
}
