-- Idempotent Shopify webhook dedupe: add a nullable, UNIQUE webhookId to
-- WebhookEvent so repeated deliveries of the same X-Shopify-Webhook-Id are
-- short-circuited (see src/app/api/webhooks/shopify/route.ts).
--
-- This migration is ADDITIVE and safe: a new nullable column + a unique index.
-- Existing rows get NULL (Postgres permits multiple NULLs under UNIQUE), so no
-- backfill or downtime is required.
--
-- DO NOT run blindly against prod from this PR. The BVI Postgres lives in the
-- IMS 2.0 Railway project (see bvi_consolidation.md). Apply via the BVI
-- deploy/migrate pipeline (`prisma migrate deploy`) as part of releasing this
-- change, not ad hoc.

-- AlterTable
ALTER TABLE "WebhookEvent" ADD COLUMN "webhookId" TEXT;

-- CreateIndex
CREATE UNIQUE INDEX "WebhookEvent_webhookId_key" ON "WebhookEvent"("webhookId");
