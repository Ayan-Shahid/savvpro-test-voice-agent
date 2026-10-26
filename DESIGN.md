# Design

## 1. The use case

A university observatory has three instruments and more people who want them than nights to give. Students
book time by phone today; the line is busy, the staff member answering has to check the sky, the weather,
the Moon and the schedule by hand, and people change their minds halfway through. AstroVoice answers that
phone line.

**Instruments** (fictional site at 30° N 75° E, 2,400 m, UTC+5, no daylight saving):

| Code | Instrument | Observes | Main limits |
|---|---|---|---|
| OPT-25 | Optical Refractor | Moon, planets, stars, bright clusters/galaxies/nebulae (mag ≤ 7 for extended) | altitude ≥ 20°, darkness, cloud ≤ 40 %, Sun ≥ 45° away |
| DSR-100 | Deep-Space Reflector | all optical targets including faint deep sky | altitude ≥ 20°, darkness, cloud ≤ 25 %, Moon glare |
| SMM-12 | Sub-Millimeter Radio Dish | Moon, planets, radio sources, molecular clouds, some galaxies/SNRs | altitude ≥ 15°, **water vapour** ≤ 4 mm (not cloud), works in daylight, Moon not in beam |

**Who:** students and staff with a semester allocation (grade A/B/C, 30/20/10 hours). **Rules:** slots on a
15-minute grid, 30 minutes to 4 hours, at least 30 minutes ahead, at most 30 nights ahead; holds last two
minutes while the caller confirms; cancelled slots go to the waitlist by grade, then first come first served.

The sky is real (JPL DE421 via Skyfield, precomputed every 5 minutes for 60 nights). The weather is simulated
but deterministic (BLAKE2b-seeded per night) with pinned demo scenarios: anchor night A clear, A+1 85 %
cirrus with low water vapour (optical blocked, dish fine), A+2/A+3 clear, A+5 wet (everything blocked).

## 2. Architecture

```
browser ──https──▶ Cloudflare ──tunnel──▶ Caddy ─┬─ /            ▶ Next.js 16 (pages, CSP nonce, auth guard)
                                                 ├─ /auth/v1/*   ▶ GoTrue (sign-up, sign-in, sessions; /admin blocked)
                                                 ├─ /api/*       ▶ FastAPI ─┬─▶ Postgres 18 (RLS on every table)
                                                 └─ /ws/*        ▶ FastAPI  ├─▶ Redis (call state, tickets, limits)
                                                                            └─▶ Deepgram (Flux STT, Flux TTS)
                                       Celery worker + beat ─▶ Postgres, Redis, SMTP (Mailpit / Resend)
```

One origin serves everything, so cookies never cross sites, CORS is unnecessary and the WebSocket shares the
page's origin.

A call turn:

1. The browser opens a call with `POST /api/calls` (Bearer token). The API creates the call row as the user,
   runs the brain's `start` event, and mints a **single-use WebSocket ticket** (30 s, bound to user, call
   and Origin; stored hashed in Redis). The token never appears in a URL.
2. Audio streams over the WebSocket to Deepgram Flux; an `EndOfTurn` transcript (with mean word confidence)
   becomes a `TurnEvent`. In text mode the typed sentence becomes the same event.
3. The API takes a per-call Redis lock, loads the `CallState`, builds a frozen `DataSnapshot` **as the user**
   (sky + weather cached by data version; busy intervals, allocation and own bookings read fresh), and calls
   `step(state, event, snapshot)`.
4. `step` returns the new state, the reply, a list of **effects** (hold, release, commit, cancel_booking,
   join_waitlist, end_call) and a full trace. The API executes the effects in one RLS transaction. If the
   database refuses a hold (someone took the slot a millisecond ago — exclusion constraint `23P01`), the
   refusal is fed back to the brain as a `hold_failed` event on fresh data and the brain decides what to say.
5. The reply goes back as text (and Flux TTS audio, with Aura-2 fallback); the trace goes to the Brain
   Inspector and, if the user keeps transcripts, to `call_turns`.

## 3. How the brain decides

`backend/src/astro_brain` is pure Python with no I/O, no clock, no randomness and no global state. Given the
same state, event and snapshot it returns the same result in any process (a test replays calls under
different `PYTHONHASHSEED`s and compares byte-for-byte). It is type-checked with `mypy --strict`.

