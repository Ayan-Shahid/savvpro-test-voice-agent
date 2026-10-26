import "server-only";
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { ANON_KEY, AUTH_COOKIE, internalOrigin, secureCookies } from "@/lib/env";

export async function supabaseServer() {
  const store = await cookies();
  return createServerClient(internalOrigin(), ANON_KEY, {
    auth: { flowType: "pkce" },
    cookieOptions: { name: AUTH_COOKIE, sameSite: "lax", secure: secureCookies(), path: "/", httpOnly: false },
    cookies: {
      getAll: () => store.getAll(),
      setAll: (list) => {
        try {
          for (const { name, value, options } of list) store.set(name, value, options);
        } catch {
          // Called from a Server Component: the proxy refreshes cookies instead.
        }
      },
    },
  });
}
