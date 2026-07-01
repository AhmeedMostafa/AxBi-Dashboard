-- =====================================================
-- FORECAST RESULTS STORAGE
-- Persists forecast outputs so users can revisit results
-- =====================================================

CREATE TABLE IF NOT EXISTS forecast_results (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id  uuid        NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  user_id     uuid        NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  config      jsonb       NOT NULL DEFAULT '{}'::jsonb,
  best_model  text,
  metrics     jsonb       NOT NULL DEFAULT '{}'::jsonb,
  result      jsonb       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_forecast_results_dataset
  ON forecast_results(dataset_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_forecast_results_user
  ON forecast_results(user_id, created_at DESC);

ALTER TABLE forecast_results ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own forecast results" ON forecast_results;
CREATE POLICY "Users can view own forecast results"
  ON forecast_results FOR SELECT
  USING (user_id = auth.uid());

DROP POLICY IF EXISTS "Users can insert own forecast results" ON forecast_results;
CREATE POLICY "Users can insert own forecast results"
  ON forecast_results FOR INSERT
  WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS "Users can delete own forecast results" ON forecast_results;
CREATE POLICY "Users can delete own forecast results"
  ON forecast_results FOR DELETE
  USING (user_id = auth.uid());
