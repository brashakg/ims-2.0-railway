// Server-side guard for every page under /dashboard/admin/*. Redirects
// any non-admin who lands on an admin URL (typed it directly, bookmark,
// stale link) to the dashboard. The Sidebar already hides these links
// for non-admins, but URL guessing isn't covered there.
import { redirect } from "next/navigation";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";

export default async function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getServerSession(authOptions);
  if (!session?.user) {
    redirect("/login");
  }
  if ((session.user as any).role !== "ADMIN") {
    redirect("/dashboard");
  }
  return <>{children}</>;
}
