// Tests for src/lib/imsSso.ts::verifyImsToken (Council Branch C — C6).
//
// Test runner: Node's built-in `node:test` + `node:assert`. We deliberately do
// NOT add vitest/jest as a dependency — the SSO verifier path is pure Node
// crypto and tests should stay equally lean. Run with:
//
//   npx tsx --test __tests__/imsSso.test.ts
//
// (tsx is already in devDependencies for the seed script.)
//
// Keys: generated FRESH per test run via Node crypto.generateKeyPairSync. We
// never commit a static private key (any committed private key = compromised).

import test from "node:test";
import assert from "node:assert/strict";
import { createSign, generateKeyPairSync, KeyObject } from "node:crypto";

// We import the module dynamically inside each test (after setting env vars
// for the public key) so the publicKey() lookup reads the fresh key.
type ClaimsT = {
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
};

function b64url(input: Buffer | string): string {
  return Buffer.from(input).toString("base64url");
}

function signRs256(
  privateKey: KeyObject,
  header: Record<string, unknown>,
  payload: Record<string, unknown>,
): string {
  const headerStr = b64url(JSON.stringify(header));
  const payloadStr = b64url(JSON.stringify(payload));
  const signer = createSign("RSA-SHA256");
  signer.update(`${headerStr}.${payloadStr}`);
  signer.end();
  const sig = signer.sign(privateKey).toString("base64url");
  return `${headerStr}.${payloadStr}.${sig}`;
}

function makeKeyPair(): { privateKey: KeyObject; publicPem: string } {
  const { privateKey, publicKey } = generateKeyPairSync("rsa", {
    modulusLength: 2048,
  });
  const publicPem = publicKey.export({ type: "spki", format: "pem" }) as string;
  return { privateKey, publicPem };
}

function baseClaims(overrides: Partial<ClaimsT> = {}): ClaimsT {
  const now = Math.floor(Date.now() / 1000);
  return {
    sub: "u1",
    email: "alice@example.com",
    role: "ADMIN",
    aud: "bvi",
    iss: "ims",
    scope: "ecommerce",
    iat: now,
    exp: now + 90,
    jti: "test-jti-1",
    ...overrides,
  };
}

async function loadVerifier(): Promise<
  (token: string | undefined | null) => ClaimsT | null
> {
  // Bust ESM cache so each test picks up the env-var override for the public key.
  const url = new URL(`../src/lib/imsSso.ts?t=${Math.random()}`, import.meta.url);
  const mod: { verifyImsToken: (t?: string | null) => ClaimsT | null } =
    await import(url.href);
  return mod.verifyImsToken;
}

test("valid token roundtrip is accepted", async () => {
  const { privateKey, publicPem } = makeKeyPair();
  process.env.IMS_SSO_PUBLIC_KEY = publicPem;
  const verify = await loadVerifier();

  const token = signRs256(privateKey, { alg: "RS256", typ: "JWT" }, baseClaims());
  const claims = verify(token);
  assert.ok(claims);
  assert.equal(claims?.email, "alice@example.com");
  assert.equal(claims?.role, "ADMIN");
});

test("tampered signature is rejected", async () => {
  const { privateKey, publicPem } = makeKeyPair();
  process.env.IMS_SSO_PUBLIC_KEY = publicPem;
  const verify = await loadVerifier();

  const token = signRs256(privateKey, { alg: "RS256", typ: "JWT" }, baseClaims());
  // Substitute a different valid-but-wrong RSA signature: just sign the SAME
  // payload with a DIFFERENT key. Same length, identical encoding, but the
  // signature does not match the public key in IMS_SSO_PUBLIC_KEY -> verify
  // must fail.
  const { privateKey: otherKey } = makeKeyPair();
  const parts = token.split(".");
  const signer = createSign("RSA-SHA256");
  signer.update(`${parts[0]}.${parts[1]}`);
  signer.end();
  parts[2] = signer.sign(otherKey).toString("base64url");
  const tampered = parts.join(".");
  assert.equal(verify(tampered), null);
});

test("expired token outside leeway is rejected", async () => {
  const { privateKey, publicPem } = makeKeyPair();
  process.env.IMS_SSO_PUBLIC_KEY = publicPem;
  const verify = await loadVerifier();

  const now = Math.floor(Date.now() / 1000);
  // exp 60s ago is well outside the 30s leeway.
  const token = signRs256(
    privateKey,
    { alg: "RS256", typ: "JWT" },
    baseClaims({ exp: now - 60, iat: now - 150 }),
  );
  assert.equal(verify(token), null);
});

