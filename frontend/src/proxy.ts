import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { ANON_KEY, AUTH_COOKIE, internalOrigin, isProd, publicOrigin, secureCookies } from "@/lib/env";
import { safeNext } from "@/lib/redirects";

const AUTH_PAGES = ["/sign-in", "/sign-up", "/forgot-password"];

function csp(nonce: string): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isProd ? "" : " 'unsafe-eval'"}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "worker-src 'self' blob:",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "base-uri 'none'",
    "object-src 'none'",
    ...(publicOrigin().startsWith("https://") ? ["upgrade-insecure-requests"] : []),
  ].join("; ");
}

/** Next 16 proxy (formerly middleware): per-request CSP nonce, session refresh, route guards. */
export async function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.getRandomValues(new Uint8Array(16))).toString("base64");
  const policy = csp(nonce);
  const reqHeaders = new Headers(request.headers);
  reqHeaders.set("x-nonce", nonce);
  reqHeaders.set("content-security-policy", policy);

  let response = NextResponse.next({ request: { headers: reqHeaders } });
  const supabase = createServerClient(internalOrigin(), ANON_KEY, {
    cookieOptions: { name: AUTH_COOKIE, sameSite: "lax", secure: secureCookies(), path: "/" },
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (list) => {
        for (const { name, value } of list) request.cookies.set(name, value);
        response = NextResponse.next({ request: { headers: reqHeaders } });
        for (const { name, value, options } of list) response.cookies.set(name, value, options);
      },
    },
  });

  const path = request.nextUrl.pathname;
  const needsUser = path === "/app" || path.startsWith("/app/");
  const onAuthPage = AUTH_PAGES.includes(path);
  let userId: string | null = null;
  if (needsUser || onAuthPage) {
    // getUser() asks GoTrue, so a revoked or forged cookie never passes.
    const { data } = await supabase.auth.getUser().catch(() => ({ data: { user: null } }));
    userId = data.user?.id ?? null;
  }

  const redirect = (to: string) => {
    const r = NextResponse.redirect(new URL(to, request.url), 303);
    for (const c of response.cookies.getAll()) r.cookies.set(c);
    r.headers.set("Content-Security-Policy", policy);
    return r;
  };
  if (needsUser && !userId) {
    return redirect(`/sign-in?next=${encodeURIComponent(safeNext(path + request.nextUrl.search))}`);
  }
  if (onAuthPage && userId) {
    return redirect(safeNext(request.nextUrl.searchParams.get("next")));
  }
  response.headers.set("Content-Security-Policy", policy);
  if (userId) response.headers.set("Cache-Control", "private, no-store");
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|textures|favicon.ico|robots.txt|api/|ws/|auth/v1/).*)"],
};
