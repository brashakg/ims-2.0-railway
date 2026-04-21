import { tagsForProductAttributes } from "@/lib/categoryAttributes";

// Use Record type for flexibility with Prisma models
type Product = Record<string, any>;

interface DiscountRule {
  category: string;
  brand?: string | null;
  subBrand?: string | null;
  percentage?: number;
  discountPercentage?: number;
}

/**
 * Generate product title for Shopify listing.
 * Format: Brand [SubBrand] ModelNo [ColorCode] [FrameSize] [FrameColor] [Shape]
 * Example: "Boss 1234 086 55 Gold Square"
 */
export function generateTitle(product: Product): string {
  const parts = [
    product.brand,
    product.subBrand,
    product.fullModelNo || product.modelNo,
    product.colorCode,
    product.frameSize,
    product.frameColor,
    product.shape,
  ].filter(Boolean);

  return parts.join(" ");
}

/**
 * Generate SKU code.
 * Format: SP-BOSS-1234-086-55
 */
export function generateSKU(product: Product): string {
  const catMap: Record<string, string> = {
    SPECTACLES: "SP",
    SUNGLASSES: "SG",
    SOLUTIONS: "SL",
  };
  const prefix = catMap[(product.category || "").toUpperCase()] || "XX";

  const brand = (product.brand || "XX")
    .replace(/[^A-Za-z]/g, "")
    .substring(0, 4)
    .toUpperCase();

  const modelNo = (product.modelNo || "XXXX").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
  const color = (product.colorCode || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
  const size = (product.frameSize || "").replace(/[^\w]/g, "").toUpperCase();

  const parts = [prefix, brand, modelNo];
  if (color) parts.push(color);
  if (size) parts.push(size);

  return parts.join("-");
}

/**
 * Generate SEO title optimized for search.
 * ~60 chars, keyword-rich.
 */
export function generateSEOTitle(product: Product): string {
  const cat = (product.category || "").toUpperCase();
  const categoryLabel =
    cat === "SUNGLASSES" ? "Sunglasses" :
    cat === "SOLUTIONS" ? "Lens Care" :
    "Eyeglasses";

  const parts = [
    "Buy",
    product.brand,
    product.fullModelNo || product.modelNo,
    product.shape,
    product.frameColor,
    product.gender,
    categoryLabel,
    "| Better Vision",
  ].filter(Boolean);

  return parts.join(" ").trim();
}

/**
 * Generate SEO meta description.
 * ~160 chars, category-specific messaging.
 */
export function generateSEODescription(product: Product): string {
  const cat = (product.category || "").toUpperCase();

  if (cat === "SUNGLASSES") {
    return [
      `Shop authentic ${product.brand || ""} ${product.fullModelNo || product.modelNo || ""} sunglasses.`,
      product.shape ? `${product.shape} frame` : "",
      product.lensColour ? `with ${product.lensColour} lenses.` : "",
      product.polarization ? `${product.polarization}.` : "",
      "Best discounted prices with pan-India free shipping. COD available.",
    ].filter(Boolean).join(" ").trim();
  }

  if (cat === "SOLUTIONS") {
    return [
      `Buy ${product.brand || ""} ${product.productName || ""}.`,
      product.recommendedFor ? `Recommended for ${product.recommendedFor}.` : "",
      product.benefits ? `${product.benefits}.` : "",
      "Best prices with pan-India free shipping. COD available.",
    ].filter(Boolean).join(" ").trim();
  }

  // Default: SPECTACLES
  return [
    `Shop authentic ${product.brand || ""} ${product.fullModelNo || product.modelNo || ""}`,
    product.shape ? `${product.shape}` : "",
    product.frameType ? `${product.frameType}` : "",
    "eyeglasses.",
    product.frameColor ? `${product.frameColor} frame` : "",
    product.templeColor ? `with ${product.templeColor} temples.` : "",
    product.frameMaterial ? `${product.frameMaterial} frame.` : "",
    "Best discounted prices with pan-India free shipping. COD available.",
  ].filter(Boolean).join(" ").trim();
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
    if (value === null || value === undefined || value === "") return;
    const token = `${prefix}_${String(value).toLowerCase()}`;
    if (!seen.has(token)) {
      tags.push(token);
      seen.add(token);
    }
  };
  addVariantFallback("framesize",    product.frameSize);
  addVariantFallback("bridge",       product.bridge);
  addVariantFallback("templelength", product.templeLength);
  addVariantFallback("weight",       product.weight);
  addVariantFallback("framecolor",   product.frameColor);
  addVariantFallback("templecolor",  product.templeColor);

  return tags.join(", ");
}

