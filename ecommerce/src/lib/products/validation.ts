// Pure validation function for the Edit Product redesign.
// Drives the top-bar issue counter, the right-rail issues panel, and the
// publish gate. Adding a new rule = one line here; the UI rebuilds from
// the returned list.
//
// Ported from CLAUDE_CODE_HANDOFF.md §6.

import type { SectionId } from "./categorySpecs";

export interface Issue {
  /** Section to scroll to when the user clicks the issue. */
  section: SectionId;
  /** Form field key (used to focus the input after scroll). */
  field: string;
  /** Plain-English message — shown in the rail and the toast. */
  msg: string;
  /** Severity. "error" blocks publish; "warning" doesn't. */
  level?: "error" | "warning";
}

/** Anything we need to validate. Loose shape so it can accept the existing
 *  controlled form state directly (no rename refactor). */
export interface ValidatableProduct {
  category?: string | null;
  productName?: string | null;
  brand?: string | null;
  modelNo?: string | null;
  fullModelNo?: string | null;
  mrp?: string | number | null;
  uvProtection?: string | null;
  power?: string | null;
  impactRating?: string | null;
  baseCurve?: string | null;
  movement?: string | null;
  images?: Array<unknown> | null;
}

function isEmpty(v: unknown): boolean {
  if (v === null || v === undefined) return true;
  if (typeof v === "string") return v.trim() === "";
  if (typeof v === "number") return !Number.isFinite(v) || v <= 0;
  return false;
}

export function getIssues(p: ValidatableProduct): Issue[] {
  const out: Issue[] = [];
  const cat = (p.category || "").toUpperCase();

  // Universal — Identity
  if (isEmpty(p.productName) && isEmpty(p.fullModelNo) && isEmpty(p.modelNo)) {
    out.push({ section: "identity", field: "productName", msg: "Product name or model number is required" });
  }
  if (isEmpty(p.brand)) {
    out.push({ section: "identity", field: "brand", msg: "Brand is required" });
  }
  if (isEmpty(p.modelNo)) {
    out.push({ section: "identity", field: "modelNo", msg: "Model number is required" });
  }

  // Universal — Pricing
  const mrp = typeof p.mrp === "string" ? parseFloat(p.mrp) : p.mrp ?? 0;
  if (!mrp || mrp <= 0) {
    out.push({ section: "pricing", field: "mrp", msg: "MRP must be greater than 0" });
  }

  // Universal — Media
  if (!p.images || p.images.length === 0) {
    out.push({ section: "media", field: "images", msg: "At least one image needed", level: "warning" });
  }

  // Category-specific
  if (cat === "SUNGLASSES" && isEmpty(p.uvProtection)) {
    out.push({ section: "lens", field: "uvProtection", msg: "UV protection required for Sunglasses" });
  }
  if (cat === "READING_GLASSES" && isEmpty(p.power)) {
    out.push({ section: "power", field: "power", msg: "Power range required for Reading Glasses" });
  }
  if (cat === "SAFETY_GLASSES" && isEmpty(p.impactRating)) {
    out.push({ section: "safety", field: "impactRating", msg: "Impact rating required for Safety Glasses" });
  }
  if (cat === "CONTACT_LENSES" && isEmpty(p.baseCurve)) {
    out.push({ section: "contact", field: "baseCurve", msg: "Base curve required for Contact Lenses" });
  }
  if (cat === "WATCHES" && isEmpty(p.movement)) {
    out.push({ section: "watch", field: "movement", msg: "Movement required for Watches" });
  }

  return out;
}

export function blockingIssues(issues: Issue[]): Issue[] {
  return issues.filter((i) => i.level !== "warning");
}
