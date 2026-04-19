import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import crypto from "crypto";
import { logActivity } from "@/lib/activityLog";
import { recomputeCustomerAggregates } from "@/lib/customerAggregates";

// Shopify sends webhooks as POST requests with HMAC verification
// The HMAC is in the X-Shopify-Hmac-Sha256 header

function verifyWebhookHmac(body: string, hmacHeader: string): boolean {
  const secret = process.env.SHOPIFY_CLIENT_SECRET || process.env.SHOPIFY_WEBHOOK_SECRET || "";
  if (!secret) {
    console.warn("No webhook secret configured — skipping HMAC verification");
    return true; // Allow if no secret (for development)
  }
  const hash = crypto
    .createHmac("sha256", secret)
    .update(body, "utf8")
    .digest("base64");
  return crypto.timingSafeEqual(Buffer.from(hash), Buffer.from(hmacHeader));
}

// Parse brand from tags (same logic as pull route)
function extractBrandFromTags(tags: string, vendor: string): string {
  const compoundWords: { [key: string]: string } = {
    tommyhilfiger: "Tommy Hilfiger", rayban: "Ray-Ban", hugoboss: "Hugo Boss",
    calvinklein: "Calvin Klein", armaniexchange: "Armani Exchange", ralphlauren: "Ralph Lauren",
    dolcegabbana: "Dolce & Gabbana", michaelkors: "Michael Kors", tomford: "Tom Ford",
    jimmychoo: "Jimmy Choo", carrerino: "Carrerino", pierrecardin: "Pierre Cardin",
    katespade: "Kate Spade", bottegaveneta: "Bottega Veneta", stellamccartney: "Stella McCartney",
    alexandermcqueen: "Alexander McQueen", robertocavalli: "Roberto Cavalli",
    victoriabeckham: "Victoria Beckham", davidbeckham: "David Beckham",
    marcjacobs: "Marc Jacobs", sevenstreet: "Seven Street",
    lenskart: "Lenskart", johnmonroe: "John Monroe",
    davidjones: "David Jones",
  };

  const tagList = tags.split(",").map((t: string) => t.trim().toLowerCase());
  for (const tag of tagList) {
    if (tag.startsWith("brand_")) {
      const brandRaw = tag.replace("brand_", "").trim();
      if (compoundWords[brandRaw]) return compoundWords[brandRaw];
      // Title case
      return brandRaw.charAt(0).toUpperCase() + brandRaw.slice(1);
    }
  }
  // Fallback: vendor (skip "Better Vision")
  const vendorLower = (vendor || "").toLowerCase();
  if (vendorLower.includes("better vision") || vendorLower.includes("bettervision")) {
    return "Unknown";
  }
  return vendor || "Unknown";
}

// Parse tag fields for product attributes
function parseWebhookTags(tags: string): Record<string, string | null> {
  const fields: Record<string, string | null> = {};
  const tagList = tags.split(",").map((t: string) => t.trim().toLowerCase());
  for (const tag of tagList) {
    if (tag.startsWith("shape_")) fields.shape = tag.replace("shape_", "").replace(/_/g, " ");
    else if (tag.startsWith("framecolor_")) fields.frameColor = tag.replace("framecolor_", "").replace(/_/g, " ");
    else if (tag.startsWith("framematerial_")) fields.frameMaterial = tag.replace("framematerial_", "").replace(/_/g, " ");
    else if (tag.startsWith("frametype_")) fields.frameType = tag.replace("frametype_", "").replace(/_/g, " ");
    else if (tag.startsWith("framesize_")) fields.frameSize = tag.replace("framesize_", "").replace(/_/g, " ");
    else if (tag.startsWith("gender_")) fields.gender = tag.replace("gender_", "").replace(/_/g, " ");
    else if (tag.startsWith("collection_")) fields.collection = tag.replace("collection_", "").replace(/_/g, " ");
    else if (tag.startsWith("style_")) fields.style = tag.replace("style_", "").replace(/_/g, " ");
    else if (tag.startsWith("templetype_")) fields.templeType = tag.replace("templetype_", "").replace(/_/g, " ");
    else if (tag.startsWith("lenscolour_")) fields.lensColour = tag.replace("lenscolour_", "").replace(/_/g, " ");
  }
  return fields;
}

