-- =====================================================
-- CASCADE DELETE FOR DATASET CHILDREN
-- columns_metadata and tracking_jobs referenced datasets(id) WITHOUT
-- ON DELETE CASCADE, so deleting a dataset left orphan rows / FK errors.
-- Re-create those FKs with ON DELETE CASCADE so removing a datasets row
-- removes all of its rows in every child table automatically.
-- (dataset_rows, forecast_logs, forecast_results already cascade.)
-- =====================================================

ALTER TABLE columns_metadata
  DROP CONSTRAINT IF EXISTS columns_metadata_dataset_id_fkey;
ALTER TABLE columns_metadata
  ADD CONSTRAINT columns_metadata_dataset_id_fkey
  FOREIGN KEY (dataset_id) REFERENCES datasets(id) ON DELETE CASCADE;

ALTER TABLE tracking_jobs
  DROP CONSTRAINT IF EXISTS tracking_jobs_dataset_id_fkey;
ALTER TABLE tracking_jobs
  ADD CONSTRAINT tracking_jobs_dataset_id_fkey
  FOREIGN KEY (dataset_id) REFERENCES datasets(id) ON DELETE CASCADE;

-- Index the FK columns so the cascade check / delete stays fast on large data.
CREATE INDEX IF NOT EXISTS idx_columns_metadata_dataset
  ON columns_metadata (dataset_id);
CREATE INDEX IF NOT EXISTS idx_tracking_jobs_dataset
  ON tracking_jobs (dataset_id);
