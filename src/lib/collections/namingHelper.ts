// Naming helpers for auto-generated collections.
//
// Per the user's C5 answer:
//  • HANDLE  → `{brand}-{category}` lowercased + hyphenated
//             e.g.  "Ray-Ban" + "SUNGLASSES"  →  "ray-ban-sunglasses"
//  • TITLE   → preserves the brand's exact spacing/casing, with the
//             SEO noun appended  e.g.  "Ray-Ban Sunglass"
//
// IMPORTANT: slugifyForRule() must mirror slugifyTagValue() in
// src/lib/categoryAttributes.ts EXACTLY — collections built here use
// the slugified form as the rule condition (TAG = "<prefix>_<slug>"),
// and that has to match what tagsForProductAttributes() emits when a
// product is saved. Any drift will silently produce empty collections.
//
// We deliberately do NOT import slugifyTagValue from categoryAttributes
// to avoid a cyclic dependency at the lib level — instead we re-implement
// it here byte-for-byte and keep the two definitions in lock-step. There
// is a small unit test in __tests__ to enforce that.

import { CATEGORIES } from "@/lib/categories";

/**
 * Lowercase + hyphenate a value for use in a Shopify TAG condition.
 *
 * MUST match src/lib/categoryAttributes.ts → slugifyTagValue() byte-for-byte.
 *   "Ray-Ban"          → "ray-ban"
 *   "BOSS 1234"        → "boss-1234"
 *   "Rose Gold"        → "rose-gold"
 *   "  spaces  "       → "spaces"
 *   "Foo & Bar (v2)"   → "foo-bar-v2"
 */
export function slugifyForRule(v: unknown): string {
  if (v === null || v === undefined) return "";
  return String(v)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * Map a category enum key to the lowercased category segment used in
 * a collection handle. Uses the human label from CATEGORIES so we get
 * "sunglasses" / "spectacles" / "clip-on-frames" rather than the
 * ALL_CAPS_WITH_UNDERSCORES enum value.
 */
export function categoryHandleSegment(category: string): string {
  const def = CATEGORIES.find((c) => c.key === category);
  // If the category is unknown (legacy values etc.) fall back to slugifying
  // the raw enum value so we still return *something* idempotent.
  const source = def?.label ?? category;
  return slugifyForRule(source);
}

/**
 * Map a category enum key to its singular SEO noun, used as the suffix
 * in an auto-generated collection title. Falls back to a Title-cased
 * version of the enum key if the category isn't registered.
 */
export function categoryNounForTitle(category: string): string {
  const def = CATEGORIES.find((c) => c.key === category);
  if (def) return def.seoNoun;
  return category
    .split("_")
    .map((seg) => seg.charAt(0) + seg.slice(1).toLowerCase())
    .join(" ");
}

/**
 * Build the URL handle for a per-brand collection.
 *   buildCollectionHandle("Ray-Ban", "SUNGLASSES")  →  "ray-ban-sunglasses"
 *   buildCollectionHandle("BOSS",   "SPECTACLES")   →  "boss-spectacles"
 *
 * Empty / missing inputs are dropped so we never produce a leading or
 * trailing hyphen.
 */
export function buildCollectionHandle(brand: string, category: string): string {
  const parts = [slugifyForRule(brand), categoryHandleSegment(category)].filter(
    (s) => s.length > 0
  );
  return parts.join("-");
}

/**
 * Build the human-readable title for a per-brand collection.
 *   buildCollectionTitle("Ray-Ban", "SUNGLASSES")  →  "Ray-Ban Sunglass"
 *   buildCollectionTitle("BOSS",   "SPECTACLES")  →  "BOSS Optical Frame"
 *
 * Brand casing is preserved exactly. The category's singular SEO noun is
 * appended (e.g. "Sunglass", "Optical Frame"). The leading brand string is
 * trim()'d but otherwise untouched.
 */
export function buildCollectionTitle(brand: string, category: string): string {
  const brandTrim = (brand ?? "").trim();
  const noun = categoryNounForTitle(category);
  if (!brandTrim) return noun;
  if (!noun) return brandTrim;
  return `${brandTrim} ${noun}`;
}

/**
 * Build a generic "{value} {categoryNoun}" title — used by dimensions
 * other than brand (shape, gender, material, accessory type, etc.).
 *
 *   buildAttributeCollectionTitle("Aviator",   "SUNGLASSES")  → "Aviator Sunglass"
 *   buildAttributeCollectionTitle("Rose Gold", "WATCHES")     → "Rose Gold Watch"
 *
 * The attribute value is preserved verbatim; only the category noun is
 * standardised.
 */
export function buildAttributeCollectionTitle(
  value: string,
  category: string
): string {
  const v = (value ?? "").trim();
  const noun = categoryNounForTitle(category);
  if (!v) return noun;
  if (!noun) return v;
  return `${v} ${noun}`;
}

/**
 * Build the URL handle for a non-brand attribute collection.
 *   buildAttributeCollectionHandle("aviator", "SUNGLASSES")
 *     → "aviator-sunglasses"
 *
 * Uses the same lower+hyphen rule as buildCollectionHandle.
 */
export function buildAttributeCollectionHandle(
  value: string,
  category: string
): string {
  const parts = [slugifyForRule(value), categoryHandleSegment(category)].filter(
    (s) => s.length > 0
  );
  return parts.join("-");
}
