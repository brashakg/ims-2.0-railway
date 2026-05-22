export type OrphanValidationReason =
  | "missing_brand"
  | "missing_modelNo"
  | "missing_title"
  | "no_price"
  | "no_variants_no_images";

export interface OrphanValidation {
  pushable: boolean;
  reasons: OrphanValidationReason[];
}

interface OrphanVariantLike {
  mrp?: number | null;
  discountedPrice?: number | null;
}

interface OrphanProductLike {
  brand?: string | null;
  modelNo?: string | null;
  title?: string | null;
  mrp?: number | null;
  variants?: OrphanVariantLike[];
  images?: unknown[];
}

export function validateOrphanForPush(product: OrphanProductLike): OrphanValidation {
  const reasons: OrphanValidationReason[] = [];

  if (!product.brand || !product.brand.trim()) {
    reasons.push("missing_brand");
  }

  if (!product.modelNo || !product.modelNo.trim()) {
    reasons.push("missing_modelNo");
  }

  const derivableTitle =
    (product.title && product.title.trim()) ||
    ((product.brand && product.modelNo) ? `${product.brand} ${product.modelNo}`.trim() : "");
  if (!derivableTitle) {
    reasons.push("missing_title");
  }

  const baseHasPrice = typeof product.mrp === "number" && product.mrp > 0;
  const anyVariantHasPrice = (product.variants || []).some(
    (v) => (typeof v.mrp === "number" && v.mrp > 0) ||
           (typeof v.discountedPrice === "number" && v.discountedPrice > 0)
  );
  if (!baseHasPrice && !anyVariantHasPrice) {
    reasons.push("no_price");
  }

  const hasVariants = (product.variants?.length || 0) > 0;
  const hasImages = (product.images?.length || 0) > 0;
  if (!hasVariants && !hasImages) {
    reasons.push("no_variants_no_images");
  }

  return { pushable: reasons.length === 0, reasons };
}

export const ORPHAN_REASON_LABELS: Record<OrphanValidationReason, string> = {
  missing_brand: "Missing brand",
  missing_modelNo: "Missing model number",
  missing_title: "Missing title (and brand+modelNo can't produce one)",
  no_price: "No price (MRP is 0 on product and all variants)",
  no_variants_no_images: "No variants and no images",
};