// Handle product create/update from Shopify
async function handleProductCreateUpdate(payload: any) {
  const shopifyGid = `gid://shopify/Product/${payload.id}`;

  // Find existing product
  const existing = await prisma.product.findFirst({
    where: { shopifyProductId: shopifyGid },
    include: { variants: true },
  });

  const title = payload.title || "";
  const status =
    payload.status === "active"
      ? "PUBLISHED"
      : payload.status === "draft"
      ? "DRAFT"
      : "ARCHIVED";
  const tags = (payload.tags || "").split(", ").filter(Boolean).join(", ");
  const mrp = payload.variants?.[0]
    ? parseFloat(payload.variants[0].compare_at_price || payload.variants[0].price || "0")
    : 0;
  const price = payload.variants?.[0]
    ? parseFloat(payload.variants[0].price || "0")
    : 0;

  // Extract brand from tags (not vendor "Better Vision")
  const brand = extractBrandFromTags(tags, payload.vendor || "");
  const parsedTags = parseWebhookTags(tags);

  // Base update data — does NOT include productName/fullModelNo to avoid
  // overwriting values that were properly parsed during the initial pull
  const updateData: Record<string, any> = {
    shopifyProductId: shopifyGid,
    title,
    status,
    brand,
    category: payload.product_type ? payload.product_type.toUpperCase() : guessCategory(payload),
    htmlDescription: payload.body_html || null,
    tags,
    pageUrl: payload.handle || null,
    mrp,
    discountedPrice: price,
    compareAtPrice: mrp,
  };

  // Add parsed tag fields if present
  if (parsedTags.shape) updateData.shape = parsedTags.shape;
  if (parsedTags.frameColor) updateData.frameColor = parsedTags.frameColor;
  if (parsedTags.frameMaterial) updateData.frameMaterial = parsedTags.frameMaterial;
  if (parsedTags.frameType) updateData.frameType = parsedTags.frameType;
  if (parsedTags.frameSize) updateData.frameSize = parsedTags.frameSize;
  if (parsedTags.gender) updateData.gender = parsedTags.gender;
  if (parsedTags.collection) updateData.collection = parsedTags.collection;
  if (parsedTags.style) updateData.style = parsedTags.style;

  let productId: string;

  if (existing) {
    // For existing products: update only Shopify-driven fields.
    // Do NOT overwrite productName/fullModelNo/modelNo/subBrand/label/colorCode
    // — those are set properly during the full pull and may have been customised.
    await prisma.product.update({
      where: { id: existing.id },
      data: updateData,
    });
    productId = existing.id;
  } else {
    // For new products: also set the display name fields from the title as defaults
    const createData = {
      ...updateData,
      sku: payload.variants?.[0]?.sku || `SHOP-${payload.handle || payload.id}`,
      productName: title || null,
      fullModelNo: title || null,
    };
    const created = await prisma.product.create({
      data: createData as any,
    });
    productId = created.id;
  }

  // Sync variants
  if (payload.variants) {
    const existingVariants = existing?.variants || [];
    const existingShopifyVIds = new Set(
      existingVariants.map((v) => v.shopifyVariantId).filter(Boolean)
    );

    for (const sv of payload.variants) {
      const svGid = `gid://shopify/ProductVariant/${sv.id}`;

      if (existingShopifyVIds.has(svGid)) {
        const local = existingVariants.find((v) => v.shopifyVariantId === svGid);
        if (local) {
          await prisma.productVariant.update({
            where: { id: local.id },
            data: {
              mrp: parseFloat(sv.compare_at_price || sv.price || "0"),
              discountedPrice: parseFloat(sv.price || "0"),
              compareAtPrice: parseFloat(sv.compare_at_price || sv.price || "0"),
              barcode: sv.barcode || null,
              sku: sv.sku || null,
              title: sv.title || null,
            },
          });
        }
      } else {
        // New variant
        const colorCode =
          sv.option1 || sv.title?.split(" / ")[0] || "DEFAULT";
        const frameSize = sv.option2 || sv.title?.split(" / ")[1] || null;

        try {
          await prisma.productVariant.create({
            data: {
              productId,
              shopifyVariantId: svGid,
              colorCode,
              frameSize,
              mrp: parseFloat(sv.compare_at_price || sv.price || "0"),
              discountedPrice: parseFloat(sv.price || "0"),
              compareAtPrice: parseFloat(sv.compare_at_price || sv.price || "0"),
              sku: sv.sku || null,
              barcode: sv.barcode || null,
              title: sv.title || null,
            },
          });
        } catch {
          // Skip duplicate
        }
      }
    }
  }

  // ── Sync inventory from webhook payload ──
  // Webhook variants have inventory_quantity (REST API format)
  if (payload.variants) {
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

    // Sum up total inventory from all variants
    const totalInventory = payload.variants.reduce(
      (sum: number, v: any) => sum + (v.inventory_quantity || 0),
      0
    );

    // Upsert product-level inventory
    await prisma.productLocation.upsert({
      where: {
        productId_locationId: {
          productId,
          locationId: defaultLocation.id,
        },
      },
      update: { quantity: totalInventory },
      create: {
        productId,
        locationId: defaultLocation.id,
        quantity: totalInventory,
      },
    });

    // Upsert variant-level inventory
    const updatedVariants = await prisma.productVariant.findMany({
      where: { productId },
    });

    for (const sv of payload.variants) {
      const svGid = `gid://shopify/ProductVariant/${sv.id}`;
      const localVariant = updatedVariants.find(
        (v) => v.shopifyVariantId === svGid
      );
      if (localVariant && sv.inventory_quantity != null) {
        await prisma.variantLocation.upsert({
          where: {
            variantId_locationId: {
              variantId: localVariant.id,
              locationId: defaultLocation.id,
            },
          },
          update: { quantity: sv.inventory_quantity || 0 },
          create: {
            variantId: localVariant.id,
            locationId: defaultLocation.id,
            quantity: sv.inventory_quantity || 0,
          },
        });
      }
    }
  }

  return productId;
}

