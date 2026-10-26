"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { GalaxyBackground } from "./galaxy";
import { Logo } from "./ui";
import { useResource, useScope } from "./session";

export type Me = {
  id: string; email: string | null; display_name: string; affiliation: string | null; is_admin: boolean; member_since: string;
  settings: { voice: string; speech_rate: number; input_mode: string; barge_in: boolean; captions: boolean; reduced_motion: "system" | "on" | "off";
    high_contrast: boolean; time_format: "12h" | "24h"; date_order: "DMY" | "MDY"; email_confirmations: boolean; keep_transcripts: boolean };
  allocation: { semester: string; grade: string; hours_granted: number; hours_used: number } | null;
};

const NAV = [{ href: "/app", label: "Call" }, { href: "/app/bookings", label: "Bookings" }, { href: "/app/settings", label: "Settings" }];

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { signOut } = useScope();
  const { data: me } = useResource<Me>("me", "/api/me");
  useEffect(() => {
    if (!me) return;
    const el = document.documentElement;
    el.dataset.motion = me.settings.reduced_motion === "system" ? "" : me.settings.reduced_motion;
    el.dataset.contrast = me.settings.high_contrast ? "high" : "";
    return () => { el.dataset.motion = ""; el.dataset.contrast = ""; };
  }, [me]);
  return (
    <>
      <GalaxyBackground />
      <header className="sticky top-0 z-20 border-b border-white/10 bg-ink-950/60 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-6 px-5">
          <Link href="/app"><Logo /></Link>
          <nav className="flex gap-1 text-sm" aria-label="Main">
            {NAV.map((n) => (
              <Link key={n.href} href={n.href} aria-current={path === n.href ? "page" : undefined}
                className={`rounded-lg px-3 py-2 ${path === n.href ? "bg-white/10 text-white" : "text-mist-300 hover:text-white"}`}>{n.label}</Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            {me?.allocation && <span className="hidden rounded-full border border-white/15 px-3 py-1 text-mist-300 sm:inline">
              Grade {me.allocation.grade} · {Math.max(0, me.allocation.hours_granted - me.allocation.hours_used).toFixed(1)} h left</span>}
            <span className="hidden text-mist-300 md:inline">{me?.display_name}</span>
            <button onClick={signOut} className="rounded-lg px-3 py-2 text-mist-300 hover:bg-white/10 hover:text-white">Sign out</button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-5 py-8">{children}</main>
    </>
  );
}
