```
```

# AxBi — Backend Architecture & Deep-Dive

> **Living document.** Update it as features evolve. Scope: **backend only** (`api/`, `core/`,
> `preprocessing/`). Frontend is referenced only where a feature spans it.
>
> Three sections:
>
> 1. **Per-file map** — what every backend file is responsible for, in flow order.
> 2. **Feature deep-dives** — end-to-end for each main function (upload, pipeline, forecasting,
>    segmentation, report, recommendations, voice/chat) with large-vs-small handling, problems
>    faced, and optimizations.
> 3. **Concepts** — the tech we lean on (Redis, Celery, Supabase, Gemini chain, Parquet,
>    WebSocket/Channels, json_repair…).

---

## Section 0 — The Big Picture

**AxBi** accepts CSV/XLSX files, runs an 8-step AI pipeline asynchronously, and produces an
interactive dashboard, a narrative report, forecasting, segmentation, recommendations, and a
voice/chat assistant.

### Request lifecycle

```
User uploads file(s) ──► POST /api/file-upload/
    │  auth via Supabase JWT
    │  multi-file MERGE (accumulation) ──► one combined CSV
    │  store raw in Supabase `raw_data` bucket
    │  create `datasets` + `tracking_jobs` rows
    │  queue Celery task ──► return HTTP 202 immediately
    ▼
Celery worker runs the 8-step pipeline (Steps 3→4→5→6→7→8)
    Step 3 clean ─► Parquet in `cleaned_data` bucket
    Step 4 profile columns ─► columns_metadata
    Step 5 AI semantic (Gemini) ─► ai_profile
    Step 6 smart preprocessing
    Step 7 dashboard blueprint (suggested_charts)
    Step 8 AI report (insights + recommendations + sections)
    ▼
Frontend polls GET /api/check/{job_id}/  (progress 0–100%)
    on complete ─► columns metadata + suggested_charts
    ▼
Dashboard renders. Report / Forecast / Segmentation / Chat run on demand.
```

### Layered view

```
            HTTP (REST)                         WebSocket
  ┌───────────────────────────┐        ┌────────────────────────┐
  │ api/views.py (endpoints)  │        │ api/live_consumer.py    │
  │ api/chat.py (assistant)   │        │  (Gemini Live proxy)    │
  └────────────┬──────────────┘        └────────────────────────┘
               │ queue / call
  ┌────────────▼──────────────┐
  │ api/tasks.py (Celery)     │  ◄── Redis broker / result backend
  └────────────┬──────────────┘
               │ calls
  ┌────────────▼───────────────────────────────────────────────┐
  │ preprocessing/  (Step 3)                                    │
  │ api/processing/ (Steps 4–8)                                 │
  │ api/forecasting/  api/segmentation/  api/recommendations/   │
  │ api/accumulation/ (upload merge)                            │
  └────────────┬───────────────────────────────────────────────┘
               │ all DB/storage/auth through
  ┌────────────▼──────────────┐
  │ api/supabase_client.py    │  ◄── Supabase (Postgres + buckets + JWT)
  └───────────────────────────┘     + Parquet artifact for heavy analytics
```

---

## Section 1 — Per-File Map (in flow order)

### Entry / config (`core/`, routing)

- **`core/settings.py`** — Django settings. Celery config (`CELERY_TASK_ROUTES` sends
  `run_forecast_task` to the `forecasts` queue; soft/hard time limits 5/6 min), CORS
  (localhost:5173 only), upload caps (`FILE_UPLOAD_MAX_MEMORY_SIZE`/`DATA_UPLOAD_MAX_MEMORY_SIZE`
  = 50 MB), Channels/ASGI app.
- **`core/celery.py`** — Celery app instance; autodiscovers tasks.
- **`core/urls.py`** — root URL conf; includes `api/urls.py`.
- **`core/asgi.py`** — ASGI entrypoint; wires Django Channels so HTTP **and** WebSocket share one
  server (needed for the live voice proxy).
- **`core/wsgi.py`** — WSGI entrypoint (plain HTTP deployments).
- **`api/urls.py`** — all REST routes → view functions.
- **`api/routing.py`** — WebSocket routes; maps `ws/live/` → `LiveProxyConsumer`.

