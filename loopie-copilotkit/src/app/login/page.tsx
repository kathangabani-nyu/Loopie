import { AuthError } from "next-auth";
import { redirect } from "next/navigation";

import { signIn } from "@/auth";

async function login(formData: FormData) {
  "use server";

  try {
    await signIn("credentials", {
      password: formData.get("password"),
      redirectTo: "/",
    });
  } catch (error) {
    if (error instanceof AuthError) {
      redirect("/login?error=invalid");
    }
    throw error;
  }
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  return (
    <main className="min-h-screen bg-[var(--background)] text-[var(--foreground)] grid place-items-center p-6">
      <form
        action={login}
        className="w-full max-w-sm rounded-2xl border border-[var(--border)] bg-[var(--card)] p-7 shadow-2xl"
      >
        <img src="/loopie-mark.svg" alt="Loopie" className="mb-5 h-10 w-10" />
        <h1 className="text-2xl font-semibold">Loopie control plane</h1>
        <p className="mt-2 text-sm text-[var(--muted-foreground)]">
          Sign in with the single-owner password to access runs, corrections, and approvals.
        </p>
        <label className="mt-6 block text-sm font-medium" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          autoFocus
          className="mt-2 w-full rounded-xl border border-[var(--input)] bg-transparent px-4 py-3 outline-none focus:ring-2 focus:ring-[var(--ring)]"
        />
        {error ? (
          <p role="alert" className="mt-3 text-sm text-[var(--destructive)]">
            Invalid password.
          </p>
        ) : null}
        <button
          type="submit"
          className="mt-5 w-full rounded-xl bg-[var(--primary)] px-4 py-3 font-semibold text-[var(--primary-foreground)]"
        >
          Sign in
        </button>
      </form>
    </main>
  );
}
