-- =====================================================
-- DATASET ROWS STORAGE ENGINE
-- Stores smart-cleaned row-level data for dashboard rendering
-- =====================================================

CREATE TABLE IF NOT EXISTS dataset_rows (
  id          bigserial   PRIMARY KEY,
  dataset_id  uuid        NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  row_index   integer     NOT NULL CHECK (row_index >= 0),
  row_data    jsonb       NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_dataset_rows_dataset_row UNIQUE (dataset_id, row_index)
);

CREATE INDEX IF NOT EXISTS idx_dataset_rows_dataset_row
  ON dataset_rows(dataset_id, row_index);

CREATE INDEX IF NOT EXISTS idx_dataset_rows_row_data_gin
  ON dataset_rows USING GIN (row_data jsonb_path_ops);

ALTER TABLE dataset_rows ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own dataset rows" ON dataset_rows;
CREATE POLICY "Users can view own dataset rows"
  ON dataset_rows FOR SELECT
  USING (
    EXISTS (
      SELECT 1
      FROM datasets d
      WHERE d.id = dataset_rows.dataset_id
        AND d.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can insert own dataset rows" ON dataset_rows;
CREATE POLICY "Users can insert own dataset rows"
  ON dataset_rows FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1
      FROM datasets d
      WHERE d.id = dataset_rows.dataset_id
        AND d.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can update own dataset rows" ON dataset_rows;
CREATE POLICY "Users can update own dataset rows"
  ON dataset_rows FOR UPDATE
  USING (
    EXISTS (
      SELECT 1
      FROM datasets d
      WHERE d.id = dataset_rows.dataset_id
        AND d.user_id = auth.uid()
    )
  )
  WITH CHECK (
    EXISTS (
      SELECT 1
      FROM datasets d
      WHERE d.id = dataset_rows.dataset_id
        AND d.user_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "Users can delete own dataset rows" ON dataset_rows;
CREATE POLICY "Users can delete own dataset rows"
  ON dataset_rows FOR DELETE
  USING (
    EXISTS (
      SELECT 1
      FROM datasets d
      WHERE d.id = dataset_rows.dataset_id
        AND d.user_id = auth.uid()
    )
  );
