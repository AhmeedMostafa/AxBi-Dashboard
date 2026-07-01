-- =====================================================
-- BI-DASHBOARD: ACTUAL CURRENT SCHEMA
-- Generated from live Supabase instance on 2026-02-05
-- Project: uvoxpjgrksznppopktmt
-- =====================================================

-- =====================================================
-- 1. PROFILES TABLE
-- =====================================================
CREATE TABLE profiles (
  id          uuid        PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  name        text        NOT NULL,
  company_name    text    NOT NULL,
  industrial_field text   NOT NULL,
  email       text        NOT NULL,
  created_at  timestamptz DEFAULT now()
);

-- =====================================================
-- 2. DATASETS TABLE
-- =====================================================
CREATE TABLE datasets (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at      timestamptz DEFAULT now() NOT NULL,
  user_id         uuid        NOT NULL REFERENCES profiles(id),
  file_name       text        NOT NULL,
  category_hint   text        NOT NULL,
  storage_path    text        NOT NULL,
  global_context  jsonb,
  status          text        NOT NULL
);

-- =====================================================
-- 3. COLUMNS METADATA TABLE
-- =====================================================
CREATE TABLE columns_metadata (
  id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at        timestamptz DEFAULT now() NOT NULL,
  dataset_id        uuid        NOT NULL REFERENCES datasets(id),
  original_name     text        NOT NULL,
  clean_name        text        NOT NULL,
  data_type         text,
  column_role       text,
  semantic_meaning  text,
  column_confidence double precision,
  is_primary_metric boolean
);

-- =====================================================
-- 4. TRACKING JOBS TABLE
-- =====================================================
CREATE TABLE tracking_jobs (
  id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at        timestamptz DEFAULT now() NOT NULL,
  dataset_id        uuid        NOT NULL REFERENCES datasets(id),
  status            text        NOT NULL,
  current_step      text        NOT NULL,
  progress_message  text        NOT NULL,
  error_log         text        NOT NULL
);
