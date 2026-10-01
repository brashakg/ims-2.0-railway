import type {
  CatalogProductDoc,
  PromoteGap,
  UpdateCatalogProductPayload,
} from '../../../services/api/catalog';
import { CANONICAL_TO_PICKER, FE_CODE_TO_CANONICAL, getCategoryFields } from './categoryFields';
import type { ProductFormValues } from './formModel';
import type { ProductDoc } from './cloneMapping';
import { docTags, productToFormValues, str } from './cloneMapping';
import { inferCategoryCode } from './inferCategory';

// ============================================================================
// Full-page review editor (/catalog/add?review=<id>) — imported doc mapping
// ----------------------------------------------------------------------------
// The "Needs review — imported" queue holds `catalog_products` docs (no spine
// row yet). The full cataloguing page opens them via these helpers:
//   catalogDocToFormValues   doc -> ProductFormValues (open the form)
//   formValuesToCatalogUpdate  DIFF-ONLY PUT payload (save fixes)
//   validateReviewForm       lenient save gate (reviewers are mid-fix)
//   promoteGapsToFormErrors  dry-run gaps -> inline form errors + other list
// ============================================================================

/** Map an imported catalog_products doc into the Quick Add form-values shape.
 *  Category: canonical long form -> picker code via CANONICAL_TO_PICKER (with
 *  the synonym table as fallback so legacy spellings still resolve). Pricing
 *  prefers the nested `pricing` block and falls back to the top-level mirror
 *  — the SAME key-presence precedence as the backend's
 *  _promote_payload_from_doc, so what the form shows is what approval would
 *  use. Attribute mapping delegates to productToFormValues. */
export function catalogDocToFormValues(doc: CatalogProductDoc): ProductFormValues {
  // Attribute + top-level mirroring reuses the clone/edit mapper wholesale.
  const base = productToFormValues(doc as unknown as ProductDoc);

  // Canonical -> picker code. inferCategoryCode covers legacy spellings the
  // strict map doesn't ('SUNGLASSES', 'Contact Lenses', ...). '' when nothing
  // resolves, so the reviewer simply picks the category (nothing mis-filed).
  const rawCategory = str(doc.category).trim().toUpperCase().replace(/[-\s]+/g, '_');
  const category =
    CANONICAL_TO_PICKER[rawCategory] || inferCategoryCode(doc.category) || '';

  // Pricing precedence mirrors _promote_payload_from_doc: the nested key wins
  // WHEN PRESENT (even if null), else the top-level mirror.
  const pricing = (doc.pricing || {}) as Record<string, unknown>;
  const pick = (nestedKey: string, topLevel: unknown): string =>
    str(nestedKey in pricing ? pricing[nestedKey] : topLevel);

  // Images: fall back to the single image_url when the array is empty.
  const images = Array.isArray(doc.images)
    ? (doc.images as unknown[]).map((u) => str(u)).filter(Boolean)
    : [];
  const imageUrl = str(doc.image_url);

  return {
    ...base,
    category,
    mrp: pick('mrp', doc.mrp),
    offerPrice: pick('offer_price', doc.offer_price),
    costPrice: pick('cost_price', undefined) || base.costPrice || '',
    discountCategory: pick('discount_category', doc.discount_category) || '',
    images: images.length > 0 ? images : imageUrl ? [imageUrl] : [],
    // Review extras: display name (name wins, title fallback) + tags.
    name: str(doc.name) || str(doc.title),
    tags: docTags(doc),
    // Online flags are meaningless on an imported doc (BVI owns Shopify).
    shopifyTags: [],
    publishPOS: true,
  };
}

const _trimEq = (a: unknown, b: unknown): boolean =>
  String(a ?? '').trim() === String(b ?? '').trim();

/** SHARED trimmed attribute-diff predicate. Both formValuesToCatalogUpdate's
 *  per-key patch AND validateReviewForm's attrsTouched gate MUST use this —
 *  when they diverged (untrimmed gate vs trimmed diff), a whitespace-only
 *  touch armed the full dictionary check while the payload omitted attributes
 *  entirely, blocking a pricing-only save the server would have accepted.
 *  Exported for the unit test that pins the two callers together. */
export const attrChanged = (
  values: ProductFormValues,
  baseline: ProductFormValues,
  key: string
): boolean => !_trimEq(values.attributes?.[key], baseline.attributes?.[key]);

const _numOrUndef = (v: unknown): number | undefined => {
  const n = parseFloat(String(v ?? '').trim());
  return Number.isFinite(n) ? n : undefined;
};

/** DIFF-ONLY PUT payload builder for the ?review editor: every untouched
 *  block is OMITTED. Critically, hsn_code/gst_rate ride on plain diffing —
 *  review mode applies NO local HSN/GST overwrite on category change, so the
 *  pair only differs from the snapshot when the reviewer explicitly picked an
 *  HSN; an untouched pair is omitted and the server's category-change
 *  re-derivation wins. Same for pricing.discount_category (omitted unless
 *  explicitly picked/changed). `expectedUpdatedAt` (the doc's updated_at as
 *  loaded) rides along for optimistic concurrency whenever provided. */
