// Shared image upload utility used by every page that uploads product
// images (new product, edit V1, edit V2, design queue). Replaces the
// inline duplicates that all read `data.url` (wrong) instead of
// `data.data.urls.original` (right) — that bug silently dropped
// uploads on the new-product form and threw on the design queue.
//
// Per F1 / U8 — every failure is surfaced via:
//   • a modal interruption (caller-driven, see ImageUploadFeedback.tsx)
//   • an inline banner near the upload area
//   • an entry in the activity log (server-side, via /api/images itself)
//
// Per the user's audit (Q1 = yes), this is the canonical implementation.

export interface UploadOk {
  success: true;
  url: string;
  fileName?: string;
  storage?: "shopify_cdn" | "local";
}

export interface UploadFail {
  success: false;
  fileName: string;
  error: string;
  /** "hard" → modal interrupt; "soft" → inline banner only. */
  level: "hard" | "soft";
}

export type UploadResult = UploadOk | UploadFail;

const MAX_PRE_COMPRESS_BYTES = 10 * 1024 * 1024; // 10 MB hard cap before compression
const COMPRESS_TARGET_MB = 4.5;
const COMPRESS_MAX_DIM = 2048;

/** Single-file upload. Returns success or a structured failure. */
export async function uploadImage(file: File): Promise<UploadResult> {
  // Hard cap check — too large to even attempt compression
  if (file.size > MAX_PRE_COMPRESS_BYTES) {
    return {
      success: false,
      fileName: file.name,
      error: `File is ${(file.size / 1024 / 1024).toFixed(1)} MB. Maximum 10 MB per file before compression.`,
      level: "hard",
    };
  }
  if (!file.type.startsWith("image/")) {
    return {
      success: false,
      fileName: file.name,
      error: `${file.name} is not an image (${file.type || "unknown type"}).`,
      level: "hard",
    };
  }

  let compressed: File;
  try {
    compressed = await compressIfNeeded(file);
  } catch (e) {
    return {
      success: false,
      fileName: file.name,
      error: `Could not read this image. It may be corrupted. (${e instanceof Error ? e.message : "unknown"})`,
      level: "hard",
    };
  }

  let res: Response;
  try {
    const fd = new FormData();
    fd.append("file", compressed);
    res = await fetch("/api/images", { method: "POST", body: fd });
  } catch (e) {
    return {
      success: false,
      fileName: file.name,
      error: `Network error uploading ${file.name}. Check your connection and try again.`,
      level: "hard",
    };
  }

  let body: Record<string, unknown>;
  try {
    body = await res.json();
  } catch {
    return {
      success: false,
      fileName: file.name,
      error: `Server returned an invalid response (HTTP ${res.status}).`,
      level: "hard",
    };
  }

  if (!res.ok || (body && body.success === false)) {
    const errorMsg =
      (body && (body.error as string | undefined)) ||
      (body && (body.message as string | undefined)) ||
      `HTTP ${res.status}`;
    return {
      success: false,
      fileName: file.name,
      error: errorMsg,
      level: res.status >= 500 ? "hard" : "soft",
    };
  }

  // The /api/images success shape is:
  //   { success: true, data: { fileName, urls: { original, processed? }, ... } }
  // Tolerate older shapes too (defensive).
  const data = (body && (body.data as Record<string, unknown>)) || {};
  const urls = (data && (data.urls as Record<string, string>)) || undefined;
  const url =
    (urls && (urls.processed || urls.original)) ||
    (data && (data.url as string | undefined)) ||
    (body && (body.url as string | undefined));

  if (!url) {
    return {
      success: false,
      fileName: file.name,
      error: "Server reported success but no image URL was returned.",
      level: "hard",
    };
  }

  return {
    success: true,
    url,
    fileName: (data.fileName as string | undefined) || file.name,
    storage: (data.storage as "shopify_cdn" | "local" | undefined) || undefined,
  };
}

/** Multi-file upload helper. Returns aggregated successes + failures
 *  so the caller can surface them in one go (modal lists all failures). */
export async function uploadImages(files: File[]): Promise<{
  uploaded: UploadOk[];
  failed: UploadFail[];
}> {
  const uploaded: UploadOk[] = [];
  const failed: UploadFail[] = [];
  for (const file of files) {
    // Sequential to avoid hammering the Shopify staged-upload endpoint.
    const r = await uploadImage(file);
    if (r.success) uploaded.push(r);
    else failed.push(r);
  }
  return { uploaded, failed };
}

/** Returns the file unchanged if already small, otherwise canvas-resizes
 *  and re-encodes to JPEG at 0.85 quality to fit `COMPRESS_TARGET_MB`. */
async function compressIfNeeded(file: File): Promise<File> {
  if (file.size <= COMPRESS_TARGET_MB * 1024 * 1024) return file;

  return new Promise((resolve, reject) => {
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      reject(new Error("Canvas not supported"));
      return;
    }
    const img = new Image();
    const objectUrl = URL.createObjectURL(file);
    img.onload = () => {
      let { width, height } = img;
      if (width > COMPRESS_MAX_DIM || height > COMPRESS_MAX_DIM) {
        const ratio = Math.min(COMPRESS_MAX_DIM / width, COMPRESS_MAX_DIM / height);
        width = Math.round(width * ratio);
        height = Math.round(height * ratio);
      }
      canvas.width = width;
      canvas.height = height;
      ctx.drawImage(img, 0, 0, width, height);
      canvas.toBlob(
        (blob) => {
          URL.revokeObjectURL(objectUrl);
          if (!blob) {
            resolve(file);
            return;
          }
          const out = new File([blob], file.name.replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg" });
          resolve(out);
        },
        "image/jpeg",
        0.85
      );
    };
    img.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("Failed to load image into canvas"));
    };
    img.src = objectUrl;
  });
}
