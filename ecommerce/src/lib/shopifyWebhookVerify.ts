import crypto from "crypto";

/**
 * Verify a Shopify webhook HMAC signature.
 *
 * SECURITY CONTRACT: returns true ONLY when a webhook secret is configured AND
 * the supplied base64 signature matches the HMAC-SHA256 of the raw body. A
 * missing secret, a missing header, or a length/value mismatch is a hard fail.
 * We never treat an unsigned webhook as valid — that previously let anyone POST
 * forged Shopify events and mutate the catalog / orders.
 *
 * Kept in src/lib as a pure, dependency-free function so it can be unit-tested
 * without standing up the route (which pulls in Prisma / the DB). Mirrors the
 * src/lib/imsSso.ts testability pattern.
 *
 * @param body       the raw request body, exactly as received
 * @param hmacHeader the X-Shopify-Hmac-Sha256 header value (base64)
 * @param secret     the shared webhook secret (SHOPIFY_CLIENT_SECRET /
 *                   SHOPIFY_WEBHOOK_SECRET). Defaults to reading the env so
 *                   callers can omit it; tests pass it explicitly.
 */
export function verifyShopifyWebhookHmac(
  body: string,
  hmacHeader: string,
  secret: string = process.env.SHOPIFY_CLIENT_SECRET ||
    process.env.SHOPIFY_WEBHOOK_SECRET ||
    ""
): boolean {
  if (!secret) {
    console.error(
      "Webhook rejected: no SHOPIFY_CLIENT_SECRET/SHOPIFY_WEBHOOK_SECRET configured — cannot verify HMAC"
    );
    return false;
  }
  if (!hmacHeader) {
    return false;
  }
  const expected = crypto
    .createHmac("sha256", secret)
    .update(body, "utf8")
    .digest("base64");
  // Compare the base64 digests in constant time. timingSafeEqual throws on
  // unequal-length buffers, and hmacHeader is attacker-controlled, so guard
  // the length first (a mismatch is already a verification failure).
  const expectedBuf = Buffer.from(expected);
  const providedBuf = Buffer.from(hmacHeader);
  if (expectedBuf.length !== providedBuf.length) {
    return false;
  }
  return crypto.timingSafeEqual(expectedBuf, providedBuf);
}