export function formValuesToCatalogUpdate(
  values: ProductFormValues,
  snapshot: ProductFormValues,
  expectedUpdatedAt?: string
): UpdateCatalogProductPayload {
  const payload: UpdateCatalogProductPayload = {};

  // Category: send the CANONICAL long form the doc stores.
  if (values.category && values.category !== snapshot.category) {
    payload.category = FE_CODE_TO_CANONICAL[values.category] || values.category;
  }

  // Attributes: per-key trimmed diff (the server deep-merges the patch).
  const attrPatch: Record<string, unknown> = {};
  const attrKeys = new Set([
    ...Object.keys(values.attributes || {}),
    ...Object.keys(snapshot.attributes || {}),
  ]);
  attrKeys.forEach((k) => {
    if (attrChanged(values, snapshot, k)) {
      attrPatch[k] = String(values.attributes?.[k] ?? '').trim();
    }
  });
  if (Object.keys(attrPatch).length > 0) payload.attributes = attrPatch;

  if (!_trimEq(values.description, snapshot.description)) {
    payload.description = String(values.description ?? '').trim();
  }

  // Display name: only a non-empty change is sent (a cleared field must not
  // blank the stored name/title).
  const nameNow = String(values.name ?? '').trim();
  if (nameNow && !_trimEq(values.name, snapshot.name)) payload.name = nameNow;

  // Tags: order-insensitive compare; an explicit empty list clears all tags.
  const tagsNow = (values.tags ?? []).map((t) => t.trim()).filter(Boolean);
  const tagsBefore = (snapshot.tags ?? []).map((t) => t.trim()).filter(Boolean);
  if (
    tagsNow.length !== tagsBefore.length ||
    tagsNow.some((t) => !tagsBefore.includes(t))
  ) {
    payload.tags = tagsNow;
  }

  // HSN/GST: plain diff == "explicitly typed" (see docstring above).
  if (!_trimEq(values.hsnCode, snapshot.hsnCode) && String(values.hsnCode ?? '').trim()) {
    payload.hsn_code = String(values.hsnCode).trim();
  }
  if (!_trimEq(values.gstRate, snapshot.gstRate)) {
    const gst = _numOrUndef(values.gstRate);
    if (gst !== undefined) payload.gst_rate = gst;
  }

  if (!_trimEq(values.weight, snapshot.weight)) {
    const w = _numOrUndef(values.weight);
    if (w !== undefined) payload.weight = w;
  }

  // Pricing: all-optional patch — only the keys that changed, and only when
  // they parse to a finite number (blank/garbage is never sent as 0).
  const pricing: NonNullable<UpdateCatalogProductPayload['pricing']> = {};
  if (!_trimEq(values.mrp, snapshot.mrp)) {
    const v = _numOrUndef(values.mrp);
    if (v !== undefined) pricing.mrp = v;
  }
  if (!_trimEq(values.offerPrice, snapshot.offerPrice)) {
    const v = _numOrUndef(values.offerPrice);
    if (v !== undefined) pricing.offer_price = v;
  }
  if (!_trimEq(values.costPrice, snapshot.costPrice)) {
    const v = _numOrUndef(values.costPrice);
    if (v !== undefined) pricing.cost_price = v;
  }
  // discount_category: omitted unless explicitly picked (changed + non-empty).
  if (
    !_trimEq(values.discountCategory, snapshot.discountCategory) &&
    String(values.discountCategory ?? '').trim()
  ) {
    pricing.discount_category = String(values.discountCategory).trim();
  }
  if (Object.keys(pricing).length > 0) payload.pricing = pricing;

  // Images: whole-array replace, only when it actually changed.
  const imgsNow = values.images ?? [];
  const imgsBefore = snapshot.images ?? [];
  if (
    imgsNow.length !== imgsBefore.length ||
    imgsNow.some((u, i) => u !== imgsBefore[i])
  ) {
    payload.images = imgsNow;
  }

  if (expectedUpdatedAt && Object.keys(payload).length > 0) {
    payload.expected_updated_at = expectedUpdatedAt;
  }
  return payload;
}

/** LENIENT validation for review saves — reviewers are mid-fix by definition,
 *  so the create-strict required-field gate NEVER runs here. Only:
 *  - MRP >= offer price when BOTH are present (server blocks it anyway);
 *  - Catalog Dictionary mirror on FILLED select values (a blank stays blank),
 *    and ONLY when the save actually touches attributes: the server validates
 *    the merged attribute set on attribute patches but lets a pricing-only
 *    patch through, so a legacy bad value must never block a price fix
 *    (pass `baseline` = the loaded snapshot to get this diff-awareness).
 *  Approval readiness is the promote dry-run's job, not this gate's. */
