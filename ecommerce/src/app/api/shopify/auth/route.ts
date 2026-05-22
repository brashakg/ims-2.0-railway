import { NextRequest, NextResponse } from "next/server";

/**
 * Shopify App Install / OAuth callback handler.
 *
 * For Dev Dashboard apps using client_credentials, Shopify redirects here
 * after installation with ?shop=...&hmac=...&host=...
 *
 * Since we use client_credentials (not auth code flow), we don't need to
 * exchange a code for a token. We just acknowledge the install and redirect
 * to our dashboard.
 */
export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const shop = searchParams.get("shop");
  const hmac = searchParams.get("hmac");

  console.log(`[Shopify Auth] Install callback received for shop: ${shop}`);

  // Redirect to our dashboard after successful install
  const baseUrl =
    process.env.NEXTAUTH_URL ||
    (process.env.RAILWAY_PUBLIC_DOMAIN
      ? `https://${process.env.RAILWAY_PUBLIC_DOMAIN}`
      : "http://localhost:3000");

  return NextResponse.redirect(`${baseUrl}/dashboard/shopify?installed=true`);
}
