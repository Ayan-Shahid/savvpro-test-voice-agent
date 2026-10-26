import { NextResponse, type NextRequest } from "next/server";
import type { EmailOtpType } from "@supabase/supabase-js";
import { supabaseServer } from "@/lib/supabase/server";
import { publicOrigin } from "@/lib/env";
import { safeNext } from "@/lib/redirects";

const TYPES: EmailOtpType[] = ["signup", "recovery", "email_change", "email", "invite", "magiclink"];

/** Email links land here with a token hash; we verify it server-side and only ever redirect on-site. */
export async function GET(req: NextRequest) {
  const url = req.nextUrl;
  const tokenHash = url.searchParams.get("token_hash");
  const type = url.searchParams.get("type") as EmailOtpType | null;
  const base = publicOrigin();
  if (!tokenHash || !type || !TYPES.includes(type) || tokenHash.length > 256) {
    return NextResponse.redirect(new URL("/sign-in?notice=link_expired", base), 303);
  }
  const supabase = await supabaseServer();
  const { error } = await supabase.auth.verifyOtp({ token_hash: tokenHash, type });
  if (error) return NextResponse.redirect(new URL("/sign-in?notice=link_expired", base), 303);
  const to = type === "recovery" ? "/reset-password" : safeNext(url.searchParams.get("next"));
  return NextResponse.redirect(new URL(to, base), 303);
}
