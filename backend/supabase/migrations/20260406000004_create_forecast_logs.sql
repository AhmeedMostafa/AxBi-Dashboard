-- =====================================================
-- FORECAST LOGS
-- Tracks every forecast run for accuracy analysis,
-- debugging, and performance monitoring.
-- =====================================================

CREATE TABLE IF NOT EXISTS forecast_logs (
  id                bigserial    PRIMARY KEY,
  dataset_id        uuid         NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  user_id           uuid         NOT NULL REFERENCES profiles(id),
  created_at        timestamptz  NOT NULL DEFAULT now(),

  -- Request parameters
  time_column       text         NOT NULL,
  target_column     text         NOT NULL,
  feature_columns   jsonb        NOT NULL DEFAULT '[]'::jsonb,
  frequency_hint    text,
  frequency_used    text,
  horizon           integer      NOT NULL,
  missing_policy    text         NOT NULL DEFAULT 'drop',

  -- Data characteristics
  input_rows        integer      NOT NULL,
  prepared_rows     integer,
  season_length     integer,
  log_transformed   boolean      NOT NULL DEFAULT false,
  non_negative      boolean      NOT NULL DEFAULT false,

  -- Model selection
  candidate_models  jsonb        NOT NULL DEFAULT '[]'::jsonb,
  eligible_models   jsonb        NOT NULL DEFAULT '[]'::jsonb,
  skipped_models    jsonb        NOT NULL DEFAULT '[]'::jsonb,

  -- Results
  forecast_possible boolean      NOT NULL DEFAULT false,
  model_results     jsonb        NOT NULL DEFAULT '[]'::jsonb,
  best_model        text,
  best_mae          double precision,
  best_rmse         double precision,
  best_wape         double precision,
  forecast_points   integer      NOT NULL DEFAULT 0,
  duration_ms       integer,

  -- Error tracking
  error_message     text,
  readiness_reasons jsonb        NOT NULL DEFAULT '[]'::jsonb
);

-- Fast lookups by dataset and user
CREATE INDEX IF NOT EXISTS idx_forecast_logs_dataset
  ON forecast_logs(dataset_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_forecast_logs_user
  ON forecast_logs(user_id, created_at DESC);

-- Filter by best model for analysis
CREATE INDEX IF NOT EXISTS idx_forecast_logs_best_model
  ON forecast_logs(best_model);

ALTER TABLE forecast_logs ENABLE ROW LEVEL SECURITY;

-- Users can only view their own forecast logs
DROP POLICY IF EXISTS "Users can view own forecast logs" ON forecast_logs;
CREATE POLICY "Users can view own forecast logs"
  ON forecast_logs FOR SELECT
  USING (user_id = auth.uid());

-- Backend service role can insert (uses service key, bypasses RLS)
DROP POLICY IF EXISTS "Users can insert own forecast logs" ON forecast_logs;
CREATE POLICY "Users can insert own forecast logs"
  ON forecast_logs FOR INSERT
  WITH CHECK (user_id = auth.uid());

-- Allow service role full access for backend inserts
DROP POLICY IF EXISTS "Service role full access on forecast_logs" ON forecast_logs;
CREATE POLICY "Service role full access on forecast_logs"
  ON forecast_logs FOR ALL
  USING (auth.role() = 'service_role');
