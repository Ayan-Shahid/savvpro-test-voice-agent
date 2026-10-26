"use client";
import { useState } from "react";
import type { Trace } from "@/lib/use-call";

const STATUS: Record<string, string> = { pass: "text-ok-300", fail: "text-danger-300", warn: "text-warn-300", skip: "text-mist-500" };
const MARK: Record<string, string> = { pass: "✓", fail: "✗", warn: "!", skip: "–" };

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return <div className="grid grid-cols-[88px_1fr] gap-3 py-1.5"><dt className="text-mist-500">{k}</dt><dd className="min-w-0 break-words">{children}</dd></div>;
}

/** The Brain Inspector: what the deterministic brain parsed, checked, offered and did on each turn. */
export function BrainInspector({ traces }: { traces: Trace[] }) {
  const [pick, setPick] = useState<number | null>(null);
  const t = pick === null ? traces[traces.length - 1] : traces[pick];
  if (!t) {
    return <div className="glass flex h-full min-h-[420px] items-center justify-center p-6 text-center text-sm text-mist-500">
      Start a call and every decision the brain makes will appear here.</div>;
  }
  const parse = t.parse ? Object.entries(t.parse).filter(([, v]) => v !== null && v !== false && !(Array.isArray(v) && v.length === 0)) : [];
  return (
    <section aria-label="Brain Inspector" className="glass flex h-full min-h-[420px] flex-col overflow-hidden">
      <header className="flex items-center justify-between border-b border-white/10 px-5 py-3">
        <h2 className="text-sm font-semibold">Brain Inspector</h2>
        <label className="flex items-center gap-2 text-xs text-mist-500">Turn
          <select className="rounded-md border border-white/15 bg-ink-900 px-2 py-1 text-mist-100" value={pick ?? traces.length - 1}
            onChange={(e) => setPick(Number(e.target.value) === traces.length - 1 ? null : Number(e.target.value))}>
            {traces.map((tr, i) => <option key={i} value={i}>{tr.turn} · {tr.intent}</option>)}
          </select>
        </label>
      </header>
      <dl className="flex-1 overflow-y-auto px-5 py-3 font-mono text-[12.5px] leading-relaxed">
        <Row k="event">{t.event}</Row>
        <Row k="intent"><span className="text-violet-400">{t.intent}</span></Row>
        <Row k="state">{t.state[0]} <span className="text-mist-500">→</span> <span className="text-aurora-300">{t.state[1]}</span></Row>
        {parse.length > 0 && <Row k="parsed">{parse.map(([k, v]) => <div key={k}>{k}: {typeof v === "object" ? JSON.stringify(v) : String(v)}</div>)}</Row>}
        <Row k="draft">{Object.entries(t.draft).filter(([, v]) => v !== null).map(([k, v]) => `${k}=${String(v)}`).join("  ") || "—"}</Row>
        <Row k="checks">{t.checks.length === 0 ? "—" : t.checks.map((c, i) => (
          <div key={`${c.rule}-${i}`} className={STATUS[c.status] ?? ""}>{MARK[c.status] ?? "·"} {c.rule}{c.reason ? ` · ${c.reason}` : ""}</div>))}</Row>
        {Array.isArray(t.offers) && t.offers.length > 0 && <Row k="offers">{(t.offers as unknown[]).map((o, i) => <div key={i}>{JSON.stringify(o)}</div>)}</Row>}
        <Row k="effects">{t.effects.length ? t.effects.map((e, i) => <div key={i}>{e.kind}</div>) : "—"}</Row>
        <Row k="undo stack">{t.stack.length ? t.stack.map((s, i) => <div key={i}>{i + 1}. {s}</div>) : "empty"}</Row>
        {t.notes?.length > 0 && <Row k="notes">{t.notes.map((n, i) => <div key={i}>{n}</div>)}</Row>}
        {t.latency_ms && <Row k="latency">brain {t.latency_ms.brain} ms · turn {t.latency_ms.turn} ms</Row>}
      </dl>
    </section>
  );
}
