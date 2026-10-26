/**
 * Redirect allow-list. Every `next=` / `redirect_to` value passes through `safeNext` before use, so an
 * attacker-supplied link can never bounce a signed-in user to another site (open redirect) or to a
 * javascript: URL. Only same-origin paths under these prefixes are allowed.
 */
export const REDIRECT_PREFIXES = ["/app", "/app/bookings", "/app/settings"] as const;
export const DEFAULT_AFTER_SIGN_IN = "/app";

const BASE = "https://astro.invalid";

export function safeNext(input: string | null | undefined, fallback: string = DEFAULT_AFTER_SIGN_IN): string {
  if (!input || typeof input !== "string" || input.length > 512) return fallback;
  let raw = input.trim();
  try {
    raw = decodeURIComponent(raw);
  } catch {
    return fallback;
  }
  // Must be a plain absolute path: not protocol-relative (//evil), not backslash tricks (/\evil), no scheme.
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\") || /[\u0000-\u001f]/.test(raw)) {
    return fallback;
  }
  let url: URL;
  try {
    url = new URL(raw, BASE);
  } catch {
    return fallback;
  }
  if (url.origin !== BASE) return fallback;
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const allowed = REDIRECT_PREFIXES.some((p) => path === p || path.startsWith(`${p}/`));
  if (!allowed) return fallback;
  return `${path}${url.search}`;
}

/** Absolute URL for links GoTrue puts in emails; GOTRUE_URI_ALLOW_LIST must contain `${origin}/auth/confirm`. */
export function confirmUrl(origin: string, next: string): string {
  return `${origin}/auth/confirm?next=${encodeURIComponent(safeNext(next))}`;
}
