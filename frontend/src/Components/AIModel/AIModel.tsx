import { useEffect, useState } from 'react'
import { AI_Model, getDatasetRows, getDatasetDashboard, listDatasets, getForecastHistory, getForecastDetail, getForecastStatus, getFeatureRecommendations, getModelCategoryStats, getColumnCorrelations } from '../../api'
import ColumnMindMap from './ColumnMindMap'
import { LogoSpinner } from '../ui/LogoSpinner'
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

const LAST_DATASET_ID_KEY = 'bi_dashboard_last_dataset_id'
const forecastSessionKey = (id: string) => `bi_forecast_result_${id}`

interface DatasetOption {
  id: string
  filename: string
  status: string
}

interface Metrics {
  mae?: number
  rmse?: number
  wape?: number
  mase?: number
}

interface ModelResult {
  model: string
  status: string
  metrics: Metrics
  test_metrics?: Metrics & { n_test?: number } | null
  folds: number
  backtest_horizon: number
  error?: string
  fit_diagnosis?: 'healthy' | 'mild_overfit' | 'overfit' | 'severe_overfit' | 'check_leakage' | null
  fit_ratio?: number | null
}

interface ForecastPoint {
  date: string
  value: number
}

interface IntervalPoint {
  date: string
  lower: number
  upper: number
}

interface SkippedModel {
  model: string
  reason: string
}

interface FeatureImportanceItem {
  feature: string
  importance: number
  importance_pct: number
}

interface AnomalyPoint {
  date: string
  original_value: number
  capped_value: number
  direction: 'up' | 'down'
}

interface ForecastResponse {
  forecast_possible: boolean
  readiness: { forecast_possible: boolean; reasons: string[] }
  target: string
  frequency: string
  frequency_auto_detected?: boolean
  horizon: number
  missing_periods_policy: string
  candidate_models: string[]
  model_results: ModelResult[]
  best_model: string | null
  metrics: Metrics
  forecast: ForecastPoint[]
  prediction_intervals: IntervalPoint[]
  historical?: ForecastPoint[]
  training_rows?: number
  test_split_ratio?: number
  skipped_models?: SkippedModel[]
  confidence?: string
  confidence_reason?: string
  ensemble?: boolean
  best_models_by_metric?: { mae?: string; rmse?: string; wape?: string; mase?: string }
  warnings?: string[]
  feature_importance?: FeatureImportanceItem[]
  anomalies?: AnomalyPoint[]
  test_comparison?: { date: string; actual: number; predicted: number }[]
}

type ChartRow = {
  date: string
  historical?: number
  forecast?: number
  interval?: [number, number]
}

interface HistoryEntry {
  id: string
  created_at: string
  target_column: string
  best_model: string | null
  best_wape: number | null
  best_mae: number | null
  best_rmse: number | null
  horizon: number
  frequency_used: string | null
  duration_ms: number | null
}

interface HistoryDetail {
  forecast_data?: {
    forecast: ForecastPoint[]
    prediction_intervals: IntervalPoint[]
    test_comparison: { date: string; actual: number; predicted: number }[]
  } | null
  error?: boolean
}

interface ReadinessCheck {
  status: 'ready' | 'warning' | 'not-recommended'
  title: string
  message: string
  tips: string[]
}

function checkDatasetReadiness(columns: string[], timeColumn: string, targetColumn: string): ReadinessCheck | null {
  if (!columns.length || !timeColumn) return null

  const allLower = columns.map(c => c.toLowerCase())
  const timeLower = timeColumn.toLowerCase()

  const isCrossSectionalId = allLower.some(c =>
    /^(employee|customer|user|person|patient|product|item|order)_?(id|name|no|number)$/.test(c) ||
    c === 'employee_name' || c === 'customer_name'
  )

  const isAttributeDate =
    timeLower.includes('hire') || timeLower.includes('birth') ||
    timeLower.includes('join') || timeLower.includes('dob') ||
    timeLower.includes('start_date') || timeLower.includes('end_date') ||
    timeLower.includes('created_at') || timeLower.includes('updated_at')

  const isGoodTimeIndex =
    ['date', 'month', 'week', 'year', 'period', 'quarter', 'day', 'timestamp'].includes(timeLower) ||
    /^(sale|order|report|record|transaction)_date$/.test(timeLower) ||
    /^(year|month|week)$/.test(timeLower)

  if (isCrossSectionalId && isAttributeDate) {
    return {
      status: 'not-recommended',
      title: 'This looks like a snapshot dataset, not a time series',
      message: `Your dataset has one row per entity (person, product, etc.) with "${timeColumn}" as a record attribute — not a time series. Forecasting needs data measured repeatedly over time, like monthly sales or daily signups.`,
      tips: [
        'Each row should represent one time period (day / week / month)',
        'Minimum 30 time-ordered rows required',
        'Example shape: date | revenue — one row per month',
      ],
    }
  }

  if (isAttributeDate) {
    return {
      status: 'warning',
      title: 'Double-check your time column',
      message: `"${timeColumn}" looks like a record attribute (when something was created or started), not a regular time index. Forecasting works best when each row represents one time period.`,
      tips: [
        'Make sure every row is a different point in time, not a different entity',
        'If rows are per-person or per-product, forecasting won\'t be meaningful',
      ],
    }
  }

  if (isGoodTimeIndex) {
    return {
      status: 'ready',
      title: 'Dataset looks ready for forecasting',
      message: `"${timeColumn}" looks like a proper time index and "${targetColumn}" will be forecast. You're good to go!`,
      tips: [],
    }
  }

  return {
    status: 'warning',
    title: 'Review your column selection',
    message: 'Make sure the time column has one row per regular interval (daily, weekly, monthly) and the target is a numeric metric that changes over time.',
    tips: [
      'Minimum 30 rows required for reliable results',
      'Irregular or sparse dates may reduce forecast accuracy',
    ],
  }
}

function buildChartData(result: ForecastResponse): ChartRow[] {
  const rows: ChartRow[] = []

  if (result.historical) {
    for (const pt of result.historical) {
      rows.push({ date: pt.date, historical: pt.value })
    }
  }

  if (result.historical?.length && result.forecast.length) {
    const last = result.historical[result.historical.length - 1]
    rows.push({
      date: last.date,
      forecast: last.value,
      interval: [last.value, last.value],
    })
  }

  for (let i = 0; i < result.forecast.length; i++) {
    const fc = result.forecast[i]
    const iv = result.prediction_intervals[i]
    rows.push({
      date: fc.date,
      forecast: fc.value,
      interval: iv ? [iv.lower, iv.upper] : undefined,
    })
  }

  return rows
}

function buildHistoryChartData(
  forecast: ForecastPoint[],
  intervals: IntervalPoint[]
): ChartRow[] {
  return forecast.map((pt, i) => ({
    date: pt.date,
    forecast: pt.value,
    interval: intervals[i] ? [intervals[i].lower, intervals[i].upper] : undefined,
  }))
}

