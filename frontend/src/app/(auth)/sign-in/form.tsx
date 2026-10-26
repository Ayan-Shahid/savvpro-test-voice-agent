"use client";
import Link from "next/link";
import { useState } from "react";
import { supabaseBrowser } from "@/lib/supabase/client";
import { safeNext } from "@/lib/redirects";
import { authMessage, fieldErrors, signIn, type FieldErrors } from "@/lib/validation";
import { Button, Notice, TextField } from "@/components/ui";
import { GoogleSignIn } from "@/components/oauth";
import { announceSignIn } from "@/components/session";

const NOTICES: Record<string, string> = {
  confirmed: "Email confirmed. You can sign in now.",
  reset: "Password updated. Sign in with your new password.",
  signed_out: "You have been signed out.",
  link_expired: "That link has expired or was already used. Request a new one.",
  oauth_cancelled: "Google sign-in was cancelled. You can try again or use your email.",
};

export function SignInForm({ next, notice }: { next: string | null; notice: string | null }) {
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);
  async function submit(form: FormData) {
    const parsed = signIn.safeParse({ email: form.get("email"), password: form.get("password") });
    if (!parsed.success) return setErrors(fieldErrors(parsed.error));
    setBusy(true); setErrors({});
    const { data, error } = await supabaseBrowser().auth.signInWithPassword(parsed.data);
    if (error || !data.user) {
      setBusy(false);
      return setErrors({ form: authMessage(error?.code, "We couldn't sign you in. Please try again.") });
    }
    announceSignIn(data.user.id);
    window.location.replace(safeNext(next)); // full load: fresh memory for the new user
  }
  return (
    <form onSubmit={(e) => { e.preventDefault(); void submit(new FormData(e.currentTarget)); }} noValidate className="flex flex-col gap-5">
      <div><h1 className="text-2xl font-semibold">Welcome back</h1><p className="mt-1 text-sm text-mist-500">Sign in to book telescope time.</p></div>
      {notice && NOTICES[notice] && <Notice tone={notice === "link_expired" || notice === "oauth_cancelled" ? "error" : "ok"}>{NOTICES[notice]}</Notice>}
      <GoogleSignIn next={next} />
      {errors.form && <Notice tone="error">{errors.form}</Notice>}
      <TextField label="Email" name="email" type="email" autoComplete="email" error={errors.email} required />
      <TextField label="Password" name="password" type="password" autoComplete="current-password" error={errors.password} required />
      <div className="-mt-2 text-right text-sm"><Link href="/forgot-password" className="text-violet-400 hover:underline">Forgot password?</Link></div>
      <Button type="submit" loading={busy}>Sign in</Button>
      <p className="text-center text-sm text-mist-500">New here? <Link href={`/sign-up${next ? `?next=${encodeURIComponent(safeNext(next))}` : ""}`} className="text-violet-400 hover:underline">Create an account</Link></p>
    </form>
  );
}
