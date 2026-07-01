-- Add a user-facing project name to datasets.
-- The onboarding wizard collects a project name; this column persists it so the
-- Projects grid and the top-bar project selector can display it instead of
-- falling back to the file name or department/category.
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS project_name text;
