import { NextAuthOptions } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";
import { compare } from "bcryptjs";
import { prisma } from "./prisma";

declare module "next-auth" {
  interface Session {
    user: {
      id: string;
      name?: string | null;
      email?: string | null;
      role: string;
      locationId?: string | null;
      /** Comma-separated FeatureKey list. NULL → use role defaults.
       *  See src/lib/features.ts for resolution semantics. */
      enabledFeatures?: string | null;
    };
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    id: string;
    role: string;
    locationId?: string | null;
    enabledFeatures?: string | null;
  }
}

export const authOptions: NextAuthOptions = {
  providers: [
    CredentialsProvider({
      name: "Credentials",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        if (!credentials?.email || !credentials?.password) {
          return null;
        }

        const user = await prisma.user.findUnique({
          where: { email: credentials.email },
        });

        if (!user) {
          return null;
        }

        const isPasswordValid = await compare(
          credentials.password,
          user.password
        );

        if (!isPasswordValid) {
          return null;
        }

        return {
          id: user.id,
          name: user.name,
          email: user.email,
          role: user.role,
          locationId: user.locationId,
          enabledFeatures: (user as any).enabledFeatures ?? null,
        } as any;
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user, trigger }: any) {
      // First sign-in: copy from the user record into the token.
      if (user) {
        token.id = user.id;
        token.role = user.role;
        token.locationId = user.locationId;
        token.enabledFeatures = (user as any).enabledFeatures ?? null;
      }

      // On every refresh ("update" trigger or routine session check), re-
      // read the role + enabledFeatures from the DB so role / feature
      // changes take effect without forcing the user to sign out and back
      // in. We intentionally ignore email/name updates here — those don't
      // change often and re-querying every request adds load.
      if (token.id) {
        try {
          const fresh = await prisma.user.findUnique({
            where: { id: token.id as string },
            select: {
              role: true,
              locationId: true,
              enabledFeatures: true,
            },
          });
          if (fresh) {
            token.role = fresh.role;
            token.locationId = fresh.locationId;
            token.enabledFeatures = fresh.enabledFeatures;
          } else {
            // User deleted while session is still valid — invalidate.
            return null as any;
          }
        } catch (err) {
          // DB blip — keep the existing token rather than logging the
          // user out unexpectedly.
        }
      }
      return token;
    },
    async session({ session, token }: any) {
      if (session.user) {
        session.user.id = token.id as string;
        session.user.role = token.role as string;
        session.user.locationId = token.locationId as string | null;
        session.user.enabledFeatures = (token.enabledFeatures ?? null) as
          | string
          | null;
      }
      return session;
    },
  },
  pages: {
    signIn: "/login",
  },
  session: {
    strategy: "jwt",
  },
  secret: process.env.NEXTAUTH_SECRET,
};