test("wrong aud is rejected", async () => {
  const { privateKey, publicPem } = makeKeyPair();
  process.env.IMS_SSO_PUBLIC_KEY = publicPem;
  const verify = await loadVerifier();

  const token = signRs256(
    privateKey,
    { alg: "RS256", typ: "JWT" },
    baseClaims({ aud: "wrong-aud" }),
  );
  assert.equal(verify(token), null);
});

test("wrong iss is rejected", async () => {
  const { privateKey, publicPem } = makeKeyPair();
  process.env.IMS_SSO_PUBLIC_KEY = publicPem;
  const verify = await loadVerifier();

  const token = signRs256(
    privateKey,
    { alg: "RS256", typ: "JWT" },
    baseClaims({ iss: "attacker" }),
  );
  assert.equal(verify(token), null);
});

test("wrong scope is rejected", async () => {
  const { privateKey, publicPem } = makeKeyPair();
  process.env.IMS_SSO_PUBLIC_KEY = publicPem;
  const verify = await loadVerifier();

  const token = signRs256(
    privateKey,
    { alg: "RS256", typ: "JWT" },
    baseClaims({ scope: "admin-everything" }),
  );
  assert.equal(verify(token), null);
});

test("alg='none' forged header is rejected (C4)", async () => {
  const { publicPem } = makeKeyPair();
  process.env.IMS_SSO_PUBLIC_KEY = publicPem;
  const verify = await loadVerifier();

  // No signature -- classic alg=none attack.
  const header = b64url(JSON.stringify({ alg: "none", typ: "JWT" }));
  const payload = b64url(JSON.stringify(baseClaims()));
  const token = `${header}.${payload}.`;
  assert.equal(verify(token), null);
});

test("alg='HS256' forged header is rejected (C4)", async () => {
  const { publicPem } = makeKeyPair();
  process.env.IMS_SSO_PUBLIC_KEY = publicPem;
  const verify = await loadVerifier();

  // Attacker tries to confuse the verifier by claiming HMAC; even if the
  // verifier picked it up symmetric, header check now bails first.
  const header = b64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = b64url(JSON.stringify(baseClaims()));
  // Fake "signature" that would pass HMAC validation against the public key
  // (not actually a real attack -- the test is that we reject BEFORE
  // attempting verify so a future verifier swap can never silently downgrade).
  const fakeSig = b64url("not-really-a-signature");
  const token = `${header}.${payload}.${fakeSig}`;
  assert.equal(verify(token), null);
});

test("token within 30s expiry leeway is still valid (C5)", async () => {
  const { privateKey, publicPem } = makeKeyPair();
  process.env.IMS_SSO_PUBLIC_KEY = publicPem;
  const verify = await loadVerifier();

  // exp 10s in the past (well within 30s leeway).
  const now = Math.floor(Date.now() / 1000);
  const token = signRs256(
    privateKey,
    { alg: "RS256", typ: "JWT" },
    baseClaims({ exp: now - 10, iat: now - 100 }),
  );
  const claims = verify(token);
  assert.ok(claims);
  assert.equal(claims?.email, "alice@example.com");
});

test("token with iat too far in the future is rejected (C5)", async () => {
  const { privateKey, publicPem } = makeKeyPair();
  process.env.IMS_SSO_PUBLIC_KEY = publicPem;
  const verify = await loadVerifier();

  // iat 5 minutes in the future -- way beyond the 60s skew leeway.
  const now = Math.floor(Date.now() / 1000);
  const token = signRs256(
    privateKey,
    { alg: "RS256", typ: "JWT" },
    baseClaims({ iat: now + 300, exp: now + 400 }),
  );
  assert.equal(verify(token), null);
});

test("missing email is rejected", async () => {
  const { privateKey, publicPem } = makeKeyPair();
  process.env.IMS_SSO_PUBLIC_KEY = publicPem;
  const verify = await loadVerifier();

  // Build claims WITHOUT email to verify the missing-email guard still fires.
  const now = Math.floor(Date.now() / 1000);
  const claimsObj = {
    sub: "u1",
    role: "ADMIN",
    aud: "bvi",
    iss: "ims",
    scope: "ecommerce",
    iat: now,
    exp: now + 90,
    jti: "no-email-jti",
  };
  const token = signRs256(privateKey, { alg: "RS256", typ: "JWT" }, claimsObj);
  assert.equal(verify(token), null);
});

test("no public key configured returns null", async () => {
  delete process.env.IMS_SSO_PUBLIC_KEY;
  const verify = await loadVerifier();
  assert.equal(verify("anything"), null);
});
