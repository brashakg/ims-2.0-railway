import { prisma } from "@/lib/prisma";
import {
  createProduct,
  setProductMetafields,
  setVariantMetafields,
  type ShopifyVariantInput,
} from "@/lib/shopify";
import { categoryLabel } from "@/lib/categories";

// Map our internal product status (DRAFT/PUBLISHED/ARCHIVED) to the
// values Shopify expects (ACTIVE/DRAFT/ARCHIVED). PUBLISHED → ACTIVE so
// the listing actually goes live; otherwise keep it as DRAFT until a
// staff member explicitly publishes.
function shopifyStatusFor(status: string | null | undefined): "ACTIVE" | "DRAFT" | "ARCHIVED" {
  const s = (status || "").toUpperCase();
  if (s === "PUBLISHED") return "ACTIVE";
  if (s === "ARCHIVED") return "ARCHIVED";
  return "DRAFT";
}

export type PushResultStatus = "SUCCESS" | "FAILED" | "SKIPPED";

export interface PushResult {
  productId: string;
  status: PushResultStatus;
  message?: string;
  shopifyProductId?: string;
  variantCount?: number;
}

export interface PushSummary {
  total: number;
  success: number;
  failed: number;
  skipped: number;
  aborted?: boolean;
  abortReason?: string;
}

type ProductWithRelations = Awaited<ReturnType<typeof fetchProductsForPush>>[number];

export async function fetchProductsForPush(ids: string[]) {
  return prisma.product.findMany({
    where: { id: { in: ids } },
    include: {
      images: { orderBy: { position: "asc" } },
      variants: {
        include: {
          images: { orderBy: { position: "asc" } },
          locations: true,
        },
        orderBy: [{ colorCode: "asc" }, { frameSize: "asc" }],
      },
    },
  });
}

export interface PushOptions {
  batchSize?: number;
  batchDelayMs?: number;
  /**
   * Circuit breaker: bail out of the push loop after this many CONSECUTIVE
   * failures. Default 5. Set to 0 to disable. Protects against the scenario
   * where Shopify is returning errors for every call (bad creds, rate cap,
   * validation failures repeating) so we don't hammer the API 60+ times.
   */
  maxConsecutiveFailures?: number;
}

