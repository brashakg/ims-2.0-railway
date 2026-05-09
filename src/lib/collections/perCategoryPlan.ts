// Per-category collection plan — codifies the user's mapping HTML answers
// for which auto-generated collections to produce per category.
//
// User's plan (from the answered Round-2 mapping HTML):
//
//   Sunglasses          : Brand × Sunglass, Shape × Sunglass, Gender × Sunglass,
//                         Best Sellers, Sale
//   Optical Frames (SPECTACLES)
//                       : Brand × Frames, Shape × Frames, Gender × Frames,
//                         Material × Frames, Best Sellers
//   Clip-On Frames      : Brand × Clip-On, Magnetic vs Traditional, Best Sellers
//   Reading Glasses     : By Power range, Single Vision, Bifocal, Progressive,
//                         Best Sellers
//   Computer Glasses    : Blue Light %, Anti-Glare, Brand, Gender, Best Sellers
//   Safety Glasses      : By use-case only
//   Contact Lenses      : By wear schedule, By brand, By material, Toric,
//                         Multifocal, Best Sellers
//   Color Contact Lenses: by colour family (top-level menu item, not folded
//                         under CONTACT_LENSES)
//   Smartglasses        : Featured only
//   Watches             : restructured manually — no auto-generation
//   Smartwatches        : Brand only
//   Accessories         : By accessory type (Case, Cleaning Kit, Chain, Strap,
//                         Cloth, Pouch, Lens Cleaner, Stand, Solution)
//
// Each PlanDimension describes one collection-generation rule. The
// ruleGenerator iterates the active category's dimensions and either
// expands per-attribute-value (for `brand`, `shape`, `gender`, ...) or
// emits a single fixed collection (for `bestSellers`, `sale`, `featured`,
// or pre-enumerated single-value rules like `visionType:Bifocal`).

export type PlanDimensionKey =
  | "brand"
  | "shape"
  | "gender"
  | "frameMaterial"
  | "power"
  | "visionType"
  | "blueLightProtection"
  | "antiGlareCoating"
  | "useCase"
  | "wearSchedule"
  | "contactLensMaterial"
  | "lensType"
  | "accessoryType"
  | "attachment"        // CLIP_ON_FRAMES — Magnetic vs Traditional
  | "colorFamily"       // COLOR_CONTACT_LENSES — sub-buckets by colour
  | "featured"
  | "bestSellers"
  | "sale";

/** A single planned auto-generation rule for a category. */
export interface PlanDimension {
  /** The dimension key (see PlanDimensionKey JSDoc). */
  key: PlanDimensionKey;
  /** Tag prefix on the product. Matches what tagsForProductAttributes emits. */
  tagPrefix: string;
  /**
   * Optional pre-enumerated values. When present, the rule generator
   * emits one collection per value WITHOUT consulting attribute options
   * (used for `visionType` / `attachment` etc.). When absent, the
   * generator expects an `attributeValues` map at call time.
   */
  fixedValues?: string[];
  /**
   * For `bestSellers` / `sale` / `featured` — these are single fixed
   * collections, not per-value expansions. The generator emits exactly
   * one CollectionPlan with the literal `fixedHandle` / `fixedTitle`.
   */
  fixedHandle?: string;
  fixedTitle?: string;
  /** Sort order for the resulting collection. */
  sortOrder?: "BEST_SELLING" | "MANUAL" | "ALPHA_ASC" | "ALPHA_DESC" | "PRICE_DESC" | "PRICE_ASC";
  /** Note for the dev — not consumed by code. */
  comment?: string;
}

/**
 * Per-category dimension list. Order roughly matches the user's listed
 * collection order so the generated `sortPriority` increments stay
 * aligned with the menu order they want.
 */