### HTTP layer

- **`api/views.py`** — every REST endpoint: `file_upload`, `append_to_dataset_view`,
  `check_job_status`, dataset list/delete/dashboard/rows/aggregate, `forecast_dataset_view`
  (+ fast/accurate mode + async branch), `get_forecast_status_view`, forecast history/detail/
  accuracy, `export_pdf`, segmentation run/results, KPI stats. Also production hardening for
  forecasting (per-user rate limit → 429, timeout → 504, `_persist_forecast_log`).
- **`api/supabase_client.py`** — single gateway to Supabase: `verify_supabase_token()` (JWT auth),
  dataset/job/column CRUD, bucket upload/download, and the **batched delete path**
  (`delete_dataset_rows` paged by `row_index`, `delete_dataset_full`).

### Async

- **`api/tasks.py`** — Celery tasks. `process_dataset_pipeline` runs Steps 3→8 sequentially;
  `run_forecast_task` runs accurate-mode forecasts on the `forecasts` queue. Holds
  `DATASET_ROWS_DISPLAY_CAP = 5000` and `_persist_dataset_rows` (display sample only).

### Preprocessing — Step 3 (`preprocessing/`)

- **`preprocessing/pipeline.py`** — Step 3 orchestrator: download raw → clean → write Parquet to
  `cleaned_data`. Handles CSV encoding fallbacks.
- **`preprocessing/cleaning.py`** — the actual cleaning: snake_case columns, null normalization,
  type coercion (`preprocess_dataframe`).
- **`preprocessing/profiling.py`** — lightweight column profiling helpers used during cleaning.
- **`preprocessing/ai_logic.py`** — AI-assisted cleaning heuristics.

### Pipeline — Steps 4–8 (`api/processing/`)

- **`step4_column_detection.py`** — profile each column: detect type, compute stats
  (min/max/mean/top-5/null-ratio) → `columns_metadata`.
- **`step5_ai_semantic.py`** — send column names + stats to Gemini → `semantic_meaning`,
  `role`, `column_confidence`. **Hardened this session**: forces JSON output, `json_repair`
  salvage, and `_fallback_profiles()` rule-based degradation so a bad AI response never kills
  the pipeline.
- **`step6_smart_preprocessing.py`** — role-based transforms using Step-5 labels (normalize
  metrics, group rare categoricals, fix datetime formats).
- **`step7_dashboard_blueprint.py`** — generate `suggested_charts` + `suggested_title`. Uses
  `response_mime_type=application/json` + `max_output_tokens=8192` to avoid JSON truncation; has
  a rule-based fallback blueprint when AI JSON is unrecoverable.
- **`step8_ai_report.py`** — narrative report: 3 `sections` + structured `insights` +
  `recommendations` (added this session). Has `_build_fallback_report` for AI failure.

### Upload merge

- **`api/accumulation/service.py`** — pure-pandas helpers to combine multiple same-schema files:
  `normalize_columns`, `schemas_match`, `detect_key`, `combine` (upsert/last-wins or dedup),
  `accumulate_files`. Used by both `file_upload` and `append_to_dataset_view`.

### Forecasting

- **`api/forecasting/service.py`** — `run_forecast_service()`: prepares the series, runs the model
  tournament, backtests, selects best, builds predictions + confidence intervals. Holds the model
  registry (`SUPPORTED_MODELS`), mode gating, point cap, adaptive folds.
- **`api/forecasting/feature_recommender.py`** — suggests which feature columns help a forecast.

### Segmentation

- **`api/segmentation/service.py`** — `run_segmentation_service()`: auto-detects strategy, samples
  large data (50k cap + 120s timeout), calls Gemini for cluster naming + insights (with salvage).
- **`api/segmentation/strategies.py`** — the three algorithms: `rfm_segmentation`, `abc_analysis`,
  `kmeans_segmentation`.

### Recommendation engine

