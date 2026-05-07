// Extracted from src/app/api/shopify/pull/route.ts so that both the
// one-shot /api/shopify/pull endpoint and the resumable
// /api/shopify/pull/chunk endpoint can share the same upsert logic
// without a route-to-route import (which doesn't play nicely with
// Next.js production builds).
import { prisma } from "@/lib/prisma";
import {
  fetchShopifyLocations,
  type ShopifyProductNode,
} from "@/lib/shopify";
import { normalizeCategory } from "@/lib/categories";

function getMetafield(
  product: ShopifyProductNode,
  namespace: string,
  key: string
): string | null {
  const mf = product.metafields.edges.find(
    (e) => e.node.namespace === namespace && e.node.key === key
  );
  return mf?.node.value || null;
}

// Helper: map Shopify status to our status
function mapStatus(shopifyStatus: string): string {
  switch (shopifyStatus) {
    case "ACTIVE":
      return "PUBLISHED";
    case "DRAFT":
      return "DRAFT";
    case "ARCHIVED":
      return "ARCHIVED";
    default:
      return "DRAFT";
  }
}

// Helper: guess category from product type, tags, etc.
//
// Shopify productType data we've seen in the wild:
//   "spectacles" (lowercase), "Sunglass" (singular), "Sunglasses",
//   "Watch" (mistyped on a sunglass product), "" (empty)
// We match on substrings to be robust to all of those, and we return
// canonical UPPERCASE keys that match src/lib/categories.ts.
function guessCategory(product: ShopifyProductNode): string {
  const type = (product.productType || "").toLowerCase();
  const tags = product.tags.map((t) => t.toLowerCase());
  const title = product.title.toLowerCase();
  const haystack = `${type} ${title} ${tags.join(" ")}`;

  // More specific categories first — "smartwatch" must match before
  // generic "watch", "computer glasses" before "glasses", etc.
  if (haystack.includes("smartglass")) return "SMARTGLASSES";
  if (haystack.includes("smartwatch")) return "SMARTWATCHES";
  if (haystack.includes("contact lens") || haystack.includes("contactlens"))
    return "CONTACT_LENSES";
  if (
    haystack.includes("solution") ||
    haystack.includes("lens care")
  )
    return "CONTACT_LENSES";
  if (
    haystack.includes("safety glass") ||
    haystack.includes("safetyglass")
  )
    return "SAFETY_GLASSES";
  if (
    haystack.includes("computer glass") ||
    haystack.includes("computerglass") ||
    haystack.includes("blue light")
  )
    return "COMPUTER_GLASSES";
  if (
    haystack.includes("reading glass") ||
    haystack.includes("readingglass")
  )
    return "READING_GLASSES";
  if (
    haystack.includes("clip-on") ||
    haystack.includes("clip on") ||
    haystack.includes("clipon")
  )
    return "CLIP_ON_FRAMES";
  if (haystack.includes("sunglass")) return "SUNGLASSES";
  if (haystack.includes("watch")) return "WATCHES";
  if (
    haystack.includes("eyewear") ||
    haystack.includes("eyeglass") ||
    haystack.includes("spectacle") ||
    haystack.includes("frame")
  )
    return "SPECTACLES";
  if (
    haystack.includes("accessor") ||
    haystack.includes("case") ||
    haystack.includes("cleaner")
  )
    return "ACCESSORIES";
  return "SPECTACLES";
}

