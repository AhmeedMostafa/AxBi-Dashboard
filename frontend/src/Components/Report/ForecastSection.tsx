import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
    Area, CartesianGrid, ComposedChart, Legend, Line,
    ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { getForecastHistory, getForecastDetail } from '../../api'
import { LogoSpinner } from '../ui/LogoSpinner'

// ── Types (mirrors AIModel.tsx) ─────────────────────────────────────────────
type ForecastPoint = { date: string; value: number }
type IntervalPoint = { date: string; lower: number; upper: number }
type TestPoint = { date: string; actual: number; predicted: number }

type HistoryEntry = {
    id: string
    created_at: string
    target_column: string
    best_model: string | null
    best_wape: number | null
    best_mae: number | null
    best_rmse: number | null
    horizon: number
    frequency_used: string | null
}

type ForecastData = {
    forecast: ForecastPoint[]
    prediction_intervals: IntervalPoint[]
    test_comparison: TestPoint[]
}

type ChartRow = {
    date: string
    forecast?: number
    interval?: [number, number]
}

// ── Helpers ─────────────────────────────────────────────────────────────────
function buildHistoryChartData(forecast: ForecastPoint[], intervals: IntervalPoint[]): ChartRow[] {
    return (forecast || []).map((pt, i) => ({
        date: pt.date,
        forecast: pt.value,
        interval: intervals?.[i] ? [intervals[i].lower, intervals[i].upper] : undefined,
    }))
}

function formatNumber(v: number): string {
    if (v == null || !isFinite(v)) return '-'
    if (Math.abs(v) >= 1000) return Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(v)
    return Intl.NumberFormat('en', { maximumFractionDigits: 2 }).format(v)
}

function SectionHeader({ icon, title }: { icon: string; title: string }) {
    return (
        <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-primary/15 flex items-center justify-center">
                <i className={`${icon} text-primary text-sm`}></i>
            </div>
            <h2 className="text-lg font-bold text-foreground">{title}</h2>
        </div>
    )
}

function Stat({ label, value }: { label: string; value: string }) {
    return (
        <div className="bg-card border border-border rounded-xl p-4">
            <p className="text-[10px] uppercase tracking-widest text-muted-foreground font-semibold mb-1">{label}</p>
            <p className="text-lg font-extrabold text-foreground truncate" title={value}>{value}</p>
        </div>
    )
}

