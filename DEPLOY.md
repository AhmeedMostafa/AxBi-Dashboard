# Deploying / Running AxBi with Docker

Run the entire stack — frontend, backend, two Celery workers, and Redis — with a
single command. Supabase is used as a hosted (free-tier) service; you provide the
keys via a local `.env` file.

```
┌──────────┐   /api, /ws   ┌──────────┐   Celery    ┌─────────┐
│ frontend │ ────────────▶ │ backend  │ ──────────▶ │  redis  │
│ (nginx)  │               │ (daphne) │ ◀────────── │ broker  │
└──────────┘               └────┬─────┘             └─────────┘
   :8080                        │                ┌──────────────────┐
                                │                │ worker (default) │
                                ▼                │ worker-forecasts │
                          Supabase + Gemini      └──────────────────┘
                            (cloud APIs)
```

## Prerequisites

- **Docker Desktop** (or Docker Engine + Compose v2). That's the only thing the host
  machine needs — no Python, Node, or Redis install required.
- Internet access (Supabase + Google Gemini are cloud APIs).
- A free **Supabase** project and a **Google Gemini** API key (steps below).

## 1. Create a Supabase project (free tier)

1. Sign up at <https://supabase.com> and create a new project.
2. Apply the database schema: open the project's **SQL Editor** and run each file in
   [`backend/supabase/migrations/`](backend/supabase/migrations/) **in filename order**
   (they are timestamp-prefixed). This creates the `profiles`, `datasets`,
   `tracking_jobs`, `columns_metadata`, `dataset_rows`, `forecast_logs`,
   `conversations`, and `conversation_messages` tables plus triggers/indexes.
3. Create two **Storage buckets**: `raw_data` and `cleaned_data`.
4. Collect these values from **Project Settings → API**:
   - Project URL → `SUPABASE_URL` and `VITE_SUPABASE_URL`
   - `service_role` key → `SUPABASE_SERVICE_KEY` *(secret)*
   - `anon` key → `VITE_SUPABASE_ANON_KEY` *(public-safe)*
   - JWT secret (JWT Settings) → `SUPABASE_JWT_SECRET` *(optional, speeds up auth)*

> Note: free Supabase projects **pause after ~7 days of inactivity**. If the app can't
> reach the DB, open the Supabase dashboard and un-pause/restore the project.

## 2. Get a Gemini API key

Create one at <https://aistudio.google.com/apikey> → `GEMINI_API_KEY`.

## 3. Configure secrets

Pick **one** approach:

### Option A — root `.env` (recommended)

```bash
cp .env.example .env
# edit .env and fill in the keys from steps 1 and 2
docker compose up --build
```

Compose auto-loads root `.env` for `${VAR}` substitution. Optional legacy files
`backend/.env` and `frontend/.env` are also picked up if they exist.

### Option B — inline in `docker-compose.yml`

Open [`docker-compose.yml`](docker-compose.yml) and edit the **`x-app-config`** block
at the top. Replace placeholders like `${SUPABASE_URL:-}` with your literal values:

```yaml
x-app-config: &app-config
  SUPABASE_URL: https://your-project.supabase.co
  SUPABASE_SERVICE_KEY: your-service-role-key
  GEMINI_API_KEY: your-gemini-key
  # ...
```

For the frontend, set **`x-frontend-build-config`** (`VITE_SUPABASE_URL`,
`VITE_SUPABASE_ANON_KEY`), then rebuild:

```bash
docker compose up --build
```

**Do not commit real keys** in compose if this repo is public — keep changes local
or use Option A.

`.env` / inline values are gitignored or local-only — they never belong in commits.

## 4. Build and run

```bash
docker compose up --build
```

First build takes several minutes (compiles the ML stack). When ready:

- App: <http://localhost:8080>
- Backend logs should show **daphne** listening on `:8000` and both Celery workers
  reporting **ready** (one default queue, one `forecasts` queue).

Stop with `Ctrl+C`; tear down with `docker compose down` (add `-v` to also drop the
Redis volume).

## 5. Smoke test

1. Open <http://localhost:8080>, register/login.
2. Upload a sample CSV from [`docs/`](docs/).
3. Watch the pipeline progress to 100% → the dashboard renders.
4. Open the **Report** page (PDF export) and run a **fast** forecast.

## Modes: development vs production-style

- **Default (`docker compose up`)** auto-merges `docker-compose.override.yml`, which
  enables **live reload**: `./backend` is bind-mounted, the web role runs Django's
  auto-reloading `runserver`, and the workers auto-restart on `.py` changes. Backend
  `:8000` is also published so you can run the Vite dev server natively
  (`cd frontend && npm run dev`) for live UI editing (its proxy targets `:8000`).
- **Clean production-style** (image-baked source, daphne, no mounts):
  ```bash
  docker compose -f docker-compose.yml up --build
  ```
  Frontend changes require a rebuild here: `docker compose build frontend`.

## Keeping Docker in sync with source (maintenance)

This stack tracks the source, but two files must be updated by hand when the code grows:

- **New Python dependency** (a new third-party `import`): add it to
  [`backend/requirements.docker.txt`](backend/requirements.docker.txt). This is the
  curated web-only deps list — the root `backend/requirements.txt` is a polluted global
  freeze and is **not** used by the image.
- **New environment variable** read by the code: add it to `.env.example`, the
  `x-app-config` block in `docker-compose.yml`, and your local `.env`.

## Secrets & the public repo

- Only `.env.example` (placeholders) and empty `${VAR:-}` defaults in compose are
  committed. Real keys live in `.env` (gitignored) or local inline compose edits.
- The Supabase **anon** key is public by design; the **service_role** key and the
  **Gemini** key are secrets — keep them out of git.
- Dockerfiles and compose reference env vars only; no keys are baked into images at rest
  (the Vite build inlines only the public anon key + URL into the browser bundle).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `build` fails on a missing module at runtime | add the package to `requirements.docker.txt`, rebuild |
| Uploads succeed but pipeline never progresses | check the `worker` container logs; ensure `redis` is up |
| Accurate-mode forecast never completes | check the `worker-forecasts` container logs |
| 401 / auth errors | verify `SUPABASE_URL` + keys; check the project isn't paused |
| Large upload rejected | files are capped at 50 MB (app + nginx `client_max_body_size`) |
