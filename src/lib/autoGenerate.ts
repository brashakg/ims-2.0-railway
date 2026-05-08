import { tagsForProductAttributes } from "@/lib/categoryAttributes";
import { normalizeCategory, CATEGORIES } from "@/lib/categories";

// Use Record type for flexibility with Prisma models
type Product = Record<string, any>;

// Look up the SEO-friendly noun form ("Sunglasses", "Watch") for a category.
// Falls back to the display label, then to the raw key.
function categorySeoNoun(key: string | null | undefined): string {
  if (!key) return "";
  const norm = normalizeCategory(key);
  const def = CATEGORIES.find((c) => c.key === norm);
  return def?.seoNoun || def?.label || key;
}

interface DiscountRule {
  category: string;
  brand?: string | null;
  subBrand?: string | null;
  percentage?: number;
  discountPercentage?: number;
}

/**
 * Generate product title for Shopify listing.
 *
 * Canonical format (per Avinash 2026-04):
 *   Brand + SubBrand + ModelNo + ColorCode + FrameSize + Label + CategoryNoun
 *
 * Example: "Ray-Ban Aviator RB3025 002/32 58 Classic Sunglasses"
 *
 * All fields are optional — missing values simply drop out of the title so
 * product-level generation (before variants exist) still yields a clean
 * title, and variant-level generation adds colour code + size as they're
 * filled in.
 */
export function generateTitle(product: Product): string {
  const parts = [
    product.brand,
    product.subBrand,
    product.fullModelNo || product.modelNo,
    product.colorCode,
    product.frameSize,
    product.label,
    categorySeoNoun(product.category),
  ]
    .map((v) => (typeof v === "string" ? v.trim() : v))
    .filter(Boolean);

  // Collapse any double spaces introduced by empty fields between filled ones.
  return parts.join(" ").replace(/\s+/g, " ").trim();
}

/**
 * Generate SKU code. Format: <CAT>-<BRAND>-<MODEL>-<COLOR>-<SIZE>
 * e.g. SG-RAYB-RB3025-00232-58
 *
 * Category codes cover all 11 canonical categories + legacy SOLUTIONS
 * alias. Unknown categories fall back to "XX".
 */
export function generateSKU(product: Product): string {
  const catMap: Record<string, string> = {
    SPECTACLES: "SP",
    CLIP_ON_FRAMES: "CF",
    SUNGLASSES: "SG",
    READING_GLASSES: "RG",
    COMPUTER_GLASSES: "CG",
    SAFETY_GLASSES: "SA",
    CONTACT_LENSES: "CL",
    SMARTGLASSES: "SM",
    WATCHES: "WT",
    SMARTWATCHES: "SW",
    ACCESSORIES: "AC",
    SOLUTIONS: "CL", // legacy alias
  };
  const norm = normalizeCategory(product.category);
  const prefix = catMap[norm] || "XX";

  const brand = (product.brand || "XX")
    .replace(/[^A-Za-z]/g, "")
    .substring(0, 4)
    .toUpperCase();

  const modelNo = (product.modelNo || "XXXX")
    .replace(/[^A-Za-z0-9]/g, "")
    .toUpperCase();
  const color = (product.colorCode || "")
    .replace(/[^A-Za-z0-9]/g, "")
    .toUpperCase();
  const size = (product.frameSize || "").replace(/[^\w]/g, "").toUpperCase();

  const parts = [prefix, brand, modelNo];
  if (color) parts.push(color);
  if (size) parts.push(size);

  return parts.join("-");
}

/**
 * Generate SEO title optimized for search. ~60 chars, keyword-rich.
 * Mirrors the canonical title order so SERP and product listing stay
 * consistent, and includes colour code + size when present so variant
 * pages show a unique SEO title per variant.
 */
export function generateSEOTitle(product: Product): string {
  const parts = [
    "Buy",
    product.brand,
    product.subBrand,
    product.fullModelNo || product.modelNo,
    product.colorCode,
    product.frameSize,
    product.label,
    product.gender,
    categorySeoNoun(product.category),
    "| Better Vision",
  ]
    .map((v) => (typeof v === "string" ? v.trim() : v))
    .filter(Boolean);

  return parts.join(" ").replace(/\s+/g, " ").trim();
}

/**
 * Generate SEO meta description. ~160 chars, category-specific messaging.
 * Includes brand, sub-brand, model, colour code and size so variant-level
 * descriptions stay distinct — key for Shopify variant SEO.
 */
