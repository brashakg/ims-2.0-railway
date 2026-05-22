// Smart-collection rule generator.
//
// Walks the PER_CATEGORY_PLAN for a given category and produces a list
// of CollectionPlan rows that the auto-generate API endpoint upserts
// into the Collection table.
//
// Properties this generator guarantees:
//   • Idempotent. Same (category, attributeValues) → identical handles
//     and rule conditions, in the same order. Safe to re-run.
//   • Tag-condition strings match exactly what the products' Shopify
//     tags will look like. We slug values via slugifyForRule(), which
//     mirrors slugifyTagValue() in src/lib/categoryAttributes.ts.
//   • Brand titles preserve the input casing/spacing (e.g. "Ray-Ban"),
//     handles are lower+hyphen.
//   • Product type is always pinned with a TYPE rule so a tag like
//     `brand_ray-ban` doesn't pull in non-eyewear stragglers that
//     happen to share the brand tag.
//
// The generator returns CollectionPlan[] only — it does NOT touch the
// database. Persistence is the caller's job (route.ts).

import {
  buildCollectionHandle,
  buildCollectionTitle,
  buildAttributeCollectionHandle,
  buildAttributeCollectionTitle,
  slugifyForRule,
} from "@/lib/collections/namingHelper";
import {
  PER_CATEGORY_PLAN,
  dimensionsForCategory,
  type PlanDimension,
  type PlanDimensionKey,
} from "@/lib/collections/perCategoryPlan";
import {
  themeSuffixForDimension,
  type ThemeSuffix,
} from "@/lib/collections/themeSuffixForCollection";
import { CATEGORIES } from "@/lib/categories";

/** A single Shopify smart-collection rule. */
export interface CollectionRule {
  /** Matches Shopify's CollectionRuleColumn enum subset we care about. */
  column: "TAG" | "TYPE" | "VENDOR" | "TITLE";
  relation: "EQUALS" | "CONTAINS" | "STARTS_WITH";
  condition: string;
}

/** What ruleGenerator emits per planned collection. */
export interface CollectionPlan {
  /** Handle (URL slug) — globally unique per Shopify shop. */
  handle: string;
  /** Display title (preserves brand casing). */
  title: string;
  /**
   * Primary tag rule condition (e.g. "brand_ray-ban"). Kept around as a
   * separate field so the API endpoint and audit log can show the
   * "matching tag" at a glance without re-parsing rules.
   */
  ruleCondition: string;
  /** Same idea — the column the primary rule operates on. */
  ruleColumn: "TAG" | "TYPE";
  /**
   * Full smart-collection rule set. For a brand × category collection,
   * this is two AND-rules: TAG = brand_ray-ban  AND  TYPE = Sunglasses.
   * The local DB stores this as JSON in Collection.rules.
   */
  rules: CollectionRule[];
  /** disjunctive=false → all rules must match (AND). */
  disjunctive: boolean;
  /** Top-level category this collection rolls up under. */
  categoryAnchor: string;
  /** "<source>:<value>" — exact format the schema doc demands. */
  autoSource: string;
  /** "BEST_SELLING" / "MANUAL" / etc. */
  sortOrder: "BEST_SELLING" | "MANUAL" | "ALPHA_ASC" | "ALPHA_DESC" | "PRICE_DESC" | "PRICE_ASC";
  /** templateSuffix for the Shopify theme. */
  themeSuffix: ThemeSuffix;
  /** Optional banner image — left undefined here, filled by merchant later. */
  bannerImage?: string;
  /** Optional 1-2-line summary. */
  shortDescription?: string;
  /** Lower number = sorted earlier in the menu / collection lists. */
  sortPriority: number;
  /** Which dimension produced this plan — useful for grouping / filters. */
  dimensionKey: PlanDimensionKey;
}

/**
 * Map a category enum key to the Shopify productType string. Right now
 * we use the human label (e.g. "Sunglasses", "Spectacles") because
 * that's what the existing pull/push code stamps on Product.category.
 *
 * Falls back to the raw enum key if the category isn't registered.
 */
function categoryToProductType(category: string): string {
  const def = CATEGORIES.find((c) => c.key === category);
  return def?.label ?? category;
}