export const PER_CATEGORY_PLAN: Record<string, PlanDimension[]> = {
  // ─── Sunglasses ─────────────────────────────────────────
  SUNGLASSES: [
    { key: "brand",       tagPrefix: "brand",  comment: "Brand × Sunglass" },
    { key: "shape",       tagPrefix: "shape",  comment: "Shape × Sunglass" },
    { key: "gender",      tagPrefix: "gender", comment: "Gender × Sunglass" },
    {
      key: "bestSellers",
      tagPrefix: "best-sellers",
      fixedHandle: "best-sellers-sunglasses",
      fixedTitle: "Best-Selling Sunglass",
      sortOrder: "BEST_SELLING",
    },
    {
      key: "sale",
      tagPrefix: "sale",
      fixedHandle: "sale-sunglasses",
      fixedTitle: "Sale Sunglass",
      sortOrder: "BEST_SELLING",
    },
  ],

  // ─── Spectacles (Optical Frames) ────────────────────────
  SPECTACLES: [
    { key: "brand",          tagPrefix: "brand",         comment: "Brand × Frames" },
    { key: "shape",          tagPrefix: "shape",         comment: "Shape × Frames" },
    { key: "gender",         tagPrefix: "gender",        comment: "Gender × Frames" },
    { key: "frameMaterial",  tagPrefix: "framematerial", comment: "Material × Frames" },
    {
      key: "bestSellers",
      tagPrefix: "best-sellers",
      fixedHandle: "best-sellers-spectacles",
      fixedTitle: "Best-Selling Optical Frame",
      sortOrder: "BEST_SELLING",
    },
  ],

  // ─── Clip-On Frames ────────────────────────────────────
  CLIP_ON_FRAMES: [
    { key: "brand", tagPrefix: "brand", comment: "Brand × Clip-On" },
    {
      key: "attachment",
      tagPrefix: "attachment",
      fixedValues: ["Magnetic", "Traditional"],
      comment: "Magnetic vs Traditional clip-on",
    },
    {
      key: "bestSellers",
      tagPrefix: "best-sellers",
      fixedHandle: "best-sellers-clip-on-frames",
      fixedTitle: "Best-Selling Clip-On Frame",
      sortOrder: "BEST_SELLING",
    },
  ],

  // ─── Reading Glasses ───────────────────────────────────
  // Power-range collections are pre-enumerated (the ranges Better Vision
  // markets are: +0.50 to +1.00, +1.25 to +2.00, +2.25 to +3.00, +3.25+).
  // Vision Type collections are also pre-enumerated single-value rules.
  READING_GLASSES: [
    {
      key: "power",
      tagPrefix: "power-range",
      fixedValues: [
        "+0.50 to +1.00",
        "+1.25 to +2.00",
        "+2.25 to +3.00",
        "+3.25 and above",
      ],
      comment: "Power-range buckets",
    },
    {
      key: "visionType",
      tagPrefix: "visiontype",
      fixedValues: ["Single Vision", "Bifocal", "Progressive"],
      comment: "Single Vision / Bifocal / Progressive",
    },
    {
      key: "bestSellers",
      tagPrefix: "best-sellers",
      fixedHandle: "best-sellers-reading-glasses",
      fixedTitle: "Best-Selling Reading Glasses",
      sortOrder: "BEST_SELLING",
    },
  ],

  // ─── Computer Glasses ──────────────────────────────────
  // Blue Light % is a tiered enum (typical retail buckets).
  COMPUTER_GLASSES: [
    {
      key: "blueLightProtection",
      tagPrefix: "bluelightprotection",
      fixedValues: ["50%", "70%", "90%", "100%"],
      comment: "Blue Light % tiers",
    },
    {
      key: "antiGlareCoating",
      tagPrefix: "antiglarecoating",
      fixedValues: ["Yes"],
      comment: "Anti-Glare ON only",
    },
    { key: "brand",  tagPrefix: "brand",  comment: "Brand × Computer Glasses" },
    { key: "gender", tagPrefix: "gender", comment: "Gender × Computer Glasses" },
    {
      key: "bestSellers",
      tagPrefix: "best-sellers",
      fixedHandle: "best-sellers-computer-glasses",
      fixedTitle: "Best-Selling Blue-Light Glass",
      sortOrder: "BEST_SELLING",
    },
  ],

  // ─── Safety Glasses ────────────────────────────────────
  // Use-case is multi-select on the product. Pre-enumerated to the
  // common retail use-cases.
  SAFETY_GLASSES: [
    {
      key: "useCase",
      tagPrefix: "usecase",
      fixedValues: [
        "Industrial",
        "Lab",
        "Medical",
        "Construction",
        "Sports",
        "Welding",
      ],
      comment: "Safety Glasses by use-case",
    },
  ],

  // ─── Contact Lenses ────────────────────────────────────
  CONTACT_LENSES: [
    {
      key: "wearSchedule",
      tagPrefix: "wearschedule",
      fixedValues: [
        "Daily Disposable",
        "Bi-Weekly",
        "Monthly",
        "Quarterly",
        "Yearly",
      ],
      comment: "By wear schedule",
    },
    { key: "brand", tagPrefix: "brand", comment: "Brand × Contact Lenses" },
    {
      key: "contactLensMaterial",
      tagPrefix: "contactlensmaterial",
      fixedValues: ["Hydrogel", "Silicone Hydrogel"],
      comment: "By material",
    },
    {
      key: "lensType",
      tagPrefix: "lenstype",
      fixedValues: ["Toric", "Multifocal"],
      comment: "Toric + Multifocal sub-buckets",
    },
    {
      key: "bestSellers",
      tagPrefix: "best-sellers",
      fixedHandle: "best-sellers-contact-lenses",
      fixedTitle: "Best-Selling Contact Lenses",
      sortOrder: "BEST_SELLING",
    },
  ],

  // ─── Color Contact Lenses ──────────────────────────────
  // Top-level menu item with sub-buckets by colour family. We use
  // `lensColour` tag prefix (matches existing AttributeMeta) but expose
  // the dimension as `colorFamily` for clarity in the plan.
  COLOR_CONTACT_LENSES: [
    {
      key: "colorFamily",
      tagPrefix: "lenscolour",
      fixedValues: [
        "Blue",
        "Brown",
        "Green",
        "Grey",
        "Hazel",
        "Honey",
        "Violet",
      ],
      comment: "Color family sub-buckets",
    },
  ],

  // ─── Smartglasses ──────────────────────────────────────
  SMARTGLASSES: [
    {
      key: "featured",
      tagPrefix: "featured",
      fixedHandle: "featured-smartglasses",
      fixedTitle: "Featured Smartglass",
      sortOrder: "MANUAL",
    },
  ],

  // ─── Watches ───────────────────────────────────────────
  // The user is restructuring Watches manually — we deliberately emit
  // an empty plan so the auto-generator never touches them.
  WATCHES: [],

  // ─── Smartwatches ──────────────────────────────────────
  SMARTWATCHES: [
    { key: "brand", tagPrefix: "brand", comment: "Brand × Smartwatches" },
  ],

  // ─── Accessories ───────────────────────────────────────
  ACCESSORIES: [
    {
      key: "accessoryType",
      tagPrefix: "accessorytype",
      fixedValues: [
        "Case",
        "Cleaning Kit",
        "Chain",
        "Strap",
        "Cloth",
        "Pouch",
        "Lens Cleaner",
        "Stand",
        "Solution",
      ],
      comment: "By accessory type",
    },
  ],
};

/** All categories that have at least one auto-generation dimension. */
export function categoriesWithAutoCollections(): string[] {
  return Object.entries(PER_CATEGORY_PLAN)
    .filter(([, dims]) => dims.length > 0)
    .map(([cat]) => cat);
}

/** Lookup helper. Returns [] for unknown / opted-out categories. */
export function dimensionsForCategory(category: string): PlanDimension[] {
  return PER_CATEGORY_PLAN[category] ?? [];
}
