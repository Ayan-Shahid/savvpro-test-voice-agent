"use client";
import { useState } from "react";
import { useResource, useScope } from "@/components/session";
import type { Me } from "@/components/app-shell";
import { Button, Notice, TextField } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { displayName } from "@/lib/validation";
import { supabaseBrowser } from "@/lib/supabase/client";

type Session = { id: string; created_at: string | null; last_active: string | null; user_agent: string; ip: string | null; mfa: boolean; current: boolean };
type S = Me["settings"];

function Section({ title, desc, children }: { title: string; desc?: string; children: React.ReactNode }) {
  return (
    <section className="glass p-6">
      <h2 className="text-lg font-semibold">{title}</h2>
      {desc && <p className="mt-1 text-sm text-mist-500">{desc}</p>}
      <div className="mt-5 space-y-4">{children}</div>
    </section>
  );
}

function Toggle({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-4">
      <span><span className="block text-sm font-medium">{label}</span>{hint && <span className="block text-xs text-mist-500">{hint}</span>}</span>
      <input type="checkbox" role="switch" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-1 size-5 accent-violet-400" />
    </label>
  );
}

function Select<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <label className="flex items-center justify-between gap-4 text-sm font-medium">{label}
      <select value={value} onChange={(e) => onChange(e.target.value as T)} className="h-10 rounded-lg border border-white/15 bg-ink-900 px-3 text-mist-100">
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}