export function generateSEODescription(product: Product): string {
  const norm = normalizeCategory(product.category);
  const noun = categorySeoNoun(product.category).toLowerCase();
  const brandPhrase = [
    product.brand,
    product.subBrand,
    product.fullModelNo || product.modelNo,
  ]
    .filter(Boolean)
    .join(" ");
  const variantPhrase = [product.colorCode, product.frameSize]
    .filter(Boolean)
    .join(" ");

  if (norm === "SUNGLASSES" || norm === "CLIP_ON_FRAMES") {
    return [
      `Shop authentic ${brandPhrase} ${noun}${variantPhrase ? ` (${variantPhrase})` : ""}.`,
      product.shape ? `${product.shape} frame.` : "",
      product.lensColour ? `${product.lensColour} lenses.` : "",
      product.polarization ? `${product.polarization}.` : "",
      "Best discounted prices with pan-India free shipping. COD available.",
    ]
      .filter(Boolean)
      .join(" ")
      .trim();
  }

  if (norm === "CONTACT_LENSES") {
    return [
      `Buy ${brandPhrase} contact lenses${variantPhrase ? ` (${variantPhrase})` : ""}.`,
      product.wearSchedule ? `${product.wearSchedule} wear.` : "",
      product.packSize ? `${product.packSize} pack.` : "",
      "Best prices with pan-India free shipping. COD available.",
    ]
      .filter(Boolean)
      .join(" ")
      .trim();
  }

  if (norm === "WATCHES" || norm === "SMARTWATCHES") {
    return [
      `Buy ${brandPhrase} ${noun}${variantPhrase ? ` (${variantPhrase})` : ""}.`,
      product.gender ? `${product.gender}'s ${noun}.` : "",
      product.warranty ? `${product.warranty}.` : "",
      "Authentic with pan-India free shipping. COD available.",
    ]
      .filter(Boolean)
      .join(" ")
      .trim();
  }

  // Default: SPECTACLES, READING / COMPUTER / SAFETY GLASSES, SMARTGLASSES
  return [
    `Shop authentic ${brandPhrase} ${noun}${variantPhrase ? ` (${variantPhrase})` : ""}.`,
    product.shape ? `${product.shape} frame.` : "",
    product.frameType ? `${product.frameType}.` : "",
    product.frameMaterial ? `${product.frameMaterial} frame.` : "",
    "Best discounted prices with pan-India free shipping. COD available.",
  ]
    .filter(Boolean)
    .join(" ")
    .trim();
}

/**
 * Generate URL-safe page slug from title.
 */
