import { NextResponse } from "next/server";

const rawStoreUrl = process.env.SHOPIFY_STORE_URL || "";
const SHOPIFY_STORE_URL = rawStoreUrl.startsWith("http")
  ? rawStoreUrl
  : `https://${rawStoreUrl}`;
const SHOPIFY_CLIENT_ID = process.env.SHOPIFY_CLIENT_ID || "";
const SHOPIFY_CLIENT_SECRET = process.env.SHOPIFY_CLIENT_SECRET || "";
const SHOPIFY_LEGACY_TOKEN =
  process.env.SHOPIFY_ACCESS_TOKEN || process.env.SHOPIFY_ADMIN_TOKEN || "";

async function getTokenForScopeCheck(): Promise<string> {
  if (SHOPIFY_CLIENT_ID && SHOPIFY_CLIENT_SECRET) {
    const response = await fetch(
      `${SHOPIFY_STORE_URL}/admin/oauth/access_token`,
      {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          client_id: SHOPIFY_CLIENT_ID,
          client_secret: SHOPIFY_CLIENT_SECRET,
          grant_type: "client_credentials",
        }).toString(),
      }
    );
    if (!response.ok) {
      const err = await response.text();
      throw new Error(`Token request failed: ${response.status} - ${err}`);
    }
    const data = await response.json();
    return data.access_token;
  }
  if (SHOPIFY_LEGACY_TOKEN) {
    return SHOPIFY_LEGACY_TOKEN;
  }
  throw new Error("No Shopify credentials configured");
}

export async function GET() {
  try {
    const token = await getTokenForScopeCheck();

    // Query access scopes
    const res = await fetch(
      `${SHOPIFY_STORE_URL}/admin/oauth/access_scopes.json`,
      {
        headers: { "X-Shopify-Access-Token": token },
      }
    );

    if (!res.ok) {
      const err = await res.text();
      return NextResponse.json(
        {
          success: false,
          error: `Scope check failed: ${res.status}`,
          details: err,
          authMethod: SHOPIFY_CLIENT_ID ? "client_credentials" : "legacy_token",
        },
        { status: 502 }
      );
    }

    const data = await res.json();
    const scopes = (data.access_scopes || []).map(
      (s: { handle: string }) => s.handle
    );

    // Check which scopes are needed for webhooks
    const neededForOrders = ["read_orders", "write_orders"];
    const neededForCustomers = ["read_customers", "write_customers"];
    const neededForFulfillments = ["read_fulfillments"];

    return NextResponse.json({
      success: true,
      authMethod: SHOPIFY_CLIENT_ID ? "client_credentials" : "legacy_token",
      scopes,
      scopeCount: scopes.length,
      missingForWebhooks: {
        orders: neededForOrders.filter((s) => !scopes.includes(s)),
        customers: neededForCustomers.filter((s) => !scopes.includes(s)),
        fulfillments: neededForFulfillments.filter((s) => !scopes.includes(s)),
      },
    });
  } catch (err: any) {
    return NextResponse.json(
      { success: false, error: err.message },
      { status: 500 }
    );
  }
}
