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

    // Clear the cached OAuth token so the next Shopify call mints a
    // fresh one. clearCachedShopifyToken is a real export now — the
    // dynamic-import escape hatches this used to need are gone.
    const { clearCachedShopifyToken, makeGraphQLRequest } = await import(
      "@/lib/shopify"
    );
    clearCachedShopifyToken();

    // Probe Shopify with a trivial query so the caller can confirm the
    // new credentials work.
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
