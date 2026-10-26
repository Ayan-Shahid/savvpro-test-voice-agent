import Link from "next/link";
import { GalaxyBackground } from "@/components/galaxy";
import { LandingSphere } from "@/components/landing-sphere";
import { Logo } from "@/components/ui";

const steps = [
  { t: "Say what you want to see", d: "“Book the refractor for Saturn on Thursday from eight to ten.” Plain speech, interruptions and changes of mind included." },
  { t: "The brain checks the sky", d: "Twelve rules run in a fixed order: altitude, darkness, the Moon, weather, maintenance, other bookings and your allocation." },
  { t: "Hold, confirm, observe", d: "The slot is held for two minutes while you confirm. The database itself makes double bookings impossible." },
];

const telescopes = [
  { n: "Optical Refractor", c: "OPT-25", d: "Planets, the Moon, bright clusters and nebulae. Needs a dark, mostly clear sky." },
  { n: "Deep-Space Reflector", c: "DSR-100", d: "Faint galaxies and nebulae. Fussy about cloud and moonlight." },
  { n: "Sub-Millimeter Radio Dish", c: "SMM-12", d: "Works in daylight and through cirrus; limited by water vapour instead." },
];

export default function Landing() {
  return (
    <>
      <GalaxyBackground />
      <LandingSphere />
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <Logo />
        <nav className="flex items-center gap-3 text-sm">
          <Link href="/sign-in" className="rounded-lg px-3 py-2 text-mist-300 hover:text-white">Sign in</Link>
          <Link href="/sign-up" className="btn-primary rounded-xl px-4 py-2 font-semibold">Create account</Link>
        </nav>
      </header>

      <main>
        <section className="mx-auto flex min-h-[86vh] max-w-6xl flex-col justify-center px-6">
          <p className="mb-4 text-sm uppercase tracking-[0.25em] text-aurora-300">Meridian Ridge University Observatory</p>
          <h1 className="max-w-2xl text-5xl font-semibold leading-[1.05] tracking-tight md:text-7xl">Book telescope time by talking.</h1>
          <p className="mt-6 max-w-xl text-lg text-mist-300">
            AstroVoice listens, checks the real sky over a 2,400 m ridge, and books the slot. Every decision comes from
            rules you can read in the Brain Inspector — no language model decides anything.
          </p>
          <div className="mt-10 flex flex-wrap gap-3">
            <Link href="/sign-up" className="btn-primary inline-flex h-12 items-center rounded-xl px-6 font-semibold">Start a call</Link>
            <a href="#how" className="inline-flex h-12 items-center rounded-xl border border-white/15 bg-white/5 px-6 font-semibold hover:bg-white/10">How it works</a>
          </div>
        </section>

        <section id="how" className="mx-auto max-w-6xl px-6 py-28">
          <h2 className="text-3xl font-semibold md:text-4xl">From a sentence to a booking</h2>
          <ol className="mt-10 grid gap-5 md:grid-cols-3">
            {steps.map((s, i) => (
              <li key={s.t} className="glass p-6">
                <span className="font-mono text-sm text-violet-400">0{i + 1}</span>
                <h3 className="mt-2 text-lg font-semibold">{s.t}</h3>
                <p className="mt-2 text-mist-300">{s.d}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="mx-auto grid max-w-6xl items-center gap-10 px-6 py-28 md:grid-cols-2">
          <div className="md:order-2">
            <h2 className="text-3xl font-semibold md:text-4xl">A brain you can inspect</h2>
            <p className="mt-4 text-mist-300">
              Open the inspector beside any call to see the parse, the state machine, every rule result, the alternatives
              the planner ranked and the undo stack. Same words, same sky, same answer — every time.
            </p>
          </div>
          <pre className="glass overflow-x-auto p-5 font-mono text-[12.5px] leading-relaxed text-mist-300 md:order-1">{`intent   SWITCH_TARGET
state    PROPOSING → CONFIRMING
checks   policy ✓  capability ✓  altitude ✓
         darkness ✓  moon ✓  weather ✓
         availability ✓  allocation ✓
effects  hold(DSR-100, 23:00–01:00)
stack    [target: Saturn] [telescope: OPT-25]`}</pre>
        </section>

        <section className="mx-auto max-w-6xl px-6 py-28">
          <h2 className="text-3xl font-semibold md:text-4xl">Three instruments</h2>
          <div className="mt-10 grid gap-5 md:grid-cols-3">
            {telescopes.map((t) => (
              <article key={t.c} className="glass p-6">
                <p className="font-mono text-xs text-aurora-300">{t.c}</p>
                <h3 className="mt-1 text-lg font-semibold">{t.n}</h3>
                <p className="mt-2 text-mist-300">{t.d}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="mx-auto max-w-6xl px-6 pb-40 pt-28 text-center">
          <h2 className="text-3xl font-semibold md:text-5xl">Clear skies tonight?</h2>
          <p className="mx-auto mt-4 max-w-lg text-mist-300">Ask the agent. It will tell you, and book you in if they are.</p>
          <Link href="/sign-up" className="btn-primary mt-8 inline-flex h-12 items-center rounded-xl px-7 font-semibold">Create a free account</Link>
        </section>
      </main>
      <footer className="border-t border-white/10 py-8 text-center text-sm text-mist-500">
        A fictional observatory at 30° N 75° E, 2,400 m. Demo data only.
      </footer>
    </>
  );
}
