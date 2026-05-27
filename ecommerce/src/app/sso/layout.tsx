import type { Metadata } from "next";

// Route-segment metadata for /sso (Council Branch C).
// Token-in-URL hardening: ensure no Referer header is ever sent while the URL
// briefly contains ?token=... (between page mount and the history.replaceState
// scrub in page.tsx). Next's App Router emits this as <meta name="referrer">
// on the rendered HTML.
export const metadata: Metadata = {
  title: "Signing in...",
  referrer: "no-referrer",
  robots: {
    index: false,
    follow: false,
    nocache: true,
  },
};

export default function SsoLayout({ children }: { children: React.ReactNode }) {
  return children;
}