- **`api/recommendations/service.py`** — entry point; orchestrates detectors → context → Gemini.
- **`api/recommendations/signal_detectors.py`** — rule-based detectors that surface signals
  (forecast decline/growth, low-confidence forecast, severe overfit, shrinking/growing segments,
  concentration risk, high-null columns, stale data, forecast error, report insights…).
- **`api/recommendations/context_builder.py`** — assembles dataset context for the prompt.
- **`api/recommendations/gemini_client.py`** — Gemini call wrapper for this module.
- **`api/recommendations/prompts.py`** — prompt templates.
- **`api/recommendations/schemas.py`** — output validation schemas.

### Voice / chat

- **`api/chat.py`** — AI assistant powered by **Gemini function-calling**. Declares **18** tools
  (navigate, list projects, dataset summary, query data, generate chart, 3D visual, detect
  anomalies, compare datasets, quality report, get recommendations, export PDF, forecast
  history/accuracy, run forecast, run segmentation, delete dataset, onboarding). Streams responses
  via SSE.
- **`api/live_consumer.py`** — **Gemini Live API WebSocket proxy** (`LiveProxyConsumer`). Bridges
  browser ↔ Django (Channels) ↔ Gemini Live so the API key never reaches the client; injects a
  dataset-aware Egyptian-Arabic system instruction; relays audio + transcripts both ways; handles
  barge-in; enforces a session cap.
- **`api/conversations.py`** — persist chat sessions + messages in Supabase; public share via
  `share_token`.
- **`api/voice_logger.py`** — **filesystem** audit log for TTS/translation/overview requests
  (JSONL index + mp3 audio under `BASE_DIR/logs/voice/`), not a DB table.
- **`api/pdf_charts.py`** — renders chart images for the WeasyPrint PDF report.

---

## Section 2 — Feature Deep-Dives

### 1. Upload + Accumulation

**Flow:** `POST /api/file-upload/` → auth → read all files in `request.FILES.getlist('file')` →
`accumulate_files()` merges them → combined CSV uploaded to `raw_data` → `datasets`+`tracking_jobs`
rows → `process_dataset_pipeline.delay()` → HTTP 202.

**Merge logic (`api/accumulation/service.py`):**

- `normalize_columns` snake_cases all headers so schema comparison is apples-to-apples.
- `schemas_match` compares column **name sets** (order-independent). A mismatched file is rejected
  **individually** with a reason, not the whole batch.
- `detect_key` auto-picks a single all-unique ID-like column (`id`/`code`/`uuid`/`key`/`number`/`no`).
- `combine`: `pd.concat`, then **upsert/last-wins** on the key (re-uploading rows with the same ID
  updates them) — or, if no key, drop exact-duplicate rows.

**Append (`append_to_dataset_view`, `POST /api/datasets/<id>/append/`):** loads the existing cleaned
Parquet as `base_df`, merges new files on top, resets `processed_path=None`, re-queues the full
pipeline so all downstream steps refresh.

**Large vs small:**

- **50 MB limit** enforced at 3 layers: frontend (`Hero.tsx`), Django settings
  (`FILE_UPLOAD_MAX_MEMORY_SIZE`/`DATA_UPLOAD_MAX_MEMORY_SIZE`), and the Supabase bucket.
- **`dataset_rows` capped at 5000** (`DATASET_ROWS_DISPLAY_CAP`). That table is only a **display
  sample** (table view + PDF read ≤2000). Heavy analytics read the **Parquet** artifact instead.
  The true row count lives in `datasets.file_info.row_count`.

**Problem faced + fix:** persisting all rows (200k–400k+) caused Postgres **statement timeouts
(`57014`)** and table bloat on insert *and* delete. Fix: cap the display sample at 5000, and delete
rows in **paged batches** (`delete_dataset_rows`, 5000 at a time, by `row_index`) backed by the
index `idx_dataset_rows_dataset_row`. A 400k dataset now deletes cleanly.

### 2. The 8-Step Pipeline

Runs inside `process_dataset_pipeline` (Celery). Each step writes its result under its own key in
`datasets.global_context` (never overwrite the whole blob — always merge).