### 3.1 State machine

States: `GREETING → COLLECTING ⇄ CLARIFYING → PROPOSING → CONFIRMING → BOOKED`, plus `WAITLIST`,
`CANCELLING` and `ENDED`. Each turn:

1. **Guards first.** Echo guard (the caller's text is ≥ 85 % similar to our last reply and ≥ 25 chars → it's
   our own voice through the speaker, ignore it). Low STT confidence (< 0.55) → "Did you say …?" before
   acting. Barge-in (`heard_full = false`) during a read-back → re-read before accepting a "yes".
2. **Parse** the utterance (§3.4) into targets, telescopes, times, nights, relative edits, picks and flags.
3. **Decide the intent** from the parse *and* the current state with a fixed precedence (forbidden targets
   before cancel, undo before anything else, a "yes" only means CONFIRM when something is pending, "yes,
   but …" with new slots is a change, not a confirmation). About 30 intents, e.g. `BOOK`, `CHANGE_SLOT`, `CONFIRM`,
   `DENY`, `UNDO`, `PICK_FIRST`, `CHECK_WEATHER`, `CHECK_VISIBILITY`, `CANCEL_BOOKING`, `JOIN_WAITLIST`,
   `OUT_OF_SCOPE`, `SILENCE`.
4. **Apply** the intent to the draft (`target, telescope, night, start, end, duration`), pushing an undo frame
   first.
5. **Advance**: ask for the single most important missing field (target → telescope → night → start →
   length), or, once complete, run the rule pipeline and either read the booking back (and take a hold) or
   explain the first failure and offer ranked alternatives.

### 3.2 Rule pipeline

Twelve rules in a fixed order; each returns `pass | warn | fail | skip` with a reason code and parameters
that the response layer turns into an explanation:

`policy` (grid, length, lead time, horizon) → `allocation` → `capability` (instrument × target kind,
magnitude) → `never_visible` (peak altitude from declination and latitude) → `sun_safety` → `altitude`
(every 5-minute sample inside the window) → `darkness` (Sun ≤ −12° for bright targets, ≤ −18° for faint;
skipped for the dish) → `moon` (glare by illumination and separation for deep sky; Moon in the dish's beam)
→ `weather` (cloud, water vapour, rain, wind per hour; beyond the forecast is a warning, not a failure) →
`maintenance` → `availability` → `caller_overlap`.

The first failure is what the caller hears ("Jupiter is below the horizon at 9 PM. It's up from 2:30 to 5:15
AM tonight."); the inspector shows all twelve.

### 3.3 Planner (alternatives)

When a request fails, the planner looks for the nearest thing the caller would accept, in this order: the
same night and telescope (same length, then trimmed), another capable telescope at the same time, then later
nights. It scans 15-minute slots, keeps maximal runs where every rule passes, and ranks candidates by the
tuple `(what changed, nights ahead, how far the start moved, minutes lost, telescope order)`. Up to three
offers are read out; "the second one" picks an offer.

### 3.4 Deterministic NLP

No model, no `dateparser`, no spaCy (all were tried in a stress test and replaced because they invented
dates from words like "march" and "sat", or turned "Saturn on Saturday" into two dates):

- **Normalisation:** lower case, our own number-word converter ("eleven thirty pm" → `11 30 pm`, "M forty
  two" → `m 42`) with protected phrases ("seven sisters", "the second one"), fillers dropped.
- **Lexicon matcher:** longest-match over ~300 aliases including mis-hearings ("beetle juice", "o ryan",
  "pleiadies", "sag a star"). Fuzzy matching (RapidFuzz) only for targets, auto-accepted at ≥ 92 with a 10-point
  lead, confirmed between 80 and 92, never for telescopes ("the refactor" → "refractor or reflector?").
- **Time grammar:** clock times, ranges, "half past ten", "quarter to midnight", durations, relative edits
  ("an hour later", "extend it by half an hour"), nights (tonight, tomorrow, weekdays, dates). Night-relative
  resolution: on an observing night 1 AM belongs to the night that started the evening before; am/pm is
  inferred from what is visible (only the daytime-capable dish ever needs to ask).
- **Self-repair and negation:** "Saturn, no wait, Jupiter" keeps the last value; "not the refractor, the other
  optical one" → reflector.

### 3.5 Data structures

| Structure | Where | Why |
|---|---|---|
| **LIFO undo stack** (`stack.py`) | every change pushes a frame (draft, state, pending question, hold) | "undo that", "go back two steps", "go back to Saturn" (named rollback); popping a frame emits compensating effects (release the hold taken since). A **barrier** after booking stops undo from silently cancelling a booking — the brain offers to cancel instead. Capped at 50 frames. |
| **Augmented AVL interval tree** (`interval_tree.py`) | busy intervals per telescope in the snapshot | O(log n + k) overlap queries and free-gap search for the planner; half-open `[start, end)` so 23:00–02:00 and 02:00–04:00 don't collide. Property-tested against brute force. |
| **Min-heap waitlist** (`waitlist.py`) | worker's promotion task | key `(grade rank, joined at, sequence)`; `pop_until_fit` pops until an entry's wanted window, shifted by its flexibility, fits the freed interval and pushes skipped entries back unchanged. |

### 3.6 Messy callers (all scripted as tests)

`backend/tests/brain/scenarios/*.yaml` holds 48 replayable calls, each asserting state, intent, effects,
reply fragments, failed rule and undo depth per turn. Highlights from the brief:

- **Horizon:** target below the horizon / setting mid-window / never visible from 30° N → real windows offered.
- **Weather:** cirrus night blocks optical, the dish is offered because it is water-vapour limited; the wet
  night blocks everything; beyond the 7-night forecast books with a "weather pending" warning; a weather
  question mid-flow leaves the draft alone.
- **Mid-flight mutation:** "switch the target to the Orion Nebula and make it the reflector instead" changes
  two slots in one breath; "yes, but make it later" changes rather than confirms.
- **Multi-slot burst:** target, telescope, night, start and end in one sentence → straight to read-back.
- Also: undo / nothing to undo / undo after commit, named rollback, barge-in, echo, low confidence,
  silence ladder (two prompts, then hang up and release holds), hold lapse during a long pause (re-checked
  before committing), slot taken, caller already booked elsewhere, maintenance, allocation exhausted, past
  and too-far dates, cancel by voice, out of scope, start over.

## 4. Data and integrity

Postgres 18 owns the truth. Integrity rules live in the database, not just the app:

- **No double booking, ever:** two GiST exclusion constraints on `bookings` — no overlapping live rows on a
  telescope, and no person in two places at once. Holds are ordinary rows (`status = 'held'`, two-minute
  expiry) under the same constraints, so a hold blocks exactly like a booking. Lapsed holds are expired by a
  security-definer function just before a new hold and by a 60 s worker sweep.
- **A booking state machine trigger** allows only held → confirmed/cancelled, confirmed → cancelled (users,
  before start), held → expired and confirmed → completed (worker). Confirming checks the hold hasn't lapsed
  and, under a per-user advisory lock, that the semester allocation isn't exceeded. Custom SQLSTATEs map to
  clean API errors (`402 allocation_exhausted`, `409 slot_conflict`, …).
- **Row-level security on every table, forced** (applies to the owner too). The API logs in as `astro_api`
  (NOINHERIT, no table privileges) and each request is one transaction that sets the verified JWT claims and
  `SET LOCAL ROLE authenticated`; both vanish at commit, so a pooled connection never carries one person
  into the next request. Admin power needs `app_metadata.role = admin` **and** MFA (`aal2`); user-editable
  `user_metadata` is never trusted. Other people's bookings are visible only through
  `astro.busy_intervals()`, which returns when, never who or what. Tests prove each role's access per table.

## 5. Security

- **Auth:** GoTrue (open sign-up or Google sign-in via OAuth 2.0 + PKCE, email confirmation, 10-character minimum, refresh-token rotation, 15-minute
  access tokens, MFA available, anonymous sign-ins off). PKCE and token-hash email links verified server-side.
  The API verifies JWT signature, audience, issuer and expiry and keeps only an allow-list of claims.
- **Redirect allow-list:** every `next=` goes through `safeNext()` (same-origin paths under `/app` only;
  rejects `//evil`, `/\evil`, schemes, encoded tricks, `..`). GoTrue's `URI_ALLOW_LIST` only accepts our
  `/auth/confirm` and `/auth/callback`. GoTrue's `/admin` is blocked at Caddy.
- **Headers:** per-request CSP nonce with `strict-dynamic` (no `unsafe-inline` scripts), `frame-ancestors
  'none'` + `X-Frame-Options: DENY`, `nosniff`, strict referrer, COOP/CORP, a tight Permissions-Policy
  (microphone only for our origin), HSTS at Caddy. API responses add `Cache-Control: no-store` and a
  `default-src 'none'` CSP.
- **API hardening:** Origin allow-list for state-changing requests and the WebSocket, 64 KB body limit,
  Redis rate limits with `Retry-After`, `extra="forbid"` request models, one error shape with a request id,
  no stack traces or SQL leaving the server.
- **Per-user state isolation in one browser:** all user data lives under a React scope keyed by user id (a
  different user remounts everything empty); the resource cache lives inside that scope, not in a module;
  every API call checks the session's user still matches the page's user; sign-in/out is broadcast to other
  tabs (BroadcastChannel) and re-checked on focus, so a tab rendered for A reloads instead of showing A's
  data to B. Verified in a browser test (`tools/e2e/iso.cjs`).
- **Secrets:** SOPS + age, Docker secrets at `/run/secrets`, refuse to start staging/production with weak or
  missing secrets, gitleaks in CI, no keys in the repo.
- **Privacy:** fake people only (`@astro.example`), audio never stored, transcripts optional and purged at
  30 days, Deepgram Model Improvement Program opt-out on.

## 6. Performance and cache stampedes

The only expensive cache is each API process's copy of the sky grids and weather (~2 MB per data version).
Rebuilds are **single-flight** (one coroutine rebuilds, concurrent turns wait and reuse it), turns that
already hold an older version **keep serving it while a rebuild runs**, and the data-version probe is cached
for 5 s so a burst of turns costs one query (a test fires 20 concurrent turns at a cold cache and asserts one
rebuild). Schedules are never cached — they are read fresh each turn and the exclusion constraints are the
final arbiter — so there is nothing to expire all at once. In the browser, components asking for the same
resource at the same time share one request. The brain itself answers a turn in well under a millisecond.

## 7. Frontend

Next.js 16 (App Router, `proxy.ts`), React 19, Tailwind 4, Motion, react-three-fiber. A GLSL galaxy shader
sits behind glass panels. The landing page's procedural **armillary sphere** travels across the page as you
scroll (right → left → top → bottom → centre); on the auth pages it floats beside the form. Primary buttons
use a **baked PNG mesh gradient with grain** (`scripts/make-mesh-textures.mjs`) — no CSS gradients or shaders,
no 3D. Field errors have a tinted background, a border and an icon. The call page is a split view: the call
on the left, the **Brain Inspector** on the right (parse, intent, state transition, every rule, offers,
effects, undo stack, latency, any past turn). Error pages exist for 400/401/402/403/404/429/500/502/503 (and
static 502/503/504 pages served by Caddy when an upstream is down). Settings cover profile, voice, display,
notifications/privacy, sessions (revoke one or all) and account deletion.

## 8. Deviations from the original plan (and why)

- **spaCy and text2num dropped** for our own lexicon matcher and number-word converter: smaller, faster,
  explicit and testable; text2num rewrote ordinals we need ("the second one").
- **`dateparser` replaced** by a custom time grammar: it invented dates from ordinary words.
- **Sky samples stored as one JSONB grid per night** instead of 250,000 `ephemeris_samples` rows: the brain
  always reads whole nights.
- **`astro.uid()` / `astro.jwt()`** instead of GoTrue's `auth.uid()`: plain GoTrue (without Supabase's
  Postgres image) does not ship `auth.jwt()`, and owning the helpers decouples policies from auth upgrades.
- **Per-user advisory lock** instead of `SELECT … FOR UPDATE` on allocations: users have no UPDATE privilege
  on allocations, which `FOR UPDATE` requires.
- **Supabase session cookie is readable by JavaScript** (the browser client needs it); CSP nonces and the
  API's Bearer-only design limit what a script could do, and access tokens live 15 minutes.
- **Saturn → Jupiter** in the "below the horizon" scenario: on 27 Oct 2026 Saturn is up at 9 PM, so the
  brief's example would not fail. Jupiter rises at 00:44 and clears 20° at 02:21.