// Special compound word mappings (frame attributes + brand names)
// Shared across multiple helpers
const compoundWords: { [key: string]: string } = {
    // Frame attributes
    fullframe: "Full Frame",
    halfrim: "Half Rim",
    rimless: "Rimless",
    stainlesssteel: "Stainless Steel",
    "1year": "1 Year",
    "2year": "2 Year",
    "3year": "3 Year",
    "5year": "5 Year",
    nonpolarized: "Non Polarized",
    "100-uvprotection": "100% UV Protection",
    // Known eyewear brands
    tommyhilfiger: "Tommy Hilfiger",
    rayban: "Ray-Ban",
    "ray-ban": "Ray-Ban",
    hugoboss: "Hugo Boss",
    calvinklein: "Calvin Klein",
    armaniexchange: "Armani Exchange",
    ralphlauren: "Ralph Lauren",
    dolcegabbana: "Dolce & Gabbana",
    michaelkors: "Michael Kors",
    tomford: "Tom Ford",
    jimmychoo: "Jimmy Choo",
    katespade: "Kate Spade",
    montblanc: "Mont Blanc",
    carrera: "Carrera",
    polaroid: "Polaroid",
    oakley: "Oakley",
    vogue: "Vogue",
    versace: "Versace",
    prada: "Prada",
    gucci: "Gucci",
    boss: "Boss",
    fendi: "Fendi",
    burberry: "Burberry",
    bvlgari: "Bvlgari",
    emporio: "Emporio Armani",
    emporioarmani: "Emporio Armani",
    giorgioarmani: "Giorgio Armani",
    coach: "Coach",
    chopard: "Chopard",
    balmain: "Balmain",
    bolon: "Bolon",
    idee: "IDEE",
    opium: "Opium",
    johnjacobs: "John Jacobs",
    lenskart: "Lenskart",
    sevenstreet: "Seven Street",
    gio: "Gio",
    titan: "Titan",
    fastrack: "Fastrack",
    stepper: "Stepper",
    silhouette: "Silhouette",
    rodenstock: "Rodenstock",
    marcjacobs: "Marc Jacobs",
    davidbeckham: "David Beckham",
    pierrecardin: "Pierre Cardin",
    harryporter: "Harry Porter",
    scottsiyabenn: "Scott Siyabenn",
    marksmith: "Mark Smith",
    davidjones: "David Jones",
    "bettervision": "Better Vision",
};

