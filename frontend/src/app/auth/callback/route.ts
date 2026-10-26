import { NextResponse, type NextRequest } from "next/server";
import { supabaseServer } from "@/lib/supabase/server";
import { publicOrigin } from "@/lib/env";
import { safeNext } from "@/lib/redirects";

/** PKCE code exchange (used if GoTrue redirects with ?code=). The verifier cookie must match. */
export async function GET(req: NextRequest) {
  const code = req.nextUrl.searchParams.get("code");
  const base = publicOrigin();
  // Provider errors (user pressed Cancel, consent denied) come back as ?error=…; never echo them.
  if (req.nextUrl.searchParams.get("error")) {
    return NextResponse.redirect(new URL("/sign-in?notice=oauth_cancelled", base), 303);
  }
  if (!code || code.length > 512) return NextResponse.redirect(new URL("/sign-in?notice=link_expired", base), 303);
  const supabase = await supabaseServer();
  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) return NextResponse.redirect(new URL("/sign-in?notice=link_expired", base), 303);
  return NextResponse.redirect(new URL(safeNext(req.nextUrl.searchParams.get("next")), base), 303);
}