// Handle product delete from Shopify
async function handleProductDelete(payload: any) {
  const shopifyGid = `gid://shopify/Product/${payload.id}`;
  const existing = await prisma.product.findFirst({
    where: { shopifyProductId: shopifyGid },
  });
  if (existing) {
    await prisma.product.update({
      where: { id: existing.id },
      data: { status: "ARCHIVED" },
    });
  }
}

// Handle inventory level update.
// Payload (REST webhook shape): { inventory_item_id, location_id, available, ... }
// - inventory_item_id is a numeric id; our ProductVariant.shopifyInventoryItemId
//   stores the GID form "gid://shopify/InventoryItem/<id>".
// - location_id is numeric; Location.shopifyLocationId stores the GID form.
// We upsert VariantLocation if both sides of the mapping resolve; otherwise
// we log a descriptive reason and return without erroring (so the webhook
// record shows the miss, not a 500).
async function handleInventoryUpdate(payload: any): Promise<string | null> {
  const rawItemId = payload?.inventory_item_id;
  const rawLocationId = payload?.location_id;
  const available = payload?.available;

  if (rawItemId === undefined || rawItemId === null) {
    return "missing inventory_item_id in payload";
  }
  if (rawLocationId === undefined || rawLocationId === null) {
    return "missing location_id in payload";
  }

  const itemGid = `gid://shopify/InventoryItem/${rawItemId}`;
  const locationGid = `gid://shopify/Location/${rawLocationId}`;

  const variant = await prisma.productVariant.findFirst({
    where: { shopifyInventoryItemId: itemGid },
    select: { id: true },
  });
  if (!variant) {
    return `variant not found for inventory_item_id ${rawItemId}`;
  }

  const location = await prisma.location.findUnique({
    where: { shopifyLocationId: locationGid },
    select: { id: true },
  });
  if (!location) {
    return `location not synced yet for location_id ${rawLocationId}`;
  }

  // Shopify can send `available: null` when a location tracks the item but
  // has no stock recorded; treat it as 0 so the row exists.
  const qty = typeof available === "number" ? available : 0;

  await prisma.variantLocation.upsert({
    where: {
      variantId_locationId: {
        variantId: variant.id,
        locationId: location.id,
      },
    },
    update: { quantity: qty },
    create: {
      variantId: variant.id,
      locationId: location.id,
      quantity: qty,
    },
  });

  return null;
}

