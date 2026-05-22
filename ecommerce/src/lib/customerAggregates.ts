import { Prisma } from "@prisma/client";
import { prisma } from "@/lib/prisma";

/**
 * Recomputes Customer.ordersCount and Customer.totalSpent from the Order table.
 *
 * ordersCount = number of orders (any status) linked to the customer
 * totalSpent  = SUM(totalPrice) over orders NOT in status 'CANCELLED'
 *
 * If customerIds is provided, only those customers are updated.
 * If omitted, every customer in the DB is recomputed (including customers
 * whose order count should reset to 0 because their orders were deleted).
 *
 * Returns the number of Customer rows updated.
 */
export async function recomputeCustomerAggregates(
  customerIds?: string[]
): Promise<number> {
  if (customerIds && customerIds.length === 0) return 0;

  if (customerIds && customerIds.length > 0) {
    // Use parameterized IN clause via Prisma.sql for safety
    const idList = Prisma.join(customerIds);
    return prisma.$executeRaw`
      UPDATE "Customer" c
      SET
        "ordersCount" = COALESCE(sub."cnt", 0),
        "totalSpent" = COALESCE(sub."sum", 0)
      FROM (
        SELECT c2.id AS cid,
               COUNT(o.id)::int AS "cnt",
               COALESCE(SUM(
                 CASE WHEN o."orderStatus" = 'CANCELLED' THEN 0 ELSE o."totalPrice" END
               ), 0)::float8 AS "sum"
        FROM "Customer" c2
        LEFT JOIN "Order" o ON o."customerId" = c2.id
        WHERE c2.id IN (${idList})
        GROUP BY c2.id
      ) sub
      WHERE c.id = sub.cid
    `;
  }

  return prisma.$executeRaw`
    UPDATE "Customer" c
    SET
      "ordersCount" = COALESCE(sub."cnt", 0),
      "totalSpent" = COALESCE(sub."sum", 0)
    FROM (
      SELECT c2.id AS cid,
             COUNT(o.id)::int AS "cnt",
             COALESCE(SUM(
               CASE WHEN o."orderStatus" = 'CANCELLED' THEN 0 ELSE o."totalPrice" END
             ), 0)::float8 AS "sum"
      FROM "Customer" c2
      LEFT JOIN "Order" o ON o."customerId" = c2.id
      GROUP BY c2.id
    ) sub
    WHERE c.id = sub.cid
  `;
}
