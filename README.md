# AstroVoice — University Observatory Telescope Time-Slot Booker

A voice agent that books time on three telescopes at a fictional observatory (Meridian Ridge, 30° N 75° E,
2,400 m, UTC+5). You talk (or type); a **deterministic brain written in Python** — a finite-state machine, a
fixed rule pipeline, an interval tree, a LIFO undo stack and a min-heap waitlist — decides what happens.
No language model makes any decision. Deepgram is used only as ears (Flux STT) and mouth (Flux TTS).

- **Use case and how the brain decides:** [DESIGN.md](DESIGN.md)
- **How AI tools were used:** [AI_USAGE.md](AI_USAGE.md)

| | |
|---|---|
| ![Landing](docs/screenshots/landing.png) | ![Call with Brain Inspector](docs/screenshots/e2e-call-booked.png) |

## What's in the repo

```
backend/          Python 3.14 — FastAPI API, the brain, Celery worker, Alembic migrations, seed tool, tests
  src/astro_brain   the deterministic brain (pure functions, no I/O)
  src/astro_sky     Skyfield sky precomputation (JPL DE421)
  src/astro_api     REST + WebSocket call loop, Deepgram adapters, RLS database sessions
  src/astro_worker  Celery: outbox email, hold sweep, waitlist promotion, retention
  src/astro_seed    builds the demo world (fake people, real sky, seeded weather)
  migrations/       Alembic (schema, exclusion constraints, triggers, row-level security)
  sql/              first-boot role/schema SQL and per-deploy bootstrap grants
  tests/            brain scenarios (EC-01…EC-54), property tests, RLS tests, API + WebSocket tests
frontend/         Next.js 16 + React 19 + Tailwind 4 + Motion + react-three-fiber
infra/            Caddy, Postgres, Redis, cloudflared config and static error pages
compose*.yaml     base stack + development / staging / production / CI overlays
scripts/secrets.sh  SOPS + age secrets workflow
.github/workflows CI (GitHub-hosted) and deploy (self-hosted runner, guarded)
tools/e2e/        Playwright browser checks (full booking call, cross-tab user isolation)
```

## Quick start (Docker, development)

Requirements: Docker with Compose v2 (Docker Desktop, or Colima on macOS), about 4 GB free RAM.

```bash
scripts/secrets.sh local                 # random development secrets in secrets/runtime/ (git-ignored)
# optional, for voice: put a Deepgram API key in secrets/runtime/deepgram_api_key
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
docker compose -f compose.yaml -f compose.dev.yaml --profile seed run --rm seed
```

Then open:

| URL | What |
|---|---|
| http://localhost:8080 | the app (landing, sign-up, sign-in, call, bookings, settings) |
| http://localhost:8025 | Mailpit — every email (sign-up confirmations, booking confirmations) lands here |
| http://localhost:8080/api/docs | API docs (development only) |

Sign in as the seeded demo account `demo@astro.example` with the password in
`secrets/runtime/seed_demo_password`, or create an account (the confirmation email appears in Mailpit).
All seeded people are fictional and use the reserved `astro.example` domain.

Without a Deepgram key everything works in **text mode**: start a "text call" and type what you would say.

### Things to try on a call

- "Book the refractor for Saturn on Wednesday from 8 to 10 PM" → "Actually make it the reflector instead"
  → "Undo that" → "Yes" (mid-call change, undo, confirm)
- "Book the reflector for Jupiter tonight at 9 PM" (below the horizon: the brain offers the real window)
- "What's the weather like tomorrow night?" (seeded cirrus night)
- "Book the refractor for the Orion Nebula tomorrow from 11 PM to 1 AM" (Moon glare; dish offered)
- "Cancel my booking on Thursday" (the demo account has a seeded dish booking)

## Google sign-in (optional)

