import Link from "next/link";

export const ERRORS: Record<string, { title: string; body: string; action?: { href: string; label: string } }> = {
  "400": { title: "That request didn't make sense", body: "Something in the link or form was malformed. Go back and try again." },
  "401": { title: "Please sign in", body: "Your session has ended or you haven't signed in yet.", action: { href: "/sign-in", label: "Sign in" } },
  "402": { title: "Out of observing hours", body: "You've used this semester's allocation. Cancel a future booking to free hours, or ask the observatory for more time.", action: { href: "/app/bookings", label: "View bookings" } },
  "403": { title: "Not allowed", body: "Your account can't open this page." },
  "404": { title: "Lost in space", body: "We couldn't find that page. It may have moved, or the link may be wrong." },
  "429": { title: "Slow down a little", body: "Too many requests in a short time. Wait a minute, then try again." },
  "500": { title: "Something went wrong", body: "An unexpected error happened on our side. It's been logged." },
  "502": { title: "The observatory is unreachable", body: "A service behind this page didn't answer. Try again in a moment." },
  "503": { title: "Down for maintenance", body: "AstroVoice is being updated or the dome is closed for servicing. Please check back shortly." },
};

export function ErrorView({ code, children }: { code: string; children?: React.ReactNode }) {
  const e = ERRORS[code] ?? ERRORS["500"]!;
  return (
    <main className="grid min-h-dvh place-items-center px-6">
      <div className="glass max-w-lg p-10 text-center">
        <p className="font-mono text-6xl font-semibold text-violet-400">{code}</p>
        <h1 className="mt-4 text-2xl font-semibold">{e.title}</h1>
        <p className="mt-3 text-mist-300">{e.body}</p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          {e.action && <Link href={e.action.href} className="btn-primary inline-flex h-11 items-center rounded-xl px-5 font-semibold">{e.action.label}</Link>}
          <Link href="/" className="inline-flex h-11 items-center rounded-xl border border-white/15 bg-white/5 px-5 font-semibold hover:bg-white/10">Home</Link>
          {children}
        </div>
      </div>
    </main>
  );
}
