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

import { createVerify } from "crypto";

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
    if (!claims.exp || claims.exp < now) return null;
    if (!claims.email) return null;
    return claims;
  } catch {
    return null;
  }
}
