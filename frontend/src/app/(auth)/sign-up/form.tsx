"use client";
import Link from "next/link";
import { useState } from "react";
import { supabaseBrowser } from "@/lib/supabase/client";
import { confirmUrl } from "@/lib/redirects";
import { authMessage, fieldErrors, signUp, type FieldErrors } from "@/lib/validation";
import { Button, Notice, TextField } from "@/components/ui";
import { GoogleSignIn } from "@/components/oauth";

export function SignUpForm({ next }: { next: string | null }) {
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  async function submit(form: FormData) {
    const parsed = signUp.safeParse(Object.fromEntries(["name", "email", "password", "confirm"].map((k) => [k, form.get(k)])));
    if (!parsed.success) return setErrors(fieldErrors(parsed.error));
    setBusy(true); setErrors({});
    const { error } = await supabaseBrowser().auth.signUp({
      email: parsed.data.email,
      password: parsed.data.password,
      options: { data: { display_name: parsed.data.name }, emailRedirectTo: confirmUrl(location.origin, next ?? "/app") },
    });
    setBusy(false);
    if (error) return setErrors({ form: authMessage(error.code, "We couldn't create your account. Please try again.") });
    setSentTo(parsed.data.email); // same message whether or not the email already existed
  }
  if (sentTo) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-semibold">Check your inbox</h1>
        <p className="text-mist-300">If <strong>{sentTo}</strong> can be registered, we sent a confirmation link to it. The link works once and expires in an hour.</p>
        <Link href="/sign-in" className="text-sm text-violet-400 hover:underline">Back to sign in</Link>
      </div>
    );
  }
  return (
    <form onSubmit={(e) => { e.preventDefault(); void submit(new FormData(e.currentTarget)); }} noValidate className="flex flex-col gap-5">
      <div><h1 className="text-2xl font-semibold">Create your account</h1><p className="mt-1 text-sm text-mist-500">New accounts start with 10 observing hours this semester.</p></div>
      <GoogleSignIn next={next} />
      {errors.form && <Notice tone="error">{errors.form}</Notice>}
      <TextField label="Your name" name="name" autoComplete="name" error={errors.name} required />
      <TextField label="Email" name="email" type="email" autoComplete="email" error={errors.email} required />
      <TextField label="Password" name="password" type="password" autoComplete="new-password" error={errors.password} hint="At least 10 characters." required />
      <TextField label="Confirm password" name="confirm" type="password" autoComplete="new-password" error={errors.confirm} required />
      <Button type="submit" loading={busy}>Create account</Button>
      <p className="text-center text-sm text-mist-500">Already registered? <Link href="/sign-in" className="text-violet-400 hover:underline">Sign in</Link></p>
    </form>
  );
}