// Handle order create/update
async function handleOrderCreateUpdate(payload: any) {
  const shopifyOrderId = `gid://shopify/Order/${payload.id}`;

  // Upsert customer if present
  let customerId: string | null = null;
  if (payload.customer) {
    const c = payload.customer;
    const shopifyCustomerId = `gid://shopify/Customer/${c.id}`;
    const addr = c.default_address || {};
    // NOTE: ordersCount/totalSpent are recomputed from the Order table
    // at the end of this handler — do not sync them from the Shopify payload.
    const customerFields = {
      email: c.email || null,
      phone: c.phone || null,
      firstName: c.first_name || null,
      lastName: c.last_name || null,
      acceptsMarketing: c.accepts_marketing || false,
      tags: c.tags || null,
      note: c.note || null,
      address1: addr.address1 || null,
      city: addr.city || null,
      state: addr.province || null,
      zip: addr.zip || null,
      country: addr.country || null,
    };
    const customer = await prisma.customer.upsert({
      where: { shopifyCustomerId },
      update: customerFields,
      create: {
        shopifyCustomerId,
        ...customerFields,
      },
    });
    customerId = customer.id;
  }

  // Map financial and fulfillment statuses
  const financialStatus = payload.financial_status || null;
  const fulfillmentStatus = payload.fulfillment_status || null;
  let orderStatus = "OPEN";
  if (payload.cancelled_at) orderStatus = "CANCELLED";
  else if (payload.closed_at) orderStatus = "CLOSED";

  const orderData = {
    shopifyOrderId,
    orderNumber: payload.order_number ? String(payload.order_number) : null,
    name: payload.name || null,
    email: payload.email || null,
    phone: payload.phone || null,
    totalPrice: parseFloat(payload.total_price || "0"),
    subtotalPrice: parseFloat(payload.subtotal_price || "0"),
    totalTax: parseFloat(payload.total_tax || "0"),
    totalDiscount: parseFloat(payload.total_discounts || "0"),
    currency: payload.currency || "INR",
    financialStatus,
    fulfillmentStatus,
    orderStatus,
    customerId,
    shippingAddress: payload.shipping_address ? JSON.stringify(payload.shipping_address) : null,
    billingAddress: payload.billing_address ? JSON.stringify(payload.billing_address) : null,
    note: payload.note || null,
    tags: payload.tags || null,
    source: payload.source_name || null,
    cancelReason: payload.cancel_reason || null,
    cancelledAt: payload.cancelled_at ? new Date(payload.cancelled_at) : null,
    closedAt: payload.closed_at ? new Date(payload.closed_at) : null,
    processedAt: payload.processed_at ? new Date(payload.processed_at) : null,
  };

  const existing = await prisma.order.findUnique({ where: { shopifyOrderId } });
  let orderId: string;

  if (existing) {
    await prisma.order.update({ where: { shopifyOrderId }, data: orderData });
    orderId = existing.id;
    // Delete old line items and recreate
    await prisma.orderLineItem.deleteMany({ where: { orderId } });
  } else {
    const order = await prisma.order.create({ data: orderData });
    orderId = order.id;
  }

  // Create line items
  if (payload.line_items) {
    for (const li of payload.line_items) {
      await prisma.orderLineItem.create({
        data: {
          orderId,
          shopifyLineItemId: li.id ? String(li.id) : null,
          productId: null,
          variantId: null,
          title: li.title || "Unknown",
          variantTitle: li.variant_title || null,
          sku: li.sku || null,
          quantity: li.quantity || 1,
          price: parseFloat(li.price || "0"),
          totalDiscount: parseFloat(li.total_discount || "0"),
        },
      });
    }
  }

  // Refresh aggregate totals for this customer now that the order is committed.
  if (customerId) {
    await recomputeCustomerAggregates([customerId]);
  }
}

