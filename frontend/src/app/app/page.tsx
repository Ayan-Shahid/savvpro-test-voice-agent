"use client";
import { motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { BrainInspector } from "@/components/inspector";
import { Button, Notice } from "@/components/ui";
import { useCall } from "@/lib/use-call";

const EXAMPLES = [
  "Book the refractor for Saturn on Thursday from 8 to 10 PM",
  "What's the weather like tomorrow night?",
  "Is the Andromeda Galaxy visible tonight?",
  "Cancel my booking on Friday",
];

function Orb({ active, speaking, listening }: { active: boolean; speaking: boolean; listening: boolean }) {
  return (
    <div className="relative mx-auto my-6 grid size-40 place-items-center" aria-hidden>
      <motion.div className="absolute inset-0 rounded-full border border-violet-400/40"
        animate={active ? { scale: speaking ? [1, 1.18, 1] : listening ? [1, 1.08, 1] : 1, opacity: [0.6, 1, 0.6] } : { scale: 1, opacity: 0.4 }}
        transition={{ duration: speaking ? 0.9 : 2.4, repeat: Infinity, ease: "easeInOut" }} />
      <motion.div className="absolute inset-5 rounded-full border border-aurora-300/40"
        animate={active ? { rotate: 360 } : { rotate: 0 }} transition={{ duration: 14, repeat: Infinity, ease: "linear" }} />
      <div className={`size-20 rounded-full ${active ? "btn-primary" : "bg-ink-700"} shadow-[0_0_60px_rgba(155,134,255,0.35)]`} />
    </div>
  );
}

function HoldRing({ expires }: { expires: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 500); return () => clearInterval(t); }, []);
  const left = Math.max(0, Math.round((expires - now) / 1000));
  return <p className="text-center text-sm text-warn-300" role="timer" aria-live="off">Slot held · {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} left to confirm</p>;
}

export default function CallPage() {
  const c = useCall();
  const [text, setText] = useState("");
  const log = useRef<HTMLOListElement>(null);
  useEffect(() => { log.current?.lastElementChild?.scrollIntoView({ block: "nearest" }); }, [c.lines]);
  const live = c.phase === "live" || c.phase === "connecting";

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <section className="glass flex min-h-[620px] flex-col p-5" aria-label="Call">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold">Booking call</h1>
          <span className="rounded-full border border-white/15 px-3 py-1 font-mono text-xs text-mist-300">{c.state}</span>
        </div>
        <Orb active={live} speaking={c.speaking} listening={c.listening} />
        {c.hold && <HoldRing expires={c.hold.expires} />}
        {c.error && <div className="my-3"><Notice tone="error">{c.error}</Notice></div>}

        <ol ref={log} aria-live="polite" className="mt-2 flex-1 space-y-3 overflow-y-auto pr-1" style={{ maxHeight: 340 }}>
          {c.lines.map((l) => (
            <li key={l.id} className={`flex ${l.who === "caller" ? "justify-end" : "justify-start"}`}>
              <p className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-[15px] ${l.who === "caller" ? "bg-violet-400/20 text-white" : l.who === "system" ? "text-sm text-mist-500" : "bg-white/7 text-mist-100"} ${l.interim ? "opacity-60 italic" : ""}`}>
                <span className="sr-only">{l.who === "caller" ? "You: " : "AstroVoice: "}</span>{l.text}
              </p>
            </li>
          ))}
        </ol>

        {!live ? (
          <div className="mt-6 flex flex-wrap justify-center gap-3">
            <Button onClick={() => c.start(true)}>Start voice call</Button>
            <Button variant="ghost" onClick={() => c.start(false)}>Start text call</Button>
          </div>
        ) : (
          <div className="mt-4 space-y-3">
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); c.say(text); setText(""); }}>
              <label htmlFor="say" className="sr-only">Say something</label>
              <input id="say" value={text} onChange={(e) => setText(e.target.value)} maxLength={500} autoComplete="off"
                placeholder="Type what you'd say…" className="h-11 flex-1 rounded-xl border border-white/12 bg-ink-900/70 px-3.5 outline-none focus:border-violet-400" />
              <Button type="submit" disabled={!text.trim() || c.phase !== "live"}>Send</Button>
            </form>
            <div className="flex flex-wrap items-center gap-2">
              {c.voice && (c.listening
                ? <Button variant="ghost" onClick={c.stopMic}>Mute mic</Button>
                : <Button variant="ghost" onClick={c.startMic}>Use microphone</Button>)}
              <Button variant="ghost" onClick={c.undo}>Undo</Button>
              <Button variant="danger" onClick={c.end} className="ml-auto">End call</Button>
            </div>
            {!c.voice && <p className="text-xs text-mist-500">Text mode: voice is not configured on this server, or you chose to type.</p>}
          </div>
        )}
        {!live && (
          <div className="mt-6">
            <p className="mb-2 text-xs uppercase tracking-wider text-mist-500">Try saying</p>
            <ul className="flex flex-wrap gap-2 text-sm text-mist-300">{EXAMPLES.map((e) => <li key={e} className="rounded-full border border-white/12 px-3 py-1">“{e}”</li>)}</ul>
          </div>
        )}
      </section>
      <BrainInspector traces={c.traces} />
    </div>
  );
}
