"use client";
import { useState } from "react";
import { useResource, useScope } from "@/components/session";
import { Button, Notice } from "@/components/ui";
import type { Me } from "@/components/app-shell";
import { fmtWhen } from "@/lib/format";
import { ApiError } from "@/lib/api";

type Booking = { id: string; status: string; start: string; end: string; telescope_name: string; target_name: string | null; source: string };
type Entry = { id: string; telescope: string; target_name: string; start: string; end: string; position: number; status: string };

export default function BookingsPage() {
  const { call, invalidate } = useScope();
  const [scope, setScope] = useState<"upcoming" | "past">("upcoming");
  const me = useResource<Me>("me", "/api/me");
  const list = useResource<{ bookings: Booking[] }>(`bookings:${scope}`, `/api/bookings?scope=${scope}`);
  const wait = useResource<{ entries: Entry[] }>("waitlist", "/api/waitlist");
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const h24 = me.data?.settings.time_format === "24h";

  async function cancel(id: string) {
    if (!window.confirm("Cancel this booking? The slot will be offered to the waitlist.")) return;
    setBusy(id);
    try {
      await call(`/api/bookings/${id}/cancel`, { method: "POST" });
      setMsg({ tone: "ok", text: "Booking cancelled." });
      invalidate("bookings"); invalidate("me");
    } catch (e) {
      setMsg({ tone: "error", text: e instanceof ApiError ? e.body.message : "Couldn't cancel that booking." });
    } finally { setBusy(null); }
  }
  async function leave(id: string) {
    try { await call(`/api/waitlist/${id}`, { method: "DELETE" }); invalidate("waitlist"); }
    catch (e) { setMsg({ tone: "error", text: e instanceof ApiError ? e.body.message : "Couldn't leave the waitlist." }); }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <div className="flex items-end justify-between">
        <h1 className="text-2xl font-semibold">Your bookings</h1>
        <div role="tablist" className="flex gap-1 rounded-xl border border-white/12 p-1 text-sm">
          {(["upcoming", "past"] as const).map((s) => (
            <button key={s} role="tab" aria-selected={scope === s} onClick={() => setScope(s)}
              className={`rounded-lg px-3 py-1.5 capitalize ${scope === s ? "bg-white/10" : "text-mist-500"}`}>{s}</button>))}
        </div>
      </div>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      {list.error && <Notice tone="error">{list.error.body.message}</Notice>}
      {list.loading ? <p className="text-mist-500">Loading…</p> : (
        <ul className="space-y-3">
          {list.data?.bookings.length === 0 && <li className="glass p-6 text-mist-500">Nothing here yet. Start a call to book.</li>}
          {list.data?.bookings.map((b) => (
            <li key={b.id} className="glass flex items-center gap-4 p-5">
              <div className="min-w-0 flex-1">
                <p className="font-semibold">{b.target_name ?? "Maintenance"} <span className="font-normal text-mist-500">on the {b.telescope_name}</span></p>
                <p className="text-sm text-mist-300">{fmtWhen(b.start, b.end, h24)} <span className="text-mist-500">(site time)</span></p>
              </div>
              <span className="rounded-full border border-white/15 px-2.5 py-0.5 text-xs capitalize text-mist-300">{b.status}</span>
              {scope === "upcoming" && b.status === "confirmed" && <Button variant="danger" loading={busy === b.id} onClick={() => cancel(b.id)}>Cancel</Button>}
            </li>
          ))}
        </ul>
      )}
      <section>
        <h2 className="mb-3 text-lg font-semibold">Waitlist</h2>
        <ul className="space-y-3">
          {wait.data?.entries.length === 0 && <li className="text-sm text-mist-500">You&apos;re not waiting for any slots.</li>}
          {wait.data?.entries.map((w) => (
            <li key={w.id} className="glass flex items-center gap-4 p-4">
              <div className="flex-1"><p className="font-medium">{w.target_name} · {w.telescope}</p>
                <p className="text-sm text-mist-300">{fmtWhen(w.start, w.end, h24)} · position {w.position}</p></div>
              <Button variant="ghost" onClick={() => leave(w.id)}>Leave</Button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