/**
 * Default sortOrder for a planned collection — uses the dimension's
 * explicit sortOrder if set, otherwise BEST_SELLING for sale + best-
 * seller buckets and MANUAL everywhere else (per user C7).
 */
function defaultSortOrder(dim: PlanDimension): CollectionPlan["sortOrder"] {
  if (dim.sortOrder) return dim.sortOrder;
  if (dim.key === "sale" || dim.key === "bestSellers") return "BEST_SELLING";
  return "MANUAL";
}

/**
 * Build the autoSource value for a single planned collection. The
 * schema comment specifies the format strictly:
 *   "<source>:<value>"     for brand:Ray-Ban-style sources
 *   "<source>:<cat>:<val>" for shape:SUNGLASSES:Aviator (category-scoped)
 *
 * To keep idempotency air-tight, we always include the category in the
 * lineage key for category-scoped dimensions, and use the literal
 * dimension key as <source>.
 */
function buildAutoSource(
  category: string,
  dim: PlanDimension,
  rawValue: string | null
): string {
  if (rawValue === null) {
    // Single fixed collection (best-sellers / sale / featured).
    return `${dim.key}:${category}`;
  }
  return `${dim.key}:${category}:${rawValue}`;
}

/**
 * Two-rule rule-set: the primary attribute tag plus a TYPE pin so the
 * collection only ever contains products of the right category. Used
 * for every per-value collection.
 */
function buildAttributeRuleSet(
  tagCondition: string,
  productType: string
): CollectionRule[] {
  return [
    { column: "TAG", relation: "EQUALS", condition: tagCondition },
    { column: "TYPE", relation: "EQUALS", condition: productType },
  ];
}

/**
 * Single-rule rule-set for a fixed-tag collection (best-sellers, sale,
 * featured) where the product is tagged once and that's enough.
 *
 * The TYPE pin is still added so a "best-sellers-sunglasses" collection
 * doesn't accidentally include a "best-sellers" tagged spectacle.
 */
function buildFixedRuleSet(
  tagCondition: string,
  productType: string
): CollectionRule[] {
  return [
    { column: "TAG", relation: "EQUALS", condition: tagCondition },
    { column: "TYPE", relation: "EQUALS", condition: productType },
  ];
}

/**
 * Generate a single per-attribute-value CollectionPlan.
 * Pure helper — does not consult any external state.
 *
 *   value      raw attribute value as the user enters it (e.g. "Ray-Ban")
 *   index      the offset within the dimension's value list — controls
 *              sortPriority so the menu order is stable per re-run.
 *   dimIndex   the offset of the dimension in the category's plan —
 *              top-level priority bucket so brand collections all sort
 *              ahead of shape collections, etc.
 */
function buildPerValuePlan(
  category: string,
  dim: PlanDimension,
  value: string,
  index: number,
  dimIndex: number
): CollectionPlan {
  const slug = slugifyForRule(value);
  const tagCondition = `${dim.tagPrefix}_${slug}`;
  const productType = categoryToProductType(category);

  let handle: string;
  let title: string;
  if (dim.key === "brand") {
    handle = buildCollectionHandle(value, category);
    title = buildCollectionTitle(value, category);
  } else {
    handle = buildAttributeCollectionHandle(value, category);
    title = buildAttributeCollectionTitle(value, category);
  }

  return {
    handle,
    title,
    ruleCondition: tagCondition,
    ruleColumn: "TAG",
    rules: buildAttributeRuleSet(tagCondition, productType),
    disjunctive: false,
    categoryAnchor: category,
    autoSource: buildAutoSource(category, dim, value),
    sortOrder: defaultSortOrder(dim),
    themeSuffix: themeSuffixForDimension(dim.key),
    sortPriority: 100 + dimIndex * 50 + index,
    dimensionKey: dim.key,
  };
}

/**
 * Generate the single fixed CollectionPlan for a best-sellers / sale /
 * featured dimension.
 */
