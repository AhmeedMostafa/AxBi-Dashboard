-- =====================================================
-- BI-DASHBOARD INITIAL SCHEMA
-- Multi-department support: Sales, HR, Operations, Marketing
-- =====================================================

-- =====================================================
-- 1. PROFILES TABLE
-- =====================================================
CREATE TABLE profiles (
  id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email text UNIQUE NOT NULL,
  full_name text NOT NULL DEFAULT '',
  avatar_url text DEFAULT '',
  department text DEFAULT 'General',
  role text DEFAULT 'Analyst',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX idx_profiles_email ON profiles(email);

COMMENT ON TABLE profiles IS 'User profiles extending Supabase auth.users';

-- =====================================================
-- 2. DATA SOURCES TABLE
-- =====================================================
CREATE TABLE data_sources (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  file_name text NOT NULL,
  file_type text NOT NULL DEFAULT 'CSV',
  file_size_bytes bigint DEFAULT 0,
  row_count integer DEFAULT 0,
  data_type text CHECK (data_type IN ('sales', 'hr', 'operations', 'marketing')),
  status text NOT NULL DEFAULT 'Processing' 
    CHECK (status IN ('Processing', 'Complete', 'Error', 'Cancelled')),
  error_message text DEFAULT NULL,
  -- Cross-functional analytics
  is_multi_type boolean DEFAULT false,
  detected_types text[] DEFAULT '{}',
  detected_dimensions jsonb DEFAULT '{}'::jsonb,
  cross_functional_enabled boolean DEFAULT true,
  classification_summary jsonb DEFAULT '{}'::jsonb,
  -- Timestamps
  upload_date timestamptz DEFAULT now(),
  processed_at timestamptz DEFAULT NULL,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE INDEX idx_data_sources_user_id ON data_sources(user_id);
CREATE INDEX idx_data_sources_status ON data_sources(status);
CREATE INDEX idx_data_sources_type ON data_sources(data_type);
CREATE INDEX idx_data_sources_upload_date ON data_sources(upload_date DESC);

COMMENT ON TABLE data_sources IS 'Tracks uploaded data files and processing status';
COMMENT ON COLUMN data_sources.detected_dimensions IS 'Columns that enable cross-functional insights';

-- =====================================================
-- 3. BUSINESS DATA TABLE (Hybrid Schema)
-- =====================================================
CREATE TABLE business_data (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  data_source_id uuid NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
  
  -- DATA TYPE IDENTIFIER
  data_type text NOT NULL CHECK (data_type IN ('sales', 'hr', 'operations', 'marketing')),
  
  -- COMMON FIELDS (All types)
  record_date date NOT NULL,
  amount numeric(15, 2) DEFAULT 0,
  quantity integer DEFAULT 0,
  category text DEFAULT 'Uncategorized',
  
  -- SALES-SPECIFIC
  customer_id text DEFAULT '',
  product_name text DEFAULT '',
  
  -- HR-SPECIFIC
  employee_id text DEFAULT '',
  position text DEFAULT '',
  department text DEFAULT '',
  
  -- OPERATIONS-SPECIFIC
  operation_type text DEFAULT '',
  efficiency_rate numeric(5, 2) DEFAULT NULL,
  downtime_hours numeric(8, 2) DEFAULT 0,
  
  -- MARKETING-SPECIFIC
  campaign_id text DEFAULT '',
  campaign_name text DEFAULT '',
  channel text DEFAULT '',
  impressions integer DEFAULT 0,
  clicks integer DEFAULT 0,
  conversions integer DEFAULT 0,
  
  -- AI-ENHANCED FIELDS
  standardized_name text DEFAULT '',
  anomaly_flag boolean DEFAULT false,
  data_quality_score numeric(3, 2) DEFAULT 1.00 
    CHECK (data_quality_score >= 0 AND data_quality_score <= 1),
  
  -- CLASSIFICATION TRACKING
  classification_confidence numeric(3, 2) DEFAULT 1.00
    CHECK (classification_confidence >= 0 AND classification_confidence <= 1),
  classification_reasoning text DEFAULT '',
  was_auto_classified boolean DEFAULT false,
  
  -- FLEXIBLE JSONB STORAGE
  custom_attributes jsonb DEFAULT '{}'::jsonb,
  original_row_data jsonb DEFAULT '{}'::jsonb,
  mapping_metadata jsonb DEFAULT '{}'::jsonb,
  
  -- TIMESTAMPS
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Indexes
CREATE INDEX idx_business_data_source_id ON business_data(data_source_id);
CREATE INDEX idx_business_data_type ON business_data(data_type);
CREATE INDEX idx_business_record_date ON business_data(record_date);
CREATE INDEX idx_business_category ON business_data(category);
CREATE INDEX idx_business_type_date ON business_data(data_type, record_date);
CREATE INDEX idx_business_source_type ON business_data(data_source_id, data_type);

-- Type-specific partial indexes
CREATE INDEX idx_business_sales_customer ON business_data(customer_id) 
  WHERE data_type = 'sales' AND customer_id != '';
CREATE INDEX idx_business_hr_employee ON business_data(employee_id) 
  WHERE data_type = 'hr' AND employee_id != '';
CREATE INDEX idx_business_hr_department ON business_data(department) 
  WHERE data_type = 'hr' AND department != '';
CREATE INDEX idx_business_ops_type ON business_data(operation_type) 
  WHERE data_type = 'operations' AND operation_type != '';
CREATE INDEX idx_business_marketing_campaign ON business_data(campaign_id) 
  WHERE data_type = 'marketing' AND campaign_id != '';
CREATE INDEX idx_business_marketing_channel ON business_data(channel) 
  WHERE data_type = 'marketing' AND channel != '';

-- Anomaly and quality indexes
CREATE INDEX idx_business_anomaly ON business_data(anomaly_flag) WHERE anomaly_flag = true;
CREATE INDEX idx_business_low_confidence ON business_data(classification_confidence) 
  WHERE classification_confidence < 0.7;

-- JSONB index
CREATE INDEX idx_business_custom_attrs ON business_data USING GIN (custom_attributes);

COMMENT ON TABLE business_data IS 'Unified table for all business data types (sales, hr, operations, marketing)';

-- =====================================================
-- 4. COLUMN MAPPINGS TABLE
-- =====================================================
CREATE TABLE column_mappings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  data_source_id uuid NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
  data_type text NOT NULL DEFAULT 'sales'
    CHECK (data_type IN ('sales', 'hr', 'operations', 'marketing')),
  user_column_name text NOT NULL,
  mapped_to_field text NOT NULL,
  confidence_score numeric(3, 2) DEFAULT 0.00 
    CHECK (confidence_score >= 0 AND confidence_score <= 1),
  ai_suggested boolean DEFAULT false,
  user_confirmed boolean DEFAULT false,
  created_at timestamptz DEFAULT now(),
  
  UNIQUE(data_source_id, user_column_name)
);

CREATE INDEX idx_column_mappings_data_source ON column_mappings(data_source_id);

COMMENT ON TABLE column_mappings IS 'Stores CSV column to standard field mappings';

-- =====================================================
-- 5. KPI TARGETS TABLE
-- =====================================================
CREATE TABLE kpi_targets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  data_type text NOT NULL DEFAULT 'sales'
    CHECK (data_type IN ('sales', 'hr', 'operations', 'marketing')),
  kpi_name text NOT NULL,
  department text NOT NULL DEFAULT 'General',
  target_value numeric(15, 2) NOT NULL DEFAULT 0,
  current_value numeric(15, 2) NOT NULL DEFAULT 0,
  unit text DEFAULT '',
  period text NOT NULL DEFAULT 'monthly' 
    CHECK (period IN ('daily', 'weekly', 'monthly', 'quarterly', 'yearly')),
  start_date date DEFAULT CURRENT_DATE,
  end_date date DEFAULT NULL,
  is_active boolean DEFAULT true,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  
  UNIQUE(user_id, kpi_name, department, period)
);

CREATE INDEX idx_kpi_targets_user_id ON kpi_targets(user_id);
CREATE INDEX idx_kpi_targets_department ON kpi_targets(department);
CREATE INDEX idx_kpi_targets_active ON kpi_targets(is_active) WHERE is_active = true;

COMMENT ON TABLE kpi_targets IS 'User KPI goals and progress tracking';

-- =====================================================
-- 6. DERIVED METRICS TABLE (Cross-Functional Analytics)
-- =====================================================
CREATE TABLE derived_metrics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  data_source_id uuid NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
  
  -- Metric type
  source_data_type text NOT NULL,
  derived_for text NOT NULL,
  
  -- The metric
  metric_name text NOT NULL,
  metric_category text NOT NULL,
  
  -- Dimension
  dimension_column text NOT NULL,
  dimension_value text NOT NULL,
  
  -- Values
  metric_value numeric(15, 4) NOT NULL,
  metric_unit text DEFAULT '',
  
  -- Context
  period_start date,
  period_end date,
  sample_size integer DEFAULT 0,
  
  -- Ranking
  rank integer,
  percentile numeric(5, 2),
  
  -- Metadata
  calculated_at timestamptz DEFAULT now(),
  calculation_method text DEFAULT 'aggregation',
  
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_derived_source ON derived_metrics(data_source_id);
CREATE INDEX idx_derived_types ON derived_metrics(source_data_type, derived_for);
CREATE INDEX idx_derived_dimension ON derived_metrics(dimension_column, dimension_value);
CREATE INDEX idx_derived_metric ON derived_metrics(metric_name);

COMMENT ON TABLE derived_metrics IS 'Pre-calculated cross-functional metrics derived from primary data';

-- =====================================================
-- 7. STANDARDIZED FIELDS REFERENCE TABLE
-- =====================================================
CREATE TABLE standardized_fields (
  id text PRIMARY KEY,
  data_type text NOT NULL,
  label text NOT NULL,
  description text,
  field_type text NOT NULL,
  examples text[] DEFAULT '{}',
  created_at timestamptz DEFAULT now()
);

-- Insert reference data
INSERT INTO standardized_fields (id, data_type, label, description, field_type, examples) VALUES
-- Sales fields
('record_date', 'sales', 'Transaction Date', 'Date of the sale', 'core', ARRAY['date', 'order_date', 'sale_date', 'transaction_date']),
('amount', 'sales', 'Sales Amount', 'Total revenue from sale', 'core', ARRAY['amount', 'total', 'revenue', 'price', 'sale_amount']),
('quantity', 'sales', 'Quantity', 'Number of units sold', 'core', ARRAY['qty', 'units', 'count', 'quantity_sold']),
('customer_id', 'sales', 'Customer ID', 'Customer identifier', 'core', ARRAY['customer', 'cust_id', 'client_id', 'buyer_id']),
('category', 'sales', 'Product Category', 'Product category', 'core', ARRAY['category', 'type', 'product_type', 'dept']),
('product_name', 'sales', 'Product Name', 'Name of product', 'core', ARRAY['product', 'item', 'sku', 'product_name']),
-- HR fields
('record_date', 'hr', 'Effective Date', 'Date of HR record', 'core', ARRAY['date', 'effective_date', 'hire_date']),
('employee_id', 'hr', 'Employee ID', 'Employee identifier', 'core', ARRAY['employee_id', 'emp_id', 'staff_id', 'worker_id']),
('amount', 'hr', 'Salary', 'Compensation amount', 'core', ARRAY['salary', 'wage', 'pay', 'compensation']),
('department', 'hr', 'Department', 'Department name', 'core', ARRAY['department', 'dept', 'division', 'team']),
('position', 'hr', 'Position', 'Job title', 'core', ARRAY['position', 'title', 'role', 'job_title']),
-- Operations fields
('record_date', 'operations', 'Operation Date', 'Date of operation', 'core', ARRAY['date', 'operation_date', 'production_date']),
('operation_type', 'operations', 'Operation Type', 'Type of operation', 'core', ARRAY['operation', 'type', 'process', 'activity']),
('amount', 'operations', 'Cost', 'Operation cost', 'core', ARRAY['cost', 'expense', 'amount', 'total_cost']),
('quantity', 'operations', 'Output', 'Units produced', 'core', ARRAY['output', 'units', 'quantity', 'production']),
('efficiency_rate', 'operations', 'Efficiency', 'Efficiency percentage', 'core', ARRAY['efficiency', 'rate', 'performance', 'yield']),
-- Marketing fields
('record_date', 'marketing', 'Campaign Date', 'Date of activity', 'core', ARRAY['date', 'campaign_date', 'run_date']),
('campaign_id', 'marketing', 'Campaign ID', 'Campaign identifier', 'core', ARRAY['campaign_id', 'campaign', 'id']),
('channel', 'marketing', 'Channel', 'Marketing channel', 'core', ARRAY['channel', 'source', 'medium', 'platform']),
('amount', 'marketing', 'Spend', 'Marketing spend', 'core', ARRAY['spend', 'cost', 'budget', 'investment']),
('impressions', 'marketing', 'Impressions', 'Number of impressions', 'core', ARRAY['impressions', 'views', 'reach']),
('clicks', 'marketing', 'Clicks', 'Number of clicks', 'core', ARRAY['clicks', 'click_count', 'visits']),
('conversions', 'marketing', 'Conversions', 'Number of conversions', 'core', ARRAY['conversions', 'conv', 'leads', 'signups']);

-- =====================================================
-- 8. TRIGGERS
-- =====================================================

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_profiles_updated_at
  BEFORE UPDATE ON profiles
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_data_sources_updated_at
  BEFORE UPDATE ON data_sources
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_business_data_updated_at
  BEFORE UPDATE ON business_data
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_kpi_targets_updated_at
  BEFORE UPDATE ON kpi_targets
  FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Auto-create profile on signup
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO profiles (id, email, full_name)
  VALUES (
    NEW.id,
    NEW.email,
    COALESCE(NEW.raw_user_meta_data->>'full_name', '')
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION handle_new_user();

-- =====================================================
-- 9. ROW LEVEL SECURITY
-- =====================================================

-- Enable RLS on all tables
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE column_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE kpi_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE derived_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE standardized_fields ENABLE ROW LEVEL SECURITY;

-- Helper function for data source ownership
CREATE OR REPLACE FUNCTION user_owns_data_source(source_id uuid)
RETURNS boolean AS $$
BEGIN
  RETURN EXISTS (
    SELECT 1 FROM data_sources
    WHERE id = source_id
    AND user_id = auth.uid()
  );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER STABLE;

-- PROFILES POLICIES
CREATE POLICY "Users can view own profile"
  ON profiles FOR SELECT USING (auth.uid() = id);
CREATE POLICY "Users can update own profile"
  ON profiles FOR UPDATE USING (auth.uid() = id);
CREATE POLICY "Users can insert own profile"
  ON profiles FOR INSERT WITH CHECK (auth.uid() = id);

-- DATA SOURCES POLICIES
CREATE POLICY "Users can view own data sources"
  ON data_sources FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can create own data sources"
  ON data_sources FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can update own data sources"
  ON data_sources FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "Users can delete own data sources"
  ON data_sources FOR DELETE USING (auth.uid() = user_id);

-- BUSINESS DATA POLICIES
CREATE POLICY "Users can view own business data"
  ON business_data FOR SELECT USING (user_owns_data_source(data_source_id));
CREATE POLICY "Users can insert own business data"
  ON business_data FOR INSERT WITH CHECK (user_owns_data_source(data_source_id));
CREATE POLICY "Users can update own business data"
  ON business_data FOR UPDATE USING (user_owns_data_source(data_source_id));
CREATE POLICY "Users can delete own business data"
  ON business_data FOR DELETE USING (user_owns_data_source(data_source_id));

-- COLUMN MAPPINGS POLICIES
CREATE POLICY "Users can view own column mappings"
  ON column_mappings FOR SELECT USING (user_owns_data_source(data_source_id));
CREATE POLICY "Users can insert own column mappings"
  ON column_mappings FOR INSERT WITH CHECK (user_owns_data_source(data_source_id));
CREATE POLICY "Users can update own column mappings"
  ON column_mappings FOR UPDATE USING (user_owns_data_source(data_source_id));
CREATE POLICY "Users can delete own column mappings"
  ON column_mappings FOR DELETE USING (user_owns_data_source(data_source_id));

-- KPI TARGETS POLICIES
CREATE POLICY "Users can view own KPI targets"
  ON kpi_targets FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can create own KPI targets"
  ON kpi_targets FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can update own KPI targets"
  ON kpi_targets FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "Users can delete own KPI targets"
  ON kpi_targets FOR DELETE USING (auth.uid() = user_id);

-- DERIVED METRICS POLICIES
CREATE POLICY "Users can view own derived metrics"
  ON derived_metrics FOR SELECT USING (user_owns_data_source(data_source_id));
CREATE POLICY "Users can insert own derived metrics"
  ON derived_metrics FOR INSERT WITH CHECK (user_owns_data_source(data_source_id));
CREATE POLICY "Users can update own derived metrics"
  ON derived_metrics FOR UPDATE USING (user_owns_data_source(data_source_id));
CREATE POLICY "Users can delete own derived metrics"
  ON derived_metrics FOR DELETE USING (user_owns_data_source(data_source_id));

-- STANDARDIZED FIELDS - Public read
CREATE POLICY "Anyone can read standardized fields"
  ON standardized_fields FOR SELECT USING (true);
