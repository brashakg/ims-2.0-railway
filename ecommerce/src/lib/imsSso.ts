// ============================================================================
// IMS -> online-store SSO: verify the IMS exchange token (RS256)
// ============================================================================
// IMS signs a short-lived exchange token with its PRIVATE key; we verify it
// here with the matching PUBLIC key (IMS_SSO_PUBLIC_KEY). Uses Node's built-in
// crypto, so no extra dependency. Fail-soft: no key / bad token -> null.
//
// The token is a JWT: base64url(header).base64url(payload).base64url(signature),
// signed RS256 over "header.payload". We verify the signature, then enforce
// aud/iss/scope/exp and require an email (we map to an EXISTING user by email).
//
// Hardening (Council Branch C):
// - Explicit alg/typ header pin: we now parse the JWT header and reject
//   anything that is not alg="RS256" (typ optional, but must be "JWT" if set).
//   Node's RSA verify already rejects HMAC sigs, but a future refactor swapping
//   verify libs could lose that defence-by-accident. This pin makes the policy
//   explicit.
// - Clock-skew leeway: exp gets 30s grace (claim_exp + 30 < now) and iat is
//   rejected if it is more than 60s in the future (clock skew the other way).

import { createVerify } from "crypto";

// Clock-skew tolerances. Tuned for normal NTP drift between a mobile/browser
// clock and Railway's server clock.
const EXP_LEEWAY_SECONDS = 30;
const IAT_FUTURE_LEEWAY_SECONDS = 60;

export interface ImsSsoClaims {
  sub: string;
  email: string;
  name?: string;
  role: string;
  aud: string;
  iss: string;
  scope: string;
  exp: number;
  iat?: number;
  jti?: string;
}

interface JwtHeader {
  alg?: string;
  typ?: string;
}

function publicKey(): string | null {
  const pem = process.env.IMS_SSO_PUBLIC_KEY;
  if (!pem) return null;
  // Tolerate \n-escaped PEM stored in a single-line env var.
  return pem.includes("\\n") ? pem.replace(/\\n/g, "\n") : pem;
}

export function imsSsoConfigured(): boolean {
  return Boolean(publicKey());
}

export function verifyImsToken(token: string | undefined | null): ImsSsoClaims | null {
  const key = publicKey();
  if (!key || !token) return null;

  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [header, payload, signature] = parts;

  try {
    // C4: explicit alg/typ pin. Reject anything not RS256 BEFORE attempting
    // signature verification so that a forged alg="none" / alg="HS256" header
    // can never reach the verifier (defence-in-depth: Node's RSA verify also
    // rejects non-RSA signatures, but a future verifier swap should not be
    // able to silently downgrade us).
    const headerObj = JSON.parse(
      Buffer.from(header, "base64url").toString("utf8"),
    ) as JwtHeader;
    if (headerObj.alg !== "RS256") return null;
    if (headerObj.typ && headerObj.typ !== "JWT") return null;

    const verifier = createVerify("RSA-SHA256");
    verifier.update(`${header}.${payload}`);
    verifier.end();
    const ok = verifier.verify(key, Buffer.from(signature, "base64url"));
    if (!ok) return null;

    const claims = JSON.parse(
      Buffer.from(payload, "base64url").toString("utf8"),
    ) as ImsSsoClaims;

    const now = Math.floor(Date.now() / 1000);
    if (claims.aud !== "bvi") return null;
    if (claims.iss !== "ims") return null;
    if (claims.scope !== "ecommerce") return null;
    // C5: 30s clock-skew grace on exp. Token is still rejected if more than
    // 30 seconds have passed since stated expiry.
    if (!claims.exp || claims.exp + EXP_LEEWAY_SECONDS < now) return null;
    // C5: reject tokens whose iat is implausibly far in the future
    // (clock-skew the other direction).
    if (claims.iat && claims.iat > now + IAT_FUTURE_LEEWAY_SECONDS) return null;
    if (!claims.email) return null;
    return claims;
  } catch {
    return null;
  }
}
