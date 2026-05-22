// /dashboard/users now redirects to /dashboard/admin/users — there were
// two separate user-management pages with subtly different UIs and the
// admin one is the canonical version. Keeping a redirect (instead of
// deleting this route) preserves any bookmarks staff may have.
import { redirect } from "next/navigation";

export default function UsersRedirect() {
  redirect("/dashboard/admin/users");
}
