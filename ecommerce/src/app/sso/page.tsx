"use client";

// SSO landing: IMS opens /sso?token=<exchange token>. We hand the token to the
// "ims-sso" NextAuth provider (which verifies it + maps to an existing user by
// email) and, on success, land the user in the app already logged in.
//
// Token-in-URL hardening (Council Branch C):
// - history.replaceState scrubs ?token=... from window.location BEFORE the
//   signIn call so a slow signIn cannot leak the URL via Referer headers,
//   browser history, devtools-paste, or third-party telemetry.
// - meta name="referrer" content="no-referrer" on the /sso route stops the
//   browser from sending the (still-in-URL during this React render) token in
//   the Referer header of any sub-resource fetched from this page.

import { Suspense, useEffect, useState } from "react";
import { signIn } from "next-auth/react";
import { useSearchParams } from "next/navigation";

function SsoInner() {
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = params.get("token");

    // SCRUB FIRST: strip ?token=... from the URL bar immediately so the token
    // never appears in window.location during the signIn round-trip. This must
    // happen before any await so even a delayed signIn doesn't leak.
    if (typeof window !== "undefined") {
      try {
        window.history.replaceState({}, "", window.location.pathname);
      } catch {
        // history APIs may throw in exotic sandboxes; the meta no-referrer
        // tag is a second layer of defence.
      }
    }

    if (!token) {
      setError("Missing sign-in token.");
      return;
    }
    (async () => {
      try {
        const res = await signIn("ims-sso", {
          token,
          redirect: false,
          callbackUrl: "/",
        });
        if (res && res.ok && !res.error) {
          window.location.href = res.url || "/";
        } else {
          setError(
            "Single sign-on failed. Your IMS account may not have a matching " +
              "online-store user, or the link expired. Please sign in manually.",
          );
        }
      } catch {
        setError("Single sign-on failed. Please sign in manually.");
      }
    })();
  }, [params]);

  return (
    <div style={{ maxWidth: 420, margin: "12vh auto", textAlign: "center", fontFamily: "system-ui, sans-serif" }}>
      {/* Defence-in-depth: tell the browser not to leak the token in Referer
          headers of any sub-resource (analytics pixels, font-CDN, etc.) fetched
          while the URL still contains ?token= (between mount and scrub). */}
      <meta name="referrer" content="no-referrer" />
      {error ? (
        <>
          <h1 style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Couldn&apos;t sign you in</h1>
          <p style={{ color: "#555", fontSize: 14, marginBottom: 16 }}>{error}</p>
          <a href="/login" style={{ color: "#2563eb", fontSize: 14 }}>Go to the login page</a>
        </>
      ) : (
        <>
          <h1 style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Signing you in…</h1>
          <p style={{ color: "#555", fontSize: 14 }}>Connecting from IMS. One moment.</p>
        </>
      )}
    </div>
  );
}

export default function SsoPage() {
  return (
    <Suspense
      fallback={
        <div style={{ textAlign: "center", marginTop: "12vh", fontFamily: "system-ui, sans-serif" }}>
          Signing you in…
        </div>
      }
    >
      <SsoInner />
    </Suspense>
  );
}