export function generatePageUrl(product: Product): string {
  const title = generateTitle(product);
  return title
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

/**
 * Generate Shopify tags. These are used bidirectionally:
 * - Push: product attributes → tags (prefix_value format)
 * - Pull: tags → product attributes (parsed by parseTagsToFields in pull route)
 *
 * Driven by src/lib/categoryAttributes.ts — every attribute applicable to
 * the product's category that has `tag: true` contributes one tag token.
 * Back-compat with existing DB tags is preserved via tagPrefix overrides
 * on the AttributeMeta entries (e.g. countryOfOrigin uses `origin_`).
 *
 * IMPORTANT: Tag prefixes must match the pull route's parseTagsToFields() exactly.
 */
export function generateTags(product: Product): string {
  const tags = tagsForProductAttributes(product.category || "", product);

  // Variant-level measurement fallbacks that may live on the parent form body
  // even though the attribute registry marks them as variant-level. Emitting
  // them on the product's tags is needed for the pull route's tag parser to
  // round-trip. These don't come through tagsForProductAttributes because
  // they're level="variant"; emit manually from the product/body here.
  const seen = new Set(tags);
  const addVariantFallback = (prefix: string, value: unknown) => {
    if (value === null || value === undefined) return;
    // Trim and slugify so whitespace-only values never produce empty tags
    // like "lensusp_" or "productusp_". Audit found 4,400+ historical
    // products had these empty tags from the old generator.
    const slug = String(value)
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    if (!slug) return;
    const token = `${prefix}_${slug}`;
    if (!seen.has(token)) {
      tags.push(token);
      seen.add(token);
    }
  };
  // framesize / bridge / templelength stay as filterable tags (round 1
  // 10.18 + round 2 U3 = "tags for all three"). Weight is intentionally
  // dropped — round 1 5.2: "Weight — Shopify field only, NOT a tag".
  addVariantFallback("framesize",    product.frameSize);
  addVariantFallback("bridge",       product.bridge);
  addVariantFallback("templelength", product.templeLength);
  addVariantFallback("framecolor",   product.frameColor);
  addVariantFallback("templecolor",  product.templeColor);

  return tags.join(", ");
}

/**
 * Generate HTML product description for the Shopify storefront.
 *
 * Format — matches the legacy Excel template exactly (Option A approved
 * by the user 2026-05-09). Theme-owned styling: NO inline <style>, NO
 * inline classes — the structure is plain headings + tables so the
 * storefront theme renders it consistently.
 *
 *   <h4>Product Details</h4>
 *   <h5>Model Number: <full model name>...</h5>
 *   <p>Marketing intro paragraph (per-category, see buildMarketingIntro)</p>
 *
 *   <h4>Technical Specifications</h4>
 *   <table><tbody>… category-specific spec rows …</tbody></table>
 *
 *   <h4>General Information</h4>
 *   <table><tbody>… brand / model / shape / colours …</tbody></table>
 *
 *   <h4>Warranty</h4>
 *   <p>…manufacturer warranty…<a href="https://bettervision.in/pages/warranty">Click Here</a></p>
 *
 * Each row is omitted when the value is missing — no "N/A" placeholders
 * to keep the description clean.
 */
export function generateHTMLDescription(product: Product): string {
  const cat = normalizeCategory(product.category);
  const noun = categorySeoNoun(product.category);

  // Helper — only emit a <tr> when value is non-empty.
  const tableRows = (pairs: Array<[label: string, value: unknown]>): string => {
    const rows = pairs
      .filter(([, v]) => v !== null && v !== undefined && String(v).trim() !== "")
      .map(([label, v]) => `<tr><td>${label}</td><td>${escapeHtml(String(v))}</td></tr>`)
      .join("");
    return rows ? `<table><tbody>${rows}</tbody></table>` : "";
  };

  // Model-number sub-line — used right under "Product Details" h5.
  // Mirrors how legacy did it: "<Brand> <Model> <Color> <Size> <Singular Noun>".
  const modelLine = [
    product.brand,
    product.fullModelNo || product.modelNo,
    product.colorCode,
    product.frameSize,
    product.label,
    noun,
  ]
    .map((v) => (typeof v === "string" ? v.trim() : v))
    .filter(Boolean)
    .join(" ")
    .replace(/\s+/g, " ");

  const intro = buildMarketingIntro(product);

  // Per-category technical / general tables. Eyewear shares one shape;
  // contact lenses, watches, smartwatches, accessories diverge.
  const { technical, general } = perCategoryTables(cat, product);

  const warrantyText = String(product.warranty || "").trim();
  const warrantyParagraph = warrantyText
    ? `<p>This product comes with a manufacturer's warranty of ${escapeHtml(warrantyText)} from the date of sale. For more details on warranty, <a title="Warranty" href="https://bettervision.in/pages/warranty" target="_blank" rel="noopener">Click Here</a>.</p>`
    : `<p>For warranty details, <a title="Warranty" href="https://bettervision.in/pages/warranty" target="_blank" rel="noopener">Click Here</a>.</p>`;

  const parts: string[] = [];
  parts.push(`<h4>Product Details</h4>`);
  if (modelLine) parts.push(`<h5>Model Number: ${escapeHtml(modelLine)}</h5>`);
  if (intro) parts.push(`<p>${intro}</p>`);
  if (technical) {
    parts.push(`<h4>Technical Specifications</h4>`);
    parts.push(technical);
  }
  if (general) {
    parts.push(`<h4>General Information</h4>`);
    parts.push(general);
  }
  // Solutions / contact-lens long-form notes (still useful for staff to
  // type custom blurbs that aren't in spec tables).
  const aboutProduct = String(product.aboutProduct || "").trim();
  if (aboutProduct) {
    parts.push(`<h4>About This Product</h4>`);
    parts.push(`<p>${escapeHtml(aboutProduct)}</p>`);
  }
  parts.push(`<h4>Warranty</h4>`);
  parts.push(warrantyParagraph);

  return parts.join("\n");

  // ─────────────────────────────────────────────────────────────────
  // Helper — chooses the per-category Technical + General table rows.
  // Eyewear families share most rows; watches / contact lenses / etc.
  // surface their own keys. ────────────────────────────────────────
  function perCategoryTables(cat: string, p: Product): { technical: string; general: string } {
    if (cat === "WATCHES" || cat === "SMARTWATCHES") {
      const technical = tableRows([
        ["Movement", p.movement],
        ["Display", p.displayType],
        ["Case Material", p.caseMaterial],
        ["Case Size", p.caseSize],
        ["Case Shape", p.caseShape],
        ["Glass Type", p.glassType],
        ["Water Resistance", p.waterResistance],
        ["Battery Life", p.batteryLife],
        ["Connectivity", p.connectivity],
        ["Health Sensors", p.healthSensors],
        ["OS Compatibility", p.osCompatibility],
        ["Functions", p.watchFunctions],
        ["Product Category", noun],
      ]);
      const general = tableRows([
        ["Brand", p.brand],
        ["Model No", p.fullModelNo || p.modelNo],
        ["Strap Material", p.strapMaterial],
        ["Strap Color", p.strapColor || p.frameColor || p.colorCode],
        ["Dial Color", p.dialColor],
        ["Dial Pattern", p.dialPattern],
        ["Gender", p.gender],
        ["Country of Origin", p.countryOfOrigin],
      ]);
      return { technical, general };
    }

    if (cat === "CONTACT_LENSES" || cat === "COLOR_CONTACT_LENSES") {
      const technical = tableRows([
        ["Lens Type", p.lensType],
        ["Material", p.contactLensMaterial || p.lensMaterial],
        ["Wear Schedule", p.wearSchedule],
        ["Pack Size", p.packSize],
        ["Base Curve", p.baseCurve],
        ["Diameter", p.diameter],
        ["Water Content", p.waterContent],
        ["Oxygen Permeability", p.oxygenPermeability],
        ["UV Protection", p.uvProtection],
        ["Product Category", noun],
      ]);
      const general = tableRows([
        ["Brand", p.brand],
        ["Model No", p.fullModelNo || p.modelNo],
        ["Tint / Colour", p.lensColour],
        ["Country of Origin", p.countryOfOrigin],
      ]);
      return { technical, general };
    }

    if (cat === "ACCESSORIES") {
      const technical = tableRows([
        ["Accessory Type", p.accessoryType],
        ["Material", p.material || p.frameMaterial],
        ["Compatibility", p.compatibility],
        ["Pack Size", p.packSize],
        ["Product Category", noun],
      ]);
      const general = tableRows([
        ["Brand", p.brand],
        ["Model No", p.fullModelNo || p.modelNo],
        ["Country of Origin", p.countryOfOrigin],
      ]);
      return { technical, general };
    }

    // Default — eyewear families: Spectacles, Sunglasses, Clip-On,
    // Reading, Computer, Safety, Smartglasses.
    // Spec rows are added per-category where applicable; missing rows
    // simply drop out via tableRows().
    const technical = tableRows([
      ["Frame Type", p.frameType],
      ["Polarization", cat === "SUNGLASSES" || cat === "CLIP_ON_FRAMES" || cat === "SMARTGLASSES" ? p.polarization : null],
      ["UV Protection", p.uvProtection],
      ["Frame Material", p.frameMaterial],
      // Computer / reading glasses have explicit lens-feature fields that
      // were called out in the round 2 mapping. Surface them when present.
      ["Blue Light Protection", (p as any).blueLightProtection],
      ["Anti-Glare Coating", (p as any).antiGlareCoating],
      ["Anti-Fog Coating", (p as any).antiFogCoating],
      ["Anti-Scratch Coating", (p as any).antiScratchCoating],
      ["Power", cat === "READING_GLASSES" ? (p as any).power : null],
      ["Vision Type", cat === "READING_GLASSES" ? (p as any).visionType : null],
      ["Side Shields", cat === "SAFETY_GLASSES" ? (p as any).sideShields : null],
      ["Certification", cat === "SAFETY_GLASSES" ? (p as any).certification : null],
      ["Use-case", cat === "SAFETY_GLASSES" ? (p as any).useCase : null],
      ["Product Category", noun],
      ["Size", p.frameSize],
      ["Bridge", p.bridge],
      ["Temple Length", p.templeLength],
      ["Weight", p.weight ? withUnit(p.weight, "g") : null],
    ]);
    const general = tableRows([
      ["Brand", p.brand],
      ["Model No", p.fullModelNo || p.modelNo],
      ["Shape", p.shape],
      ["Frame Code", p.colorCode],
      ["Temple Colour", p.templeColor],
      ["Lens Colour", p.lensColour],
      ["Gender", p.gender],
      ["Country of Origin", p.countryOfOrigin],
    ]);
    return { technical, general };
  }
}

/**
 * Per-category marketing intro paragraph (Option A — legacy pattern,
 * approved by user 2026-05-09). Each template fills only with values
 * actually present on the product so the prose doesn't read as a Mad
 * Lib. Falls back to a generic line for unknown categories.
 */
export function buildMarketingIntro(product: Product): string {
  const cat = normalizeCategory(product.category);
  const noun = categorySeoNoun(product.category);
  const brand = trimOrEmpty(product.brand);
  const model = trimOrEmpty(product.fullModelNo || product.modelNo);
  const colorCode = trimOrEmpty(product.colorCode);
  const colorName = trimOrEmpty(product.colorName || product.frameColor);
  const sizeStr = trimOrEmpty(product.frameSize);
  const lensColour = trimOrEmpty(product.lensColour);
  const shape = trimOrEmpty(product.shape);
  const gender = trimOrEmpty(product.gender) || "Unisex";
  const frameMaterial = trimOrEmpty(product.frameMaterial);
  const polarization = trimOrEmpty(product.polarization);
  const uv = trimOrEmpty(product.uvProtection);

  // Reusable phrases — keep tone consistent across categories.
  const ctaShort = "Discover your perfect pair at Better Vision today!";
  const ctaWatch = "Authentic, fully warrantied, and shipped pan-India. Order from Better Vision.";
  const ctaLens = "Ships free across India with COD support. Order now, only at Better Vision.";
  const ctaAccessory = "Backed by Better Vision quality. Order now.";
  const trust = "Enjoy unbeatable discounts and guaranteed authentic products.";

  // Pieces that combine per template.
  const brandModel = [brand, model].filter(Boolean).join(" ");
  const colorPhrase = colorName || colorCode;
  const sizeSuffix = sizeStr ? ` size ${sizeStr}` : "";
  const lookPhrase = shape ? `${shape.toLowerCase()} look` : "classic look";

  switch (cat) {
    case "SUNGLASSES":
    case "CLIP_ON_FRAMES": {
      const colourLine = [
        colorPhrase ? `${colorPhrase} colour` : "",
        lensColour ? `with ${lensColour} lenses` : "",
      ]
        .filter(Boolean)
        .join(" ");
      const polUv = [polarization, uv].filter(Boolean).join(", ");
      return [
        `Reflect your style with the ${brandModel} ${shape || ""} ${noun}.`.replace(/\s+/g, " ").trim(),
        colourLine ? `This ${noun} comes in ${colourLine},` : `This ${noun}`,
        `with a ${lookPhrase} that turns every step into a moment.`,
        `Suitable for ${gender}${sizeSuffix ? `,` : ""}${sizeSuffix ? ` this model comes in${sizeSuffix}.` : "."}`,
        polUv ? `${polUv}.` : "",
        trust,
        ctaShort,
      ]
        .filter(Boolean)
        .join(" ")
        .replace(/\s+/g, " ");
    }

    case "SPECTACLES":
      return [
        `Step into a sharper look with the ${brandModel} ${shape || ""} ${noun}.`.replace(/\s+/g, " ").trim(),
        frameMaterial ? `Crafted in ${frameMaterial},` : "",
        colorPhrase ? `this frame comes in ${colorPhrase}${sizeSuffix ? ` for${sizeSuffix}` : ""}.` : "",
        `Lightweight, comfortable, and ready for your prescription. Suitable for ${gender}.`,
        `Backed by manufacturer warranty and free pan-India shipping. Pick up your pair at Better Vision today.`,
      ]
        .filter(Boolean)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();

    case "READING_GLASSES": {
      const power = trimOrEmpty((product as any).power);
      const vision = trimOrEmpty((product as any).visionType);
      const lensFeatures = [
        (product as any).antiGlareCoating ? "anti-glare" : "",
        (product as any).blueLightProtection ? "blue-light protection" : "",
      ]
        .filter(Boolean)
        .join(" and ");
      return [
        `See clearly, day after day, with the ${brandModel} ${noun}.`,
        frameMaterial ? `${frameMaterial} frame${shape ? `, ${shape.toLowerCase()} shape` : ""},` : "",
        colorPhrase ? `available in ${colorPhrase}.` : "",
        vision || power ? `${vision ? vision + " " : ""}${power ? `at ${power} power` : ""}${lensFeatures ? ` with ${lensFeatures}` : ""} — easy on the eyes during long screen sessions.` : "",
        `Suitable for ${gender}.`,
        `Backed by manufacturer warranty and free pan-India shipping. Pick up your pair at Better Vision.`,
      ]
        .filter(Boolean)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
    }

    case "COMPUTER_GLASSES": {
      const lensFeatures = [
        (product as any).antiGlareCoating ? "anti-glare" : "",
        (product as any).blueLightProtection ? "blue-light protection" : "",
      ]
        .filter(Boolean)
        .join(" and ");
      return [
        `Long screen sessions, easier on your eyes — meet the ${brandModel} ${noun}.`,
        frameMaterial ? `${frameMaterial} frame,` : "",
        colorPhrase ? `${colorPhrase} colour,` : "",
        lensFeatures ? `with ${lensFeatures} built in.` : "",
        `Suitable for ${gender}.`,
        ctaWatch,
      ]
        .filter(Boolean)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
    }

    case "SAFETY_GLASSES": {
      const useCase = trimOrEmpty((product as any).useCase);
      return [
        `Stay protected on the job with the ${brandModel} ${noun}.`,
        `Impact-rated lenses${frameMaterial ? ` and a ${frameMaterial.toLowerCase()} frame` : ""}${shape ? ` in ${shape.toLowerCase()}` : ""},${colorPhrase ? ` available in ${colorPhrase}.` : "."}`,
        useCase ? `Built for ${useCase}.` : "",
        `Suitable for ${gender}.`,
        `Backed by manufacturer warranty. Order from Better Vision.`,
      ]
        .filter(Boolean)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
    }

    case "CONTACT_LENSES":
    case "COLOR_CONTACT_LENSES": {
      const material = trimOrEmpty(product.contactLensMaterial || product.lensMaterial);
      const wear = trimOrEmpty(product.wearSchedule);
      const pack = trimOrEmpty(product.packSize);
      const tint = trimOrEmpty(product.lensColour);
      const isColor = cat === "COLOR_CONTACT_LENSES";
      const opener = isColor
        ? `Switch up your look with ${brandModel} ${noun}${tint ? ` in ${tint}` : ""}.`
        : `Comfort that lasts, all day, with ${brandModel} ${noun}.`;
      const specBits = [
        material ? `${material} material` : "",
        wear ? `${wear.toLowerCase()} wear` : "",
        pack ? `pack of ${pack}` : "",
      ].filter(Boolean);
      const specSentence = specBits.length ? capitalizeFirst(specBits.join(", ") + ".") : "";
      return [opener, specSentence, ctaLens].filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
    }

    case "SMARTGLASSES":
      return [
        `Tech meets style with the ${brandModel} ${noun}.`,
        frameMaterial ? `${frameMaterial} frame${colorPhrase ? ` in ${colorPhrase}` : ""}${sizeSuffix ? `, size ${sizeStr}` : ""}.` : "",
        `Built-in audio, camera, and connectivity in a frame you'd actually wear.`,
        `Suitable for ${gender}.`,
        ctaWatch,
      ]
        .filter(Boolean)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();

    case "WATCHES": {
      const dial = trimOrEmpty(product.dialColor);
      const strapMat = trimOrEmpty(product.strapMaterial);
      const movement = trimOrEmpty(product.movement);
      const wr = trimOrEmpty(product.waterResistance);
      // Build the descriptive sentence as proper clauses joined by commas,
      // ending with a period — avoids the "Watch.Black dial" merge bug.
      const clauses: string[] = [];
      if (dial) clauses.push(`${dial} dial${strapMat ? ` paired with a ${strapMat.toLowerCase()} strap` : ""}`);
      else if (strapMat) clauses.push(`${strapMat} strap`);
      if (movement) clauses.push(`powered by precise ${movement.toLowerCase()} movement`);
      if (wr) clauses.push(`built to handle ${wr} water resistance`);
      const descSentence = clauses.length ? capitalizeFirst(clauses.join(", ") + ".") : "";
      return [
        `Discover everyday craftsmanship with the ${brandModel} ${noun}.`,
        descSentence,
        `Perfect for ${gender}.`,
        ctaWatch,
      ]
        .filter(Boolean)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
    }

    case "SMARTWATCHES": {
      const display = trimOrEmpty(product.displayType);
      const battery = trimOrEmpty(product.batteryLife);
      const sensors = trimOrEmpty(product.healthSensors);
      const clauses: string[] = [];
      if (display) clauses.push(`${display} display`);
      if (battery) clauses.push(`${battery} battery`);
      if (sensors) clauses.push(`tracks ${sensors.toLowerCase()}`);
      const descSentence = clauses.length ? capitalizeFirst(clauses.join(", ") + ".") : "";
      return [
        `Your day, on your wrist — the ${brandModel} ${noun}.`,
        descSentence,
        `Pairs with iOS and Android.`,
        ctaWatch,
      ]
        .filter(Boolean)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
    }

    case "ACCESSORIES": {
      const accType = trimOrEmpty(product.accessoryType) || trimOrEmpty(product.label) || "accessory";
      const material = trimOrEmpty(product.material || product.frameMaterial);
      const compat = trimOrEmpty(product.compatibility);
      const pack = trimOrEmpty(product.packSize);
      return [
        `Keep your eyewear at its best with the ${brandModel} ${accType}.`,
        material ? `${material},` : "",
        compat ? `compatible with ${compat},` : "",
        pack ? `pack of ${pack}.` : "",
        ctaAccessory,
      ]
        .filter(Boolean)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
    }

    default:
      return [
        `Explore the ${brandModel} from Better Vision.`,
        `Authentic products, manufacturer warranty, and free pan-India shipping. Order yours today.`,
      ].join(" ");
  }
}

function trimOrEmpty(v: unknown): string {
  return typeof v === "string" ? v.trim() : v ? String(v).trim() : "";
}

function capitalizeFirst(s: string): string {
  return s.length ? s[0].toUpperCase() + s.slice(1) : s;
}

function withUnit(v: unknown, unit: string): string {
  const s = trimOrEmpty(v);
  if (!s) return "";
  // Avoid double-suffixing if user already wrote "37g".
  if (new RegExp(`${unit}\\s*$`, "i").test(s)) return s;
  return `${s}${unit}`;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Calculate discounted price based on discount rules, with specificity
 * ranking: (category + brand + subBrand) > (category + brand) > (category).
 * The most specific matching rule wins.
 *
 * Returns MRP unchanged if no rule matches (no discount).
 */
export function calculateDiscountedPrice(
  mrp: number,
  category: string,
  discountRules: DiscountRule[],
  brand?: string | null,
  subBrand?: string | null
): number {
  if (!mrp || mrp <= 0) return 0;

  const rule = findMatchingDiscountRule(
    discountRules,
    category,
    brand,
    subBrand
  );
  if (!rule) return mrp;

  const pct = rule.discountPercentage ?? rule.percentage ?? 0;
  const discountAmount = (mrp * pct) / 100;
  return Math.round((mrp - discountAmount) * 100) / 100;
}

/**
 * Pick the most specific matching discount rule for a product.
 * Exported so other call sites can reuse the same precedence.
 */
export function findMatchingDiscountRule(
  rules: DiscountRule[],
  category: string,
  brand?: string | null,
  subBrand?: string | null
): DiscountRule | undefined {
  const cat = (category || "").toLowerCase();
  const br = (brand || "").toLowerCase().trim();
  const sb = (subBrand || "").toLowerCase().trim();

  // Partition rules by category-match first to limit the search.
  const catMatches = rules.filter((r) => r.category.toLowerCase() === cat);
  if (catMatches.length === 0) return undefined;

  // 1) Most specific: category + brand + subBrand.
  if (br && sb) {
    const exact = catMatches.find(
      (r) =>
        (r.brand || "").toLowerCase() === br &&
        (r.subBrand || "").toLowerCase() === sb
    );
    if (exact) return exact;
  }
  // 2) category + brand (subBrand null/empty on rule).
  if (br) {
    const byBrand = catMatches.find(
      (r) =>
        (r.brand || "").toLowerCase() === br &&
        !(r.subBrand && r.subBrand.trim())
    );
    if (byBrand) return byBrand;
  }
  // 3) category only (brand null on rule).
  const byCat = catMatches.find(
    (r) => !(r.brand && r.brand.trim()) && !(r.subBrand && r.subBrand.trim())
  );
  return byCat;
}
