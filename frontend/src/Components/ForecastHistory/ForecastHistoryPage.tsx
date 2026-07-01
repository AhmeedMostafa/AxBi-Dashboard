import { useEffect, useState } from 'react'
import { getForecastHistory, deleteForecast, listDatasets, getForecastDetail } from '../../api'
import { LoadingSkeleton } from '../ui/LoadingSkeleton'
import { LogoSpinner } from '../ui/LogoSpinner'
import {
  Area,
  ComposedChart,
  CartesianGrid,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

interface Dataset {
  id: string
  filename: string
  category?: string
  status?: string
}

interface ForecastRecord {
  id: string
  dataset_id: string
  best_model: string | null
  best_mae: number | null
  best_rmse: number | null
  best_wape: number | null
  target_column: string | null
  time_column: string | null
  horizon: number | null
  frequency_used: string | null
  duration_ms: number | null
  created_at: string
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

interface ForecastDetail {
  forecast_data?: {
    forecast: ForecastPoint[]
    prediction_intervals: IntervalPoint[]
    test_comparison: { date: string; actual: number; predicted: number }[]
  } | null
  error?: boolean
}

const MODEL_LABELS: Record<string, string> = {
  naive: 'Naive',
  seasonal_naive: 'Seasonal Naive',
  ets: 'ETS',
  sarimax: 'SARIMAX',
  catboost: 'CatBoost',
  lightgbm: 'LightGBM',
  prophet: 'Prophet',
}

function formatNumber(n: number | null | undefined): string {
  if (n === undefined || n === null) return '—'
  if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
  if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 1 })
  return n.toFixed(3)
}

function buildForecastChartData(
  forecast: ForecastPoint[],
  intervals: IntervalPoint[]
): { date: string; forecast?: number; interval?: [number, number] }[] {
  return forecast.map((pt, i) => ({
    date: pt.date,
    forecast: pt.value,
    interval: intervals[i] ? [intervals[i].lower, intervals[i].upper] : undefined,
  }))
}

