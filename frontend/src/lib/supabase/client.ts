"use client";
import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import { ANON_KEY, AUTH_COOKIE } from "@/lib/env";

let client: SupabaseClient | null = null;

export function supabaseBrowser(): SupabaseClient {
  if (!client) {
    client = createBrowserClient(window.location.origin, ANON_KEY, {
      auth: { flowType: "pkce", detectSessionInUrl: false, persistSession: true },
      cookieOptions: { name: AUTH_COOKIE, sameSite: "lax", secure: window.location.protocol === "https:", path: "/" },
    });
  }
  return client;
}
