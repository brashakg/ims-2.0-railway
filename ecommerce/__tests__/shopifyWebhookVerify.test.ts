// Tests for src/lib/shopifyWebhookVerify.ts::verifyShopifyWebhookHmac
// (online-store BVI security P0 — council item S16c: never process an
// unsigned/forged Shopify webhook).
//
// Test runner: Node's built-in `node:test` + `node:assert` (same as
// imsSso.test.ts — no jest/vitest dependency; the verifier is pure Node
// crypto). Run with:
//
//   npx tsx --test __tests__/shopifyWebhookVerify.test.ts
//
// The secret is passed explicitly to the function (its 3rd arg) so these
// tests never depend on process.env and can't leak into other tests.

import test from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { verifyShopifyWebhookHmac } from "../src/lib/shopifyWebhookVerify.ts";

const SECRET = "shpss_test_webhook_secret_value";
const BODY = JSON.stringify({ id: 123, title: "Ray-Ban Aviator" });

function sign(body: string, secret: string): string {
  return createHmac("sha256", secret).update(body, "utf8").digest("base64");
}

test("valid signature is accepted", () => {
  const sig = sign(BODY, SECRET);
  assert.equal(verifyShopifyWebhookHmac(BODY, sig, SECRET), true);
});

test("tampered body is rejected (signature no longer matches)", () => {
  const sig = sign(BODY, SECRET);
  const tamperedBody = BODY.replace("Aviator", "Wayfarer");
  assert.equal(verifyShopifyWebhookHmac(tamperedBody, sig, SECRET), false);
});

test("signature made with the wrong secret is rejected", () => {
  const sig = sign(BODY, "the-wrong-secret");
  assert.equal(verifyShopifyWebhookHmac(BODY, sig, SECRET), false);
});

test("missing signature header is rejected (S16c — no unsigned webhooks)", () => {
  assert.equal(verifyShopifyWebhookHmac(BODY, "", SECRET), false);
});

test("no configured secret is rejected (was the bypass — used to return true)", () => {
  const sig = sign(BODY, SECRET);
  assert.equal(verifyShopifyWebhookHmac(BODY, sig, ""), false);
});

test("garbage / wrong-length signature is rejected, not crashed", () => {
  // timingSafeEqual throws on unequal-length buffers; the length guard must
  // catch this and return false rather than throw a 500.
  assert.equal(
    verifyShopifyWebhookHmac(BODY, "not-a-real-base64-hmac", SECRET),
    false
  );
});

test("empty body with its correct signature still verifies", () => {
  const sig = sign("", SECRET);
  assert.equal(verifyShopifyWebhookHmac("", sig, SECRET), true);
});