| Step | File                             | Job                                                                             |
| ---- | -------------------------------- | ------------------------------------------------------------------------------- |
| 3    | `preprocessing/pipeline.py`    | Clean (snake_case, null-normalize, coerce types) → Parquet in`cleaned_data`. |
| 4    | `step4_column_detection.py`    | Profile each column (type + stats) →`columns_metadata`.                      |
| 5    | `step5_ai_semantic.py`         | Gemini: semantic meaning + role + confidence →`ai_profile`.                  |
| 6    | `step6_smart_preprocessing.py` | Role-based transforms using Step-5 labels.                                      |
| 7    | `step7_dashboard_blueprint.py` | `suggested_charts` + `suggested_title`.                                     |
| 8    | `step8_ai_report.py`           | Narrative report:`sections` + structured `insights` + `recommendations`.  |

**Gemini model chain** (Steps 5/7/8 + segmentation): `gemini-2.5-flash` → `gemini-2.5-pro` →
fallback. If one is rate-limited/unavailable, the next is tried automatically.

**Problem faced + fix (this session):** Step 5 crashed the whole pipeline on **malformed Gemini
JSON** (unescaped quotes inside descriptions). The strong salvage layer (`json_repair`) was never
installed, so it was dead. Fixes:

1. Installed `json_repair` (added to `requirements.txt`); it recovers the unescaped-quote case.
2. Forced `response_mime_type=application/json` on the **first** call (not just the retry) and
   raised `max_output_tokens` 4096 → 8192.
3. Added `_fallback_profiles()` — if both AI attempts still fail, build rule-based roles from the
   Step-4 stats so the pipeline **finishes** (dashboard renders; columns just lack AI prose).

**Large vs small:** Step 3 cleans a 400k-row file in ~7–9 s. Steps 4–8 cost depends on column count
(batched 30 at a time in Step 5), not row count.

### 3. Forecasting

**Entry:** `POST /api/datasets/<id>/forecast/` with `mode` ∈ `{"fast","accurate"}` (default fast).

**Core idea — cost scales with POINTS, not rows.** `_prepare_series_frame` collapses ALL dimensions
into one global series via `set_index(time).resample(freq).agg(sum)`. Points = time-span ÷
frequency, so a 400k-row multi-entity upload collapses to ~1140 weekly points and forecasts as fast
as a small file.

**Point cap (frequency-agnostic):** if the resampled series exceeds `max_points` (1200), the
frequency is coarsened one step (D→W→MS→QS) and re-aggregated until under the cap.
`MAX_DAILY_POINTS_BEFORE_WEEKLY = 1500` is a fast-path shortcut to weekly.

**The two modes:**

- **`fast`** — drops `SLOW_MODELS = {sarimax, prophet, catboost}`; runs cheap statistical models +
  the single fast tree (LightGBM). Runs **synchronously** in the request.
- **`accurate`** — runs all models **except** `ACCURATE_SKIP_MODELS = {sarimax}` (its auto_arima
  grid is pathologically slow). Runs **async** on the Celery `forecasts` queue; the view returns
  `202 {job_id}`, the frontend polls `GET /api/forecasts/status/<job_id>/`.

**Models (`SUPPORTED_MODELS`) and use cases:**

| Model                       | Type                       | Best for                                                                     |
| --------------------------- | -------------------------- | ---------------------------------------------------------------------------- |
| `naive`                   | baseline                   | last-value carry-forward; sanity floor + ensemble.                           |
| `seasonal_naive`          | baseline                   | strong fixed seasonality (same period last cycle).                           |
| `ets` / `exp_smoothing` | statistical (Holt-Winters) | trend + seasonality, smooth series. Always available.                        |
| `sarimax`                 | statistical                | autocorrelated series — but**skipped** (too slow, usually times out). |
| `prophet`                 | additive                   | holidays / multiple seasonalities, longer histories. (Slow.)                 |
| `catboost`                | tree                       | same as LightGBM, higher accuracy but slow. Accurate-mode only.              |

> Note: the live registry is these 8 models. Older docs mentioned Theta/Croston — they are **not**
> in `SUPPORTED_MODELS`.

