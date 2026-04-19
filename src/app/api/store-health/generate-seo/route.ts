import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import { generateSeoForProduct } from "@/lib/seoGenerator";
import { updateProduct } from "@/lib/shopify";

interface GenerateSeoRequest {
  limit?: number;
  dryRun?: boolean;
  pushToShopify?: boolean;
  mode?: "missing_title" | "missing_description" | "missing_either";
  productIds?: string[]; // override: only process these
}

// POST /api/store-health/generate-seo
// Uses Anthropic's API to generate missing SEO titles + meta descriptions
// for products that lack them. Processes up to `limit` products per call
// (default 25, max 50 to keep server-side runtime < 60s per chunk).
// Call repeatedly from the UI to work through the backlog.
export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const body: GenerateSeoRequest = await request.json().catch(() => ({}));
    const limit = Math.min(Math.max(body.limit ?? 25, 1), 50);
    const dryRun = body.dryRun === true;
    const pushToShopify = body.pushToShopify !== false; // default true
    const mode = body.mode ?? "missing_either";

    // Build the "needs SEO" filter
    let gapFilter: Record<string, unknown>;
    if (mode === "missing_title") {
      gapFilter = { OR: [{ seoTitle: null }, { seoTitle: "" }] };
    } else if (mode === "missing_description") {
      gapFilter = { OR: [{ seoDescription: null }, { seoDescription: "" }] };
    } else {
      gapFilter = {
        OR: [
          { seoTitle: null },
          { seoTitle: "" },
          { seoDescription: null },
          { seoDescription: "" },
        ],
      };
    }

    const where: Record<string, unknown> =
      body.productIds && body.productIds.length > 0
        ? { id: { in: body.productIds } }
        : gapFilter;

    const products = await prisma.product.findMany({
      where,
      take: limit,
      orderBy: { updatedAt: "asc" },
      include: {
        variants: {
          select: { colorName: true, colorCode: true, frameSize: true },
          take: 20,
        },
      },
    });

    if (products.length === 0) {
      return NextResponse.json({
        success: true,
        message: "No products match the filter — nothing to generate.",
        summary: { generated: 0, skipped: 0, failed: 0, pushed: 0 },
        remaining: 0,
      });
    }

    const results: Array<{
      productId: string;
      title?: string;
      status: "SUCCESS" | "FAILED" | "SKIPPED";
      message?: string;
      generated?: { seoTitle: string; seoDescription: string };
      pushed?: boolean;
    }> = [];

    let totalTokensIn = 0;
    let totalTokensOut = 0;
    let totalCacheRead = 0;
    let generated = 0;
    let pushed = 0;
    let failed = 0;
    let skipped = 0;

    for (const p of products) {
      try {
        const variantColors = Array.from(
          new Set(
            p.variants
              .map((v) => v.colorName || v.colorCode)
              .filter((c): c is string => Boolean(c && c.trim()))
          )
        );
        const variantSizes = Array.from(
          new Set(
            p.variants
              .map((v) => v.frameSize)
              .filter((s): s is string => Boolean(s && s.trim()))
          )
        );

        const seo = await generateSeoForProduct({
          brand: p.brand,
          modelNo: p.modelNo,
          title: p.title,
          category: p.category,
          shape: p.shape,
          frameMaterial: p.frameMaterial,
          frameType: p.frameType,
          gender: p.gender,
          countryOfOrigin: p.countryOfOrigin,
          warranty: p.warranty,
          lensMaterial: p.lensMaterial,
          polarization: p.polarization,
          uvProtection: p.uvProtection,
          productUSP: p.productUSP,
          variantColors,
          variantSizes,
        });

        totalTokensIn += seo.tokensIn || 0;
        totalTokensOut += seo.tokensOut || 0;
        totalCacheRead += seo.cacheReadTokens || 0;

        if (dryRun) {
          results.push({
            productId: p.id,
            title: p.title || undefined,
            status: "SUCCESS",
            generated: {
              seoTitle: seo.seoTitle,
              seoDescription: seo.seoDescription,
            },
            message: "Dry run — not persisted",
          });
          generated++;
          continue;
        }

        // Only fill gaps, never overwrite existing non-empty SEO.
        const updateData: { seoTitle?: string; seoDescription?: string } = {};
        if (!p.seoTitle || !p.seoTitle.trim()) {
          updateData.seoTitle = seo.seoTitle;
        }
        if (!p.seoDescription || !p.seoDescription.trim()) {
          updateData.seoDescription = seo.seoDescription;
        }

        if (Object.keys(updateData).length === 0) {
          results.push({
            productId: p.id,
            title: p.title || undefined,
            status: "SKIPPED",
            message: "Both SEO fields already set; no gaps to fill",
          });
          skipped++;
          continue;
        }

        await prisma.product.update({
          where: { id: p.id },
          data: updateData,
        });

        let pushedNow = false;
        if (pushToShopify && p.shopifyProductId) {
          const r = await updateProduct(p.shopifyProductId, {
            seoTitle: updateData.seoTitle,
            seoDescription: updateData.seoDescription,
          });
          pushedNow = r.success;
          if (!r.success) {
            await prisma.syncLog.create({
              data: {
                productId: p.id,
                action: "SEO_UPDATE",
                status: "FAILED",
                message: r.message,
              },
            });
          } else {
            pushed++;
            await prisma.syncLog.create({
              data: {
                productId: p.id,
                action: "SEO_UPDATE",
                status: "SUCCESS",
                message: "SEO title/description synced to Shopify",
              },
            });
          }
        }

        results.push({
          productId: p.id,
          title: p.title || undefined,
          status: "SUCCESS",
          generated: {
            seoTitle: seo.seoTitle,
            seoDescription: seo.seoDescription,
          },
          pushed: pushedNow,
        });
        generated++;
      } catch (e) {
        failed++;
        results.push({
          productId: p.id,
          title: p.title || undefined,
          status: "FAILED",
          message: e instanceof Error ? e.message : "Unknown error",
        });
      }
    }

    // Count remaining gaps so UI can show progress
    const remaining = await prisma.product.count({ where: gapFilter });

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: dryRun ? "AI_SEO_DRYRUN" : "AI_SEO_GENERATE",
      entity: "PRODUCT",
      details: `AI SEO ${dryRun ? "dry-run" : "generate"}: ${generated} ok, ${skipped} skipped, ${failed} failed; pushed ${pushed} to Shopify; tokens in=${totalTokensIn}, out=${totalTokensOut}, cache=${totalCacheRead}; remaining=${remaining}`,
    });

    return NextResponse.json({
      success: true,
      summary: {
        generated,
        skipped,
        failed,
        pushed,
        tokens: {
          in: totalTokensIn,
          out: totalTokensOut,
          cacheRead: totalCacheRead,
        },
      },
      remaining,
      results,
    });
  } catch (error) {
    return NextResponse.json(
      {
        success: false,
        error: error instanceof Error ? error.message : "Unknown error",
      },
      { status: 500 }
    );
  }
}