// ── Component ────────────────────────────────────────────────────────────────
export default function ForecastSection({ datasetId }: { datasetId: string }) {
    const navigate = useNavigate()
    const [loading, setLoading] = useState(true)
    const [entry, setEntry] = useState<HistoryEntry | null>(null)
    const [fd, setFd] = useState<ForecastData | null>(null)

    useEffect(() => {
        if (!datasetId) { setLoading(false); return }
        let cancelled = false
        const load = async () => {
            setLoading(true)
            try {
                const hist = await getForecastHistory(datasetId)
                const list: HistoryEntry[] = hist?.forecasts || []
                if (!list.length) {
                    if (!cancelled) { setEntry(null); setFd(null) }
                    return
                }
                const latest = list[0]
                const detail = await getForecastDetail(latest.id)
                if (!cancelled) {
                    setEntry(latest)
                    setFd(detail?.forecast_data || null)
                }
            } catch {
                if (!cancelled) { setEntry(null); setFd(null) }
            } finally {
                if (!cancelled) setLoading(false)
            }
        }
        void load()
        return () => { cancelled = true }
    }, [datasetId])

    if (loading) {
        return (
            <div className="mt-10">
                <SectionHeader icon="fa-solid fa-chart-line" title="Forecast" />
                <div className="flex items-center justify-center h-40"><LogoSpinner size={32} /></div>
            </div>
        )
    }

    // Empty state — no forecast run for this dataset yet
    if (!entry || !fd || !fd.forecast?.length) {
        return (
            <div className="mt-10">
                <SectionHeader icon="fa-solid fa-chart-line" title="Forecast" />
                <div className="mt-4 bg-card border border-border rounded-2xl p-8 flex flex-col items-center text-center gap-3">
                    <div className="w-12 h-12 rounded-xl bg-muted flex items-center justify-center">
                        <i className="fa-solid fa-chart-line text-primary text-lg"></i>
                    </div>
                    <p className="text-sm font-semibold text-foreground">No forecast yet for this dataset</p>
                    <p className="text-xs text-muted-foreground max-w-sm">Run a forecast to project this dataset's key metric into the future — the result will appear here.</p>
                    <button
                        onClick={() => navigate('/AI-Insights')}
                        className="mt-1 flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-xl text-xs font-semibold hover:opacity-90 transition-opacity cursor-pointer"
                    >
                        <i className="fa-solid fa-wand-magic-sparkles"></i>
                        Run a forecast
                    </button>
                </div>
            </div>
        )
    }

    const chartData = buildHistoryChartData(fd.forecast, fd.prediction_intervals)
    // WAPE is a fraction (0–1) error metric; accuracy = 100 − WAPE% (mirrors AIModel).
    const accuracy = entry.best_wape != null ? Math.max(0, 100 - entry.best_wape * 100) : null
    const accuracyStr = accuracy != null ? `${accuracy.toFixed(1)}%` : '—'
    const mae = entry.best_mae != null ? formatNumber(entry.best_mae) : '—'
    const test = fd.test_comparison || []

    return (
        <div className="mt-10">
            <div className="flex items-center justify-between">
                <SectionHeader icon="fa-solid fa-chart-line" title="Forecast" />
                <button
                    onClick={() => navigate('/AI-Insights')}
                    className="flex items-center gap-2 px-3 py-1.5 bg-card border border-border hover:border-primary/40 rounded-xl text-xs font-semibold text-muted-foreground hover:text-foreground transition-all cursor-pointer"
                >
                    <i className="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                    Open AI Insights
                </button>
            </div>
            <p className="text-sm text-muted-foreground mt-1 mb-4">
                Most recent forecast for this dataset — projected {prettify(entry.target_column)} with a 95% confidence band.
            </p>

            {/* Summary */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
                <Stat label="Target" value={prettify(entry.target_column)} />
                <Stat label="Best Model" value={entry.best_model || '—'} />
                <Stat label="Accuracy" value={accuracyStr} />
                <Stat label="Horizon" value={`${entry.horizon}${entry.frequency_used ? ' · ' + entry.frequency_used : ''}`} />
            </div>

            {/* Forecast projection chart */}
            <div className="bg-card border border-border rounded-2xl overflow-hidden">
                <div className="px-6 pt-5 pb-2">
                    <h3 className="text-base font-bold text-foreground">Projection</h3>
                </div>
                <div className="h-[360px] px-3 pb-2">
                    <ResponsiveContainer width="100%" height="100%">
                        <ComposedChart data={chartData} margin={{ top: 10, right: 24, left: 8, bottom: 5 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                            <XAxis dataKey="date" stroke="#64748b" tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={(v: string) => String(v).slice(5)} />
                            <YAxis stroke="#64748b" tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={(v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v))} />
                            <Tooltip
                                contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--popover-foreground)' }}
                                labelStyle={{ color: 'var(--popover-foreground)' }}
                                itemStyle={{ color: 'var(--popover-foreground)' }}
                                formatter={(value: unknown, name: unknown) => {
                                    if (Array.isArray(value)) return [`${formatNumber(value[0])} – ${formatNumber(value[1])}`, 'Confidence']
                                    return [formatNumber(value as number), name === 'forecast' ? 'Forecast' : (name as string)]
                                }}
                            />
                            <Legend />
                            <Area type="monotone" dataKey="interval" fill="#5A5AF6" fillOpacity={0.12} stroke="none" name="95% Confidence" legendType="rect" />
                            <Line type="monotone" dataKey="forecast" stroke="#5A5AF6" strokeWidth={2} strokeDasharray="6 3" dot={false} name="Forecast" connectNulls={false} />
                        </ComposedChart>
                    </ResponsiveContainer>
                </div>
            </div>

            {/* Accuracy: actual vs predicted on the holdout */}
            {test.length > 0 && (
                <div className="mt-6 bg-card border border-border rounded-2xl overflow-hidden">
                    <div className="px-6 pt-5 pb-2">
                        <h3 className="text-base font-bold text-foreground">Actual vs Predicted (holdout)</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">How the winning model tracked real values on unseen test data{mae !== '—' ? ` · MAE ${mae}` : ''}.</p>
                    </div>
                    <div className="h-[280px] px-3 pb-2">
                        <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart data={test} margin={{ top: 10, right: 24, left: 8, bottom: 5 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                                <XAxis dataKey="date" stroke="#64748b" tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={(v: string) => String(v).slice(5)} />
                                <YAxis stroke="#64748b" tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={(v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v))} />
                                <Tooltip
                                    contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--popover-foreground)' }}
                                    labelStyle={{ color: 'var(--popover-foreground)' }}
                                    itemStyle={{ color: 'var(--popover-foreground)' }}
                                    formatter={(value: unknown, name: unknown) => [formatNumber(value as number), name === 'actual' ? 'Actual' : 'Predicted']}
                                />
                                <Legend />
                                <Line type="monotone" dataKey="actual" stroke="#22d3ee" strokeWidth={2} dot={false} name="Actual" />
                                <Line type="monotone" dataKey="predicted" stroke="#f59e0b" strokeWidth={2} strokeDasharray="6 3" dot={false} name="Predicted" />
                            </ComposedChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            )}
        </div>
    )
}

function prettify(name: string): string {
    return (name || '').replace(/[-_]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}