**Selection — two-stage tournament + adaptive folds:**

- If candidates > 4: screen ALL on a **single fold**, rank by WAPE, keep the **top 3 non-baseline**
  + always-kept baselines (`naive`, `seasonal_naive`). Full CV + 70/30 holdout run only on
    survivors. Eliminated models are reported with `status:"eliminated"` (informational).
- **Adaptive folds:** CV ceiling scales with series length — `<150→5, 150–600→3, >600→2, >1200→1`.
- Best selected by avg-rank across MAE/RMSE/WAPE/MASE. If runner-up is within 25% → 50/50 ensemble.

**Fit diagnosis (`test_mae / cv_mae`):** `<0.70` check_leakage · `0.70–1.20` healthy ·
`1.20–1.50` mild_overfit · `1.50–2.00` overfit · `>2.00` severe_overfit.

**Worst / best cases:**

- *Worst (before fixes):* accurate run took **770 s** — SARIMAX alone wasted ~6 min timing out, and
  a flat "all models × 3 folds + holdout" was the rest.
- *Best (after fixes):* SARIMAX dropped, tournament + adaptive folds → **400k accurate ≈ 13–19 s**,
  **fast ≈ 5 s**, same series grain.

**Production hardening:** 30 s per-user rate limit (429), 300 s hard timeout on the sync path (504),
every run persisted to `forecast_logs`. On Windows `--pool=solo`, Celery `time_limit` is advisory —
the frontend poll ceiling (~30 min) is the effective guard.

### 4. Segmentation

**Entry:** `POST /api/datasets/<id>/segmentation/`. `run_segmentation_service()` auto-detects the
strategy from column metadata:

- **RFM** (`rfm_segmentation`) — needs entity ID + date + monetary (e.g. customer transactions).
- **ABC / Pareto** (`abc_analysis`) — needs a dimension + a value column (e.g. product revenue).
- **K-Means** (`kmeans_segmentation`) — fallback for any dataset with ≥2 numeric columns; PCA for
  the scatter plot.

**Large vs small:** the K-Means/PCA path **samples to 50,000 rows** (fixed seed) and runs inside a
**120 s timeout** wrapper (clean `ValueError` on timeout). RFM/ABC stay on the full df because they
aggregate to one row per entity, so row count doesn't inflate cost.

**AI naming:** Gemini names clusters + writes business insights; falls back to rule-based summaries
if Gemini fails. Output stored in `global_context.segmentation`.

**Status:** backend fully implemented; `runSegmentation`/`getSegmentationResults` exist in the
frontend `api.js` but no dedicated UI route yet.

### 5. Report (Step 8)

**`global_context.step8` = `{ sections, report_html, insights, recommendations }`:**

- `sections` — 3 narrative blocks (Exec Summary / Key Insights / Recommendations). Feeds
  `report_html` (PDF), the audio overview, and back-compat rendering.
- `insights` — array of `{title, detail, metric, sentiment ∈ positive|risk|neutral}`.
- `recommendations` — array of `{title, detail, priority ∈ high|medium|low}`.

The frontend renders structured **insight cards** + a **priority action board** when the arrays are
present; **old datasets** (only `sections`) fall back to the prose render. The fallback report path
(`_build_fallback_report`) also emits structured arrays so the cards never go blank on AI failure.

**PDF export:** `POST /api/datasets/<id>/export-pdf/` via **WeasyPrint** (needs Cairo/Pango/GDK-
PixBuf). Chart images rendered by `api/pdf_charts.py`. *Follow-up:* PDF template still uses
`sections` — not yet enriched with the new structured cards.

### 6. Recommendation Engine

**Flow:** `signal_detectors.run_all_detectors(ctx)` → list of rule-based signals →
`context_builder.build_dataset_context` assembles the prompt context → `gemini_client.call_gemini`
with `prompts.build_prompt` → output validated against `schemas.py`.

**Detectors** surface things like: forecast decline/growth, low-confidence forecast, severe
overfit, shrinking top segment, growing at-risk segment, concentration risk, high-null columns,
stale data, high/moderate forecast error, report insights, and "no forecast yet". Detectors run
fast (rule-based, ~0.3 s); the Gemini call turns signals into prioritized business recommendations.

