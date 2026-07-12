import { createHash, timingSafeEqual } from "node:crypto";

import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";

function constantTimePasswordMatch(candidate: string, expected: string): boolean {
  const candidateDigest = createHash("sha256").update(candidate).digest();
  const expectedDigest = createHash("sha256").update(expected).digest();
  return timingSafeEqual(candidateDigest, expectedDigest);
}

export const { auth, handlers, signIn, signOut } = NextAuth({
  secret: process.env.AUTH_SECRET,
  trustHost: true,
  session: {
    strategy: "jwt",
    maxAge: 12 * 60 * 60,
  },
  pages: {
    signIn: "/login",
  },
  providers: [
    Credentials({
      name: "Loopie owner password",
      credentials: {
        password: { label: "Password", type: "password" },
      },
      authorize(credentials) {
        const expected = process.env.LOOPIE_ADMIN_PASSWORD;
        const candidate = credentials?.password;
        if (!expected || typeof candidate !== "string") return null;
        if (!constantTimePasswordMatch(candidate, expected)) return null;
        return { id: "loopie-owner", name: "Loopie Owner" };
      },
    }),
  ],
  callbacks: {
    authorized: ({ auth: session }) => Boolean(session?.user),
  },
});
