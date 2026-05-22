// Maps an auto-generated collection to a Shopify theme template suffix.
//
// Per the user's C8 answer, we route to one of five custom collection
// templates depending on what the collection represents:
//
//   brand    →  collection.brand.liquid     — header, filters, brand banner
//   gender   →  collection.gender.liquid    — gender-specific layout, hero
//   shape    →  collection.shape.liquid     — shape illustrations + filters
//   sale     →  collection.sale.liquid      — sale price callouts + countdown
//   default  →  collection.liquid           — fallback for everything else
//
// We pick the suffix off the dimension key of the PlanDimension that
// produced the collection. Sale and best-sellers are special-cased to
// the sale template (they share the same merchandising treatment per
// C8). Everything else — frame material, accessory type, vision type,
// blue-light tier, wear schedule, etc. — falls through to the default
// template, which keeps the suffix list short and theme-maintenance
// simple.

import type { PlanDimensionKey } from "@/lib/collections/perCategoryPlan";

export type ThemeSuffix = "brand" | "gender" | "shape" | "sale" | "default";

/** All known theme suffixes — exported for the validation in route.ts. */
export const THEME_SUFFIXES: ThemeSuffix[] = [
  "brand",
  "gender",
  "shape",
  "sale",
  "default",
];

/**
 * Return the templateSuffix string the Shopify theme expects for a
 * collection produced by the given PlanDimensionKey. Pure, total —
 * unknown keys deliberately collapse to "default" rather than throwing
 * so callers can stay compact.
 */
export function themeSuffixForDimension(key: PlanDimensionKey): ThemeSuffix {
  switch (key) {
    case "brand":
      return "brand";
    case "gender":
      return "gender";
    case "shape":
      return "shape";
    case "sale":
    case "bestSellers":
      // Best-sellers gets the same sale-template merchandising (per C8 follow-up:
      // both use the price-callout / urgency layout).
      return "sale";

    case "frameMaterial":
    case "power":
    case "visionType":
    case "blueLightProtection":
    case "antiGlareCoating":
    case "useCase":
    case "wearSchedule":
    case "contactLensMaterial":
    case "lensType":
    case "accessoryType":
    case "attachment":
    case "colorFamily":
    case "featured":
      return "default";
  }
}
