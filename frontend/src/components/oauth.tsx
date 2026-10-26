"use client";
import { useEffect, useState } from "react";
import { supabaseBrowser } from "@/lib/supabase/client";
import { safeNext } from "@/lib/redirects";
import { Button, Notice } from "./ui";

/**
 * "Continue with Google": OAuth 2.0 authorisation-code flow with PKCE, brokered by GoTrue.
 * The browser goes to /auth/v1/authorize → Google → /auth/v1/callback (GoTrue) → /auth/callback?code=…
 * (our route), which exchanges the code server-side with the PKCE verifier cookie and redirects only to an
 * allow-listed path. The button only appears when GoTrue reports Google as enabled.
 */
export function GoogleSignIn({ next }: { next: string | null }) {
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetch("/auth/v1/settings", { credentials: "omit", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((s: { external?: Record<string, boolean> } | null) => { if (live) setEnabled(Boolean(s?.external?.google)); })
      .catch(() => undefined);
    return () => { live = false; };
  }, []);

  if (!enabled) return null;

  async function go() {
    setBusy(true); setError(null);
    const redirectTo = `${location.origin}/auth/callback?next=${encodeURIComponent(safeNext(next))}`;
    const { error: e } = await supabaseBrowser().auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo, queryParams: { prompt: "select_account" } },
    });
    if (e) { setBusy(false); setError("Google sign-in isn't available right now. Use your email instead."); }
  }

  return (
    <div className="flex flex-col gap-3">
      {error && <Notice tone="error">{error}</Notice>}
      <Button type="button" variant="ghost" loading={busy} onClick={go} className="w-full">
        Continue with Google
      </Button>
      <div className="flex items-center gap-3 text-xs uppercase tracking-wider text-mist-500" aria-hidden>
        <span className="h-px flex-1 bg-white/10" />or<span className="h-px flex-1 bg-white/10" />
      </div>
    </div>
  );
}
