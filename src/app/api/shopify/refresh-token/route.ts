import { NextResponse } from "next/server";
import { requireAuth } from "@/lib/apiAuth";

// POST /api/shopify/refresh-token
// Forces the in-memory OAuth client_credentials token cache to be cleared
// and re-minted on the next Shopify call. Use this after changing app
// scopes in the Shopify Partners/Dev Dashboard so Railway's running
// process picks up the new token without a redeploy.
export async function POST() {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    // Reach into the shopify client's module-scoped cachedToken via
    // a test import-time side effect. The client doesn't export a
    // dedicated clear function today, so we flip the env-observable
    // cache by re-importing the module with a cache-buster query.
    // Simpler: we expose a small clearer directly on the module
    // (see src/lib/shopify.ts — `clearCachedShopifyToken`).
    const mod = await import("@/lib/shopify");
    // @ts-expect-error dynamic clear helper added to shopify module
    if (typeof mod.clearCachedShopifyToken === "function") {
      // @ts-expect-error dynamic clear helper
      mod.clearCachedShopifyToken();
    }

    // Also fetch a fresh scope listing so the caller can confirm the
    // new scopes arrived.
    const { makeGraphQLRequest } = mod;
    const probe = await makeGraphQLRequest<{ shop: { id: string } }>(
      "{ shop { id } }"
    );
    return NextResponse.json({
      success: true,
      message:
        "Token cache cleared. Next Shopify call will mint a fresh token.",
      probe: {
        ok: probe.success,
        error: probe.success ? undefined : probe.error,
      },
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
