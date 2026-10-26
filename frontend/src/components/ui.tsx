"use client";
import { forwardRef, useId, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";

type Variant = "primary" | "ghost" | "danger";

export function Button({ variant = "primary", loading, className = "", children, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean }) {
  const base = "inline-flex items-center justify-center gap-2 rounded-xl px-5 h-11 text-sm font-semibold transition-[filter,background-color,border-color] select-none";
  const styles: Record<Variant, string> = {
    primary: "btn-primary",
    ghost: "border border-white/15 bg-white/5 hover:bg-white/10 text-mist-100",
    danger: "border border-danger-500/60 bg-danger-50 text-danger-300 hover:bg-danger-500/20",
  };
  return (
    <button {...rest} disabled={rest.disabled || loading} aria-busy={loading || undefined}
      className={`${base} ${styles[variant]} ${className}`}>
      {loading && <span className="size-4 rounded-full border-2 border-white/40 border-t-white animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

function ErrorIcon() {
  return (
    <svg viewBox="0 0 20 20" className="size-4 shrink-0" aria-hidden fill="currentColor">
      <path fillRule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm-.75-11.5a.75.75 0 0 1 1.5 0v4a.75.75 0 0 1-1.5 0v-4ZM10 14.75a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z" clipRule="evenodd" />
    </svg>
  );
}

type FieldProps = InputHTMLAttributes<HTMLInputElement> & { label: string; error?: string | null; hint?: ReactNode };

/** Text field whose error state is a tinted background, a border and an icon, not just red text. */
export const TextField = forwardRef<HTMLInputElement, FieldProps>(function TextField(
  { label, error, hint, className = "", id, ...rest }, ref) {
  const auto = useId();
  const fid = id ?? auto;
  const msgId = `${fid}-msg`;
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <label htmlFor={fid} className="text-sm font-medium text-mist-300">{label}</label>
      <input ref={ref} id={fid} {...rest} aria-invalid={error ? true : undefined}
        aria-describedby={error || hint ? msgId : undefined}
        className={`h-11 rounded-xl px-3.5 text-[15px] text-mist-100 placeholder:text-mist-500 outline-none transition-colors border ${
          error ? "bg-danger-50 border-danger-500 focus:border-danger-300"
                : "bg-ink-900/70 border-white/12 focus:border-violet-400"}`} />
      {error ? (
        <p id={msgId} role="alert" className="flex items-start gap-2 rounded-lg border border-danger-500/50 bg-danger-50 px-3 py-2 text-[13px] text-danger-300">
          <ErrorIcon /><span>{error}</span>
        </p>
      ) : hint ? <p id={msgId} className="text-[13px] text-mist-500">{hint}</p> : null}
    </div>
  );
});

export function Notice({ tone = "info", children }: { tone?: "info" | "error" | "ok"; children: ReactNode }) {
  const t = { info: "border-white/15 bg-white/5 text-mist-300", error: "border-danger-500/60 bg-danger-50 text-danger-300", ok: "border-ok-300/40 bg-ok-300/10 text-ok-300" }[tone];
  return <div role={tone === "error" ? "alert" : "status"} className={`flex items-start gap-2 rounded-xl border px-3.5 py-2.5 text-sm ${t}`}>
    {tone === "error" && <ErrorIcon />}<div>{children}</div></div>;
}

export function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-semibold tracking-tight ${className}`}>
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.6">
        <circle cx="16" cy="16" r="12" opacity=".5" /><ellipse cx="16" cy="16" rx="12" ry="4.5" transform="rotate(-25 16 16)" />
        <ellipse cx="16" cy="16" rx="4.5" ry="12" transform="rotate(-25 16 16)" opacity=".7" /><circle cx="16" cy="16" r="2.4" fill="currentColor" />
      </svg>
      AstroVoice
    </span>
  );
}