function buildFixedPlan(
  category: string,
  dim: PlanDimension,
  dimIndex: number
): CollectionPlan {
  const productType = categoryToProductType(category);
  // Tag the merchandiser stamps on a hand-picked product — e.g. "best-sellers"
  // or "sale" or "featured". Slugify so casing differences in the input
  // tagPrefix never produce a mismatched condition.
  const tagCondition = slugifyForRule(dim.tagPrefix);
  if (!dim.fixedHandle || !dim.fixedTitle) {
    throw new Error(
      `[ruleGenerator] dimension ${dim.key} on category ${category} ` +
        `is missing fixedHandle/fixedTitle`
    );
  }
  return {
    handle: dim.fixedHandle,
    title: dim.fixedTitle,
    ruleCondition: tagCondition,
    ruleColumn: "TAG",
    rules: buildFixedRuleSet(tagCondition, productType),
    disjunctive: false,
    categoryAnchor: category,
    autoSource: buildAutoSource(category, dim, null),
    sortOrder: defaultSortOrder(dim),
    themeSuffix: themeSuffixForDimension(dim.key),
    // Fixed collections sort first inside their tier, hence + 0 offset.
    sortPriority: 100 + dimIndex * 50,
    dimensionKey: dim.key,
  };
}

/**
 * Resolve the value list for a non-fixed dimension. Two sources:
 *   1. dim.fixedValues (defined on the plan).
 *   2. attributeValues[dim.key]  (passed in by the caller — typically
 *      pulled from AttributeOption rows for that attribute).
 *
 * If neither has values we return [] so the dimension is silently
 * skipped — the caller can't produce a per-brand collection if no
 * brands exist yet, and that's fine.
 */
function resolveDimensionValues(
  dim: PlanDimension,
  attributeValues: Record<string, string[]>
): string[] {
  if (dim.fixedValues && dim.fixedValues.length > 0) return dim.fixedValues;
  const live = attributeValues[dim.key];
  if (live && live.length > 0) {
    // De-dupe + drop blanks so we never produce a "  " collection.
    const seen = new Set<string>();
    const out: string[] = [];
    for (const v of live) {
      const trimmed = (v ?? "").trim();
      if (!trimmed) continue;
      if (seen.has(trimmed)) continue;
      seen.add(trimmed);
      out.push(trimmed);
    }
    return out;
  }
  return [];
}

/**
 * Main entry point.
 *
 *   category         "SUNGLASSES" / "SPECTACLES" / etc.
 *   attributeValues  { brand: ["Ray-Ban", "BOSS"], shape: ["Aviator"], ... }
 *
 * Returns a flat list of CollectionPlan ready to upsert. Order is
 * deterministic — caller can rely on sortPriority for menu placement.
 */
export function generateCollectionsForCategory(
  category: string,
  attributeValues: Record<string, string[]> = {}
): CollectionPlan[] {
  const dims = dimensionsForCategory(category);
  if (dims.length === 0) return [];

  const out: CollectionPlan[] = [];

  for (let dimIndex = 0; dimIndex < dims.length; dimIndex++) {
    const dim = dims[dimIndex];

    // Fixed-collection dimensions: best-sellers / sale / featured.
    // We treat featured the same way — Smartglasses gets a single
    // hand-curated bucket regardless of fixedValues.
    if (
      (dim.key === "bestSellers" || dim.key === "sale" || dim.key === "featured") &&
      dim.fixedHandle &&
      dim.fixedTitle
    ) {
      out.push(buildFixedPlan(category, dim, dimIndex));
      continue;
    }

    // Per-value expansion.
    const values = resolveDimensionValues(dim, attributeValues);
    for (let i = 0; i < values.length; i++) {
      out.push(buildPerValuePlan(category, dim, values[i], i, dimIndex));
    }
  }

  return out;
}

/**
 * Convenience wrapper that runs generateCollectionsForCategory across
 * every category that has a non-empty plan. Used by the API endpoint
 * when no `category` filter is passed in the body.
 *
 *   attributeValuesByCategory  per-category map of dimension → values.
 *                              Pass empty record (or omit) to only get
 *                              the fixed collections (best-sellers /
 *                              sale / featured / pre-enumerated).
 */
export function generateCollectionsForAllCategories(
  attributeValuesByCategory: Record<string, Record<string, string[]>> = {}
): CollectionPlan[] {
  const out: CollectionPlan[] = [];
  for (const cat of Object.keys(PER_CATEGORY_PLAN)) {
    const attrs = attributeValuesByCategory[cat] ?? {};
    const plans = generateCollectionsForCategory(cat, attrs);
    out.push(...plans);
  }
  return out;
}