export default function SettingsPage() {
  const { call, invalidate, email, signOut } = useScope();
  const me = useResource<Me>("me", "/api/me");
  const sessions = useResource<{ sessions: Session[] }>("sessions", "/api/me/sessions");
  const [nameErr, setNameErr] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [deleteErr, setDeleteErr] = useState<string | null>(null);

  if (me.loading || !me.data) return <p className="text-mist-500">Loading…</p>;
  const m = me.data;

  async function save(patch: Partial<S>) {
    try {
      await call("/api/me/settings", { method: "PATCH", body: JSON.stringify(patch) });
      invalidate("me");
      setFlash({ tone: "ok", text: "Saved." });
    } catch (e) {
      setFlash({ tone: "error", text: e instanceof ApiError ? e.body.message : "Couldn't save that setting." });
    }
  }

  async function saveProfile(form: FormData) {
    const parsed = displayName.safeParse(form.get("display_name"));
    if (!parsed.success) return setNameErr(parsed.error.issues[0]?.message ?? "Check your name.");
    const aff = String(form.get("affiliation") ?? "").trim();
    if (aff.length > 120) return setNameErr(null);
    setNameErr(null); setSaving(true);
    try {
      await call("/api/me/profile", { method: "PATCH", body: JSON.stringify({ display_name: parsed.data, affiliation: aff || null }) });
      invalidate("me"); setFlash({ tone: "ok", text: "Profile updated." });
    } catch (e) {
      const f = e instanceof ApiError ? e.body.fields?.find((x) => x.field === "display_name") : undefined;
      setNameErr(f?.message ?? (e instanceof ApiError ? e.body.message : "Couldn't save your profile."));
    } finally { setSaving(false); }
  }

  async function revoke(id: string) {
    try { await call(`/api/me/sessions/${id}`, { method: "DELETE" }); invalidate("sessions"); }
    catch (e) { setFlash({ tone: "error", text: e instanceof ApiError ? e.body.message : "Couldn't sign that session out." }); }
  }

  async function signOutEverywhere() {
    await supabaseBrowser().auth.signOut({ scope: "global" });
    await signOut();
  }

  async function deleteAccount() {
    if (confirmText !== "DELETE") return setDeleteErr("Type DELETE in capitals to confirm.");
    setDeleteErr(null);
    try {
      await call("/api/me/delete", { method: "POST", body: JSON.stringify({ confirm: "DELETE" }) });
      await signOut();
    } catch (e) { setDeleteErr(e instanceof ApiError ? e.body.message : "Couldn't delete your account."); }
  }

  const s = m.settings;
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      {flash && <Notice tone={flash.tone}>{flash.text}</Notice>}

      <Section title="Profile" desc={`Signed in as ${email ?? m.email ?? ""}.`}>
        <form onSubmit={(e) => { e.preventDefault(); void saveProfile(new FormData(e.currentTarget)); }} noValidate className="space-y-4">
          <TextField label="Display name" name="display_name" defaultValue={m.display_name} error={nameErr} maxLength={80} />
          <TextField label="Affiliation (optional)" name="affiliation" defaultValue={m.affiliation ?? ""} maxLength={120} hint="Department or group, shown only to you." />
          <Button type="submit" loading={saving}>Save profile</Button>
        </form>
        {m.allocation && <p className="text-sm text-mist-300">Semester {m.allocation.semester}: grade {m.allocation.grade}, {m.allocation.hours_used} of {m.allocation.hours_granted} hours used.</p>}
      </Section>

      <Section title="Voice" desc="How AstroVoice sounds and listens.">
        <Select label="Voice" value={s.voice} onChange={(v) => save({ voice: v })}
          options={[["flux-haley-en", "Haley (Flux)"], ["aura-2-thalia-en", "Thalia (Aura-2)"], ["aura-2-orion-en", "Orion (Aura-2)"], ["aura-2-andromeda-en", "Andromeda (Aura-2)"]]} />
        <Select label="Speaking rate" value={String(s.speech_rate)} onChange={(v) => save({ speech_rate: Number(v) })}
          options={[["0.85", "Slower"], ["1", "Normal"], ["1.15", "Faster"]]} />
        <Select label="Microphone" value={s.input_mode} onChange={(v) => save({ input_mode: v })}
          options={[["hands_free", "Hands-free"], ["push_to_talk", "Push to talk"]]} />
        <Toggle label="Let me interrupt" hint="Speaking while AstroVoice talks stops it (barge-in)." checked={s.barge_in} onChange={(v) => save({ barge_in: v })} />
        <Toggle label="Show captions" checked={s.captions} onChange={(v) => save({ captions: v })} />
      </Section>

      <Section title="Display">
        <Select label="Time format" value={s.time_format} onChange={(v) => save({ time_format: v })} options={[["12h", "12-hour"], ["24h", "24-hour"]]} />
        <Select label="Date order" value={s.date_order} onChange={(v) => save({ date_order: v })} options={[["DMY", "Day / month"], ["MDY", "Month / day"]]} />
        <Select label="Reduce motion" value={s.reduced_motion} onChange={(v) => save({ reduced_motion: v })} options={[["system", "Follow system"], ["on", "On"], ["off", "Off"]]} />
        <Toggle label="High contrast" checked={s.high_contrast} onChange={(v) => save({ high_contrast: v })} />
      </Section>

      <Section title="Notifications & privacy">
        <Toggle label="Email me confirmations" checked={s.email_confirmations} onChange={(v) => save({ email_confirmations: v })} />
        <Toggle label="Keep call transcripts" hint="Kept for 30 days for the inspector. Audio is never stored." checked={s.keep_transcripts} onChange={(v) => save({ keep_transcripts: v })} />
      </Section>

      <Section title="Sessions" desc="Devices signed in to your account.">
        <ul className="divide-y divide-white/10">
          {sessions.data?.sessions.map((x) => (
            <li key={x.id} className="flex items-center gap-3 py-3 text-sm">
              <div className="min-w-0 flex-1"><p className="truncate">{x.user_agent || "Unknown device"}</p>
                <p className="text-xs text-mist-500">{x.ip ?? "—"} · last active {x.last_active ? new Date(x.last_active).toLocaleString() : "—"}</p></div>
              {x.current ? <span className="text-xs text-aurora-300">This device</span> : <Button variant="ghost" onClick={() => revoke(x.id)}>Sign out</Button>}
            </li>
          ))}
        </ul>
        <Button variant="ghost" onClick={signOutEverywhere}>Sign out everywhere</Button>
      </Section>

      <Section title="Delete account" desc="Cancels your future bookings and permanently removes your data. This can't be undone.">
        <TextField label='Type "DELETE" to confirm' value={confirmText} onChange={(e) => setConfirmText(e.target.value)} error={deleteErr} autoComplete="off" />
        <Button variant="danger" onClick={deleteAccount}>Delete my account</Button>
      </Section>
    </div>
  );
}