### 7. Voice / Chat

**Chatbot (`api/chat.py`)** — Gemini **function-calling**. The model decides which of ~17 declared
tools to call; the backend executes the tool (query data, generate a chart, run a forecast, run
segmentation, export PDF, navigate, etc.) and feeds the result back. Responses stream via SSE
(`type: function_call` events + text). This is how the assistant can *act* on the app, not just
chat.

**Live voice (`api/live_consumer.py`)** — a secure WebSocket **proxy** to the Gemini Live API:

```
Browser  <-- ws/live/ -->  Django (Channels)  <-- wss -->  Gemini Live API
```

The browser cannot authenticate to Gemini Live directly (and embedding the key would leak it), so
the consumer authenticates the browser via Supabase JWT in the query string, opens the upstream
Live session, injects a dataset-aware **Egyptian-Arabic** system instruction, relays audio +
transcripts both ways, forwards barge-in/interruption, and enforces a hard session cap to protect
quota. **The API key never leaves the server.**

**Persistence + sharing (`api/conversations.py`)** — chat sessions and messages are stored in
Supabase (`conversations`, `conversation_messages`); a `share_token` enables a public read-only
share page. Not linked to `datasets`.

**Audit log (`api/voice_logger.py`)** — every TTS/translation/overview request is logged to the
**filesystem** (JSONL index + mp3 audio under `BASE_DIR/logs/voice/`), trimmed per user
(`VOICE_LOG_MAX_ENTRIES_PER_USER`, default 500). Toggle with `VOICE_LOG_ENABLED` /
`VOICE_LOG_KEEP_AUDIO`. Not a DB table.

---

## Section 3 — Concepts

- **Redis** — in-memory store used as Celery's **message broker** (task queue) and **result
  backend** (where `AsyncResult` reads task status/return values). Must be running for any async
  work.
- **Celery** — distributed task queue. We run two workers: the default queue for the upload
  pipeline, and a dedicated `-Q forecasts` worker for accurate forecasts (so heavy forecasts never
  block uploads). **Windows `--pool=solo` caveat:** the solo pool can't kill a child process, so
  `time_limit`/`soft_time_limit` are **advisory** there — the frontend poll ceiling is the real
  guard.
- **Supabase** — hosted Postgres + storage + auth.
  - **PostgREST** — the REST interface we hit through `api/supabase_client.py`.
  - **JWT auth** — every request carries `Authorization: Bearer <token>`; the backend validates it
    with the service key before doing anything.
  - **Buckets** — `raw_data` (uploads) and `cleaned_data` (Parquet).
  - **Cascade deletes** — child tables FK to `datasets(id)` `ON DELETE CASCADE`. But we never rely
    on cascade for `dataset_rows` (large) — we batch-delete it first to avoid the statement timeout.
  - **Statement timeout `57014`** — Postgres aborts a query that runs too long; the bloated
    `dataset_rows` table triggered it on both insert and delete until we capped + indexed + batched.
- **Parquet** — columnar file format. The cleaned dataset is stored as one Parquet artifact; heavy
  analytics (forecasting, segmentation) read it directly, so we can keep the DB `dataset_rows` table
  to a tiny 5000-row display sample.
- **Gemini model chain** — ordered list of models tried in turn on rate-limit/outage. Combined with
  `response_mime_type=application/json`, raised token caps, and **`json_repair`** (a library that
  repairs malformed JSON — trailing commas, unescaped quotes, truncation) plus rule-based fallbacks,
  the AI steps degrade gracefully instead of failing.
- **Django Channels / WebSocket** — Channels adds async WebSocket support to Django (via ASGI). It
  powers the live voice proxy; HTTP and WS share one server through `core/asgi.py`.
- **Pandas resample + point cap** — `resample(freq).agg(sum)` collapses any time series to a fixed
  grain; coarsening the frequency keeps the modeled series under the point cap regardless of how
  many raw rows came in.

---

*End of document. Append new features and concepts here as the project grows.*