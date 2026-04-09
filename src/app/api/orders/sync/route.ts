import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { makeGraphQLRequest } from "@/lib/shopify";

interface ShopifyOrder {
  id: string;
  name: string;
  email: string;
  phone: string;
  totalPriceSet: { shopMoney: { amount: string } };
  subtotalPriceSet: { shopMoney: { amount: string } };
  totalTaxSet: { shopMoney: { amount: string } };
  totalDiscountsSet: { shopMoney: { amount: string } };
  currencyCode: string;
  displayFinancialStatus: string;
  displayFulfillmentStatus: string;
  closed: boolean;
  cancelledAt: string | null;
  closedAt: string | null;
  processedAt: string | null;
  note: string | null;
  tags: string[];
  createdAt: string;
  lineItems: {
    edges: Array<{
      node: {
        id: string;
        title: string;
        variantTitle: string;
        sku: string;
        quantity: number;
        originalUnitPriceSet: { shopMoney: { amount: string } };
        totalDiscountSet: { shopMoney: { amount: string } };
        product: { id: string } | null;
        variant: { id: string } | null;
      };
    }>;
  };
  customer: {
    id: string;
    email: string;
    phone: string;
    firstName: string;
    lastName: string;
    ordersCount: string;
    totalSpentV2: { amount: string };
    addresses: Array<{
      address1: string;
      address2: string;
      city: string;
      province: string;
      zip: string;
      country: string;
    }>;
    tags: string[];
    note: string;
    acceptsMarketing: boolean;
    taxExempt: boolean;
    verifiedEmail: boolean;
  } | null;
  shippingAddress: {
    address1: string;
    city: string;
    province: string;
    zip: string;
    country: string;
  } | null;
  billingAddress: {
    address1: string;
    city: string;
    province: string;
    zip: string;
    country: string;
  } | null;
}

interface OrdersResponse {
  orders: {
    edges: Array<{ node: ShopifyOrder; cursor: string }>;
    pageInfo: { hasNextPage: boolean };
  };
}

