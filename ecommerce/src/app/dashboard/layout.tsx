"use client";

import { useSession } from "next-auth/react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import Sidebar from "@/components/Sidebar";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { data: session, status } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (status === "unauthenticated") {
      router.push("/login");
    }
  }, [status, router]);

  if (status === "loading") {
    return (
      <div
        className="flex items-center justify-center h-screen"
        style={{ background: "var(--bg)" }}
      >
        <div className="text-center">
          <div
            className="w-12 h-12 border-4 rounded-full animate-spin mx-auto mb-4"
            style={{
              borderColor: "var(--border-strong)",
              borderTopColor: "var(--brand)",
            }}
          />
          <p style={{ color: "var(--text-secondary)" }}>Loading…</p>
        </div>
      </div>
    );
  }

  if (status === "unauthenticated") {
    return null;
  }

  return (
    // Polaris-flavored shell. The .polaris-shell class scopes the new
    // 13px / system-font / light-gray styling to the dashboard only —
    // login and any non-shell pages keep the original 16px / Tailwind
    // styling so they don't suddenly look broken.
    //
    // Sidebar is sticky-positioned inside the flex row instead of
    // fixed/absolute, so the main column flows naturally without a
    // hardcoded margin-left.
    <div
      className="polaris-shell flex min-h-screen"
      style={{ background: "var(--bg)" }}
    >
      <Sidebar />
      <main className="flex-1 min-w-0 overflow-auto">{children}</main>
    </div>
  );
}