/**
 * Generate HTML product description for Shopify listing page.
 * Structured table layout with product specs.
 */
export function generateHTMLDescription(product: Product): string {
  const styles = `
    <style>
      .product-details-section {
        font-family: Arial, sans-serif;
        color: #333;
        line-height: 1.6;
      }
      .section-title {
        font-size: 18px;
        font-weight: bold;
        margin: 20px 0 10px 0;
        color: #1a1a1a;
        border-bottom: 2px solid #007bff;
        padding-bottom: 8px;
      }
      .details-table {
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 20px;
      }
      .details-table th,
      .details-table td {
        border: 1px solid #ddd;
        padding: 12px;
        text-align: left;
      }
      .details-table th {
        background-color: #f8f9fa;
        font-weight: bold;
        color: #333;
      }
      .details-table tr:nth-child(even) {
        background-color: #f9f9f9;
      }
      .details-table tr:hover {
        background-color: #f0f0f0;
      }
    </style>
  `;

  const createTable = (rows: string[][]): string => {
    // Filter out N/A rows
    const validRows = rows.filter(([, value]) => value && value !== "N/A");
    if (validRows.length === 0) return "";
    let html = '<table class="details-table"><tbody>';
    validRows.forEach(([label, value]) => {
      html += `<tr><td><strong>${label}</strong></td><td>${value}</td></tr>`;
    });
    html += "</tbody></table>";
    return html;
  };

  const cat = (product.category || "").toUpperCase();

  // Build sections based on category
  const productDetailsRows = [
    ["Frame Color", product.frameColor || "N/A"],
    ["Temple Color", product.templeColor || "N/A"],
    ["Shape", product.shape || "N/A"],
    ["Weight", product.weight || "N/A"],
    ["Bridge Width", product.bridge || "N/A"],
    ["Temple Length", product.templeLength || "N/A"],
  ];

  const technicalRows = [
    ["Frame Material", product.frameMaterial || "N/A"],
    ["Temple Material", product.templeMaterial || "N/A"],
    ["Frame Type", product.frameType || "N/A"],
    ["Lens Material", product.lensMaterial || "N/A"],
    ...(cat === "SUNGLASSES" ? [
      ["Lens Colour", product.lensColour || "N/A"],
      ["Tint", product.tint || "N/A"],
      ["Polarization", product.polarization || "N/A"],
      ["UV Protection", product.uvProtection || "N/A"],
    ] : []),
  ];

  const generalRows = [
    ["Brand", product.brand || "N/A"],
    ["Model", product.fullModelNo || product.modelNo || "N/A"],
    ["Size", product.frameSize || "N/A"],
    ["Gender", product.gender || "N/A"],
    ["Country of Origin", product.countryOfOrigin || "N/A"],
    ["GTIN", product.gtin || "N/A"],
  ];

  let html = styles;
  html += '<div class="product-details-section">';

  if (cat === "SOLUTIONS") {
    // Solutions get a different layout
    const solutionRows = [
      ["Brand", product.brand || "N/A"],
      ["Product Name", product.productName || "N/A"],
      ["Recommended For", product.recommendedFor || "N/A"],
      ["Benefits", product.benefits || "N/A"],
      ["Ingredients", product.ingredients || "N/A"],
      ["Instructions", product.instructions || "N/A"],
    ];
    html += '<h3 class="section-title">Product Information</h3>';
    html += createTable(solutionRows);
    if (product.aboutProduct) {
      html += '<h3 class="section-title">About This Product</h3>';
      html += `<p>${product.aboutProduct}</p>`;
    }
  } else {
    html += '<h3 class="section-title">Product Details</h3>';
    html += createTable(productDetailsRows);
    html += '<h3 class="section-title">Technical Specifications</h3>';
    html += createTable(technicalRows);
    html += '<h3 class="section-title">General Information</h3>';
    html += createTable(generalRows);
  }

  if (product.warranty) {
    html += '<h3 class="section-title">Warranty</h3>';
    html += `<p>${product.warranty}</p>`;
  }

  if (product.productUSP) {
    html += '<h3 class="section-title">Why Choose This Product</h3>';
    html += `<p>${product.productUSP}</p>`;
  }

  html += "</div>";
  return html;
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
