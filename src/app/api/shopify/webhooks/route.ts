import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import {
  registerWebhook,
  listWebhooks,
  deleteWebhook,
} from "@/lib/shopify";

// The webhook topics we want to subscribe to. GraphQL uses SHOUTY_SNAKE_CASE
// for topic names; the incoming webhook payload's X-Shopify-Topic header
// uses dotted lowercase (e.g. "orders/create") which is what the dispatcher
// in /api/webhooks/shopify/route.ts matches on.
const WEBHOOK_TOPICS = [
  // Products
  "PRODUCTS_CREATE",
  "PRODUCTS_UPDATE",
  "PRODUCTS_DELETE",
  // Inventory
  "INVENTORY_LEVELS_UPDATE",
  "INVENTORY_ITEMS_UPDATE",
  // Orders
  "ORDERS_CREATE",
  "ORDERS_UPDATED",
  "ORDERS_CANCELLED",
  "ORDERS_FULFILLED",
  "ORDERS_PAID",
  "ORDERS_DELETE",
  // Refunds (track refunded orders + recompute customer totals)
  "REFUNDS_CREATE",
  // Customers
  "CUSTOMERS_CREATE",
  "CUSTOMERS_UPDATE",
  "CUSTOMERS_DELETE",
  // Collections
  "COLLECTIONS_CREATE",
  "COLLECTIONS_UPDATE",
  "COLLECTIONS_DELETE",
  // Fulfillments (order ship/deliver state)
  "FULFILLMENTS_CREATE",
  "FULFILLMENTS_UPDATE",
  // Locations (keep our Location table auto-synced)
  "LOCATIONS_CREATE",
  "LOCATIONS_UPDATE",
  "LOCATIONS_DELETE",
  "LOCATIONS_ACTIVATE",
  "LOCATIONS_DEACTIVATE",
  // App lifecycle
  "APP_UNINSTALLED",
] as const;

// GET /api/shopify/webhooks — List all registered webhooks
export async function GET() {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const result = await listWebhooks();
    if (!result.success) {
      return NextResponse.json(
        { success: false, error: result.error },
        { status: 502 }
      );
    }

    // Also get local records
    const localWebhooks = await prisma.webhookSubscription.findMany({
      orderBy: { createdAt: "desc" },
    });

    // Recent webhook events
    const recentEvents = await prisma.webhookEvent.findMany({
      orderBy: { createdAt: "desc" },
      take: 50,
    });

    return NextResponse.json({
      success: true,
      data: {
        shopifyWebhooks: result.webhooks || [],
        localWebhooks,
        recentEvents,
        availableTopics: WEBHOOK_TOPICS,
      },
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}

// POST /api/shopify/webhooks — Register webhook subscriptions
export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const body = await request.json().catch(() => ({}));
    const action = body.action || "register_all";

    // Determine callback URL from environment
    const baseUrl =
      process.env.NEXTAUTH_URL ||
      (process.env.RAILWAY_PUBLIC_DOMAIN
        ? `https://${process.env.RAILWAY_PUBLIC_DOMAIN}`
        : "http://localhost:3000");
    const callbackUrl = `${baseUrl}/api/webhooks/shopify`;

    if (action === "register_all") {
      // Register all webhook topics
      const results: Array<{
        topic: string;
        success: boolean;
        webhookId?: string;
        error?: string;
      }> = [];

      // First, get existing webhooks to avoid duplicates
      const existing = await listWebhooks();
      const existingTopics = new Set(
        (existing.webhooks || []).map((w) => w.topic)
      );

      for (const topic of WEBHOOK_TOPICS) {
        if (existingTopics.has(topic)) {
          results.push({
            topic,
            success: true,
            error: "Already registered",
          });
          continue;
        }

        const result = await registerWebhook(topic, callbackUrl);
        results.push({
          topic,
          success: result.success,
          webhookId: result.webhookId,
          error: result.error,
        });

        if (result.success && result.webhookId) {
          // Save to local DB
          await prisma.webhookSubscription.upsert({
            where: { shopifyWebhookId: result.webhookId },
            update: {
              topic,
              callbackUrl,
              active: true,
            },
            create: {
              shopifyWebhookId: result.webhookId,
              topic,
              callbackUrl,
              format: "JSON",
              active: true,
            },
          });
        }
      }

      const successCount = results.filter((r) => r.success).length;
      return NextResponse.json({
        success: true,
        message: `Registered ${successCount}/${WEBHOOK_TOPICS.length} webhooks`,
        callbackUrl,
        results,
      });
    }

    if (action === "register_single") {
      const topic = body.topic;
      if (!topic) {
        return NextResponse.json(
          { success: false, error: "topic is required" },
          { status: 400 }
        );
      }

      const result = await registerWebhook(topic, callbackUrl);
      if (result.success && result.webhookId) {
        await prisma.webhookSubscription.upsert({
          where: { shopifyWebhookId: result.webhookId },
          update: { topic, callbackUrl, active: true },
          create: {
            shopifyWebhookId: result.webhookId,
            topic,
            callbackUrl,
            format: "JSON",
            active: true,
          },
        });
      }

      return NextResponse.json({
        success: result.success,
        webhookId: result.webhookId,
        error: result.error,
      });
    }

    if (action === "delete") {
      const webhookId = body.webhookId;
      if (!webhookId) {
        return NextResponse.json(
          { success: false, error: "webhookId is required" },
          { status: 400 }
        );
      }

      const result = await deleteWebhook(webhookId);
      if (result.success) {
        await prisma.webhookSubscription
          .delete({ where: { shopifyWebhookId: webhookId } })
          .catch(() => {});
      }

      return NextResponse.json({
        success: result.success,
        error: result.error,
      });
    }

    if (action === "delete_all") {
      const existing = await listWebhooks();
      const results: Array<{ id: string; success: boolean; error?: string }> = [];

      for (const wh of existing.webhooks || []) {
        const result = await deleteWebhook(wh.id);
        results.push({ id: wh.id, success: result.success, error: result.error });
        if (result.success) {
          await prisma.webhookSubscription
            .delete({ where: { shopifyWebhookId: wh.id } })
            .catch(() => {});
        }
      }

      return NextResponse.json({
        success: true,
        message: `Deleted ${results.filter((r) => r.success).length} webhooks`,
        results,
      });
    }

    return NextResponse.json(
      { success: false, error: "Invalid action. Use: register_all, register_single, delete, delete_all" },
      { status: 400 }
    );
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
