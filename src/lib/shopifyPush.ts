import { prisma } from "@/lib/prisma";
import {
  createProduct,
  setProductMetafields,
  type ShopifyVariantInput,
} from "@/lib/shopify";

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
}

export async function pushProductsToShopify(
  products: ProductWithRelations[],
  options: PushOptions = {}
): Promise<{ results: PushResult[]; summary: PushSummary }> {
  const BATCH_SIZE = options.batchSize ?? 10;
  const BATCH_DELAY_MS = options.batchDelayMs ?? 1000;

  const results: PushResult[] = [];

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
            return {
              optionValues,
              price: (v.discountedPrice || v.mrp || product.mrp || 0).toString(),
              compareAtPrice: (v.mrp || product.mrp || 0).toString(),
              sku: v.sku || "",
              barcode: v.barcode || "",
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
        tags: product.tags?.split(", ") || [],
        productOptions: productOptions.length > 0 ? productOptions : undefined,
        variants: shopifyVariants.length > 0 ? shopifyVariants : undefined,
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
              }
            }
          }
        }

        const metafields: Array<{
          namespace: string;
          key: string;
          value: string;
          type: string;
        }> = [];
        const pushMetafield = (key: string, value: string | null | undefined) => {
          if (value) {
            metafields.push({
              namespace: "custom",
              key,
              value,
              type: "single_line_text_field",
            });
          }
        };
        pushMetafield("brand", product.brand);
        pushMetafield("model_no", product.modelNo);
        pushMetafield("frame_material", product.frameMaterial);
        pushMetafield("shape", product.shape);
        pushMetafield("gender", product.gender);
        pushMetafield("country_of_origin", product.countryOfOrigin);
        pushMetafield("warranty", product.warranty);

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
    }
  }

  const summary: PushSummary = {
    total: products.length,
    success: results.filter((r) => r.status === "SUCCESS").length,
    failed: results.filter((r) => r.status === "FAILED").length,
    skipped: results.filter((r) => r.status === "SKIPPED").length,
  };

  return { results, summary };
}
