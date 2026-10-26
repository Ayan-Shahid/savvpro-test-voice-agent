"use client";
import { useState } from "react";
import { supabaseBrowser } from "@/lib/supabase/client";
import { authMessage, fieldErrors, reset, type FieldErrors } from "@/lib/validation";
import { Button, Notice, TextField } from "@/components/ui";

export default function ResetPassword() {
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);
  async function submit(form: FormData) {
    const parsed = reset.safeParse({ password: form.get("password"), confirm: form.get("confirm") });
    if (!parsed.success) return setErrors(fieldErrors(parsed.error));
    setBusy(true); setErrors({});
    const sb = supabaseBrowser();
    const { error } = await sb.auth.updateUser({ password: parsed.data.password });
    if (error) {
      setBusy(false);
      return setErrors({ form: error.code === "session_not_found" || error.status === 401
        ? "Your reset link has expired. Request a new one." : authMessage(error.code, "We couldn't update your password.") });
    }
    await sb.auth.signOut({ scope: "others" });
    window.location.replace("/app");
  }
  return (
    <form onSubmit={(e) => { e.preventDefault(); void submit(new FormData(e.currentTarget)); }} noValidate className="flex flex-col gap-5">
      <div><h1 className="text-2xl font-semibold">Choose a new password</h1><p className="mt-1 text-sm text-mist-500">Other devices will be signed out.</p></div>
      {errors.form && <Notice tone="error">{errors.form}</Notice>}
      <TextField label="New password" name="password" type="password" autoComplete="new-password" error={errors.password} hint="At least 10 characters." />
      <TextField label="Confirm new password" name="confirm" type="password" autoComplete="new-password" error={errors.confirm} />
      <Button type="submit" loading={busy}>Update password</Button>
    </form>
  );
}
