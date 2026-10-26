"use client";
import Link from "next/link";
import { useState } from "react";
import { z } from "zod";
import { supabaseBrowser } from "@/lib/supabase/client";
import { email } from "@/lib/validation";
import { Button, TextField } from "@/components/ui";

export default function ForgotPassword() {
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  async function submit(form: FormData) {
    const parsed = z.object({ email }).safeParse({ email: form.get("email") });
    if (!parsed.success) return setError(parsed.error.issues[0]?.message ?? "Check the email address.");
    setBusy(true); setError(null);
    await supabaseBrowser().auth.resetPasswordForEmail(parsed.data.email, { redirectTo: `${location.origin}/auth/confirm?next=/reset-password` });
    setBusy(false); setDone(true); // never reveal whether the address is registered
  }
  if (done) return <div className="flex flex-col gap-4"><h1 className="text-2xl font-semibold">Check your inbox</h1>
    <p className="text-mist-300">If that address has an account, a reset link is on its way. It expires in an hour.</p>
    <Link href="/sign-in" className="text-sm text-violet-400 hover:underline">Back to sign in</Link></div>;
  return (
    <form onSubmit={(e) => { e.preventDefault(); void submit(new FormData(e.currentTarget)); }} noValidate className="flex flex-col gap-5">
      <div><h1 className="text-2xl font-semibold">Reset your password</h1><p className="mt-1 text-sm text-mist-500">We&apos;ll email you a link.</p></div>
      <TextField label="Email" name="email" type="email" autoComplete="email" error={error} required />
      <Button type="submit" loading={busy}>Send reset link</Button>
      <Link href="/sign-in" className="text-center text-sm text-violet-400 hover:underline">Back to sign in</Link>
    </form>
  );
}