// Helper: convert hyphenated/lowercase text to title case
// Handles compound words like "fullframe" -> "Full Frame", "orochiaro" -> "Orochiaro"
function toTitleCase(text: string): string {
  if (!text) return "";

  // Check for exact matches first
  if (compoundWords[text.toLowerCase()]) {
    return compoundWords[text.toLowerCase()];
  }

  // For normal words, capitalize first letter of each word
  return text
    .split(/[\s_-]+/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

// Helper: parse Shopify tags into structured fields
// Tags format: "brand_burberry", "shape_square", "framecolor_orochiaro", etc.
function parseTagsToFields(tags: string[]): {
  brand?: string;
  subBrand?: string;
  shape?: string;
  frameColor?: string;
  templeColor?: string;
  frameMaterial?: string;
  templeMaterial?: string;
  frameType?: string;
  frameSize?: string;
  bridge?: string;
  templeLength?: string;
  warranty?: string;
  weight?: string;
  gender?: string;
  lensColour?: string;
  tint?: string;
  polarization?: string;
  uvProtection?: string;
  lensMaterial?: string;
  lensUSP?: string;
  countryOfOrigin?: string;
  productUSP?: string;
} {
  const parsed: any = {};

  for (const tag of tags) {
    const [prefix, ...valueParts] = tag.split("_");
    if (!prefix || valueParts.length === 0) continue;

    const value = valueParts.join("_").trim(); // Handle cases where value contains underscore
    // Skip empty-value tags. Historical pushes emitted "lensusp_",
    // "productusp_", "origin_", "lensmaterial_", "product_" with no value
    // after the underscore. Without this guard, parseTagsToFields would
    // overwrite real attribute data with empty strings during pull.
    if (!value) continue;
    const lowerPrefix = prefix.toLowerCase();

    // Parse known tag prefixes
    if (lowerPrefix === "shape") {
      parsed.shape = toTitleCase(value);
    } else if (lowerPrefix === "framecolor") {
      parsed.frameColor = toTitleCase(value);
    } else if (lowerPrefix === "templecolor") {
      parsed.templeColor = toTitleCase(value);
    } else if (lowerPrefix === "framematerial") {
      parsed.frameMaterial = toTitleCase(value);
    } else if (lowerPrefix === "templematerial") {
      parsed.templeMaterial = toTitleCase(value);
    } else if (lowerPrefix === "frametype") {
      parsed.frameType = toTitleCase(value);
    } else if (lowerPrefix === "framesize") {
      parsed.frameSize = value; // Keep as-is (numeric)
    } else if (lowerPrefix === "bridge") {
      parsed.bridge = value; // Keep as-is (numeric)
    } else if (lowerPrefix === "templelength") {
      parsed.templeLength = value; // Keep as-is (numeric)
    } else if (lowerPrefix === "warranty") {
      parsed.warranty = toTitleCase(value);
    } else if (lowerPrefix === "weight") {
      parsed.weight = value; // Keep as-is (with unit like "27g")
    } else if (lowerPrefix === "gender") {
      parsed.gender = toTitleCase(value);
    } else if (lowerPrefix === "brand") {
      parsed.brand = toTitleCase(value);
    } else if (lowerPrefix === "lenscolour") {
      parsed.lensColour = toTitleCase(value);
    } else if (lowerPrefix === "tint") {
      parsed.tint = toTitleCase(value);
    } else if (lowerPrefix === "polarization") {
      parsed.polarization = toTitleCase(value);
    } else if (lowerPrefix === "uvprotection") {
      parsed.uvProtection = toTitleCase(value);
    } else if (lowerPrefix === "lensmaterial") {
      parsed.lensMaterial = toTitleCase(value);
    } else if (lowerPrefix === "lensusp") {
      parsed.lensUSP = toTitleCase(value);
    } else if (lowerPrefix === "origin") {
      parsed.countryOfOrigin = toTitleCase(value);
    } else if (lowerPrefix === "productusp") {
      parsed.productUSP = toTitleCase(value);
    } else if (lowerPrefix === "subbrand") {
      parsed.subBrand = toTitleCase(value);
    }
  }

  return parsed;
}

// Helper: upsert a single Shopify product into local DB
// Sync real Shopify locations into the local Location table and return
// a map of shopifyLocationId → local Location.id that upsertProduct can
// use to write per-location VariantLocation rows in a single pass.
export async function syncShopifyLocationsToMap(): Promise<{
  locationMap: Map<string, string>;
  synced: number;
}> {
  const map = new Map<string, string>();
  let synced = 0;
  try {
    const locResult = await fetchShopifyLocations();
    if (locResult.success && locResult.locations) {
      for (const loc of locResult.locations) {
        const code =
          loc.name
            .toUpperCase()
            .replace(/[^A-Z0-9]/g, "")
            .substring(0, 10) || loc.id.split("/").pop()!;
        const local = await prisma.location.upsert({
          where: { shopifyLocationId: loc.id },
          update: {
            name: loc.name,
            address: loc.address?.formatted?.join(", ") || null,
            isActive: loc.isActive ?? true,
          },
          create: {
            name: loc.name,
            code,
            address: loc.address?.formatted?.join(", ") || null,
            shopifyLocationId: loc.id,
            isActive: loc.isActive ?? true,
          },
        });
        map.set(loc.id, local.id);
        synced++;
      }
    }
  } catch (e) {
    console.error("Location sync during pull failed:", e);
  }
  return { locationMap: map, synced };
}

export async function upsertShopifyProduct(
  sp: ShopifyProductNode,
  locationMap: Map<string, string> = new Map()
) {
  const existing = await prisma.product.findFirst({
    where: { shopifyProductId: sp.id },
    include: { variants: true, images: true },
  });

  // Use Shopify productType, but normalize via the alias map so legacy
  // values like "Sunglass" / "spectacles" / "Watch" all map to the
  // canonical UPPERCASE keys we use locally. If it doesn't match anything,
  // guess from tags + title.
  const rawCategory = sp.productType
    ? normalizeCategory(sp.productType)
    : "";
  const KNOWN_CATEGORIES = new Set([
    "SPECTACLES", "CLIP_ON_FRAMES", "SUNGLASSES", "READING_GLASSES",
    "COMPUTER_GLASSES", "SAFETY_GLASSES", "CONTACT_LENSES", "SMARTGLASSES",
    "WATCHES", "SMARTWATCHES", "ACCESSORIES",
  ]);
  const category = KNOWN_CATEGORIES.has(rawCategory)
    ? rawCategory
    : guessCategory(sp);
  const modelNo = getMetafield(sp, "custom", "model_no") || "";

  // Extract first variant price as base MRP
  const firstVariant = sp.variants.edges[0]?.node;
  const baseMrp = firstVariant
    ? parseFloat(firstVariant.compareAtPrice || firstVariant.price || "0")
    : 0;
  const basePrice = firstVariant ? parseFloat(firstVariant.price || "0") : 0;

  // Parse tags to extract structured fields
  const parsedTags = parseTagsToFields(sp.tags);

  // Brand priority: tag (brand_boss) > metafield > vendor
  // If vendor is the store name "Better Vision", try to extract brand from title
  const vendorName = sp.vendor || "";
  const isStoreName = vendorName.toLowerCase().includes("better vision") || vendorName.toLowerCase().includes("bettervision");

  // Try to extract brand from the first word(s) of the title via compound word lookup
  let titleBrand = "";
  if (sp.title) {
    const titleWords = sp.title.split(/\s+/);
    // Try first two words, then first word
    const twoWordKey = titleWords.slice(0, 2).join("").toLowerCase();
    const oneWordKey = titleWords[0]?.toLowerCase() || "";
    if (compoundWords[twoWordKey]) {
      titleBrand = compoundWords[twoWordKey];
    } else if (compoundWords[oneWordKey]) {
      titleBrand = compoundWords[oneWordKey];
    }
  }

  const brand =
    parsedTags.brand ||
    getMetafield(sp, "custom", "brand") ||
    (isStoreName ? titleBrand : vendorName) ||
    vendorName ||
    "";

  // Try to extract model number from title if metafield is empty
  // Titles are often "BOSS 1234" or "PRADA PR 54XV" — strip the brand prefix
  let effectiveModelNo = modelNo;
  if (!effectiveModelNo && sp.title && brand && brand !== "Unknown") {
    const titleUpper = sp.title.toUpperCase();
    const brandUpper = brand.toUpperCase();
    if (titleUpper.startsWith(brandUpper)) {
      effectiveModelNo = sp.title.slice(brand.length).trim();
    } else {
      // Title might not start with brand, use the full title as model reference
      effectiveModelNo = sp.title;
    }
  }

  // Build productData, only adding parsed tag values if the field is null/empty
  const productData: any = {
    shopifyProductId: sp.id,
    title: sp.title,
    category,
    status: mapStatus(sp.status),
    brand,
    modelNo: effectiveModelNo || null,
    fullModelNo: sp.title || null,
    productName: sp.title || null,
    htmlDescription: sp.descriptionHtml || null,
    seoTitle: sp.seo?.title || null,
    seoDescription: sp.seo?.description || null,
    tags: sp.tags.join(", "),
    pageUrl: sp.handle || null,
    mrp: baseMrp,
    discountedPrice: basePrice,
    compareAtPrice: baseMrp,
    gender: getMetafield(sp, "custom", "gender") || null,
    frameMaterial: getMetafield(sp, "custom", "frame_material") || null,
    shape: getMetafield(sp, "custom", "shape") || null,
    countryOfOrigin: getMetafield(sp, "custom", "country_of_origin") || null,
    warranty: getMetafield(sp, "custom", "warranty") || null,
  };

  // Add parsed tag values only if field is currently null/empty
  if (!productData.shape && parsedTags.shape) {
    productData.shape = parsedTags.shape;
  }
  if (!productData.frameMaterial && parsedTags.frameMaterial) {
    productData.frameMaterial = parsedTags.frameMaterial;
  }
  if (!productData.gender && parsedTags.gender) {
    productData.gender = parsedTags.gender;
  }
  if (!productData.warranty && parsedTags.warranty) {
    productData.warranty = parsedTags.warranty;
  }

  // Product-level fields (material/type/lens/UV etc. are shared across variants).
  if (parsedTags.templeMaterial) {
    productData.templeMaterial = parsedTags.templeMaterial;
  }
  if (parsedTags.frameType) {
    productData.frameType = parsedTags.frameType;
  }
  // NOTE: legacy variant-level fields (frameColor, templeColor, frameSize,
  // bridge, templeLength, weight, lensColour, tint) are no longer written
  // to Product. They live on ProductVariant now. The columns still exist
  // on Product for backward compat with existing data; we just stopped
  // writing to them so new pulls don't reintroduce duplication.
  if (parsedTags.polarization) {
    productData.polarization = parsedTags.polarization;
  }
  if (parsedTags.uvProtection) {
    productData.uvProtection = parsedTags.uvProtection;
  }
  if (parsedTags.lensMaterial && !productData.lensMaterial) {
    productData.lensMaterial = parsedTags.lensMaterial;
  }
  if (parsedTags.lensUSP) {
    productData.lensUSP = parsedTags.lensUSP;
  }
  if (parsedTags.countryOfOrigin && !productData.countryOfOrigin) {
    productData.countryOfOrigin = parsedTags.countryOfOrigin;
  }
  if (parsedTags.productUSP) {
    productData.productUSP = parsedTags.productUSP;
  }
  if (parsedTags.subBrand) {
    productData.subBrand = parsedTags.subBrand;
  }

  let productId: string;

  if (existing) {
    // Update existing product
    await prisma.product.update({
      where: { id: existing.id },
      data: productData,
    });
    productId = existing.id;
  } else {
    // Create new product
    const created = await prisma.product.create({
      data: {
        ...productData,
        sku: firstVariant?.sku || `SHOP-${sp.handle || sp.id.split("/").pop()}`,
      },
    });
    productId = created.id;
  }

  // ── Sync images ──
  // Get existing image URLs to avoid duplicates
  const existingImages = existing?.images || [];
  const existingImageUrls = new Set(existingImages.map((i) => i.url));

  for (let idx = 0; idx < sp.images.edges.length; idx++) {
    const img = sp.images.edges[idx].node;
    if (!existingImageUrls.has(img.url)) {
      await prisma.productImage.create({
        data: {
          productId,
          url: img.url,
          originalUrl: img.url,
          position: idx,
          shopifyMediaId: img.id,
          isProcessed: true,
        },
      });
    }
  }

  // ── Sync variants ──
  const existingVariants = existing?.variants || [];
  const existingVariantShopifyIds = new Set(
    existingVariants.map((v) => v.shopifyVariantId).filter(Boolean)
  );

  for (const ve of sp.variants.edges) {
    const sv = ve.node;
    if (existingVariantShopifyIds.has(sv.id)) {
      // Update existing variant
      const localVariant = existingVariants.find(
        (v) => v.shopifyVariantId === sv.id
      );
      if (localVariant) {
        await prisma.productVariant.update({
          where: { id: localVariant.id },
          data: {
            mrp: parseFloat(sv.compareAtPrice || sv.price || "0"),
            discountedPrice: parseFloat(sv.price || "0"),
            compareAtPrice: parseFloat(sv.compareAtPrice || sv.price || "0"),
            barcode: sv.barcode || null,
            title: sv.title,
            shopifyInventoryItemId: sv.inventoryItem?.id || null,
          },
        });
      }
    } else {
      // Extract color and size from selectedOptions
      const colorOpt = sv.selectedOptions.find(
        (o) => o.name.toLowerCase() === "color" || o.name.toLowerCase() === "colour"
      );
      const sizeOpt = sv.selectedOptions.find(
        (o) => o.name.toLowerCase() === "size"
      );

      const colorCode = colorOpt?.value || sv.title.split(" / ")[0] || "DEFAULT";
      const frameSize = sizeOpt?.value || sv.title.split(" / ")[1] || null;

      // Check if this variant already exists by colorCode + frameSize
      const existingByCode = existingVariants.find(
        (v) => v.colorCode === colorCode && v.frameSize === frameSize
      );

      if (existingByCode) {
        await prisma.productVariant.update({
          where: { id: existingByCode.id },
          data: {
            shopifyVariantId: sv.id,
            shopifyInventoryItemId: sv.inventoryItem?.id || null,
            mrp: parseFloat(sv.compareAtPrice || sv.price || "0"),
            discountedPrice: parseFloat(sv.price || "0"),
            compareAtPrice: parseFloat(sv.compareAtPrice || sv.price || "0"),
            barcode: sv.barcode || null,
            title: sv.title,
            sku: sv.sku || null,
          },
        });
      } else {
        try {
          await prisma.productVariant.create({
            data: {
              productId,
              shopifyVariantId: sv.id,
              shopifyInventoryItemId: sv.inventoryItem?.id || null,
              colorCode,
              frameSize,
              mrp: parseFloat(sv.compareAtPrice || sv.price || "0"),
              discountedPrice: parseFloat(sv.price || "0"),
              compareAtPrice: parseFloat(sv.compareAtPrice || sv.price || "0"),
              sku: sv.sku || null,
              barcode: sv.barcode || null,
              title: sv.title,
            },
          });
        } catch (e) {
          // Duplicate key — skip silently
          console.warn(`Skipped duplicate variant: ${sv.id}`, e);
        }
      }
    }
  }

  // ── Sync inventory quantities ──
  // Get or create a default "Shopify" location for inventory tracking
  let defaultLocation = await prisma.location.findFirst({
    where: { code: "SHOPIFY" },
  });
  if (!defaultLocation) {
    defaultLocation = await prisma.location.create({
      data: {
        name: "Shopify Online Store",
        code: "SHOPIFY",
        address: "Online",
        isActive: true,
      },
    });
  }

  // Upsert product-level inventory (totalInventory)
  await prisma.productLocation.upsert({
    where: {
      productId_locationId: {
        productId,
        locationId: defaultLocation.id,
      },
    },
    update: { quantity: sp.totalInventory || 0 },
    create: {
      productId,
      locationId: defaultLocation.id,
      quantity: sp.totalInventory || 0,
    },
  });

  // Upsert variant-level inventory (inventoryQuantity — aggregated across
  // all Shopify locations, kept on the synthetic SHOPIFY location for
  // backwards compatibility with existing reports).
  const updatedVariants = await prisma.productVariant.findMany({
    where: { productId },
  });

  const perLocationEnabled =
    (process.env.PULL_PER_LOCATION_INVENTORY ?? "true").toLowerCase() !==
    "false";

  for (const ve of sp.variants.edges) {
    const sv = ve.node;
    const localVariant = updatedVariants.find(
      (v) => v.shopifyVariantId === sv.id
    );
    if (!localVariant) continue;

    // Aggregate (SHOPIFY synthetic location)
    await prisma.variantLocation.upsert({
      where: {
        variantId_locationId: {
          variantId: localVariant.id,
          locationId: defaultLocation.id,
        },
      },
      update: { quantity: sv.inventoryQuantity || 0 },
      create: {
        variantId: localVariant.id,
        locationId: defaultLocation.id,
        quantity: sv.inventoryQuantity || 0,
      },
    });

    // Per-location (real Shopify locations)
    if (!perLocationEnabled) continue;
    const levels = sv.inventoryItem?.inventoryLevels?.edges || [];
    for (const le of levels) {
      const shopifyLocId = le.node.location.id;
      const localLocId = locationMap.get(shopifyLocId);
      if (!localLocId) {
        // Location hasn't been synced yet — skip; next pull will catch it.
        continue;
      }
      const available = le.node.quantities.find((q) => q.name === "available");
      const qty = available?.quantity ?? 0;
      await prisma.variantLocation.upsert({
        where: {
          variantId_locationId: {
            variantId: localVariant.id,
            locationId: localLocId,
          },
        },
        update: { quantity: qty },
        create: {
          variantId: localVariant.id,
          locationId: localLocId,
          quantity: qty,
        },
      });
    }
  }

  return productId;
}
