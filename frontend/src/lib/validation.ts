import { z } from "zod";

export const email = z.string().trim().toLowerCase().min(1, "Enter your email address.").max(254)
  .pipe(z.email({ message: "That doesn't look like an email address." }));
export const password = z.string().min(10, "Use at least 10 characters.").max(128, "Use at most 128 characters.");
export const displayName = z.string().trim().min(1, "Tell us what to call you.").max(80, "Keep it under 80 characters.")
  .refine((v) => !/[\u0000-\u001f<>]/.test(v), "Please avoid special characters.");

export const signIn = z.object({ email, password: z.string().min(1, "Enter your password.") });
export const signUp = z.object({ name: displayName, email, password, confirm: z.string() })
  .refine((v) => v.password === v.confirm, { path: ["confirm"], message: "Passwords don't match." });
export const reset = z.object({ password, confirm: z.string() })
  .refine((v) => v.password === v.confirm, { path: ["confirm"], message: "Passwords don't match." });

export type FieldErrors = Record<string, string>;

export function fieldErrors(err: z.ZodError): FieldErrors {
  const out: FieldErrors = {};
  for (const issue of err.issues) {
    const k = String(issue.path[0] ?? "form");
    if (!out[k]) out[k] = issue.message;
  }
  return out;
}

/** GoTrue messages are mapped to friendly copy; we never reveal whether an email is registered. */
export function authMessage(code: string | undefined, fallback: string): string {
  switch (code) {
    case "invalid_credentials": return "That email and password don't match.";
    case "email_not_confirmed": return "Please confirm your email first — check your inbox.";
    case "over_request_rate_limit":
    case "over_email_send_rate_limit": return "Too many attempts. Please wait a minute and try again.";
    case "weak_password": return "Choose a stronger password: longer, and not a common one.";
    case "same_password": return "Choose a password you haven't used here before.";
    case "signup_disabled": return "Sign-up is closed right now.";
    default: return fallback;
  }
}
