import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";
import { authOptions } from "@/lib/auth";
import { effectiveFeatures, type FeatureKey } from "@/lib/features";

type Role = "ADMIN" | "DESIGN_MANAGER" | "CATALOG_MANAGER" | "STAFF";

interface AuthResult {
  authorized: boolean;
  session: any;
  response?: NextResponse;
}

interface RequireAuthOptions {
  /** Restrict to specific roles. ADMIN is always allowed implicitly. */
  roles?: Role | Role[];
  /** Require the user to have a specific feature enabled. ADMIN bypasses. */
  feature?: FeatureKey;
}

/**
 * Reusable auth guard for API routes. Three calling forms:
 *
 *   requireAuth()                                  → any logged-in user
 *   requireAuth("ADMIN")                           → ADMIN only
 *   requireAuth(["ADMIN","CATALOG_MANAGER"])       → either role
 *   requireAuth({ feature: "reports" })            → user has reports
 *   requireAuth({ roles: "ADMIN", feature: ... })  → both checks
 *
 * The legacy positional form is preserved so existing callers keep
 * working. New endpoints should pass an options object.
 */
export async function requireAuth(
  arg?: Role | Role[] | RequireAuthOptions
): Promise<AuthResult> {
  const session = await getServerSession(authOptions);

  if (!session?.user) {
    return {
      authorized: false,
      session: null,
      response: NextResponse.json(
        { success: false, error: "Authentication required" },
        { status: 401 }
      ),
    };
  }

  // Normalize the argument into the options shape.
  let opts: RequireAuthOptions = {};
  if (Array.isArray(arg)) {
    opts.roles = arg;
  } else if (typeof arg === "string") {
    opts.roles = arg as Role;
  } else if (arg && typeof arg === "object") {
    opts = arg;
  }

  // Role check (ADMIN always implicitly allowed).
  const userRole = session.user.role as Role | undefined;
  if (opts.roles) {
    const allowed = Array.isArray(opts.roles) ? opts.roles : [opts.roles];
    if (userRole !== "ADMIN" && (!userRole || !allowed.includes(userRole))) {
      return {
        authorized: false,
        session,
        response: NextResponse.json(
          { success: false, error: "Insufficient role permissions" },
          { status: 403 }
        ),
      };
    }
  }

  // Feature check. The session payload carries enabledFeatures (set in
  // src/lib/auth.ts session/jwt callbacks). ADMIN bypasses this too.
  if (opts.feature && userRole !== "ADMIN") {
    const userFeatures = effectiveFeatures({
      role: userRole,
      enabledFeatures: (session.user as any).enabledFeatures ?? null,
    });
    if (!userFeatures.includes(opts.feature)) {
      return {
        authorized: false,
        session,
        response: NextResponse.json(
          {
            success: false,
            error: `Feature "${opts.feature}" is disabled for your account. Ask an admin to enable it.`,
          },
          { status: 403 }
        ),
      };
    }
  }

  return { authorized: true, session };
}