function exportForecastCsv(result: ForecastResponse) {
  const rows: string[] = ['date,predicted,lower,upper']
  result.forecast.forEach((pt, i) => {
    const iv = result.prediction_intervals?.[i]
    rows.push(`${pt.date},${pt.value},${iv?.lower ?? ''},${iv?.upper ?? ''}`)
  })
  const blob = new Blob([rows.join('\n')], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `forecast_${result.target}_horizon${result.horizon}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

function formatNumber(n: number | undefined | null): string {
  if (n === undefined || n === null) return '—'
  if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 1 })
  return n.toFixed(3)
}

const MODEL_LABELS: Record<string, string> = {
  naive: 'Naive',
  seasonal_naive: 'Seasonal Naive',
  ets: 'ETS (Exp. Smoothing)',
  exp_smoothing: 'ETS (Exp. Smoothing)',
  sarimax: 'SARIMAX (Auto-ARIMA)',
  catboost: 'CatBoost',
  lightgbm: 'LightGBM',
  prophet: 'Prophet',
}

type ColumnMeta = {
  column_key?: string
  clean_name?: string
  original_name?: string
  data_type?: string
  is_primary_metric?: boolean
  ai_profile?: { role?: string } | string
}

function columnMetaKey(col: ColumnMeta): string {
  return col.column_key ?? col.clean_name ?? col.original_name ?? ''
}

function aiRole(col: ColumnMeta): string {
  const ap = col.ai_profile
  if (ap && typeof ap === 'object') return (ap.role ?? '').toLowerCase()
  if (typeof ap === 'string') {
    try { return (JSON.parse(ap)?.role ?? '').toLowerCase() } catch { return '' }
  }
  return ''
}

function detectDefaultTimeColumn(availableColumns: string[], metadata: ColumnMeta[] = []): string {
  for (const col of metadata) {
    const key = columnMetaKey(col)
    if (!key || !availableColumns.includes(key)) continue
    const dt = (col.data_type ?? '').toLowerCase()
    const role = aiRole(col)
    if (dt === 'datetime' || dt === 'date' || role === 'date') return key
  }
  const lowered = availableColumns.map((col) => ({ raw: col, lower: col.toLowerCase() }))
  const match =
    lowered.find((c) => c.lower.includes('date')) ||
    lowered.find((c) => c.lower.includes('time')) ||
    lowered.find((c) => c.lower.includes('timestamp')) ||
    lowered.find((c) => c.lower.endsWith('_at'))
  return match ? match.raw : availableColumns[0] || ''
}

function isRatioLikeMetric(key: string): boolean {
  const k = key.toLowerCase()
  return /margin|ratio|pct|percent|rate|share|return/.test(k)
}

/** Pick a numeric measure — never default to text/id columns like store_id. */
function detectDefaultTargetColumn(
  availableColumns: string[],
  timeColumn: string,
  metadata: ColumnMeta[] = [],
): string {
  const metaByKey = new Map(metadata.map((c) => [columnMetaKey(c), c]).filter(([k]) => k))

  const isNumericCol = (key: string): boolean => {
    const m = metaByKey.get(key)
    if (!m) return false
    const dt = (m.data_type ?? '').toLowerCase()
    const role = aiRole(m)
    return dt === 'numeric' || role === 'measure'
  }

  const numericCols = availableColumns.filter((c) => c !== timeColumn && isNumericCol(c))

  // Prefer headline business KPIs over ratios (profit_margin, discount_pct, etc.).
  const prefer = ['net_revenue', 'revenue', 'sales', 'units_sold', 'amount', 'value', 'total']
  for (const hint of prefer) {
    const hit = numericCols.find((c) => c.toLowerCase().includes(hint))
    if (hit) return hit
  }

  for (const col of metadata) {
    const key = columnMetaKey(col)
    if (key && key !== timeColumn && availableColumns.includes(key) && col.is_primary_metric && isNumericCol(key) && !isRatioLikeMetric(key)) {
      return key
    }
  }

  if (numericCols.length) return numericCols[0]

  const nameHints = ['revenue', 'sales', 'units_sold', 'amount', 'value', 'total', 'quantity']
  for (const hint of nameHints) {
    const hit = availableColumns.find((c) => c !== timeColumn && c.toLowerCase().includes(hint))
    if (hit) return hit
  }

  const avoid = ['id', 'store', 'region', 'category', 'name', 'type', 'status']
  const safe = availableColumns.find(
    (c) => c !== timeColumn && !avoid.some((a) => c.toLowerCase().includes(a)),
  )
  return safe ?? availableColumns.find((c) => c !== timeColumn) ?? availableColumns[0] ?? ''
}

function isForecastFeatureColumn(key: string, metadata: ColumnMeta[]): boolean {
  const col = metadata.find((c) => columnMetaKey(c) === key)
  if (!col) return false
  const dt = (col.data_type ?? '').toLowerCase()
  const role = aiRole(col)
  if (dt === 'datetime' || dt === 'date' || role === 'date') return false
  if (role === 'id' || role === 'dimension' || role === 'geographic' || role === 'descriptive') return false
  if (dt === 'text' || dt === 'boolean') return false
  return dt === 'numeric' || role === 'measure'
}

export default function AIModel() {
  const [datasets, setDatasets] = useState<DatasetOption[]>([])
  const [loadingDatasets, setLoadingDatasets] = useState(true)
  const [datasetId, setDatasetId] = useState(() => {
    if (typeof window === 'undefined') return ''
    return localStorage.getItem(LAST_DATASET_ID_KEY) || ''
  })
  const [columns, setColumns] = useState<string[]>([])
  const [timeColumn, setTimeColumn] = useState('')
  const [targetColumn, setTargetColumn] = useState('')
  const [featureColumns, setFeatureColumns] = useState<string[]>([])
  const [featureScores, setFeatureScores] = useState<
    { feature: string; score: number; method: string }[]
  >([])
  const [featuresAutoSelected, setFeaturesAutoSelected] = useState(false)
  const [frequency, setFrequency] = useState('auto')
  const [horizon, setHorizon] = useState(30)
  const [mode, setMode] = useState<'fast' | 'accurate'>('fast')
  const [loading, setLoading] = useState(false)
  const [loadingColumns, setLoadingColumns] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ForecastResponse | null>(null)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [expandedHistoryId, setExpandedHistoryId] = useState<string | null>(null)
  const [historyDetails, setHistoryDetails] = useState<Record<string, HistoryDetail>>({})
  const [historyDetailLoading, setHistoryDetailLoading] = useState<Record<string, boolean>>({})
  const [modelGuideOpen, setModelGuideOpen] = useState(false)
  const [modelCategoryStats, setModelCategoryStats] = useState<any>(null)
  const [mindMapOpen, setMindMapOpen] = useState(false)
  const loadHistory = async (id: string) => {
    if (!id) return
    try {
      const res = await getForecastHistory(id)
      setHistory(Array.isArray(res?.forecasts) ? res.forecasts : [])
    } catch {
      setHistory([])
    }
  }

  const loadHistoryDetail = async (id: string) => {
    if (historyDetails[id] || historyDetailLoading[id]) return
    setHistoryDetailLoading(prev => ({ ...prev, [id]: true }))
    try {
      const detail = await getForecastDetail(id)
      const fd = detail?.forecast_data
      setHistoryDetails(prev => ({
        ...prev,
        [id]: {
          forecast_data: fd
            ? {
                forecast: fd.forecast || [],
                prediction_intervals: fd.prediction_intervals || [],
                test_comparison: fd.test_comparison || [],
              }
            : null,
        },
      }))
    } catch {
      setHistoryDetails(prev => ({ ...prev, [id]: { error: true } }))
    } finally {
      setHistoryDetailLoading(prev => ({ ...prev, [id]: false }))
    }
  }

  const toggleHistoryCard = (id: string) => {
    if (expandedHistoryId === id) {
      setExpandedHistoryId(null)
    } else {
      setExpandedHistoryId(id)
      loadHistoryDetail(id)
    }
  }

  const applyFeatureRecommendations = async (
    ds: string,
    target: string,
    time: string,
    availableColumns: string[],
    metadata: ColumnMeta[] = [],
  ) => {
    try {
      const res = await getFeatureRecommendations(ds, { target, time })
      const recs: { feature: string; score: number; method: string }[] =
        res?.recommendations ?? []
      const valid = recs.filter(
        (r) => availableColumns.includes(r.feature) && isForecastFeatureColumn(r.feature, metadata),
      )
      if (valid.length > 0) {
        setFeatureColumns(valid.slice(0, 2).map((r) => r.feature))
        setFeatureScores(valid)
        setFeaturesAutoSelected(true)
        return
      }
    } catch (err) {
      console.error('feature recommendation failed:', err)
    }
    // Fallback: numeric measure columns only (skip dimensions/ids).
    const fallback = availableColumns.filter(
      (col) => col !== time && col !== target && isForecastFeatureColumn(col, metadata),
    )
    setFeatureColumns(fallback.slice(0, 2))
    setFeatureScores([])
    setFeaturesAutoSelected(false)
  }

  const loadColumns = async (id?: string) => {
    const target = (id ?? datasetId).trim()
    if (!target) return

    localStorage.setItem(LAST_DATASET_ID_KEY, target)
    setLoadingColumns(true)
    setError(null)
    setHistory([])
    setColumns([])
    setTimeColumn('')
    setTargetColumn('')
    setFeatureColumns([])
    setExpandedHistoryId(null)
    setHistoryDetails({})

    // Restore cached forecast result for this dataset (clears on browser refresh automatically)
    const cached = sessionStorage.getItem(forecastSessionKey(target))
    if (cached) {
      try {
        setResult(JSON.parse(cached))
      } catch {
        setResult(null)
      }
    } else {
      setResult(null)
    }

    try {
      const [rowsRes, dashRes] = await Promise.all([
        getDatasetRows(target, { limit: 1, offset: 0 }),
        getDatasetDashboard(target).catch(() => null),
      ])
      const row = rowsRes?.rows?.[0]?.row_data
      const availableColumns = row ? Object.keys(row) : []
      const metadata: ColumnMeta[] = Array.isArray(dashRes?.columns) ? dashRes.columns : []

      if (!availableColumns.length) {
        setError('No rows found for this dataset. Complete preprocessing first.')
        return
      }

      const defaultTime = detectDefaultTimeColumn(availableColumns, metadata)
      const defaultTarget = detectDefaultTargetColumn(availableColumns, defaultTime, metadata)

      setColumns(availableColumns)
      setTimeColumn(defaultTime)
      setTargetColumn(defaultTarget)
      await applyFeatureRecommendations(target, defaultTarget, defaultTime, availableColumns, metadata)
    } catch (err) {
      console.error('Error loading dataset columns:', err)
      setError('Failed to load columns for this dataset.')
    } finally {
      setLoadingColumns(false)
    }
    void loadHistory(target)
  }

  useEffect(() => {
    listDatasets()
      .then((res) => {
        const completed: DatasetOption[] = (res.datasets || []).filter(
          (d: DatasetOption) => d.status === 'completed'
        )
        setDatasets(completed)
        const saved = localStorage.getItem(LAST_DATASET_ID_KEY) || ''
        if (saved && completed.some((d) => d.id === saved)) {
          setDatasetId(saved)
          loadColumns(saved)
        } else if (completed.length > 0) {
          setDatasetId(completed[0].id)
          loadColumns(completed[0].id)
        }
      })
      .catch(() => setError('Failed to load projects list.'))
      .finally(() => setLoadingDatasets(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (modelGuideOpen && !modelCategoryStats) {
      getModelCategoryStats()
        .then(setModelCategoryStats)
        .catch(() => {})
    }
  }, [modelGuideOpen, modelCategoryStats])

  // Poll an async (accurate-mode) forecast job until it finishes.
  // Returns the service-shape forecast on success; throws on failure/timeout.
  const pollForecast = async (jobId: string) => {
    const intervalMs = 5000
    const maxAttempts = 360 // ~30 min ceiling (accurate runs all models on a worker)
    for (let i = 0; i < maxAttempts; i++) {
      await new Promise((r) => setTimeout(r, intervalMs))
      const status = await getForecastStatus(jobId)
      if (status?.status === 'completed') {
        if (!status.forecast) throw new Error('Forecast finished but returned no result.')
        return status.forecast
      }
      if (status?.status === 'failed') {
        throw new Error(status.error || 'Forecast failed.')
      }
    }
    throw new Error('Forecast timed out while running in the background.')
  }

  const forecast = async () => {
    if (!datasetId.trim()) {
      setError('Please select a project first.')
      return
    }
    localStorage.setItem(LAST_DATASET_ID_KEY, datasetId.trim())
    if (!timeColumn || !targetColumn) {
      setError('Please load columns and select time/target before forecasting.')
      return
    }

    setLoading(true)
    setError(null)
    setResult(null)

    const payload = {
      time_column: timeColumn.trim(),
      target_column: targetColumn.trim(),
      id_columns: [],
      feature_columns: featureColumns.filter(
        (col) => col !== timeColumn && col !== targetColumn
      ),
      frequency: frequency === 'auto' ? null : (frequency.trim() || null),
      horizon: Number(horizon),
      candidate_models: ['naive', 'seasonal_naive', 'ets', 'sarimax', 'catboost', 'lightgbm', 'prophet'],
      mode,
    }

    try {
      let response = await AI_Model(datasetId.trim(), payload)

      // Accurate mode runs async on a Celery worker → poll until done.
      if (response && response.status === 'queued' && response.job_id) {
        response = await pollForecast(response.job_id)
      }

      setResult(response)
      // Persist across navigation (cleared on browser refresh)
      try {
        sessionStorage.setItem(forecastSessionKey(datasetId.trim()), JSON.stringify(response))
      } catch {
        // sessionStorage quota exceeded — silently skip
      }
      void loadHistory(datasetId.trim())
    } catch (error) {
      console.error('Error fetching forecast:', error)
      const e = error as { response?: { data?: { message?: string; error?: string; detail?: string }; status?: number }; message?: string }
      const data = e?.response?.data
      const msg = data?.message || data?.error || data?.detail || e?.message || 'Forecast request failed.'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  const chartData = result ? buildChartData(result) : []

  return (
    <div className="p-6 text-foreground">
      <div className="mb-8">
        <h2 className="text-2xl font-bold">AI Forecasting</h2>
        <p className="text-sm text-muted-foreground mt-1">Configure and run time-series predictions on your datasets</p>
      </div>

      {/* ── Controls Panel ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
        <div className="lg:col-span-2 bg-card border border-border rounded-2xl p-6 space-y-5">
          <div className="flex items-center gap-2 mb-2">
            <i className="fa-solid fa-sliders text-primary" />
            <h3 className="text-sm font-bold uppercase tracking-wider text-muted-foreground">Configuration</h3>
          </div>

          <label className="grid gap-1.5">
            <span className="text-xs text-muted-foreground font-medium">Project</span>
            {loadingDatasets ? (
              <div className="rounded-xl px-3 py-2.5 bg-card border border-border text-muted-foreground text-sm flex items-center gap-2">
                <LogoSpinner size={14} />
                Loading projects...
              </div>
            ) : datasets.length === 0 ? (
              <div className="rounded-xl px-3 py-2.5 bg-card border border-border text-muted-foreground text-sm">
                No completed projects found.
              </div>
            ) : (
              <select
                className="rounded-xl px-3 py-2.5 bg-card border border-border text-foreground text-sm focus:border-primary focus:outline-none transition-colors"
                value={datasetId}
                onChange={(e) => {
                  const id = e.target.value
                  setDatasetId(id)
                  loadColumns(id)
                }}
              >
                {datasets.map((ds) => (
                  <option key={ds.id} value={ds.id}>
                    {ds.filename}
                  </option>
                ))}
              </select>
            )}
            {loadingColumns && (
              <span className="text-xs text-muted-foreground flex items-center gap-1.5">
                <LogoSpinner size={14} />
                Loading columns...
              </span>
            )}
          </label>

          <label className="grid gap-1.5">
            <span className="text-xs text-muted-foreground font-medium">Time Column</span>
            <select
              className="rounded-xl px-3 py-2.5 bg-card border border-border text-foreground text-sm focus:border-primary focus:outline-none transition-colors"
              value={timeColumn}
              onChange={(e) => setTimeColumn(e.target.value)}
              disabled={!columns.length}
            >
              <option value="">Select time column</option>
              {columns.map((col) => (
                <option key={col} value={col}>{col}</option>
              ))}
            </select>
          </label>

          <label className="grid gap-1.5">
            <span className="text-xs text-muted-foreground font-medium">Target Column</span>
            <select
              className="rounded-xl px-3 py-2.5 bg-card border border-border text-foreground text-sm focus:border-primary focus:outline-none transition-colors"
              value={targetColumn}
              onChange={(e) => {
                const newTarget = e.target.value
                setTargetColumn(newTarget)
                if (newTarget && columns.length) {
                  void applyFeatureRecommendations(datasetId, newTarget, timeColumn, columns)
                }
              }}
              disabled={!columns.length}
            >
              <option value="">Select target column</option>
              {columns.map((col) => (
                <option key={col} value={col}>{col}</option>
              ))}
            </select>
          </label>

          <label className="grid gap-1.5">
            <span className="flex items-center gap-2 text-xs text-muted-foreground font-medium">
              Feature Columns
              {featuresAutoSelected && (
                <span
                  className="rounded-full bg-primary/15 text-primary px-2 py-0.5 text-[10px] font-semibold"
                  title={featureScores
                    .map((s) => `${s.feature}: ${s.score} (${s.method})`)
                    .join('\n')}
                >
                  Auto-selected by correlation with target
                </span>
              )}
            </span>
            <select
              multiple
              className="rounded-xl px-3 py-2.5 bg-card border border-border text-foreground text-sm min-h-20 focus:border-primary focus:outline-none transition-colors"
              value={featureColumns}
              onChange={(e) =>
                setFeatureColumns(
                  Array.from(e.target.selectedOptions).map((option) => option.value)
                )
              }
              disabled={!columns.length}
            >
              {columns
                .filter((col) => col !== timeColumn && col !== targetColumn)
                .map((col) => (
                  <option key={col} value={col}>{col}</option>
                ))}
            </select>
            {featuresAutoSelected && featureScores.length > 0 && (
              <ul className="mt-1 grid gap-0.5 text-[11px] text-muted-foreground">
                {featureScores.slice(0, 5).map((s) => (
                  <li key={s.feature} className="flex justify-between">
                    <span>{s.feature}</span>
                    <span className="tabular-nums text-muted-foreground">
                      {s.score.toFixed(2)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <span className="text-xs text-muted-foreground">Hold Ctrl/Cmd to select multiple.</span>
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="grid gap-1.5">
              <span className="text-xs text-muted-foreground font-medium">Frequency</span>
              <select
                className="rounded-xl px-3 py-2.5 bg-card border border-border text-foreground text-sm focus:border-primary focus:outline-none transition-colors"
                value={frequency}
                onChange={(e) => setFrequency(e.target.value)}
              >
                <option value="auto">Auto-detect</option>
                <option value="D">Daily</option>
                <option value="W">Weekly</option>
                <option value="MS">Monthly</option>
                <option value="QS">Quarterly</option>
              </select>
            </label>
            <label className="grid gap-1.5">
              <span className="text-xs text-muted-foreground font-medium">Horizon</span>
              <input
                type="number"
                className="rounded-xl px-3 py-2.5 bg-card border border-border text-foreground text-sm focus:border-primary focus:outline-none transition-colors"
                placeholder="30"
                value={horizon}
                onChange={(e) => setHorizon(Number(e.target.value))}
                min={1}
              />
            </label>
          </div>

          {/* Speed vs accuracy */}
          <label className="grid gap-1.5">
            <span className="text-xs text-muted-foreground font-medium">Mode</span>
            <div className="grid grid-cols-2 gap-2">
              {([
                { id: 'fast', label: 'Fast', sub: 'Seconds · quick models' },
                { id: 'accurate', label: 'Accurate', sub: 'All models · runs in background' },
              ] as const).map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => setMode(m.id)}
                  className={`rounded-xl px-3 py-2.5 border text-left transition-colors ${
                    mode === m.id
                      ? 'border-primary bg-primary/10 text-foreground'
                      : 'border-border bg-card text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <span className="block text-sm font-semibold">{m.label}</span>
                  <span className="block text-[11px] text-muted-foreground">{m.sub}</span>
                </button>
              ))}
            </div>
          </label>

          {/* Action buttons */}
          <div className="pt-3 flex flex-col gap-2">
            <button
              onClick={forecast}
              disabled={loading || !datasetId || !timeColumn || !targetColumn}
              className="w-full py-3 rounded-xl bg-primary hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed text-primary-foreground text-sm font-bold transition-all hover:scale-[1.02] active:scale-[0.98] flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <LogoSpinner size={16} />
                  Running Forecast...
                </>
              ) : (
                <>
                  <i className="fa-solid fa-wand-magic-sparkles" />
                  Run Forecast
                </>
              )}
            </button>
            {result && (
              <button
                onClick={() => exportForecastCsv(result)}
                className="w-full py-2.5 rounded-xl border border-border hover:border-primary/50 text-muted-foreground hover:text-foreground text-sm font-medium transition-colors flex items-center justify-center gap-2"
              >
                <i className="fa-solid fa-file-csv" />
                Export CSV
              </button>
            )}
          </div>
        </div>

        {/* Results area (right 2/3) */}
        <div className="lg:col-span-2 space-y-6">

      {/* ── Readiness Banner ── */}
      {(() => {
        const check = columns.length ? checkDatasetReadiness(columns, timeColumn, targetColumn) : null
        if (!check) return null

        const styles = {
          ready: {
            wrap: 'bg-success/50 border border-success/30',
            icon: 'fa-circle-check text-success',
            title: 'text-white',
            body: 'text-success',
            tip: 'text-success/60',
          },
          warning: {
            wrap: 'bg-warning/50 border border-warning/30',
            icon: 'fa-triangle-exclamation text-warning',
            title: 'text-warning',
            body: 'text-amber-200/80',
            tip: 'text-warning/60',
          },
          'not-recommended': {
            wrap: 'bg-destructive/10 border border-destructive/30',
            icon: 'fa-circle-xmark text-destructive',
            title: 'text-destructive',
            body: 'text-red-200/80',
            tip: 'text-destructive/60',
          },
        }[check.status]

        return (
          <div className={`rounded-xl px-4 py-4 ${styles.wrap}`}>
            <div className="flex items-start gap-3">
              <i className={`fa-solid ${styles.icon} mt-0.5 text-base shrink-0`}></i>
              <div>
                <p className={`text-sm font-semibold ${styles.title}`}>{check.title}</p>
                <p className={`text-sm mt-1 ${styles.body}`}>{check.message}</p>
                {check.tips.length > 0 && (
                  <ul className={`mt-2 space-y-0.5 text-xs list-disc list-inside ${styles.tip}`}>
                    {check.tips.map((tip, i) => <li key={i}>{tip}</li>)}
                  </ul>
                )}
              </div>
            </div>
          </div>
        )
      })()}

      {error && (
        <div className="rounded-xl px-4 py-3 bg-destructive/10 border border-destructive/30">
          <p className="text-sm text-destructive flex items-center gap-2">
            <i className="fa-solid fa-circle-exclamation" />
            {error}
          </p>
        </div>
      )}

        </div>{/* end results area */}
      </div>{/* end grid */}



      {/* ── Results ── */}
      {result && (
        <div className="mt-6 space-y-6 max-w-6xl">
          {/* Status Banner */}
          {(() => {
            const conf = result.confidence || 'high';
            const selectionMethod = result.ensemble ? 'ensemble (avg-rank)' : 'avg-rank';
            const bannerStyle = !result.forecast_possible
              ? 'bg-destructive/10 text-destructive border border-destructive/30'
              : conf === 'high'
                ? 'bg-success/10 text-success border border-success/30'
                : conf === 'medium'
                  ? 'bg-warning/10 text-warning border border-warning/30'
                  : 'bg-warning/10 text-warning border border-warning/30';
            const label = !result.forecast_possible
              ? 'Forecast not possible'
              : conf === 'high'
                ? `Forecast ready -- best by ${selectionMethod}: ${result.best_model}`
                : `Forecast ready (${conf} confidence) -- best by ${selectionMethod}: ${result.best_model}`;
            return (
              <div className={`rounded-lg px-4 py-3 text-sm font-semibold ${bannerStyle}`}>
                <div>{label}</div>
                {result.confidence_reason && result.forecast_possible && (
                  <div className="text-xs font-normal mt-1 opacity-80">{result.confidence_reason}</div>
                )}
                {result.best_models_by_metric && result.forecast_possible && (
                  <div className="text-xs font-normal mt-1 opacity-80">
                    Best by metric: MAE {result.best_models_by_metric.mae}, RMSE {result.best_models_by_metric.rmse}, WAPE {result.best_models_by_metric.wape}, MASE {result.best_models_by_metric.mase}
                  </div>
                )}
              </div>
            );
          })()}

          {/* Summary banner */}
          {result.best_model && (() => {
            const wape = result.metrics.wape
            const accuracy = wape !== undefined ? Math.max(0, 100 - wape * 100) : null
            const mae = result.metrics.mae
            const testRows = result.training_rows != null && result.test_split_ratio != null
              ? Math.round(result.training_rows * result.test_split_ratio) : null
            const totalError = mae != null && testRows != null ? mae * testRows : null

            const accuracyColor = accuracy == null ? 'text-foreground'
              : accuracy >= 95 ? 'text-success'
              : accuracy >= 85 ? 'text-warning'
              : 'text-destructive'

            return (
              <div className="bg-card border border-border rounded-xl p-5 space-y-4">
                <div className="flex flex-wrap gap-6 items-center">
                  <div>
                    <span className="text-xs text-muted-foreground uppercase tracking-wide">Best Model</span>
                    <p className="text-lg font-bold text-primary">
                      {MODEL_LABELS[result.best_model] || result.best_model}
                    </p>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground uppercase tracking-wide">Accuracy</span>
                    <p className={`text-2xl font-bold ${accuracyColor}`}>
                      {accuracy !== null ? accuracy.toFixed(1) + '%' : '—'}
                    </p>
                    <span className="text-xs text-muted-foreground">100% − WAPE</span>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground uppercase tracking-wide">Total Error (test set)</span>
                    <p className="text-lg font-semibold text-foreground">
                      {totalError !== null ? formatNumber(totalError) : '—'}
                    </p>
                    <span className="text-xs text-muted-foreground">MAE × {testRows ?? '?'} test rows</span>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground uppercase tracking-wide">MAE</span>
                    <p className="text-lg font-semibold">{formatNumber(mae)}</p>
                    <span className="text-xs text-muted-foreground">avg error per period</span>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground uppercase tracking-wide">RMSE</span>
                    <p className="text-lg font-semibold">{formatNumber(result.metrics.rmse)}</p>
                    <span className="text-xs text-muted-foreground">penalizes large errors</span>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground uppercase tracking-wide">WAPE</span>
                    <p className="text-lg font-semibold">
                      {wape !== undefined ? (wape * 100).toFixed(1) + '%' : '—'}
                    </p>
                    <span className="text-xs text-muted-foreground">% of total actuals</span>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground uppercase tracking-wide">Frequency</span>
                    <p className="text-lg font-semibold">
                      {result.frequency}
                      {result.frequency_auto_detected && (
                        <span className="ml-1 text-xs text-muted-foreground font-normal">(auto)</span>
                      )}
                    </p>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground uppercase tracking-wide">Train / Test rows</span>
                    <p className="text-lg font-semibold">
                      {result.training_rows != null && result.test_split_ratio != null
                        ? `${Math.round(result.training_rows * (1 - result.test_split_ratio))} / ${testRows}`
                        : (result.training_rows ?? '—')}
                    </p>
                  </div>
                </div>
              </div>
            )
          })()}

          {/* Forecast chart */}
          {chartData.length > 0 && (() => {
            const forecastVals = result.forecast.map(p => p.value)
            const forecastRange = forecastVals.length > 1
              ? Math.max(...forecastVals) - Math.min(...forecastVals)
              : 0
            const histVals = (result.historical ?? []).map(p => p.value)
            const histRange = histVals.length > 1
              ? Math.max(...histVals) - Math.min(...histVals)
              : 1
            const histMean = histVals.length > 0
              ? histVals.reduce((a, b) => a + b, 0) / histVals.length
              : 1
            const isFlat = histRange > 0 && forecastRange / histRange < 0.05
            const isVolatile = histMean > 0 && histRange / histMean > 0.3
            return (
            <div className="bg-card border border-border rounded-xl p-5">
              <h3 className="text-lg font-semibold mb-2">
                Forecast: {result.target}
              </h3>
              {isFlat && !isVolatile && (
                <div className="flex items-start gap-2 mb-4 px-3 py-2 rounded-lg bg-sky-950/40 border border-sky-700/40 text-xs text-info">
                  <i className="fa-solid fa-circle-info mt-0.5 shrink-0"></i>
                  <span>
                    This metric is <strong>stable</strong> — the forecast reflects the expected continuation of the current trend. A flat line means the model found no significant upward or downward pattern to project.
                  </span>
                </div>
              )}
              {isFlat && isVolatile && (
                <div className="flex items-start gap-2 mb-4 px-3 py-2 rounded-lg bg-amber-950/40 border border-warning/30 text-xs text-warning">
                  <i className="fa-solid fa-chart-line mt-0.5 shrink-0"></i>
                  <span>
                    <strong>High volatility detected</strong> — this metric has large unpredictable spikes (likely driven by external events like campaigns or promotions). The model is forecasting the <strong>expected average level</strong> rather than individual spikes, which is the most reliable prediction possible for this type of data.
                  </span>
                </div>
              )}
              <ResponsiveContainer width="100%" height={400}>
                <ComposedChart data={chartData} margin={{ top: 10, right: 30, left: 10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis
                    dataKey="date"
                    stroke="#64748b"
                    tick={{ fill: '#94a3b8', fontSize: 11 }}
                    tickFormatter={(v: string) => v.slice(5)}
                  />
                  <YAxis
                    stroke="#64748b"
                    tick={{ fill: '#94a3b8', fontSize: 11 }}
                    tickFormatter={(v: number) =>
                      v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)
                    }
                  />
                  <Tooltip
                    contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--popover-foreground)' }}
                    labelStyle={{ color: '#e2e8f0' }}
                    itemStyle={{ color: '#e2e8f0' }}
                    formatter={(value: any, name: any) => {
                      if (Array.isArray(value)) return [`${formatNumber(value[0])} – ${formatNumber(value[1])}`, 'Confidence']
                      return [formatNumber(value), name === 'historical' ? 'Historical' : 'Forecast']
                    }}
                  />
                  <Legend />
                  <Area
                    type="monotone"
                    dataKey="interval"
                    fill="#5A5AF6"
                    fillOpacity={0.12}
                    stroke="none"
                    name="95% Confidence"
                    legendType="rect"
                  />
                  <Line
                    type="monotone"
                    dataKey="historical"
                    stroke="#22d3ee"
                    strokeWidth={2}
                    dot={false}
                    name="Historical"
                    connectNulls={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="forecast"
                    stroke="#5A5AF6"
                    strokeWidth={2}
                    strokeDasharray="6 3"
                    dot={false}
                    name="Forecast"
                    connectNulls={false}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            )
          })()}

          {/* Model comparison table */}
          {result.model_results.length > 0 && (() => {
            const overfitModels = result.model_results.filter(
              m => m.fit_diagnosis === 'overfit' || m.fit_diagnosis === 'severe_overfit'
            )
            const hasMultipleOverfit = overfitModels.length >= 2
            return (
            <div className="bg-card border border-border rounded-xl p-5">
              <h3 className="text-lg font-semibold mb-3">Model Comparison</h3>
              {hasMultipleOverfit && (
                <div className="flex items-start gap-2 mb-4 px-3 py-2 rounded-lg bg-red-950/30 border border-destructive/30 text-xs text-destructive">
                  <i className="fa-solid fa-triangle-exclamation mt-0.5 shrink-0"></i>
                  <span>
                    <strong>{overfitModels.length} models show overfitting</strong> ({overfitModels.map(m => m.model).join(', ')}). This usually means the data has irregular spikes that these models memorized during training but couldn't generalize. The ensemble and best model selection automatically deprioritize overfit models.
                  </span>
                </div>
              )}
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground text-xs uppercase tracking-wide">
                      <th className="py-3 px-4" rowSpan={2}>Model</th>
                      <th className="py-3 px-4" rowSpan={2}>Status</th>
                      <th className="py-2 px-4 text-center border-b border-border" colSpan={4}>
                        CV (cross-validation)
                      </th>
                      <th className="py-2 px-4 text-center border-b border-border border-l border-l-[#3a4060]" colSpan={5}>
                        Test (30% holdout)
                      </th>
                      <th className="py-3 px-4" rowSpan={2}>Folds</th>
                    </tr>
                    <tr className="border-b border-border text-muted-foreground text-xs uppercase tracking-wide">
                      <th className="py-2 px-4">MAE</th>
                      <th className="py-2 px-4">RMSE</th>
                      <th className="py-2 px-4">WAPE</th>
                      <th className="py-2 px-4">MASE</th>
                      <th className="py-2 px-4 border-l border-border">MAE</th>
                      <th className="py-2 px-4">RMSE</th>
                      <th className="py-2 px-4">WAPE</th>
                      <th className="py-2 px-4">MASE</th>
                      <th className="py-2 px-4 text-success">Accuracy</th>
                      <th className="py-2 px-4">Fit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.model_results.map((mr) => {
                      const isBest = mr.model === result.best_model
                      const tm = mr.test_metrics
                      return (
                        <tr
                          key={mr.model}
                          className={`border-b border-border ${isBest ? 'bg-primary/10' : ''}`}
                        >
                          <td className="py-3 px-4 font-medium">
                            {MODEL_LABELS[mr.model] || mr.model}
                            {isBest && (
                              <span className="ml-2 text-xs bg-primary text-primary-foreground px-2 py-0.5 rounded-full">
                                Best
                              </span>
                            )}
                          </td>
                          <td className="py-3 px-4">
                            {mr.status === 'ok' ? (
                              <span className="text-success">OK</span>
                            ) : (
                              <span className="text-destructive" title={mr.error}>Failed</span>
                            )}
                          </td>
                          <td className="py-3 px-4">{formatNumber(mr.metrics.mae)}</td>
                          <td className="py-3 px-4">{formatNumber(mr.metrics.rmse)}</td>
                          <td className="py-3 px-4">
                            {mr.metrics.wape !== undefined ? (mr.metrics.wape * 100).toFixed(1) + '%' : '—'}
                          </td>
                          <td className="py-3 px-4">
                            {mr.status === 'ok' && mr.metrics.mase != null ? mr.metrics.mase.toFixed(3) : '—'}
                          </td>
                          <td className="py-3 px-4 border-l border-border">
                            {tm ? formatNumber(tm.mae) : '—'}
                          </td>
                          <td className="py-3 px-4">{tm ? formatNumber(tm.rmse) : '—'}</td>
                          <td className="py-3 px-4">
                            {tm?.wape !== undefined ? (tm.wape * 100).toFixed(1) + '%' : '—'}
                          </td>
                          <td className="py-3 px-4">
                            {tm?.mase != null ? tm.mase.toFixed(3) : '—'}
                          </td>
                          <td className="py-3 px-4 font-semibold">
                            {tm?.wape !== undefined ? (() => {
                              const acc = Math.max(0, 100 - tm.wape * 100)
                              const color = acc >= 95 ? 'text-success' : acc >= 85 ? 'text-warning' : 'text-destructive'
                              return <span className={color}>{acc.toFixed(1)}%</span>
                            })() : '—'}
                          </td>
                          <td className="py-3 px-4">
                            {(() => {
                              const d = mr.fit_diagnosis
                              const ratio = mr.fit_ratio
                              if (!d) return <span className="text-muted-foreground">—</span>
                              const cfg = {
                                healthy:        { label: 'Healthy',        color: 'text-success', icon: 'fa-circle-check' },
                                mild_overfit:   { label: 'Mild overfit',   color: 'text-warning',   icon: 'fa-triangle-exclamation' },
                                overfit:        { label: 'Overfit',        color: 'text-destructive',     icon: 'fa-circle-xmark' },
                                severe_overfit: { label: 'Severe overfit', color: 'text-destructive',     icon: 'fa-circle-xmark' },
                                check_leakage:  { label: 'Easy test split', color: 'text-info',    icon: 'fa-circle-info' },
                              }[d] ?? { label: d, color: 'text-muted-foreground', icon: 'fa-minus' }
                              return (
                                <span className={`flex items-center gap-1.5 text-xs font-medium ${cfg.color}`} title={d === 'check_leakage' ? `Test MAE was lower than CV average — the holdout period happened to be easier to predict (ratio: ${ratio}×)` : `Test/CV ratio: ${ratio}×`}>
                                  <i className={`fa-solid ${cfg.icon}`}></i>
                                  {cfg.label}
                                  <span className="text-muted-foreground font-normal">({ratio}×)</span>
                                </span>
                              )
                            })()}
                          </td>
                          <td className="py-3 px-4">{mr.folds}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              {result.model_results.filter(m => m.status === 'failed' && m.error).map(mr => (
                <p key={mr.model} className="mt-2 text-xs text-destructive/80">
                  {MODEL_LABELS[mr.model] || mr.model}: {mr.error}
                </p>
              ))}
            </div>
            )
          })()}

          {/* Skipped models */}
          {result.skipped_models && result.skipped_models.length > 0 && (
            <div className="bg-card border border-yellow-800/40 rounded-lg p-3">
              <h3 className="text-xs font-semibold text-warning mb-2">Skipped Models</h3>
              <ul className="text-xs text-yellow-200/70 space-y-1">
                {result.skipped_models.map((s, i) => (
                  <li key={i}><span className="font-medium text-warning">{MODEL_LABELS[s.model] || s.model}</span>: {s.reason}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Not possible message */}
          {!result.forecast_possible && (
            <div className="bg-destructive/10 border border-red-800 rounded-xl p-5">
              <h3 className="text-lg font-semibold text-destructive mb-2">Forecast Not Possible</h3>
              <ul className="text-sm text-red-200 list-disc list-inside">
                {result.readiness.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
          )}

          {/* Forecast warnings */}
          {result.warnings && result.warnings.length > 0 && (
            <div className="bg-card border border-warning/30 rounded-lg p-3">
              <h3 className="text-xs font-semibold text-warning mb-2">Forecast Warnings</h3>
              <ul className="text-xs text-amber-200/80 space-y-1">
                {result.warnings.map((w: string, i: number) => (
                  <li key={i}>- {w}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Test Set: Actual vs Predicted */}
          {result.test_comparison && result.test_comparison.length > 0 && (
            <div className="bg-card border border-border rounded-xl p-5">
              <h3 className="text-lg font-semibold mb-1">Test Set: Actual vs Predicted</h3>
              <p className="text-xs text-muted-foreground mb-4">
                How the best model performed on the 30% holdout — dates the model never saw during training.
              </p>
              <ResponsiveContainer width="100%" height={280}>
                <ComposedChart data={result.test_comparison} margin={{ top: 4, right: 20, left: 10, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="date" stroke="#64748b" tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={(v: string) => v.slice(5)} />
                  <YAxis stroke="#64748b" tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={(v: number) => v >= 1000 ? `${(v/1000).toFixed(1)}k` : String(v)} />
                  <Tooltip
                    contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--popover-foreground)' }}
                    labelStyle={{ color: '#e2e8f0' }}
                    formatter={(value: any, name: any) => [formatNumber(value), name === 'actual' ? 'Actual' : 'Predicted']}
                  />
                  <Legend />
                  <Line type="monotone" dataKey="actual" stroke="#22d3ee" strokeWidth={2} dot={false} name="actual" />
                  <Line type="monotone" dataKey="predicted" stroke="#f59e0b" strokeWidth={2} dot={false} strokeDasharray="5 3" name="predicted" />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Feature Importance */}
          {result.feature_importance && result.feature_importance.length > 0 && (
            <div className="bg-card border border-border rounded-xl p-5">
              <h3 className="text-lg font-semibold mb-4">
                Feature Importance
                <span className="ml-2 text-xs text-muted-foreground font-normal">
                  ({MODEL_LABELS[result.best_model ?? ''] || result.best_model})
                </span>
              </h3>
              <ResponsiveContainer width="100%" height={Math.max(180, result.feature_importance.length * 28)}>
                <BarChart
                  layout="vertical"
                  data={result.feature_importance}
                  margin={{ top: 4, right: 40, left: 10, bottom: 4 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
                  <XAxis
                    type="number"
                    stroke="#64748b"
                    tick={{ fill: '#94a3b8', fontSize: 11 }}
                    tickFormatter={(v: number) => v.toFixed(1) + '%'}
                    domain={[0, 'dataMax']}
                  />
                  <YAxis
                    type="category"
                    dataKey="feature"
                    stroke="#64748b"
                    tick={{ fill: '#94a3b8', fontSize: 11 }}
                    width={130}
                  />
                  <Tooltip
                    contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--popover-foreground)' }}
                    formatter={(v: any) => [Number(v).toFixed(2) + '%', 'Importance']}
                  />
                  <Bar dataKey="importance_pct" radius={[0, 4, 4, 0]}>
                    {result.feature_importance.map((_, idx) => (
                      <Cell
                        key={idx}
                        fill={idx === 0 ? '#5A5AF6' : idx === 1 ? '#818cf8' : '#475569'}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Anomaly Detection */}
          {result.anomalies && result.anomalies.length > 0 && (
            <div className="bg-card border border-amber-800/40 rounded-xl p-5">
              <h3 className="text-lg font-semibold mb-1 text-warning">
                Anomalies Detected
                <span className="ml-2 text-xs font-normal text-warning/70">
                  ({result.anomalies.length} outlier{result.anomalies.length > 1 ? 's' : ''} capped by IQR)
                </span>
              </h3>
              <p className="text-xs text-muted-foreground mb-3">
                These data points exceeded the IQR fence and were capped before training.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-xs text-left">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground uppercase tracking-wide">
                      <th className="py-2 px-3">Date</th>
                      <th className="py-2 px-3">Original</th>
                      <th className="py-2 px-3">Capped To</th>
                      <th className="py-2 px-3">Direction</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.anomalies.slice(0, 20).map((a, i) => (
                      <tr key={i} className="border-b border-border">
                        <td className="py-2 px-3 text-muted-foreground">{a.date}</td>
                        <td className="py-2 px-3 text-destructive font-mono">{formatNumber(a.original_value)}</td>
                        <td className="py-2 px-3 text-muted-foreground font-mono">{formatNumber(a.capped_value)}</td>
                        <td className="py-2 px-3">
                          {a.direction === 'up'
                            ? <span className="text-destructive">High outlier</span>
                            : <span className="text-info">Low outlier</span>
                          }
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {result.anomalies.length > 20 && (
                  <p className="text-xs text-muted-foreground mt-2">
                    + {result.anomalies.length - 20} more anomalies not shown
                  </p>
                )}
              </div>
            </div>
          )}

      
        </div>
      )}

      {/* ── Model Guide Section ── */}
      <div className="mt-8 bg-card border border-border rounded-2xl overflow-hidden">
        <button
          className="w-full px-6 py-4 flex items-center justify-between text-left hover:bg-card transition-colors"
          onClick={() => setModelGuideOpen(!modelGuideOpen)}
        >
          <div className="flex items-center gap-3">
            <i className="fa-solid fa-graduation-cap text-primary" />
            <div>
              <h3 className="text-sm font-bold text-foreground">Model Guide by Category</h3>
              <p className="text-xs text-muted-foreground">Which models work best for different data types</p>
            </div>
          </div>
          <i className={`fa-solid fa-chevron-${modelGuideOpen ? 'up' : 'down'} text-muted-foreground`} />
        </button>

        {modelGuideOpen && (
          <div className="px-6 pb-6 space-y-6">
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-3">Recommended Models by Dataset Category</h4>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border/50">
                      <th className="text-left py-2 px-3 text-muted-foreground font-medium">Category</th>
                      <th className="text-left py-2 px-3 text-muted-foreground font-medium">Best Models</th>
                      <th className="text-left py-2 px-3 text-muted-foreground font-medium">Why</th>
                    </tr>
                  </thead>
                  <tbody className="text-muted-foreground">
                    {((modelCategoryStats?.static_recommendations
                      ? Object.entries(modelCategoryStats.static_recommendations)
                      : [
                          ['Sales', { models: ['CatBoost', 'LightGBM', 'ETS'], justification: 'Revenue/demand data has strong seasonality + nonlinear promotions; tree models capture exogenous effects.' }],
                          ['Marketing', { models: ['Prophet', 'SARIMAX'], justification: 'Campaign data has trend changes + weekly seasonality; Prophet handles changepoints well.' }],
                          ['Operations', { models: ['ETS', 'Seasonal Naive'], justification: 'Operational metrics are often smooth seasonal; simpler models avoid overfitting.' }],
                          ['HR', { models: ['ETS', 'Naive'], justification: 'Workforce metrics are stable/slowly trending; complex models overfit on small HR datasets.' }],
                          ['Business', { models: ['CatBoost', 'Prophet'], justification: 'General business KPIs vary; tree models adapt to feature-rich data; Prophet handles mixed patterns.' }],
                        ]
                    ) as [string, any][]).map(([cat, info]) => (
                      <tr key={cat} className="border-b border-border/50 hover:bg-card/50">
                        <td className="py-2.5 px-3 font-medium text-foreground">{cat}</td>
                        <td className="py-2.5 px-3">
                          <div className="flex flex-wrap gap-1.5">
                            {(info.models || []).map((m: string) => (
                              <span key={m} className="px-2 py-0.5 rounded-md bg-primary/15 text-primary text-xs font-medium">{m}</span>
                            ))}
                          </div>
                        </td>
                        <td className="py-2.5 px-3 text-xs text-muted-foreground">{info.justification}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {modelCategoryStats?.dynamic_stats && Object.keys(modelCategoryStats.dynamic_stats).length > 0 && (
              <div>
                <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-3">Your Forecast History by Category</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                  {Object.entries(modelCategoryStats.dynamic_stats).map(([cat, data]: [string, any]) => (
                    <div key={cat} className="bg-card border border-border rounded-xl p-4">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm font-semibold text-foreground">{cat}</span>
                        <span className="text-xs text-muted-foreground">{data.total_runs} runs</span>
                      </div>
                      <div className="space-y-1.5">
                        {(data.models || []).slice(0, 3).map((m: any) => (
                          <div key={m.model} className="flex items-center justify-between">
                            <span className="text-xs text-muted-foreground">{MODEL_LABELS[m.model] || m.model}</span>
                            <div className="flex items-center gap-2">
                              <div className="w-16 h-1.5 bg-muted rounded-full overflow-hidden">
                                <div
                                  className="h-full bg-primary rounded-full"
                                  style={{ width: `${m.win_rate}%` }}
                                />
                              </div>
                              <span className="text-xs text-muted-foreground w-10 text-right">{m.win_rate}%</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Column Relationships Mind Map ── */}
      <div className="mt-4 bg-card border border-border rounded-2xl overflow-hidden">
        <button
          className="w-full px-6 py-4 flex items-center justify-between text-left hover:bg-card transition-colors"
          onClick={() => setMindMapOpen(!mindMapOpen)}
        >
          <div className="flex items-center gap-3">
            <i className="fa-solid fa-diagram-project text-primary" />
            <div>
              <h3 className="text-sm font-bold text-foreground">Column Relationships</h3>
              <p className="text-xs text-muted-foreground">Interactive mind map showing how columns correlate with each other</p>
            </div>
          </div>
          <i className={`fa-solid fa-chevron-${mindMapOpen ? 'up' : 'down'} text-muted-foreground`} />
        </button>

        {mindMapOpen && datasetId && (
          <div className="px-6 pb-6">
            <ColumnMindMap
              datasetId={datasetId}
              targetColumn={targetColumn}
              onSelectFeature={(col) => {
                if (!featureColumns.includes(col)) {
                  setFeatureColumns([...featureColumns, col])
                  setFeaturesAutoSelected(false)
                }
              }}
            />
          </div>
        )}
      </div>
    </div>
  )
}