export async function pushProductsToShopify(
  products: ProductWithRelations[],
  options: PushOptions = {}
): Promise<{ results: PushResult[]; summary: PushSummary }> {
  const BATCH_SIZE = options.batchSize ?? 10;
  const BATCH_DELAY_MS = options.batchDelayMs ?? 1000;
  const MAX_CONSECUTIVE_FAILURES = options.maxConsecutiveFailures ?? 5;

  const results: PushResult[] = [];
  let consecutiveFailures = 0;
  let aborted = false;
  let abortReason: string | undefined;

  for (let i = 0; i < products.length; i++) {
    const product = products[i];

    if (i > 0 && i % BATCH_SIZE === 0) {
      await new Promise((resolve) => setTimeout(resolve, BATCH_DELAY_MS));
    }

    if (product.shopifyProductId) {
      results.push({
        productId: product.id,
        status: "SKIPPED",
        message: "Product already synced to Shopify",
      });
      continue;
    }

    try {
      const hasVariants = product.variants.length > 0;

      const uniqueColors = [
        ...new Set(product.variants.map((v) => v.colorCode).filter(Boolean)),
      ];
      const uniqueSizes = [
        ...new Set(
          product.variants
            .map((v) => v.frameSize)
            .filter(Boolean) as string[]
        ),
      ];

      const productOptions: Array<{
        name: string;
        values: Array<{ name: string }>;
      }> = [];

      if (hasVariants && uniqueColors.length > 0) {
        productOptions.push({
          name: "Color",
          values: uniqueColors.map((c) => ({ name: c })),
        });
      }
      if (hasVariants && uniqueSizes.length > 0) {
        productOptions.push({
          name: "Size",
          values: uniqueSizes.map((s) => ({ name: s })),
        });
      }

      const shopifyVariants: ShopifyVariantInput[] = hasVariants
        ? product.variants.map((v) => {
            const optionValues: Array<{ optionName: string; name: string }> = [];
            if (v.colorCode) {
              optionValues.push({ optionName: "Color", name: v.colorCode });
            }
            if (v.frameSize) {
              optionValues.push({ optionName: "Size", name: v.frameSize });
            }
            // Variant barcode fallback chain: variant.barcode → product.gtin
            // → product.upc. GTIN/UPC are pushed to Shopify's variant.barcode
            // field — that's what Google Shopping / marketplaces read.
            const barcode =
              v.barcode || product.gtin || product.upc || "";
            return {
              optionValues,
              price: (v.discountedPrice || v.mrp || product.mrp || 0).toString(),
              compareAtPrice: (v.mrp || product.mrp || 0).toString(),
              sku: v.sku || "",
              barcode,
            };
          })
        : [];

      const allImages = [
        ...product.images.map((img) => ({
          src: img.url.startsWith("http")
            ? img.url
            : `${process.env.NEXTAUTH_URL || "http://localhost:3000"}${img.url}`,
          alt: product.title || "",
        })),
        ...product.variants.flatMap((v) =>
          v.images.map((img) => ({
            src: img.url.startsWith("http")
              ? img.url
              : `${process.env.NEXTAUTH_URL || "http://localhost:3000"}${img.url}`,
            alt: `${product.title || ""} ${v.colorCode || ""}`.trim(),
          }))
        ),
      ];

      const shopifyResult = await createProduct({
        title: product.title || `${product.brand} ${product.modelNo || ""}`.trim(),
        description: product.htmlDescription || "",
        images: allImages.length > 0 ? allImages : undefined,
        seoTitle: product.seoTitle || "",
        seoDescription: product.seoDescription || "",
        tags: product.tags?.split(", ").map((t) => t.trim()).filter(Boolean) || [],
        productOptions: productOptions.length > 0 ? productOptions : undefined,
        variants: shopifyVariants.length > 0 ? shopifyVariants : undefined,
        // Audit fixes:
        //   vendor = brand (was defaulting to store name)
        //   productType = human label (was sending UPPERCASE enum key)
        //   status     = mapped from product.status, not always ACTIVE
        vendor: product.brand || undefined,
        productType: categoryLabel(product.category) || product.category || "",
        status: shopifyStatusFor(product.status),
      });

      if (shopifyResult.success && shopifyResult.shopifyId) {
        await prisma.product.update({
          where: { id: product.id },
          data: {
            shopifyProductId: shopifyResult.shopifyId,
            status: "PUBLISHED",
          },
        });

        if (shopifyResult.variantIds && hasVariants) {
          for (const svId of shopifyResult.variantIds) {
            if (svId.sku) {
              const localVariant = product.variants.find(
                (v) => v.sku === svId.sku
              );
              if (localVariant) {
                await prisma.productVariant.update({
                  where: { id: localVariant.id },
                  data: { shopifyVariantId: svId.shopifyVariantId },
                });

                // Push per-variant metafields. colorName, frameColor,
                // templeColor, lensColour and tint vary by variant — they
                // belong on the variant, not on the parent product. The
                // storefront can read these to show "Havana" instead of
                // raw colour code "086".
                const variantMfs: Array<{
                  namespace: string;
                  key: string;
                  value: string;
                  type: string;
                }> = [];
                const pushVMf = (key: string, value: string | null | undefined) => {
                  if (value && String(value).trim()) {
                    variantMfs.push({
                      namespace: "custom",
                      key,
                      value: String(value).trim(),
                      type: "single_line_text_field",
                    });
                  }
                };
                pushVMf("color_name", localVariant.colorName);
                pushVMf("frame_color", localVariant.frameColor);
                pushVMf("temple_color", localVariant.templeColor);
                pushVMf("lens_colour", localVariant.lensColour);
                pushVMf("tint", localVariant.tint);
                pushVMf("bridge", localVariant.bridge);
                pushVMf("temple_length", localVariant.templeLength);
                pushVMf("weight", localVariant.weight);
                if (variantMfs.length > 0) {
                  await setVariantMetafields(svId.shopifyVariantId, variantMfs).catch(
                    () => {}
                  );
                }
              }
            }
          }
        }

        // Audit 2026-05: expanded metafield set from 7 → 15 keys.
        // Tag-only attrs (templeMaterial, frameType, polarization,
        // uvProtection, lensMaterial, lensColour, tint, productUSP,
        // subBrand) are fragile — staff can accidentally remove them by
        // editing tags in Shopify admin. Pushing them as metafields too
        // means they survive any tag edit.
        const metafields: Array<{
          namespace: string;
          key: string;
          value: string;
          type: string;
        }> = [];
        const pushMetafield = (key: string, value: string | null | undefined) => {
          if (value && String(value).trim()) {
            metafields.push({
              namespace: "custom",
              key,
              value: String(value).trim(),
              type: "single_line_text_field",
            });
          }
        };
        // Identity (all on Product table)
        pushMetafield("brand", product.brand);
        pushMetafield("sub_brand", product.subBrand);
        pushMetafield("model_no", product.modelNo);
        pushMetafield("label", product.label);
        // Frame (all on Product)
        pushMetafield("shape", product.shape);
        pushMetafield("frame_material", product.frameMaterial);
        pushMetafield("temple_material", product.templeMaterial);
        pushMetafield("frame_type", product.frameType);
        // Lens-base (on Product). lensColour and tint are per-variant
        // (they live on ProductVariant) so they're set as variant
        // metafields below in the variantIds loop, NOT as product
        // metafields here. Pulling a single value from one variant
        // would mislead anyone reading the product page.
        pushMetafield("lens_material", product.lensMaterial);
        pushMetafield("polarization", product.polarization);
        pushMetafield("uv_protection", product.uvProtection);
        // Demographics / origin / commerce
        pushMetafield("gender", product.gender);
        pushMetafield("country_of_origin", product.countryOfOrigin);
        pushMetafield("warranty", product.warranty);
        pushMetafield("product_usp", product.productUSP);

        if (metafields.length > 0) {
          await setProductMetafields(shopifyResult.shopifyId, metafields);
        }

        await prisma.syncLog.create({
          data: {
            productId: product.id,
            action: "SYNC",
            status: "SUCCESS",
            message: `Synced to Shopify with ${shopifyResult.variantIds?.length || 0} variant(s)`,
          },
        });

        results.push({
          productId: product.id,
          status: "SUCCESS",
          shopifyProductId: shopifyResult.shopifyId,
          variantCount: shopifyResult.variantIds?.length || 0,
          message: shopifyResult.message,
        });
        consecutiveFailures = 0;
      } else {
        await prisma.syncLog.create({
          data: {
            productId: product.id,
            action: "SYNC",
            status: "FAILED",
            message: shopifyResult.message,
          },
        });

        results.push({
          productId: product.id,
          status: "FAILED",
          message: shopifyResult.message,
        });
        consecutiveFailures++;
      }
    } catch (syncError) {
      const errMsg =
        syncError instanceof Error ? syncError.message : "Unknown sync error";

      await prisma.syncLog.create({
        data: {
          productId: product.id,
          action: "SYNC",
          status: "FAILED",
          message: errMsg,
        },
      });

      results.push({
        productId: product.id,
        status: "FAILED",
        message: errMsg,
      });
      consecutiveFailures++;
    }

    // Circuit breaker: after N consecutive failures, assume the system is in
    // a bad state (auth expired, throttle-storm, validation bug) and stop.
    // Without this, a broken config would churn through hundreds of products
    // and bury the real error in a wall of failed SyncLog rows.
    if (
      MAX_CONSECUTIVE_FAILURES > 0 &&
      consecutiveFailures >= MAX_CONSECUTIVE_FAILURES
    ) {
      aborted = true;
      abortReason = `Aborted after ${consecutiveFailures} consecutive failures. Check Shopify credentials, scopes, and recent SyncLog entries before retrying.`;
      break;
    }
  }

  const summary: PushSummary = {
    total: products.length,
    success: results.filter((r) => r.status === "SUCCESS").length,
    failed: results.filter((r) => r.status === "FAILED").length,
    skipped: results.filter((r) => r.status === "SKIPPED").length,
    aborted,
    abortReason,
  };

  return { results, summary };
}
