// ============================================================================
// IMS 2.0 - Product Add: shared field config + payload mapping
// ============================================================================
// Single source of truth for the product-add CATEGORIES list, the
// category-specific field config, and the payload-building logic. Imported by
// BOTH the fast one-screen "Quick Add" (QuickAddPage) and the step-by-step
// "Guided Add" wizard (AddProductPage) so the two modes stay byte-identical in
// what fields they collect and what payload they POST. Do NOT redefine these
// fields elsewhere — extend them here.

export * from './categoryFields';
export * from './formModel';
export * from './cloneMapping';
export * from './reviewMapping';
export * from './variantRules';
export * from './inferCategory';
