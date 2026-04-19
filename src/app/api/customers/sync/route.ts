import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { makeGraphQLRequest } from "@/lib/shopify";
import { recomputeCustomerAggregates } from "@/lib/customerAggregates";

interface ShopifyCustomer {
  id: string;
  email: string | null;
  phone: string | null;
  firstName: string | null;
  lastName: string | null;
  ordersCount: string;
  totalSpentV2: { amount: string };
  createdAt: string;
  updatedAt: string;
  addresses: Array<{
    address1: string | null;
    address2: string | null;
    city: string | null;
    province: string | null;
    zip: string | null;
    country: string | null;
  }>;
  tags: string[];
  note: string | null;
  acceptsMarketing: boolean;
  taxExempt: boolean;
  verifiedEmail: boolean;
}

interface CustomersResponse {
  customers: {
    edges: Array<{ node: ShopifyCustomer; cursor: string }>;
    pageInfo: { hasNextPage: boolean };
  };
}

export async function POST() {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const query = `
      query($cursor: String) {
        customers(first: 50, after: $cursor, sortKey: CREATED_AT, reverse: true) {
          edges {
            node {
              id
              email
              phone
              firstName
              lastName
              ordersCount
              totalSpentV2 { amount }
              createdAt
              updatedAt
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
            cursor
          }
          pageInfo { hasNextPage }
        }
      }
    `;

    let cursor: string | null = null;
    let allCustomers: ShopifyCustomer[] = [];
    let pageCount = 0;
    const maxPages = 20; // Up to 1000 customers

    do {
      const result: { success: boolean; data?: CustomersResponse; error?: string } =
        await makeGraphQLRequest<CustomersResponse>(query, { cursor });

      if (!result.success || !result.data) {
        return NextResponse.json(
          { success: false, error: result.error || "Failed to fetch customers from Shopify" },
          { status: 500 }
        );
      }

      const edges = result.data.customers.edges;
      allCustomers.push(...edges.map((e) => e.node));

      if (result.data.customers.pageInfo.hasNextPage && edges.length > 0) {
        cursor = edges[edges.length - 1].cursor;
      } else {
        cursor = null;
      }
      pageCount++;
    } while (cursor && pageCount < maxPages);

    let created = 0;
    let updated = 0;

    for (const c of allCustomers) {
      const addr = c.addresses?.[0];

      // NOTE: ordersCount and totalSpent are intentionally NOT synced here.
      // They are derived from our Order table via recomputeCustomerAggregates
      // after this loop — Shopify's numbers can be stale vs. our order sync.
      const customerData = {
        email: c.email || undefined,
        phone: c.phone || undefined,
        firstName: c.firstName || undefined,
        lastName: c.lastName || undefined,
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
      };

      const existing = await prisma.customer.findUnique({
        where: { shopifyCustomerId: c.id },
      });

      if (existing) {
        await prisma.customer.update({
          where: { shopifyCustomerId: c.id },
          data: customerData,
        });
        updated++;
      } else {
        await prisma.customer.create({
          data: {
            shopifyCustomerId: c.id,
            ...customerData,
          },
        });
        created++;
      }
    }

    // Recompute ordersCount/totalSpent from the Order table for every customer.
    // Cheap (single UPDATE) and keeps these fields consistent with orders sync.
    const recomputed = await recomputeCustomerAggregates();

    return NextResponse.json({
      success: true,
      message: `Synced ${allCustomers.length} customers (${created} new, ${updated} updated); recomputed totals for ${recomputed}`,
      data: { total: allCustomers.length, created, updated, recomputed },
    });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}