export function validateReviewForm(
  values: ProductFormValues,
  baseline?: ProductFormValues
): Record<string, string> {
  const errors: Record<string, string> = {};

  const mrpNum = parseFloat(String(values.mrp ?? ''));
  const offerNum = parseFloat(String(values.offerPrice ?? ''));
  if (Number.isFinite(mrpNum) && Number.isFinite(offerNum) && offerNum > mrpNum) {
    errors.offer_price = 'Offer price cannot exceed MRP';
  }

  // Does this save touch attributes at all? (No baseline = assume yes.)
  let attrsTouched = !baseline;
  if (baseline) {
    const keys = new Set([
      ...Object.keys(values.attributes || {}),
      ...Object.keys(baseline.attributes || {}),
    ]);
    for (const k of keys) {
      // Same trimmed predicate as formValuesToCatalogUpdate's payload diff —
      // a whitespace-only touch must NOT arm the dictionary gate when the
      // payload would omit attributes entirely.
      if (attrChanged(values, baseline, k)) {
        attrsTouched = true;
        break;
      }
    }
  }

  if (values.category && attrsTouched) {
    getCategoryFields(values.category).forEach((field) => {
      const val = values.attributes?.[field.name];
      if (
        val &&
        field.type === 'select' &&
        Array.isArray(field.options) &&
        field.options.length > 0 &&
        !field.options.some((o) => o.toLowerCase() === String(val).trim().toLowerCase())
      ) {
        errors[field.name] =
          `"${val}" is not in the allowed list for ${field.label} — pick one from the dropdown (manage values in Settings)`;
      }
    });
  }

  return errors;
}

/** Map promote dry-run gaps onto the form's inline error keys. A gap lands
 *  inline only when the current category's form actually renders that field
 *  (its attribute inputs + the MRP/offer/category form-level inputs); every
 *  other gap (hsn/discount tier/unknown field/no field at all) goes to the
 *  `other` list so nothing is silently invisible. */
export function promoteGapsToFormErrors(
  gaps: PromoteGap[],
  category: string
): { errors: Record<string, string>; other: string[] } {
  const errors: Record<string, string> = {};
  const other: string[] = [];
  const rendered = new Set<string>([
    'category',
    'mrp',
    'offer_price',
    ...getCategoryFields(category).map((f) => f.name),
  ]);
  (gaps || []).forEach((g) => {
    const field = g.field || '';
    if (field && rendered.has(field) && !errors[field]) {
      errors[field] = g.message;
    } else {
      other.push(g.message);
    }
  });
  return { errors, other };
}

/** Concurrency-conflict recovery: overlay the reviewer's CHANGED fields (user
 *  vs their old snapshot) onto the freshly re-fetched values, so a "changed by
 *  someone else" 409 reloads the latest doc without losing their edits. */
export function overlayChangedFormValues(
  fresh: ProductFormValues,
  oldSnapshot: ProductFormValues,
  user: ProductFormValues
): ProductFormValues {
  const out: ProductFormValues = { ...fresh, attributes: { ...fresh.attributes } };

  Object.keys(user.attributes || {}).forEach((k) => {
    if (!_trimEq(user.attributes[k], oldSnapshot.attributes?.[k])) {
      out.attributes[k] = user.attributes[k];
    }
  });

  const scalarKeys = [
    'category', 'description', 'hsnCode', 'gstRate', 'weight',
    'mrp', 'offerPrice', 'costPrice', 'discountCategory', 'name',
  ] as const;
  scalarKeys.forEach((k) => {
    if (!_trimEq(user[k], oldSnapshot[k])) {
      (out as unknown as Record<string, unknown>)[k] = user[k];
    }
  });

  const arrEq = (a?: string[], b?: string[]) =>
    (a ?? []).length === (b ?? []).length && (a ?? []).every((v, i) => v === (b ?? [])[i]);
  if (!arrEq(user.tags, oldSnapshot.tags)) out.tags = user.tags;
  if (!arrEq(user.images, oldSnapshot.images)) out.images = user.images;

  return out;
}

/** Best-effort field for a PUT 422 dictionary error message, so it can render
 *  inline under the offending input instead of only as a toast. Returns null
 *  when the message doesn't name a field the current category renders. */
export function dictionaryErrorField(message: string, category: string): string | null {
  const msg = String(message || '');
  if (/brand '.+' is not in the brand master/i.test(msg)) return 'brand_name';
  if (/sub-brand '.+' is not defined/i.test(msg)) return 'subbrand';
  const m = /is not an allowed value for ([^.]+)\./i.exec(msg);
  if (m) {
    const label = m[1].trim().toLowerCase();
    const hit = getCategoryFields(category).find(
      (f) => f.label.toLowerCase() === label || f.name.toLowerCase() === label
    );
    if (hit) return hit.name;
  }
  return null;
}