1. In [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → **OAuth consent screen**:
   External, app name "AstroVoice", your support email, scopes `openid`, `email`, `profile`.
2. **Credentials → Create credentials → OAuth client ID → Web application**.
   - Authorized JavaScript origins: `http://localhost:8080` (and `https://astro.ayandev.app`)
   - Authorized redirect URIs: `http://localhost:8080/auth/v1/callback` (and `https://astro.ayandev.app/auth/v1/callback`)
3. Put the client ID in `.env` (`GOOGLE_ENABLED=true`, `GOOGLE_CLIENT_ID=…`) and the client secret in
   `secrets/runtime/google_client_secret` (development) or via `scripts/secrets.sh edit <env>` (staging/production).
4. `docker compose -f compose.yaml -f compose.dev.yaml up -d gotrue` — a "Continue with Google" button appears
   on the sign-in and sign-up pages (it only shows when GoTrue reports Google as enabled).

The flow is OAuth 2.0 with PKCE through GoTrue; the code is exchanged server-side at `/auth/callback`, and the
redirect after sign-in goes through the same allow-list as every other redirect.

## Running without Docker

```bash
# backend (uv + Python 3.14)
cd backend && uv sync --extra sky
export ASTRO_DB_HOST=localhost ASTRO_DB_NAME=astrovoice ASTRO_JWT_SECRET=... ASTRO_REDIS_URL=redis://localhost:6379/0
uv run alembic upgrade head && uv run python -m astro_seed
uv run uvicorn astro_api.main:app --factory --reload
# frontend (Node 22.12+; the images use Node 26)
cd frontend && npm ci && PUBLIC_ORIGIN=http://localhost:8080 INTERNAL_ORIGIN=http://localhost:8080 npm run dev
```

You also need Postgres, GoTrue and Caddy in front (see `infra/caddy/Caddyfile`), which is why Docker is the
recommended path.

## Tests

```bash
cd backend
uv run pytest tests/brain                       # the brain: 48 scripted calls + property + determinism tests
ASTRO_TEST_PG=postgresql://postgres@localhost:5432/postgres \
ASTRO_TEST_REDIS=redis://localhost:6379/5 uv run pytest   # + migrations, RLS, API, WebSocket (177 tests)
uv run ruff check src tests migrations && uv run mypy

cd frontend && npm run lint && npm run typecheck && npm test && npm run build
BASE_URL=http://localhost:8080 node tools/e2e/e2e.cjs /tmp   # browser: sign in, call, book, inspect
```

The database tests create a throwaway database and build it exactly like a deploy does (roles, schemas,
GoTrue's `auth` tables, bootstrap grants, `alembic upgrade head`), then act as anonymous, two users, the
worker and an admin to prove what each can read and change.

## Environments

| | Images | Email | Ingress | Seed |
|---|---|---|---|---|
| development (`compose.dev.yaml`) | `dev` targets, hot reload | Mailpit | `localhost:8080` | full demo |
| staging (`compose.staging.yaml`) | `prod` targets | Resend SMTP relay on ayandev.app | Cloudflare tunnel | full demo, re-planted |
| production (`compose.prod.yaml`) | `prod` targets, read-only, resource limits | Resend SMTP relay | Cloudflare tunnel | catalogue only |
| CI (`compose.ci.yaml`) | `test` target | — | — | per test |

## Secrets

Secrets never enter git in plaintext and never sit in environment variables. `secrets/<env>.enc.yaml` is
encrypted with SOPS + age (`.sops.yaml` lists the recipients). On deploy, `scripts/secrets.sh render <env>`
decrypts them into `secrets/runtime/` (0600, git-ignored), Compose mounts them as Docker secrets at
`/run/secrets`, and the deploy job shreds them afterwards. CI runs gitleaks on every push.

```bash
scripts/secrets.sh init staging    # generate strong random values, encrypted
scripts/secrets.sh edit staging    # add deepgram_api_key, smtp_password (Resend), cloudflared_token
```

## Deployment

`.github/workflows/ci.yml` lints, type-checks and tests everything on GitHub-hosted runners for every PR.
`.github/workflows/deploy.yml` runs on a self-hosted runner (a Mac under Colima) only for pushes to `main`
in the repository named by the `DEPLOY_OWNER` variable, so forks and pull requests can never reach it. It
renders secrets, builds images, runs migrations, rolls out with health checks, smoke-tests
`/api/health/ready` and wipes the rendered secrets. The public hostname (`astro.ayandev.app`) comes in
through a Cloudflare tunnel, so the host opens no inbound ports.