// Handle order cancellation
async function handleOrderCancel(payload: any) {
  const shopifyOrderId = `gid://shopify/Order/${payload.id}`;
  const cancelled = await prisma.order.findUnique({
    where: { shopifyOrderId },
    select: { customerId: true },
  });
  await prisma.order.updateMany({
    where: { shopifyOrderId },
    data: {
      orderStatus: "CANCELLED",
      cancelReason: payload.cancel_reason || null,
      cancelledAt: payload.cancelled_at ? new Date(payload.cancelled_at) : new Date(),
    },
  });
  if (cancelled?.customerId) {
    await recomputeCustomerAggregates([cancelled.customerId]);
  }
}

// Handle customer create/update
async function handleCustomerCreateUpdate(payload: any) {
  const shopifyCustomerId = `gid://shopify/Customer/${payload.id}`;
  const addr = payload.default_address || {};

  // NOTE: ordersCount/totalSpent are derived from our Order table via
  // recomputeCustomerAggregates — never take them from the customer payload.
  const customerFields = {
    email: payload.email || null,
    phone: payload.phone || null,
    firstName: payload.first_name || null,
    lastName: payload.last_name || null,
    acceptsMarketing: payload.accepts_marketing || false,
    verified: payload.verified_email || false,
    tags: payload.tags || null,
    note: payload.note || null,
    address1: addr.address1 || null,
    address2: addr.address2 || null,
    city: addr.city || null,
    state: addr.province || null,
    zip: addr.zip || null,
    country: addr.country || null,
  };
  const customer = await prisma.customer.upsert({
    where: { shopifyCustomerId },
    update: customerFields,
    create: {
      shopifyCustomerId,
      ...customerFields,
    },
  });
  await recomputeCustomerAggregates([customer.id]);
}

// Handle customer delete
async function handleCustomerDelete(payload: any) {
  const shopifyCustomerId = `gid://shopify/Customer/${payload.id}`;
  await prisma.customer.deleteMany({ where: { shopifyCustomerId } });
}

// Handle collection events
async function handleCollectionCreateUpdate(payload: any) {
  const shopifyGid = `gid://shopify/Collection/${payload.id}`;
  const existing = await prisma.collection.findUnique({
    where: { shopifyCollectionId: shopifyGid },
  });

  const data = {
    title: payload.title || "",
    handle: payload.handle || null,
    description: payload.body_html || null,
    descriptionHtml: payload.body_html || null,
    sortOrder: payload.sort_order || null,
    published: payload.published_at !== null,
    lastSyncedAt: new Date(),
  };

  if (existing) {
    if (!existing.locallyModified) {
      await prisma.collection.update({
        where: { shopifyCollectionId: shopifyGid },
        data,
      });
    }
  } else {
    await prisma.collection.create({
      data: {
        shopifyCollectionId: shopifyGid,
        ...data,
        collectionType: "CUSTOM",
      },
    });
  }
}

async function handleCollectionDelete(payload: any) {
  const shopifyGid = `gid://shopify/Collection/${payload.id}`;
  await prisma.collection
    .delete({ where: { shopifyCollectionId: shopifyGid } })
    .catch(() => {});
}

function guessCategory(payload: any): string {
  const type = (payload.product_type || "").toLowerCase();
  const tags = (payload.tags || "").toLowerCase();
  const title = (payload.title || "").toLowerCase();

  if (
    type.includes("sunglass") ||
    tags.includes("sunglass") ||
    title.includes("sunglass")
  ) {
    return "SUNGLASSES";
  }
  if (
    type.includes("solution") ||
    tags.includes("solution")
  ) {
    return "SOLUTIONS";
  }
  return "SPECTACLES";
}

