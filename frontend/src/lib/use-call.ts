"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useScope } from "@/components/session";
import { ApiError } from "@/lib/api";

export type Trace = {
  turn: number; event: string; state: [string, string]; intent: string;
  parse: Record<string, unknown> | null; draft: Record<string, unknown>;
  checks: { rule: string; status: string; reason: string; params: Record<string, unknown> }[];
  offers: unknown; effects: { kind: string; [k: string]: unknown }[]; stack: string[]; notes: string[];
  latency_ms?: { brain: number; turn: number }; data?: Record<string, unknown>;
};
export type Line = { id: number; who: "caller" | "agent" | "system"; text: string; interim?: boolean };
export type Phase = "idle" | "connecting" | "live" | "ended" | "error";
type TurnOut = { reply: string; state: string; question: boolean; ended: boolean; trace: Trace; events: { type: string; [k: string]: unknown }[] };
type StartOut = TurnOut & { call_id: string; ticket: string; ws_path: string; voice: boolean };

const SILENCE_MS = 12000;

function downsample(input: Float32Array, from: number, to = 16000): ArrayBuffer {
  const ratio = from / to, len = Math.floor(input.length / ratio), out = new Int16Array(len);
  for (let i = 0; i < len; i++) {
    const s = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)] ?? 0));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out.buffer;
}

/** One call: REST start → WebSocket (ticket) → text or voice turns. The brain lives on the server. */
export function useCall() {
  const { call } = useScope();
  const [phase, setPhase] = useState<Phase>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [traces, setTraces] = useState<Trace[]>([]);
  const [state, setState] = useState<string>("—");
  const [voice, setVoice] = useState(false);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [hold, setHold] = useState<{ id: string; expires: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ws = useRef<WebSocket | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const pendingAudio = useRef(false);
  const mic = useRef<{ ctx: AudioContext; stream: MediaStream; node: ScriptProcessorNode } | null>(null);
  const silence = useRef<number | null>(null);
  const seq = useRef(0);
  const voiceMode = useRef(false);

  const add = useCallback((who: Line["who"], text: string, interim = false) => {
    setLines((ls) => {
      const base = ls.length && ls[ls.length - 1]?.interim ? ls.slice(0, -1) : ls;
      return [...base, { id: ++seq.current, who, text, interim }];
    });
  }, []);

  const clearSilence = () => { if (silence.current) { clearTimeout(silence.current); silence.current = null; } };
  const armSilence = useCallback(() => {
    clearSilence();
    if (!voiceMode.current) return;
    silence.current = window.setTimeout(() => ws.current?.send(JSON.stringify({ type: "silence", speak: true })), SILENCE_MS);
  }, []);

  const onTurn = useCallback((o: TurnOut) => {
    add("agent", o.reply);
    setState(o.state);
    setTraces((t) => [...t.slice(-49), o.trace]);
    for (const e of o.events) {
      if (e.type === "hold") setHold({ id: String(e.booking_id), expires: Date.parse(String(e.expires_at)) });
      if (e.type === "booked" || e.type === "hold_released") setHold(null);
    }
    if (o.ended) { setPhase("ended"); setHold(null); }
  }, [add]);

  const stopMic = useCallback(() => {
    const m = mic.current; mic.current = null;
    if (m) { m.node.disconnect(); m.stream.getTracks().forEach((t) => t.stop()); void m.ctx.close(); }
    setListening(false);
    ws.current?.readyState === WebSocket.OPEN && ws.current.send(JSON.stringify({ type: "audio_stop" }));
  }, []);

  const end = useCallback(() => {
    clearSilence(); stopMic();
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify({ type: "hangup" }));
    audio.current?.pause();
  }, [stopMic]);

  useEffect(() => () => { clearSilence(); stopMic(); ws.current?.close(); }, [stopMic]);

  const start = useCallback(async (wantVoice: boolean) => {
    setError(null); setLines([]); setTraces([]); setHold(null); setPhase("connecting");
    try {
      const s = await call<StartOut>("/api/calls", { method: "POST", body: JSON.stringify({ channel: wantVoice ? "voice" : "text" }) });
      voiceMode.current = s.voice; setVoice(s.voice);
      onTurn(s);
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const sock = new WebSocket(`${proto}://${location.host}${s.ws_path}?ticket=${encodeURIComponent(s.ticket)}`);
      sock.binaryType = "blob";
      ws.current = sock;
      sock.onopen = () => setPhase("live");
      sock.onclose = () => { setPhase((p) => (p === "ended" ? p : "ended")); stopMic(); clearSilence(); };
      sock.onerror = () => setError("The connection dropped. Start a new call to continue.");
      sock.onmessage = (ev) => {
        if (ev.data instanceof Blob) {
          if (!pendingAudio.current) return;
          pendingAudio.current = false;
          const url = URL.createObjectURL(ev.data);
          const el = audio.current ?? new Audio(); audio.current = el;
          el.src = url; setSpeaking(true); clearSilence();
          el.onended = () => { setSpeaking(false); URL.revokeObjectURL(url); armSilence(); };
          void el.play().catch(() => setSpeaking(false));
          return;
        }
        const m = JSON.parse(String(ev.data)) as { type: string; [k: string]: unknown };
        switch (m.type) {
          case "reply": onTurn(m as unknown as TurnOut); break;
          case "audio": pendingAudio.current = true; break;
          case "transcript": add("caller", String(m.text), !m.final); if (m.final) clearSilence(); break;
          case "user_speaking":
            clearSilence();
            if (audio.current && !audio.current.paused) { audio.current.pause(); setSpeaking(false); sock.send(JSON.stringify({ type: "barge_in" })); }
            break;
          case "hold": setHold({ id: String(m.booking_id), expires: Date.parse(String(m.expires_at)) }); break;
          case "booked": case "hold_released": setHold(null); break;
          case "error": setError(String(m.message)); break;
          case "tts_unavailable": add("system", "Voice playback is unavailable; replies are shown as text."); break;
        }
      };
    } catch (e) {
      setPhase("error");
      setError(e instanceof ApiError ? e.body.message : "Couldn't start the call. Please try again.");
    }
  }, [call, onTurn, add, armSilence, stopMic]);

  const say = useCallback((text: string) => {
    const t = text.trim();
    if (!t || ws.current?.readyState !== WebSocket.OPEN) return;
    clearSilence(); add("caller", t);
    if (audio.current && !audio.current.paused) { audio.current.pause(); setSpeaking(false); ws.current.send(JSON.stringify({ type: "barge_in" })); }
    ws.current.send(JSON.stringify({ type: "text", text: t.slice(0, 500), speak: voiceMode.current }));
  }, [add]);

  const undo = useCallback(() => ws.current?.send(JSON.stringify({ type: "undo", speak: voiceMode.current })), []);

  const startMic = useCallback(async () => {
    if (!voiceMode.current || mic.current || ws.current?.readyState !== WebSocket.OPEN) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
      const ctx = new AudioContext();
      const src = ctx.createMediaStreamSource(stream);
      const node = ctx.createScriptProcessor(2048, 1, 1);
      node.onaudioprocess = (e) => {
        if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(downsample(e.inputBuffer.getChannelData(0), ctx.sampleRate));
      };
      src.connect(node); node.connect(ctx.destination);
      ws.current.send(JSON.stringify({ type: "audio_start", sample_rate: 16000 }));
      mic.current = { ctx, stream, node }; setListening(true);
    } catch {
      setError("Microphone access was blocked. You can type instead.");
    }
  }, []);

  return { phase, lines, traces, state, voice, listening, speaking, hold, error, start, say, undo, end, startMic, stopMic };
}