export default function ForecastHistoryPage() {
  const [datasets, setDatasets]             = useState<Dataset[]>([])
  const [datasetsLoading, setDatasetsLoading] = useState(true)
  const [selectedId, setSelectedId]         = useState<string | null>(null)
  const [forecasts, setForecasts]           = useState<ForecastRecord[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError]     = useState<string | null>(null)
  const [deletingId, setDeletingId]         = useState<string | null>(null)
  const [expandedId, setExpandedId]         = useState<string | null>(null)
  const [details, setDetails]               = useState<Record<string, ForecastDetail>>({})
  const [detailLoading, setDetailLoading]   = useState<Record<string, boolean>>({})

  useEffect(() => {
    listDatasets()
      .then((data: any) => setDatasets(data?.datasets || data || []))
      .catch(() => setDatasets([]))
      .finally(() => setDatasetsLoading(false))
  }, [])

  const selectProject = async (id: string) => {
    if (id === selectedId) return
    setSelectedId(id)
    setForecasts([])
    setDetails({})
    setExpandedId(null)
    setHistoryError(null)
    setHistoryLoading(true)
    try {
      const data = await getForecastHistory(id)
      setForecasts(data?.forecasts || [])
    } catch (e: any) {
      setHistoryError(e?.response?.data?.error || e?.message || 'Failed to load history')
    } finally {
      setHistoryLoading(false)
    }
  }

  const loadDetail = async (id: string) => {
    if (details[id] || detailLoading[id]) return
    setDetailLoading(prev => ({ ...prev, [id]: true }))
    try {
      const record = await getForecastDetail(id)
      const fd = record?.forecast_data
      setDetails(prev => ({
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
      setDetails(prev => ({ ...prev, [id]: { error: true } }))
    } finally {
      setDetailLoading(prev => ({ ...prev, [id]: false }))
    }
  }

  const toggleExpand = (id: string) => {
    if (expandedId === id) {
      setExpandedId(null)
    } else {
      setExpandedId(id)
      loadDetail(id)
    }
  }

  const handleDelete = async (forecastId: string) => {
    if (!window.confirm('Delete this forecast run? This cannot be undone.')) return
    setDeletingId(forecastId)
    try {
      await deleteForecast(forecastId)
      setForecasts(prev => prev.filter(f => f.id !== forecastId))
      setDetails(prev => { const n = { ...prev }; delete n[forecastId]; return n })
      if (expandedId === forecastId) setExpandedId(null)
    } catch (e: any) {
      alert(e?.response?.data?.error || e?.message || 'Failed to delete forecast')
    } finally {
      setDeletingId(null)
    }
  }

  const selectedDataset = datasets.find(d => d.id === selectedId)

  return (
    <div className="p-6 text-foreground">
      <h2 className="text-2xl font-bold mb-1">Forecast History</h2>
      <p className="text-muted-foreground text-sm mb-6">Select a project to view its forecast runs.</p>

      {/* ── Project cards ── */}
      {datasetsLoading ? (
        <div className="flex flex-wrap gap-3 mb-8">
          <LoadingSkeleton variant="table-row" count={3} />
        </div>
      ) : datasets.length === 0 ? (
        <p className="text-muted-foreground text-sm">No datasets found. Upload a file first.</p>
      ) : (
        <div className="flex flex-wrap gap-3 mb-8">
          {datasets.map(ds => {
            const active = ds.id === selectedId
            return (
              <button
                key={ds.id}
                onClick={() => selectProject(ds.id)}
                className={`flex items-center gap-3 px-4 py-3 rounded-xl border text-left transition-all ${
                  active
                    ? 'bg-primary/15 border-primary text-primary-foreground shadow-[0_0_0_1px_#5A5AF6]'
                    : 'bg-card border-border text-muted-foreground hover:border-primary/50 hover:text-foreground'
                }`}
              >
                <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 text-sm font-bold ${
                  active ? 'bg-primary text-primary-foreground' : 'bg-muted text-primary'
                }`}>
                  {ds.filename.slice(0, 2).toUpperCase()}
                </div>
                <div>
                  <p className="text-sm font-semibold leading-tight max-w-[180px] truncate">{ds.filename}</p>
                  {ds.category && <p className="text-xs text-muted-foreground capitalize">{ds.category}</p>}
                </div>
                {active && (
                  <svg className="w-4 h-4 text-primary shrink-0 ml-1" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd"/>
                  </svg>
                )}
              </button>
            )
          })}
        </div>
      )}

      {/* ── History panel ── */}
      {selectedId && (
        <>
          <div className="flex items-center gap-3 mb-4">
            <h3 className="text-lg font-semibold">
              {selectedDataset?.filename ?? 'Forecast Runs'}
            </h3>
            {historyLoading && (
              <div className="flex items-center gap-2">
                <LogoSpinner size={16} />
                <span className="text-xs text-muted-foreground">Loading...</span>
              </div>
            )}
          </div>

          {historyError && (
            <div className="bg-destructive/10 border border-destructive/30 rounded-lg p-3 text-destructive mb-4 text-sm">
              {historyError}
            </div>
          )}

          {!historyLoading && !historyError && forecasts.length === 0 && (
            <p className="text-muted-foreground text-sm">No forecast runs found for this project yet.</p>
          )}

          <div className="space-y-3 max-w-5xl">
            {forecasts.map(fc => {
              const acc = fc.best_wape != null ? Math.max(0, 100 - fc.best_wape * 100) : null
              const accColor = acc == null ? 'text-muted-foreground' : acc >= 95 ? 'text-success' : acc >= 85 ? 'text-warning' : 'text-destructive'
              const isExpanded = expandedId === fc.id
              const detail = details[fc.id]
              const isDetailLoading = detailLoading[fc.id]

              return (
                <div key={fc.id} className="bg-card border border-border rounded-xl overflow-hidden">
                  {/* Summary row */}
                  <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-4">
                    {/* Clickable area */}
                    <button
                      className="flex flex-wrap items-center gap-x-6 gap-y-2 flex-1 text-left"
                      onClick={() => toggleExpand(fc.id)}
                    >
                      {/* Model */}
                      <div className="min-w-[140px]">
                        <p className="text-xs text-muted-foreground mb-0.5">Best Model</p>
                        <p className="text-sm font-bold text-primary">
                          {MODEL_LABELS[fc.best_model ?? ''] || fc.best_model || '—'}
                        </p>
                      </div>

                      {/* Accuracy */}
                      <div className="min-w-[90px]">
                        <p className="text-xs text-muted-foreground mb-0.5">Accuracy</p>
                        <p className={`text-sm font-bold ${accColor}`}>
                          {acc != null ? acc.toFixed(1) + '%' : '—'}
                        </p>
                      </div>

                      {/* MAE */}
                      <div className="min-w-[80px]">
                        <p className="text-xs text-muted-foreground mb-0.5">MAE</p>
                        <p className="text-sm font-semibold">{formatNumber(fc.best_mae)}</p>
                      </div>

                      {/* RMSE */}
                      <div className="min-w-[80px]">
                        <p className="text-xs text-muted-foreground mb-0.5">RMSE</p>
                        <p className="text-sm font-semibold">{formatNumber(fc.best_rmse)}</p>
                      </div>

                      {/* Target */}
                      {fc.target_column && (
                        <div className="min-w-[100px]">
                          <p className="text-xs text-muted-foreground mb-0.5">Target</p>
                          <p className="text-sm text-muted-foreground">{fc.target_column}</p>
                        </div>
                      )}

                      {/* Horizon */}
                      {fc.horizon != null && (
                        <div className="min-w-[60px]">
                          <p className="text-xs text-muted-foreground mb-0.5">Horizon</p>
                          <p className="text-sm text-muted-foreground">{fc.horizon}</p>
                        </div>
                      )}

                      {/* Date + duration */}
                      <div className="ml-auto text-right">
                        <p className="text-xs text-muted-foreground">
                          {new Date(fc.created_at).toLocaleString(undefined, {
                            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                          })}
                        </p>
                        {fc.duration_ms != null && (
                          <p className="text-xs text-muted-foreground mt-0.5">{(fc.duration_ms / 1000).toFixed(1)}s</p>
                        )}
                      </div>

                      <i className={`fa-solid fa-chevron-${isExpanded ? 'up' : 'down'} text-muted-foreground text-xs`}></i>
                    </button>

                    {/* Delete */}
                    <button
                      onClick={() => handleDelete(fc.id)}
                      disabled={deletingId === fc.id}
                      className="text-xs bg-destructive/10 hover:bg-destructive/10 border border-destructive/30 text-destructive hover:text-destructive px-3 py-1.5 rounded-lg disabled:opacity-60 transition-colors shrink-0"
                    >
                      {deletingId === fc.id ? 'Deleting...' : 'Delete'}
                    </button>
                  </div>

                  {/* Expanded detail */}
                  {isExpanded && (
                    <div className="border-t border-border px-5 pb-5 bg-card/40">
                      {isDetailLoading && (
                        <div className="py-5 space-y-3">
                          <div className="flex gap-6">
                            <LoadingSkeleton variant="kpi" count={4} />
                          </div>
                          <LoadingSkeleton variant="chart" count={1} />
                        </div>
                      )}

                      {!isDetailLoading && detail?.error && (
                        <p className="text-xs text-destructive py-5">Failed to load forecast data.</p>
                      )}

                      {!isDetailLoading && detail && !detail.error && !detail.forecast_data && (
                        <div className="py-5">
                          <p className="text-xs text-muted-foreground">
                            Chart not available — this run was saved before forecast data storage was enabled.
                            Run a new forecast to see the predicted period graph here.
                          </p>
                          {/* Still show metrics from record */}
                          <div className="flex flex-wrap gap-6 mt-4">
                            <div>
                              <span className="text-xs text-muted-foreground uppercase tracking-wide">Accuracy</span>
                              <p className={`text-xl font-bold ${accColor}`}>{acc != null ? acc.toFixed(1) + '%' : '—'}</p>
                              <span className="text-xs text-muted-foreground">100% − WAPE</span>
                            </div>
                            <div>
                              <span className="text-xs text-muted-foreground uppercase tracking-wide">MAE</span>
                              <p className="text-xl font-semibold">{formatNumber(fc.best_mae)}</p>
                            </div>
                            <div>
                              <span className="text-xs text-muted-foreground uppercase tracking-wide">RMSE</span>
                              <p className="text-xl font-semibold">{formatNumber(fc.best_rmse)}</p>
                            </div>
                          </div>
                        </div>
                      )}

                      {!isDetailLoading && detail?.forecast_data && detail.forecast_data.forecast.length > 0 && (() => {
                        const chartData = buildForecastChartData(
                          detail.forecast_data.forecast,
                          detail.forecast_data.prediction_intervals
                        )
                        const tc = detail.forecast_data.test_comparison

                        return (
                          <div className="pt-4">
                            {/* Metrics strip */}
                            <div className="flex flex-wrap gap-6 mb-5">
                              <div>
                                <span className="text-xs text-muted-foreground uppercase tracking-wide">Accuracy</span>
                                <p className={`text-2xl font-bold ${accColor}`}>
                                  {acc != null ? acc.toFixed(1) + '%' : '—'}
                                </p>
                                <span className="text-xs text-muted-foreground">100% − WAPE</span>
                              </div>
                              <div>
                                <span className="text-xs text-muted-foreground uppercase tracking-wide">MAE</span>
                                <p className="text-2xl font-semibold">{formatNumber(fc.best_mae)}</p>
                                <span className="text-xs text-muted-foreground">avg error / period</span>
                              </div>
                              <div>
                                <span className="text-xs text-muted-foreground uppercase tracking-wide">RMSE</span>
                                <p className="text-2xl font-semibold">{formatNumber(fc.best_rmse)}</p>
                                <span className="text-xs text-muted-foreground">penalizes large errors</span>
                              </div>
                              <div>
                                <span className="text-xs text-muted-foreground uppercase tracking-wide">Horizon</span>
                                <p className="text-2xl font-semibold">{fc.horizon ?? '—'}</p>
                                <span className="text-xs text-muted-foreground">{fc.frequency_used ?? 'auto'} freq</span>
                              </div>
                              <div>
                                <span className="text-xs text-muted-foreground uppercase tracking-wide">Points</span>
                                <p className="text-2xl font-semibold">{detail.forecast_data.forecast.length}</p>
                                <span className="text-xs text-muted-foreground">forecast periods</span>
                              </div>
                            </div>

                            {/* Forecast chart — predicted period */}
                            <p className="text-xs text-muted-foreground mb-2 uppercase tracking-wide">Predicted period</p>
                            <ResponsiveContainer width="100%" height={260}>
                              <ComposedChart data={chartData} margin={{ top: 4, right: 20, left: 10, bottom: 4 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                                <XAxis
                                  dataKey="date"
                                  stroke="#64748b"
                                  tick={{ fill: '#94a3b8', fontSize: 10 }}
                                  tickFormatter={(v: string) => v.slice(5)}
                                />
                                <YAxis
                                  stroke="#64748b"
                                  tick={{ fill: '#94a3b8', fontSize: 10 }}
                                  tickFormatter={(v: number) =>
                                    v >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M`
                                    : v >= 1000 ? `${(v / 1000).toFixed(1)}k`
                                    : String(v)
                                  }
                                />
                                <Tooltip
                                  contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--popover-foreground)' }}
                                  labelStyle={{ color: '#e2e8f0' }}
                                  formatter={(value: any, name: any) => {
                                    if (Array.isArray(value)) return [`${value[0].toLocaleString()} – ${value[1].toLocaleString()}`, 'Confidence']
                                    return [value.toLocaleString(undefined, { maximumFractionDigits: 2 }), name === 'forecast' ? 'Forecast' : name]
                                  }}
                                />
                                <Legend />
                                <Area
                                  type="monotone"
                                  dataKey="interval"
                                  fill="#5A5AF6"
                                  fillOpacity={0.15}
                                  stroke="none"
                                  name="95% Confidence"
                                  legendType="rect"
                                />
                                <Line
                                  type="monotone"
                                  dataKey="forecast"
                                  stroke="#5A5AF6"
                                  strokeWidth={2.5}
                                  strokeDasharray="6 3"
                                  dot={false}
                                  name="Forecast"
                                />
                              </ComposedChart>
                            </ResponsiveContainer>

                            {/* Test set: actual vs predicted */}
                            {tc.length > 0 && (
                              <div className="mt-6">
                                <p className="text-xs text-muted-foreground mb-2 uppercase tracking-wide">
                                  Test set — actual vs predicted ({tc.length} holdout periods)
                                </p>
                                <ResponsiveContainer width="100%" height={200}>
                                  <ComposedChart data={tc} margin={{ top: 4, right: 20, left: 10, bottom: 4 }}>
                                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                                    <XAxis dataKey="date" stroke="#64748b" tick={{ fill: '#94a3b8', fontSize: 10 }} tickFormatter={(v: string) => v.slice(5)} />
                                    <YAxis stroke="#64748b" tick={{ fill: '#94a3b8', fontSize: 10 }} tickFormatter={(v: number) => v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)} />
                                    <Tooltip
                                      contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--popover-foreground)' }}
                                      labelStyle={{ color: '#e2e8f0' }}
                                      formatter={(value: any, name: any) => [
                                        value.toLocaleString(undefined, { maximumFractionDigits: 2 }),
                                        name === 'actual' ? 'Actual' : 'Predicted'
                                      ]}
                                    />
                                    <Legend />
                                    <Line type="monotone" dataKey="actual" stroke="#22d3ee" strokeWidth={2} dot={false} name="actual" />
                                    <Line type="monotone" dataKey="predicted" stroke="#f59e0b" strokeWidth={2} dot={false} strokeDasharray="5 3" name="predicted" />
                                  </ComposedChart>
                                </ResponsiveContainer>
                              </div>
                            )}
                          </div>
                        )
                      })()}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