// Background processing: fire-and-forget webhook processing
// Returns 200 to Shopify immediately, then processes async
async function processWebhookInBackground(
  topic: string,
  payload: any,
  shopifyId: string | null,
  shopifyDomain: string,
  eventId: string
) {
  let skipReason: string | null = null;
  try {
    switch (topic) {
      case "products/create":
      case "products/update":
        await handleProductCreateUpdate(payload);
        break;
      case "products/delete":
        await handleProductDelete(payload);
        break;
      case "inventory_levels/update":
        skipReason = await handleInventoryUpdate(payload);
        break;
      case "orders/create":
      case "orders/updated":
      case "orders/fulfilled":
      case "orders/paid":
        await handleOrderCreateUpdate(payload);
        break;
      case "orders/cancelled":
        await handleOrderCancel(payload);
        break;
      case "customers/create":
      case "customers/update":
        await handleCustomerCreateUpdate(payload);
        break;
      case "customers/delete":
        await handleCustomerDelete(payload);
        break;
      case "collections/create":
      case "collections/update":
        await handleCollectionCreateUpdate(payload);
        break;
      case "collections/delete":
        await handleCollectionDelete(payload);
        break;
      case "fulfillments/create":
      case "fulfillments/update":
        console.log(`Fulfillment event: ${topic} for order ${payload.order_id}`);
        break;
      case "app/uninstalled":
        console.warn("App uninstalled from Shopify store!");
        break;
      default:
        console.log(`Unhandled webhook topic: ${topic}`);
    }

    await prisma.webhookEvent.update({
      where: { id: eventId },
      data: {
        status: "PROCESSED",
        message: skipReason
          ? `Handled ${topic} for ${shopifyDomain} (skipped: ${skipReason})`
          : `Handled ${topic} for ${shopifyDomain}`,
      },
    });

    logActivity({
      action: "WEBHOOK",
      entity: topic.split("/")[0]?.toUpperCase() || "SHOPIFY",
      entityId: shopifyId,
      details: `Webhook ${topic} from ${shopifyDomain}`,
    });
  } catch (processError) {
    const errMsg =
      processError instanceof Error ? processError.message : "Processing failed";
    await prisma.webhookEvent.update({
      where: { id: eventId },
      data: { status: "FAILED", message: errMsg },
    }).catch(() => {});
    console.error(`Webhook processing error (${topic}):`, errMsg);
  }
}

// POST /api/webhooks/shopify — Receive incoming Shopify webhooks
// IMPORTANT: Returns 200 immediately, processes webhook in background to avoid Shopify 408 timeouts
export async function POST(request: NextRequest) {
  try {
    const rawBody = await request.text();
    const hmacHeader = request.headers.get("x-shopify-hmac-sha256") || "";
    const topic = request.headers.get("x-shopify-topic") || "unknown";
    const shopifyDomain = request.headers.get("x-shopify-shop-domain") || "";

    // Verify HMAC (fast operation — do before returning 200)
    if (hmacHeader && !verifyWebhookHmac(rawBody, hmacHeader)) {
      console.error("Webhook HMAC verification failed");
      prisma.webhookEvent.create({
        data: { topic, status: "FAILED", message: "HMAC verification failed" },
      }).catch(() => {});
      return NextResponse.json(
        { success: false, error: "HMAC verification failed" },
        { status: 401 }
      );
    }

    const payload = JSON.parse(rawBody);
    const shopifyId = payload.id ? String(payload.id) : null;

    // Quick-log the event and return 200 immediately
    // Use a non-awaited create so we respond fast, but catch errors
    const eventPromise = prisma.webhookEvent.create({
      data: {
        topic,
        shopifyId,
        payload: rawBody.substring(0, 5000),
        status: "RECEIVED",
      },
    });

    // Fire-and-forget: process webhook in the background
    // This prevents Shopify 408 timeout errors (5s limit)
    eventPromise
      .then((event) => {
        // Process in background — no await, no blocking
        processWebhookInBackground(topic, payload, shopifyId, shopifyDomain, event.id);
      })
      .catch((err) => {
        console.error("Failed to log webhook event:", err);
        // Still try to process even if logging failed
        processWebhookInBackground(topic, payload, shopifyId, shopifyDomain, "unknown");
      });

    // Return 200 to Shopify immediately — webhook will be processed async
    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Webhook handler error:", error);
    return NextResponse.json({ success: true }); // Still 200 to prevent retries
  }
}
