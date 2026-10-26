// No build-time NEXT_PUBLIC_* values: one image serves development, staging and production.
// The browser talks to GoTrue on its own origin (/auth/v1); the server goes through the internal origin.
export const ANON_KEY = "astrovoice-public"; // GoTrue does not check the apikey header; supabase-js requires one.
// Fixed cookie name: the browser reaches GoTrue via the public origin and the server via the internal one, and
// supabase-js would otherwise derive two different cookie names from the two hostnames.
export const AUTH_COOKIE = "sb-astrovoice-auth-token";

export function internalOrigin(): string {
  return process.env.INTERNAL_ORIGIN ?? "http://localhost:8080";
}

export function publicOrigin(): string {
  return process.env.PUBLIC_ORIGIN ?? "http://localhost:8080";
}

export const isProd = process.env.NODE_ENV === "production";
export const secureCookies = () => publicOrigin().startsWith("https://");
