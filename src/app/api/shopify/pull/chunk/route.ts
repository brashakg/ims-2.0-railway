import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import { fetchProductsPage } from "@/lib/shopify";
import {
  upsertShopifyProduct,
  syncShopifyLocationsToMap,
} from "@/lib/shopifyPullHelpers";

// Stay well under Railway's 300s cap so the browser never sees a
// request killed mid-flight.
export const maxDuration = 120;

interface ChunkRequest {
  cursor?: string | null;
  maxSeconds?: number;
  // Shopify location map is rebuilt from the DB each call — cheap single query.
}

// POST /api/shopify/pull/chunk
// Resumable full-pull. Processes Shopify product pages starting from
// `cursor` until either:
//   - Shopify reports hasNextPage=false (done), OR
//   - the server-side timer exceeds `maxSeconds` (default 60) — the UI
//     can then call again with the returned nextCursor.
//
// Each page upserts products + variants + per-location inventory via
// the same upsertProduct used by the full /api/shopify/pull route, so
// the data shape is identical.
export async function POST(request: NextRequest) {
  const started = Date.now();
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const body: ChunkRequest = await request.json().catch(() => ({}));
    const cursorIn: string | null = body.cursor ?? null;
    const maxSeconds = Math.min(Math.max(body.maxSeconds ?? 60, 10), 110);

    // Build the location map ONCE per chunk. Locations rarely change
    // between chunks; if they do, the next chunk will pick up the new
    // map. If the Shopify scope is missing we still proceed — products
    // will upsert, just without per-location inventory.
    const { locationMap, synced: locationsSynced } =
      await syncShopifyLocationsToMap();

    let cursor: string | null = cursorIn;
    let pagesProcessed = 0;
    let productsProcessed = 0;
    let newCount = 0;
    let updatedCount = 0;
    let errorCount = 0;
    const errors: string[] = [];
    let hasNextPage = true;
    let done = false;

    while (hasNextPage) {
      // Pre-flight timer check — do this BEFORE fetching the next page
      // so we don't half-complete a page and lose the cursor.
      if ((Date.now() - started) / 1000 > maxSeconds) {
        break;
      }

      const page = await fetchProductsPage(cursor);
      if (!page.success) {
        errorCount++;
        errors.push(page.error || "Failed to fetch page");
        break;
      }

      for (const sp of page.products || []) {
        try {
          const existing = await prisma.product.findFirst({
            where: { shopifyProductId: sp.id },
            select: { id: true },
          });
          await upsertShopifyProduct(sp, locationMap);
          productsProcessed++;
          if (existing) updatedCount++;
          else newCount++;
        } catch (e) {
          errorCount++;
          errors.push(
            `${sp.title || sp.id}: ${e instanceof Error ? e.message : "Unknown"}`
          );
        }
      }

      pagesProcessed++;
      cursor = page.nextCursor ?? null;
      hasNextPage = Boolean(page.hasNextPage);
      if (!hasNextPage) done = true;
    }

    const elapsedSec = ((Date.now() - started) / 1000).toFixed(1);

    // Only log a completion ActivityLog once the loop actually finishes —
    // otherwise a chunked pull would write dozens of PULL entries.
    if (done) {
      logActivity({
        userId: (auth.session?.user as any)?.id,
        userName: auth.session?.user?.name,
        userEmail: auth.session?.user?.email,
        action: "PULL",
        entity: "SHOPIFY",
        details: `Chunked pull complete. Products processed across all chunks: see SyncLog.`,
      });
    }

    return NextResponse.json({
      success: true,
      done,
      nextCursor: done ? null : cursor,
      summary: {
        pagesProcessed,
        productsProcessed,
        newCount,
        updatedCount,
        errorCount,
        locationsSynced,
        elapsedSeconds: Number(elapsedSec),
      },
      errors: errors.slice(0, 10),
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