export async function POST() {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const query = `
      query($cursor: String) {
        orders(first: 50, after: $cursor, sortKey: CREATED_AT, reverse: true) {
          edges {
            node {
              id
              name
              email
              phone
              totalPriceSet { shopMoney { amount } }
              subtotalPriceSet { shopMoney { amount } }
              totalTaxSet { shopMoney { amount } }
              totalDiscountsSet { shopMoney { amount } }
              currencyCode
              displayFinancialStatus
              displayFulfillmentStatus
              closed
              cancelledAt
              closedAt
              processedAt
              note
              tags
              createdAt
              customer {
                id
                email
                phone
                firstName
                lastName
                ordersCount
                totalSpentV2 { amount }
                addresses(first: 1) {
                  address1
                  address2
                  city
                  province
                  zip
                  country
                }
                tags
                note
                acceptsMarketing
                taxExempt
                verifiedEmail
              }
              shippingAddress {
                address1
                city
                province
                zip
                country
              }
              billingAddress {
                address1
                city
                province
                zip
                country
              }
              lineItems(first: 50) {
                edges {
                  node {
                    id
                    title
                    variantTitle
                    sku
                    quantity
                    originalUnitPriceSet { shopMoney { amount } }
                    totalDiscountSet { shopMoney { amount } }
                    product { id }
                    variant { id }
                  }
                }
              }
            }
            cursor
          }
          pageInfo { hasNextPage }
        }
      }
    `;

    let cursor: string | null = null;
    let allOrders: ShopifyOrder[] = [];
    let pageCount = 0;
    const maxPages = 100; // Fetch up to ~5000 orders (50 per page)

    do {
      const result: { success: boolean; data?: OrdersResponse; error?: string } =
        await makeGraphQLRequest<OrdersResponse>(query, { cursor });

      if (!result.success || !result.data) {
        return NextResponse.json(
          { success: false, error: result.error || "Failed to fetch orders" },
          { status: 500 }
        );
      }

      const edges = result.data.orders.edges;
      allOrders.push(...edges.map((e) => e.node));

      if (result.data.orders.pageInfo.hasNextPage && edges.length > 0) {
        cursor = edges[edges.length - 1].cursor;
      } else {
        cursor = null;
      }
      pageCount++;
    } while (cursor && pageCount < maxPages);

    let created = 0;
    let updated = 0;

    for (const shopifyOrder of allOrders) {
      const shopifyGid = shopifyOrder.id;

      let customerId: string | undefined;
      if (shopifyOrder.customer) {
        const c = shopifyOrder.customer;
        const addr = c.addresses?.[0];
        const customer = await prisma.customer.upsert({
          where: { shopifyCustomerId: c.id },
          update: {
            email: c.email || undefined,
            phone: c.phone || undefined,
            firstName: c.firstName || undefined,
            lastName: c.lastName || undefined,
            ordersCount: parseInt(c.ordersCount) || 0,
            totalSpent: parseFloat(c.totalSpentV2?.amount) || 0,
            address1: addr?.address1 || undefined,
            address2: addr?.address2 || undefined,
            city: addr?.city || undefined,
            state: addr?.province || undefined,
            zip: addr?.zip || undefined,
            country: addr?.country || undefined,
            tags: c.tags?.join(", ") || undefined,
            note: c.note || undefined,
            acceptsMarketing: c.acceptsMarketing || false,
            taxExempt: c.taxExempt || false,
            verified: c.verifiedEmail || false,
          },
          create: {
            shopifyCustomerId: c.id,
            email: c.email || undefined,
            phone: c.phone || undefined,
            firstName: c.firstName || undefined,
            lastName: c.lastName || undefined,
            ordersCount: parseInt(c.ordersCount) || 0,
            totalSpent: parseFloat(c.totalSpentV2?.amount) || 0,
            address1: addr?.address1 || undefined,
            address2: addr?.address2 || undefined,
            city: addr?.city || undefined,
            state: addr?.province || undefined,
            zip: addr?.zip || undefined,
            country: addr?.country || undefined,
            tags: c.tags?.join(", ") || undefined,
            note: c.note || undefined,
            acceptsMarketing: c.acceptsMarketing || false,
            taxExempt: c.taxExempt || false,
            verified: c.verifiedEmail || false,
          },
        });
        customerId = customer.id;
      }

      const orderStatus = shopifyOrder.cancelledAt
        ? "CANCELLED"
        : shopifyOrder.closed
        ? "CLOSED"
        : "OPEN";

      const existing = await prisma.order.findUnique({
        where: { shopifyOrderId: shopifyGid },
      });

      const orderData = {
        name: shopifyOrder.name || undefined,
        email: shopifyOrder.email || undefined,
        phone: shopifyOrder.phone || undefined,
        totalPrice: parseFloat(shopifyOrder.totalPriceSet?.shopMoney?.amount) || 0,
        subtotalPrice: parseFloat(shopifyOrder.subtotalPriceSet?.shopMoney?.amount) || 0,
        totalTax: parseFloat(shopifyOrder.totalTaxSet?.shopMoney?.amount) || 0,
        totalDiscount: parseFloat(shopifyOrder.totalDiscountsSet?.shopMoney?.amount) || 0,
        currency: shopifyOrder.currencyCode || "INR",
        financialStatus: shopifyOrder.displayFinancialStatus?.toLowerCase() || undefined,
        fulfillmentStatus: shopifyOrder.displayFulfillmentStatus?.toLowerCase() || undefined,
        orderStatus,
        customerId: customerId || undefined,
        shippingAddress: shopifyOrder.shippingAddress ? JSON.stringify(shopifyOrder.shippingAddress) : undefined,
        billingAddress: shopifyOrder.billingAddress ? JSON.stringify(shopifyOrder.billingAddress) : undefined,
        note: shopifyOrder.note || undefined,
        tags: shopifyOrder.tags?.join(", ") || undefined,
        cancelledAt: shopifyOrder.cancelledAt ? new Date(shopifyOrder.cancelledAt) : undefined,
        closedAt: shopifyOrder.closedAt ? new Date(shopifyOrder.closedAt) : undefined,
        processedAt: shopifyOrder.processedAt ? new Date(shopifyOrder.processedAt) : undefined,
      };

      if (existing) {
        await prisma.order.update({
          where: { shopifyOrderId: shopifyGid },
          data: orderData,
        });
        await prisma.orderLineItem.deleteMany({ where: { orderId: existing.id } });
        if (shopifyOrder.lineItems?.edges) {
          await prisma.orderLineItem.createMany({
            data: shopifyOrder.lineItems.edges.map((e) => ({
              orderId: existing.id,
              shopifyLineItemId: e.node.id,
              title: e.node.title || "Unknown",
              variantTitle: e.node.variantTitle || undefined,
              sku: e.node.sku || undefined,
              quantity: e.node.quantity || 1,
              price: parseFloat(e.node.originalUnitPriceSet?.shopMoney?.amount) || 0,
              totalDiscount: parseFloat(e.node.totalDiscountSet?.shopMoney?.amount) || 0,
              productId: undefined,
              variantId: undefined,
            })),
          });
        }
        updated++;
      } else {
        const order = await prisma.order.create({
          data: {
            shopifyOrderId: shopifyGid,
            orderNumber: shopifyOrder.name?.replace("#", "") || undefined,
            ...orderData,
          },
        });
        if (shopifyOrder.lineItems?.edges) {
          await prisma.orderLineItem.createMany({
            data: shopifyOrder.lineItems.edges.map((e) => ({
              orderId: order.id,
              shopifyLineItemId: e.node.id,
              title: e.node.title || "Unknown",
              variantTitle: e.node.variantTitle || undefined,
              sku: e.node.sku || undefined,
              quantity: e.node.quantity || 1,
              price: parseFloat(e.node.originalUnitPriceSet?.shopMoney?.amount) || 0,
              totalDiscount: parseFloat(e.node.totalDiscountSet?.shopMoney?.amount) || 0,
            })),
          });
        }
        created++;
      }
    }

    return NextResponse.json({
      success: true,
      message: `Synced ${allOrders.length} orders (${created} new, ${updated} updated)`,
      data: { total: allOrders.length, created, updated },
    });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}
