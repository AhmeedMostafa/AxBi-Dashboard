import { useEffect, useMemo, useState } from 'react'
import {
    Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart,
    Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer, Scatter,
    ScatterChart, Tooltip, XAxis, YAxis,
} from 'recharts'
import { exportPdf, getDatasetRows, getDatasetDashboard, runSegmentation, aggregateCharts, updateDatasetCategory, detectDatasetCategory } from '../../api'
import { useSpeechSynthesis } from '../../hooks/useSpeechSynthesis'
import AudioOverviewButton from '../common/AudioOverviewButton'
import { LogoSpinner } from '../ui/LogoSpinner'
import toast from 'react-hot-toast'

const LAST_DATASET_ID_KEY = 'bi_dashboard_last_dataset_id'
const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4']

type ReportSection = { title: string; content: string }

type ChartSpec = {
    chart_type: string
    title: string
    x_axis: string | null
    y_axis: string | null
    columns: string[]
    reason: string
}

type ColumnMeta = {
    clean_name?: string
    original_name?: string
    column_key?: string
    display_name?: string
    data_type?: string
    technical_stats?: Record<string, unknown> | string | null
    ai_profile?: Record<string, unknown> | string | null
}

type DatasetRow = { row_index: number; row_data: Record<string, unknown> }

type AggregatedPoint = { label?: string; value?: number; name?: string; x?: number; y?: number; line?: number; line_name?: string; cumulative?: number; max?: number; unit?: string }

type CategoryDetection = {
    resolved_category?: string
    detected_category?: string
    user_category?: string
    confidence?: number
    overridden?: boolean
    mismatch_warning?: boolean
    user_confirmed?: boolean
}

type SegmentData = {
    name: string
    size: number
    percentage: number
    avg_metrics: Record<string, number | null>
    top_entities: string[]
}

type SegmentChart = {
    chart_type: string
    title: string
    data: Array<Record<string, unknown>>
}

type SegmentInsight = { title: string; content: string }

type SegmentationResult = {
    status: string
    method: string
    method_label: string
    entity_column: string | null
    category: string
    segments: SegmentData[]
    insights: SegmentInsight[]
    charts: SegmentChart[]
    scatter_data?: Array<{ x: number; y: number; cluster: number; entity: string }>
    generated_at: string
    duration_ms: number
    method_meta?: Record<string, unknown>
}

function parseJson(v: unknown): Record<string, unknown> {
    if (!v) return {}
    if (typeof v === 'object' && !Array.isArray(v)) return v as Record<string, unknown>
    if (typeof v === 'string') {
        try { const p = JSON.parse(v); return typeof p === 'object' && p ? p : {} } catch { return {} }
    }
    return {}
}

function parseNumeric(v: unknown): number | null {
    if (typeof v === 'number') return isFinite(v) ? v : null
    if (typeof v === 'string') {
        const n = Number(v.replace(/,/g, '').trim())
        return isFinite(n) ? n : null
    }
    return null
}

function normalizeLabel(v: unknown): string | null {
    if (v == null) return null
    const t = String(v).trim()
    return t || null
}

function normalizeDateLabel(v: unknown): string | null {
    const t = normalizeLabel(v)
    if (!t) return null
    const d = new Date(t)
    return isNaN(d.getTime()) ? t : d.toISOString().slice(0, 10)
}

function isDateColumn(key: string | null, cols: Map<string, ColumnMeta>): boolean {
    if (!key) return false
    const m = cols.get(key)
    if (!m) return false
    const dt = String(m.data_type ?? '').toLowerCase()
    const role = String((parseJson(m.ai_profile) as Record<string, unknown>).role ?? '').toLowerCase()
    return dt.includes('date') || role === 'date' || role === 'time'
}

function buildGrouped(rows: DatasetRow[], xKey: string | null, yKey: string | null, cols: Map<string, ColumnMeta>, countMode = false) {
    if (!xKey) return [] as { label: string; value: number }[]
    const dateAxis = isDateColumn(xKey, cols)
    const map = new Map<string, { label: string; value: number; t: number | null }>()
    for (const row of rows) {
        const raw = row.row_data[xKey]
        const label = dateAxis ? normalizeDateLabel(raw) : normalizeLabel(raw)
        if (!label) continue
        const num = yKey ? parseNumeric(row.row_data[yKey]) : null
        if (num == null && !countMode) continue
        const c = num ?? 1
        const existing = map.get(label)
        if (existing) { existing.value += c; continue }
        const t = dateAxis ? new Date(label).getTime() : null
        map.set(label, { label, value: c, t: isNaN(t as number) ? null : t })
    }
    const out = [...map.values()]
    if (dateAxis) out.sort((a, b) => (a.t ?? 0) - (b.t ?? 0))
    else out.sort((a, b) => b.value - a.value)
    return out
}

function compactNumber(v: number): string {
    if (!isFinite(v)) return '-'
    return Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(v)
}

function prettify(name: string): string {
    return name.replace(/[-_]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function getRoleConfig(role: string): { label: string; color: string; icon: string } {
    const r = role.toLowerCase()
    if (r.includes('time') || r.includes('date')) return { label: 'Date / Time', color: 'bg-info/15 text-info', icon: 'fa-solid fa-calendar' }
    if (r.includes('metric') || r.includes('measure') || r.includes('numeric') || r.includes('kpi')) return { label: 'Metric', color: 'bg-emerald-500/20 text-success', icon: 'fa-solid fa-chart-line' }
    if (r.includes('entity') || r.includes('id') || r.includes('identifier') || r.includes('key')) return { label: 'Identifier', color: 'bg-purple-500/20 text-purple-300', icon: 'fa-solid fa-fingerprint' }
    if (r.includes('categor') || r.includes('dimension') || r.includes('group')) return { label: 'Category', color: 'bg-warning/15 text-warning', icon: 'fa-solid fa-tag' }
    if (r.includes('target') || r.includes('label')) return { label: 'Target', color: 'bg-red-500/20 text-destructive', icon: 'fa-solid fa-bullseye' }
    return { label: 'Field', color: 'bg-gray-500/20 text-muted-foreground', icon: 'fa-solid fa-circle-dot' }
}

export default function ReportPage() {
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)
    const [sections, setSections] = useState<ReportSection[]>([])
    const [aiDepartment, setAiDepartment] = useState('')
    const [userCategory, setUserCategory] = useState('')
    const [generatedAt, setGeneratedAt] = useState('')
    const [title, setTitle] = useState('')
    const [datasetId, setDatasetId] = useState('')
    const [exporting, setExporting] = useState(false)
    const [charts, setCharts] = useState<ChartSpec[]>([])
    const [columns, setColumns] = useState<ColumnMeta[]>([])
    const [rows, setRows] = useState<DatasetRow[]>([])
    const [fileName, setFileName] = useState('')
    const [rowCount, setRowCount] = useState<string>('')
    const [colCount, setColCount] = useState<string>('')
    const [segmentation, setSegmentation] = useState<SegmentationResult | null>(null)
    const [segLoading, setSegLoading] = useState(false)
    const [segError, setSegError] = useState<string | null>(null)
    const [aggResults, setAggResults] = useState<AggregatedPoint[][]>([])
    const [catDetection, setCatDetection] = useState<CategoryDetection | null>(null)
    const [catBannerDismissed, setCatBannerDismissed] = useState(false)
    const [catUpdating, setCatUpdating] = useState(false)

    const { isSupported: ttsSupported } = useSpeechSynthesis();

    const columnsByKey = useMemo(() => {
        const m = new Map<string, ColumnMeta>()
        for (const c of columns) {
            const k = c.column_key ?? c.clean_name ?? c.original_name
            if (k) m.set(k, c)
        }
        return m
    }, [columns])

    useEffect(() => {
        const savedId = localStorage.getItem(LAST_DATASET_ID_KEY)
        if (!savedId) {
            setError('No dataset found. Please upload and process a file first.')
            setLoading(false)
            return
        }
        setDatasetId(savedId)
        loadReport(savedId)
    }, [])

    useEffect(() => {
        if (!datasetId || !charts.length) return
        const vizOnly = charts.filter(c => c.chart_type !== 'kpi_card')
        if (!vizOnly.length) return
        aggregateCharts(datasetId, vizOnly)
            .then((res: { results?: Array<{ data?: AggregatedPoint[] }> }) => {
                setAggResults(
                    Array.isArray(res?.results)
                        ? res.results.map(r => r.data ?? [])
                        : []
                )
            })
            .catch(() => setAggResults([]))
    }, [datasetId, charts])

    const loadReport = async (dsId: string) => {
        setLoading(true)
        setError(null)
        try {
            // Use getDatasetDashboard directly — more reliable than job status lookup
            const res = await getDatasetDashboard(dsId)
            if (!res || res.status !== 'completed') {
                setError('Dataset processing is not complete yet. Please wait for the pipeline to finish.')
                return
            }

            const gc = res.data?.global_context
            if (!gc) { setError('Dataset processing is not complete yet.'); return }

            const step8 = gc.step8
            const step7 = gc.step7
            if (!step8?.sections?.length) {
                setError('No AI report available. Pipeline Step 8 may not have completed.')
                return
            }

            const cols: ColumnMeta[] = res.columns || []
            const fi = res.data?.file_info || {}

            // Prefer resolved_category from AI detection; fall back to step8.department, then category_hint
            const catDet = gc.category_detection || null
            const resolvedDept = catDet?.resolved_category || step8.department || res.data?.category_hint || 'Business'

            setSections(step8.sections)
            setAiDepartment(resolvedDept)
            setUserCategory(res.data?.category_hint || '')
            setGeneratedAt(step8.generated_at || '')
            setTitle(step7?.suggested_title || `${resolvedDept} Report`)
            setCharts(step7?.suggested_charts || [])
            setColumns(cols)
            setFileName(res.data?.file_name || '')
            setRowCount(String(fi.row_count ?? ''))
            setColCount(String(fi.column_count ?? cols.length ?? ''))

            // Category detection info
            if (catDet) {
                setCatDetection(catDet)
            } else {
                // Dataset was processed before category detection was added — run it now silently
                detectDatasetCategory(dsId)
                    .then((result: { category_detection?: CategoryDetection }) => {
                        const det = result?.category_detection
                        if (!det) return
                        setCatDetection(det)
                        // Update department display with resolved value
                        const resolved = det.resolved_category
                        if (resolved) {
                            setAiDepartment(resolved)
                            setTitle(prev => {
                                // Only update title if it still contains the old department
                                return prev
                            })
                        }
                    })
                    .catch(() => { /* silent — detection is best-effort */ })
            }

            // Load existing segmentation if available
            if (gc.segmentation?.status === 'completed') {
                setSegmentation(gc.segmentation)
            }

            try {
                const rowRes = await getDatasetRows(dsId, { limit: 2000, offset: 0 })
                const parsed = (rowRes?.rows || []).map((r: { row_index?: number; row_data?: unknown }) => ({
                    row_index: r.row_index ?? 0,
                    row_data: (r.row_data && typeof r.row_data === 'object' ? r.row_data : {}) as Record<string, unknown>,
                }))
                setRows(parsed)
                // Use total_rows from rows response as fallback if file_info didn't have it
                if (!fi.row_count && rowRes?.total_rows) {
                    setRowCount(String(rowRes.total_rows))
                }
            } catch { /* charts will be empty but report still shows */ }
        } catch (err: unknown) {
            const e = err as { response?: { data?: { message?: string; error?: string } }; message?: string }
            setError(e?.response?.data?.message || e?.response?.data?.error || e?.message || 'Failed to load report.')
        } finally {
            setLoading(false)
        }
    }

    const handleExportPdf = async () => {
        if (!datasetId || exporting) return
        setExporting(true)
        const loadingToast = toast.loading('Generating PDF with charts — this may take a moment...')
        try {
            const blob = await exportPdf(datasetId)
            toast.dismiss(loadingToast)
            const url = window.URL.createObjectURL(blob)
            const link = document.createElement('a')
            link.href = url
            link.download = `${title.replace(/[^a-zA-Z0-9 _-]/g, '_')}_Report.pdf`
            document.body.appendChild(link)
            link.click()
            link.remove()
            window.URL.revokeObjectURL(url)
            toast.success('PDF downloaded successfully!')
        } catch (err: unknown) {
            toast.dismiss(loadingToast)
            const e = err as { response?: { data?: unknown; status?: number }; message?: string }
            let msg = e?.message || 'PDF export failed.'
            if (e?.response?.data instanceof Blob) {
                try {
                    const text = await (e.response.data as Blob).text()
                    const parsed = JSON.parse(text)
                    msg = parsed.message || parsed.error || msg
                } catch { /* keep original msg */ }
            } else if (e?.response?.data && typeof e.response.data === 'object') {
                const d = e.response.data as { message?: string; error?: string }
                msg = d.message || d.error || msg
            }
            toast.error(msg)
        } finally {
            setExporting(false)
        }
    }

    const handleRunSegmentation = async () => {
        if (!datasetId || segLoading) return
        setSegLoading(true)
        setSegError(null)
        const loadingToast = toast.loading('Running segmentation analysis — this may take a moment...')
        try {
            const result = await runSegmentation(datasetId)
            toast.dismiss(loadingToast)
            setSegmentation(result)
            toast.success(`Segmentation complete: ${result.method_label}`)
        } catch (err: unknown) {
            toast.dismiss(loadingToast)
            const e = err as { response?: { data?: { message?: string; error?: string } }; message?: string }
            const msg = e?.response?.data?.message || e?.response?.data?.error || e?.message || 'Segmentation failed.'
            setSegError(msg)
            toast.error(msg)
        } finally {
            setSegLoading(false)
        }
    }

    const formatDate = (iso: string) => {
        if (!iso) return ''
        try {
            return new Date(iso).toLocaleDateString('en-US', {
                year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit',
            })
        } catch { return iso }
    }

    const sectionMeta: Record<string, { icon: string; accent: string }> = {
        executive: { icon: 'fa-solid fa-file-lines', accent: 'from-info/15 to-info/5 border-info/30' },
        insight: { icon: 'fa-solid fa-lightbulb', accent: 'from-amber-500/20 to-amber-600/5 border-warning/30' },
        recommend: { icon: 'fa-solid fa-bullseye', accent: 'from-success/15 to-success/5 border-success/30' },
    }

    const getSectionStyle = (t: string) => {
        const l = t.toLowerCase()
        if (l.includes('executive') || l.includes('summary')) return sectionMeta.executive
        if (l.includes('insight') || l.includes('finding')) return sectionMeta.insight
        if (l.includes('recommend') || l.includes('action')) return sectionMeta.recommend
        return { icon: 'fa-solid fa-circle-info', accent: 'from-purple-500/20 to-purple-600/5 border-purple-500/30' }
    }

    const handleCategoryChange = async (newCategory: string) => {
        if (!datasetId || catUpdating) return
        setCatUpdating(true)
        try {
            await updateDatasetCategory(datasetId, newCategory)
            toast.success(`Category updated to ${newCategory}`)
            setAiDepartment(newCategory)
            setCatDetection(prev => prev ? { ...prev, resolved_category: newCategory, mismatch_warning: false, user_confirmed: true } : prev)
            setCatBannerDismissed(true)
        } catch {
            toast.error('Failed to update category.')
        } finally {
            setCatUpdating(false)
        }
    }

    const showOverrideBanner = !catBannerDismissed && catDetection?.overridden
    const showMismatchBanner = !catBannerDismissed && !catDetection?.overridden
        && catDetection?.mismatch_warning && !catDetection?.user_confirmed

    // Legacy simple mismatch: category_hint differs from step8.department but no AI detection data
    const deptMismatch = !catDetection && userCategory && aiDepartment.toLowerCase() !== userCategory.toLowerCase()

    const kpiCharts = charts.filter(c => c.chart_type === 'kpi_card')
    const vizCharts = charts.filter(c => c.chart_type !== 'kpi_card')

    if (loading) {
        return (
            <div className="flex items-center justify-center py-32">
                <div className="text-center flex flex-col items-center">
                    <LogoSpinner size={64} className="mb-6" />
                    <p className="text-muted-foreground text-sm">Loading AI report...</p>
                </div>
            </div>
        )
    }

    if (error) {
        return (
            <div className="flex items-center justify-center py-32">
                <div className="max-w-md bg-gradient-to-b from-destructive/10 to-destructive/5 border border-destructive/30 rounded-2xl p-10 text-center">
                    <div className="w-14 h-14 bg-red-500/10 rounded-2xl flex items-center justify-center mx-auto mb-5">
                        <i className="fa-solid fa-triangle-exclamation text-2xl text-destructive"></i>
                    </div>
                    <h2 className="text-xl font-bold text-destructive mb-3">Report Unavailable</h2>
                    <p className="text-muted-foreground text-sm leading-relaxed mb-6">{error}</p>
                    <a href="/BI-Dashboard" className="inline-block px-6 py-2.5 bg-primary hover:bg-primary/90 rounded-xl text-sm font-semibold transition-colors">
                        Go to Dashboard
                    </a>
                </div>
            </div>
        )
    }

    return (
        <div>
            {/* ── Hero Header ── */}
            <div className="relative overflow-hidden">
                <div className="absolute inset-0 bg-gradient-to-br from-[#5A5AF6]/10 via-transparent to-purple-600/5"></div>
                <div className="relative px-8 md:px-12 py-10 border-b border-border">
                    <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-6">
                        <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-3 mb-3">
                                <div className="w-10 h-10 bg-primary/15 rounded-xl flex items-center justify-center">
                                    <i className="fa-solid fa-chart-pie text-primary"></i>
                                </div>
                                <span className="text-xs text-muted-foreground uppercase tracking-widest font-semibold">AI-Generated Report</span>
                            </div>
                            <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight mb-3 leading-tight">{title}</h1>
                            <div className="flex flex-wrap items-center gap-3">
                                {/* AI-detected department — primary */}
                                <span className="bg-primary text-primary-foreground px-3.5 py-1 rounded-full text-xs font-bold tracking-wide">
                                    {aiDepartment}
                                </span>
                                {/* User-selected category — shown as mismatch warning if different */}
                                {deptMismatch && (
                                    <span className="flex items-center gap-1.5 bg-red-500/15 border border-red-500/40 text-destructive px-3 py-1 rounded-full text-xs font-semibold">
                                        <i className="fa-solid fa-triangle-exclamation text-[10px]"></i>
                                        Uploaded as: {prettify(userCategory)}
                                    </span>
                                )}
                                {generatedAt && (
                                    <span className="text-muted-foreground text-xs">
                                        <i className="fa-regular fa-clock me-1"></i>
                                        {formatDate(generatedAt)}
                                    </span>
                                )}
                            </div>
                        </div>
                        <div className="flex items-center gap-3">
                            {ttsSupported && (
                                <AudioOverviewButton
                                    label="Listen"
                                    datasetId={datasetId || undefined}
                                    downloadName={`report-${(title || 'overview').toString().toLowerCase().replace(/\s+/g, '-').slice(0, 40)}`}
                                    text={() => {
                                        const execSummary = sections[0]?.content || '';
                                        return execSummary || `${title}. ${sections.map(s => s.title).join('. ')}`;
                                    }}
                                />
                            )}
                            <button
                                onClick={handleExportPdf}
                                disabled={exporting}
                                className="flex items-center gap-2.5 px-6 py-3 bg-gradient-to-r from-[#5A5AF6] to-[#7c3aed] hover:from-[#4747ef] hover:to-[#6d28d9] rounded-xl text-sm font-bold transition-all disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer shadow-lg shadow-[#5A5AF6]/20 whitespace-nowrap"
                            >
                                {exporting ? <LogoSpinner size={16} /> : <i className="fa-solid fa-file-pdf"></i>}
                                {exporting ? 'Generating PDF...' : 'Download Full PDF Report'}
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <div className="px-8 md:px-12 py-8 space-y-10">

                {/* ── Category auto-overridden info banner ── */}
                {showOverrideBanner && catDetection && (
                    <div className="flex items-start gap-4 bg-primary/5 border border-primary/30 rounded-2xl px-5 py-4">
                        <div className="w-9 h-9 bg-primary/15 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5">
                            <i className="fa-solid fa-wand-magic-sparkles text-primary text-sm"></i>
                        </div>
                        <div className="flex-1 min-w-0">
                            <p className="text-sm font-semibold text-foreground mb-1">
                                Category auto-corrected to <span className="text-primary">{catDetection.resolved_category}</span>
                            </p>
                            <p className="text-xs text-muted-foreground leading-relaxed">
                                You uploaded this as <span className="text-foreground font-medium">{prettify(catDetection.user_category ?? '')}</span>, but our AI
                                identified it as a <span className="text-foreground font-medium">{catDetection.detected_category}</span> dataset
                                with <span className="text-foreground font-medium">{Math.round((catDetection.confidence ?? 0) * 100)}%</span> confidence.
                                This report and all analysis reflect the corrected category.
                            </p>
                        </div>
                        <button
                            onClick={() => handleCategoryChange(catDetection.user_category ?? '')}
                            disabled={catUpdating}
                            className="text-xs text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap flex-shrink-0 cursor-pointer disabled:opacity-50"
                        >
                            Revert to {prettify(catDetection.user_category ?? '')}
                        </button>
                        <button onClick={() => setCatBannerDismissed(true)} className="text-muted-foreground hover:text-muted-foreground cursor-pointer flex-shrink-0">
                            <i className="fa-solid fa-xmark text-sm"></i>
                        </button>
                    </div>
                )}

                {/* ── Category mismatch warning banner (low confidence) ── */}
                {showMismatchBanner && catDetection && (
                    <div className="bg-warning/10 border border-warning/30 rounded-2xl px-5 py-4">
                        <div className="flex items-start gap-4">
                            <div className="w-9 h-9 bg-warning/15 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5">
                                <i className="fa-solid fa-triangle-exclamation text-warning text-sm"></i>
                            </div>
                            <div className="flex-1 min-w-0">
                                <p className="text-sm font-semibold text-warning mb-1">
                                    Possible category mismatch detected
                                </p>
                                <p className="text-xs text-warning/90 leading-relaxed mb-4">
                                    You selected <span className="font-semibold text-warning">{prettify(catDetection.user_category ?? '')}</span> but our AI
                                    suspects this may be a <span className="font-semibold text-warning">{catDetection.detected_category}</span> dataset
                                    ({Math.round((catDetection.confidence ?? 0) * 100)}% confidence).
                                    Using the wrong category may reduce the accuracy of segmentation, recommendations, and this report's insights.
                                </p>
                                <div className="flex flex-wrap gap-3">
                                    <button
                                        onClick={() => handleCategoryChange(catDetection.detected_category ?? '')}
                                        disabled={catUpdating}
                                        className="flex items-center gap-2 px-4 py-2 bg-amber-500 hover:bg-amber-400 text-black text-xs font-bold rounded-lg transition-colors disabled:opacity-50 cursor-pointer"
                                    >
                                        {catUpdating
                                            ? <><LogoSpinner size={14} /> Updating...</>
                                            : <><i className="fa-solid fa-check"></i> Change to {catDetection.detected_category}</>}
                                    </button>
                                    <button
                                        onClick={() => handleCategoryChange(catDetection.user_category ?? '')}
                                        disabled={catUpdating}
                                        className="flex items-center gap-2 px-4 py-2 bg-muted hover:bg-accent border border-warning/30 text-warning text-xs font-semibold rounded-lg transition-colors disabled:opacity-50 cursor-pointer"
                                    >
                                        <i className="fa-solid fa-forward"></i> Keep {prettify(catDetection.user_category ?? '')} (may reduce accuracy)
                                    </button>
                                </div>
                            </div>
                            <button onClick={() => setCatBannerDismissed(true)} className="text-muted-foreground hover:text-muted-foreground cursor-pointer flex-shrink-0">
                                <i className="fa-solid fa-xmark text-sm"></i>
                            </button>
                        </div>
                    </div>
                )}

                {/* ── Dataset Overview Bar ── */}
                {fileName && (
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <InfoCard icon="fa-solid fa-file-csv" label="Dataset" value={fileName} />
                        <InfoCard icon="fa-solid fa-table-cells" label="Total Rows" value={rowCount || '-'} />
                        <InfoCard icon="fa-solid fa-table-columns" label="Columns" value={colCount || '-'} />
                        <InfoCard icon="fa-solid fa-layer-group" label="Department" value={aiDepartment} />
                    </div>
                )}

                {/* ── Segmentation Analysis ── */}
                <SegmentationSection
                    segmentation={segmentation}
                    segLoading={segLoading}
                    segError={segError}
                    onRunSegmentation={handleRunSegmentation}
                />

                {/* ── KPI Cards ── */}
                {kpiCharts.length > 0 && rows.length > 0 && (
                    <div>
                        <SectionHeader icon="fa-solid fa-gauge-high" title="Key Performance Indicators" />
                        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mt-4">
                            {kpiCharts.map((kpi, i) => (
                                <KpiCard key={i} spec={kpi} rows={rows} />
                            ))}
                        </div>
                    </div>
                )}

                {/* ── Charts ── */}
                {vizCharts.length > 0 && (aggResults.length > 0 || rows.length > 0) && (
                    <div>
                        <SectionHeader icon="fa-solid fa-chart-column" title="Data Visualizations" />
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-4">
                            {vizCharts.map((chart, i) => (
                                <ChartCard key={i} spec={chart} rows={rows} columnsByKey={columnsByKey} aggData={aggResults[i] ?? []} />
                            ))}
                        </div>
                    </div>
                )}

                {/* ── AI Narrative Sections ── */}
                <div>
                    <SectionHeader icon="fa-solid fa-brain" title="AI Analysis Report" />
                    <div className="space-y-5 mt-4">
                        {sections.map((section, i) => {
                            const style = getSectionStyle(section.title)
                            return (
                                <div key={i} className={`bg-gradient-to-br ${style.accent} border rounded-2xl p-7 md:p-8`}>
                                    <div className="flex items-center gap-3 mb-5">
                                        <div className="w-11 h-11 bg-white/5 backdrop-blur rounded-xl flex items-center justify-center">
                                            <i className={`${style.icon} text-lg text-foreground/80`}></i>
                                        </div>
                                        <div>
                                            <span className="text-[10px] uppercase tracking-widest text-muted-foreground font-semibold">Section {i + 1}</span>
                                            <h2 className="text-xl font-bold leading-tight">{section.title}</h2>
                                        </div>
                                    </div>
                                    <div className="text-foreground leading-relaxed text-[14px] space-y-3 pl-1">
                                        {section.content.split('\n').map((p, pi) =>
                                            p.trim() ? <p key={pi}>{p}</p> : null
                                        )}
                                    </div>
                                </div>
                            )
                        })}
                    </div>
                </div>

                {/* ── Data Snapshot (replaces raw column profile) ── */}
                {columns.length > 0 && (
                    <DataSnapshot columns={columns} />
                )}

                {/* ── Footer ── */}
                <div className="pt-8 pb-4 border-t border-border text-center">
                    <p className="text-xs text-muted-foreground">
                        <i className="fa-solid fa-robot me-1"></i>
                        This report was auto-generated by AxBi AI Analytics Engine.
                        All insights are based on automated statistical profiling and should be validated by domain experts.
                    </p>
                </div>
            </div>
        </div>
    )
}

/* ═══════════════════════════════════════════════════════════ */
/* Sub-components                                              */
/* ═══════════════════════════════════════════════════════════ */

function SectionHeader({ icon, title }: { icon: string; title: string }) {
    return (
        <div className="flex items-center gap-3">
            <div className="w-9 h-9 bg-primary/10 rounded-lg flex items-center justify-center">
                <i className={`${icon} text-primary text-sm`}></i>
            </div>
            <h2 className="text-xl font-bold tracking-tight">{title}</h2>
        </div>
    )
}

function InfoCard({ icon, label, value }: { icon: string; label: string; value: string }) {
    const displayValue = value.length > 28 ? value.slice(0, 25) + '...' : value
    return (
        <div className="bg-gradient-to-br from-primary/[0.07] to-card border border-primary/20 rounded-xl px-5 py-4 flex items-center gap-4 shadow-sm">
            <div className="w-10 h-10 bg-primary/10 rounded-lg flex items-center justify-center flex-shrink-0">
                <i className={`${icon} text-primary text-sm`}></i>
            </div>
            <div className="min-w-0">
                <p className="text-[10px] uppercase tracking-widest text-muted-foreground font-semibold">{label}</p>
                <p className="text-sm font-bold text-foreground truncate" title={value}>{displayValue}</p>
            </div>
        </div>
    )
}

function KpiCard({ spec, rows }: { spec: ChartSpec; rows: DatasetRow[] }) {
    const targetKey = spec.y_axis ?? spec.columns?.[0] ?? null
    if (!targetKey) return null

    let total = 0
    let count = 0
    for (const row of rows) {
        const v = parseNumeric(row.row_data[targetKey])
        if (v != null) { total += v; count++ }
    }
    if (count === 0) return null

    return (
        <div className="bg-card border border-border rounded-xl p-5 hover:border-primary/30 transition-colors">
            <p className="text-[10px] uppercase tracking-widest text-muted-foreground font-semibold mb-1">{spec.title}</p>
            <p className="text-3xl font-extrabold text-foreground mb-1">{compactNumber(total)}</p>
            <p className="text-xs text-muted-foreground">{prettify(targetKey)}</p>
        </div>
    )
}

/* ── Data Snapshot — user-friendly column overview ── */
function DataSnapshot({ columns }: { columns: ColumnMeta[] }) {
    return (
        <div>
            <SectionHeader icon="fa-solid fa-table-list" title="Data Snapshot" />
            <p className="text-sm text-muted-foreground mt-1 mb-4">
                What's in your dataset — each field explained in plain language.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
                {columns.slice(0, 18).map((col, i) => {
                    const stats = parseJson(col.technical_stats)
                    const ai = parseJson(col.ai_profile)
                    const role = String(ai.column_role || ai.role || '')
                    const meaning = String(ai.semantic_meaning || ai.description || '')
                    const nullRatio = stats.null_ratio != null ? Number(stats.null_ratio) : 0
                    const completePct = Math.round((1 - nullRatio) * 100)
                    const cfg = getRoleConfig(role)
                    const colName = col.display_name || col.clean_name || col.original_name || 'Column'

                    // Build a plain-language value summary
                    let valueSummary = ''
                    const mn = stats.min != null ? compactNumber(Number(stats.min)) : null
                    const mx = stats.max != null ? compactNumber(Number(stats.max)) : null
                    if (mn != null && mx != null) {
                        valueSummary = `${mn} – ${mx}`
                    } else {
                        const samples = Array.isArray(stats.top_5_samples) ? stats.top_5_samples : []
                        if (samples.length) valueSummary = samples.slice(0, 3).map(String).join(', ')
                    }

                    return (
                        <div key={i} className="bg-card border border-border rounded-xl p-4 hover:border-primary/20 transition-colors">
                            {/* Header */}
                            <div className="flex items-start justify-between gap-2 mb-2">
                                <div className="flex items-center gap-2 min-w-0">
                                    <i className={`${cfg.icon} text-xs shrink-0`} style={{ color: 'inherit', opacity: 0.6 }}></i>
                                    <p className="font-semibold text-foreground text-sm leading-tight truncate">{prettify(colName)}</p>
                                </div>
                                <span className={`shrink-0 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide ${cfg.color}`}>
                                    {cfg.label}
                                </span>
                            </div>

                            {/* Semantic meaning */}
                            {meaning ? (
                                <p className="text-xs text-muted-foreground leading-relaxed mb-3 line-clamp-2">{meaning}</p>
                            ) : (
                                <p className="text-xs text-muted-foreground italic mb-3">No description available</p>
                            )}

                            {/* Completeness bar */}
                            <div className="mb-2">
                                <div className="flex justify-between items-center mb-1">
                                    <span className="text-[10px] text-muted-foreground">Data completeness</span>
                                    <span className={`text-[10px] font-semibold ${completePct >= 90 ? 'text-success' : completePct >= 70 ? 'text-warning' : 'text-destructive'}`}>
                                        {completePct}%
                                    </span>
                                </div>
                                <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                                    <div
                                        className={`h-full rounded-full transition-all ${completePct >= 90 ? 'bg-emerald-500' : completePct >= 70 ? 'bg-amber-500' : 'bg-red-500'}`}
                                        style={{ width: `${completePct}%` }}
                                    />
                                </div>
                            </div>

                            {/* Value range / samples */}
                            {valueSummary && (
                                <p className="text-[10px] text-muted-foreground mt-2 truncate" title={valueSummary}>
                                    <span className="text-muted-foreground">Values: </span>{valueSummary}
                                </p>
                            )}
                        </div>
                    )
                })}
            </div>
        </div>
    )
}

const SEG_COLORS = ['#8b5cf6', '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#06b6d4', '#ec4899', '#14b8a6', '#f97316', '#6366f1']

function SegmentationSection({
    segmentation,
    segLoading,
    segError,
    onRunSegmentation,
}: {
    segmentation: SegmentationResult | null
    segLoading: boolean
    segError: string | null
    onRunSegmentation: () => void
}) {
    if (!segmentation) {
        return (
            <div>
                <SectionHeader icon="fa-solid fa-layer-group" title="Data Segmentation" />
                <div className="mt-4 bg-gradient-to-br from-[#5A5AF6]/10 to-[#7c3aed]/5 border border-primary/20 rounded-2xl p-8 text-center">
                    <div className="w-16 h-16 bg-primary/15 rounded-2xl flex items-center justify-center mx-auto mb-5">
                        <i className="fa-solid fa-object-group text-2xl text-primary"></i>
                    </div>
                    <h3 className="text-lg font-bold text-foreground mb-2">Segmentation Analysis</h3>
                    <p className="text-muted-foreground text-sm max-w-lg mx-auto mb-6">
                        Automatically detect entity types and apply the best segmentation strategy
                        (RFM, ABC/Pareto, or K-Means clustering) based on your data structure.
                    </p>
                    {segError && <p className="text-destructive text-sm mb-4">{segError}</p>}
                    <button
                        onClick={onRunSegmentation}
                        disabled={segLoading}
                        className="inline-flex items-center gap-2.5 px-6 py-3 bg-gradient-to-r from-[#5A5AF6] to-[#7c3aed] hover:from-[#4747ef] hover:to-[#6d28d9] rounded-xl text-sm font-bold transition-all disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer shadow-lg shadow-[#5A5AF6]/20"
                    >
                        {segLoading ? <LogoSpinner size={16} /> : <i className="fa-solid fa-play"></i>}
                        {segLoading ? 'Analyzing...' : 'Run Segmentation Analysis'}
                    </button>
                </div>
            </div>
        )
    }

    const methodIcon: Record<string, string> = {
        rfm: 'fa-solid fa-ranking-star',
        abc: 'fa-solid fa-arrow-down-wide-short',
        kmeans: 'fa-solid fa-circle-nodes',
    }

    const pieData = segmentation.charts?.find(c => c.chart_type === 'pie')
    const barCharts = segmentation.charts?.filter(c => c.chart_type === 'bar') || []

    return (
        <div>
            <div className="flex items-center justify-between">
                <SectionHeader icon="fa-solid fa-layer-group" title="Data Segmentation" />
                <button
                    onClick={onRunSegmentation}
                    disabled={segLoading}
                    className="flex items-center gap-2 px-4 py-2 bg-card border border-border hover:border-primary/40 rounded-xl text-xs font-semibold text-muted-foreground hover:text-foreground transition-all disabled:opacity-50 cursor-pointer"
                >
                    {segLoading ? <LogoSpinner size={14} /> : <i className="fa-solid fa-rotate"></i>}
                    {segLoading ? 'Re-running...' : 'Re-run'}
                </button>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-3">
                <span className="inline-flex items-center gap-2 bg-primary/15 text-primary px-4 py-1.5 rounded-full text-xs font-bold">
                    <i className={methodIcon[segmentation.method] || 'fa-solid fa-layer-group'}></i>
                    {segmentation.method_label}
                </span>
                {segmentation.entity_column && (
                    <span className="text-muted-foreground text-xs">
                        Entity: <span className="text-foreground font-medium">{prettify(segmentation.entity_column)}</span>
                    </span>
                )}
                <span className="text-muted-foreground text-xs">{segmentation.segments.length} segments found</span>
                {segmentation.method_meta?.silhouette_score != null && (
                    <span className="text-muted-foreground text-xs">
                        Silhouette: <span className="text-foreground font-medium">{String(segmentation.method_meta.silhouette_score)}</span>
                    </span>
                )}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-6">
                {pieData && (
                    <div className="bg-card border border-border rounded-2xl overflow-hidden">
                        <div className="px-6 pt-5 pb-2">
                            <h3 className="text-base font-bold text-foreground">{pieData.title}</h3>
                        </div>
                        <div className="h-[280px] px-3 pb-1">
                            <ResponsiveContainer width="100%" height="100%">
                                <PieChart>
                                    <Pie data={pieData.data as Array<{ name: string; value: number }>} dataKey="value" nameKey="name" outerRadius={95} innerRadius={55}>
                                        {(pieData.data as Array<{ name: string; value: number }>).map((_, i) => (
                                            <Cell key={i} fill={SEG_COLORS[i % SEG_COLORS.length]} />
                                        ))}
                                    </Pie>
                                    <Tooltip contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12, color: 'var(--popover-foreground)' }} labelStyle={{ color: 'var(--popover-foreground)' }} itemStyle={{ color: 'var(--popover-foreground)' }} />
                                    <Legend />
                                </PieChart>
                            </ResponsiveContainer>
                        </div>
                    </div>
                )}
                {barCharts[0] && (
                    <div className="bg-card border border-border rounded-2xl overflow-hidden">
                        <div className="px-6 pt-5 pb-2">
                            <h3 className="text-base font-bold text-foreground">{barCharts[0].title}</h3>
                        </div>
                        <div className="h-[280px] px-3 pb-1">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={barCharts[0].data as Array<{ label: string; value: number }>}>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
                                    <XAxis dataKey="label" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                                    <YAxis tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                                    <Tooltip contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12, color: 'var(--popover-foreground)' }} labelStyle={{ color: 'var(--popover-foreground)' }} itemStyle={{ color: 'var(--popover-foreground)' }} />
                                    <Bar dataKey="value" name="Count" radius={[6, 6, 0, 0]}>
                                        {(barCharts[0].data as Array<{ label: string; value: number }>).map((_, i) => (
                                            <Cell key={i} fill={SEG_COLORS[i % SEG_COLORS.length]} />
                                        ))}
                                    </Bar>
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    </div>
                )}
            </div>

            {barCharts[1] && (
                <div className="mt-6 bg-card border border-border rounded-2xl overflow-hidden">
                    <div className="px-6 pt-5 pb-2">
                        <h3 className="text-base font-bold text-foreground">{barCharts[1].title}</h3>
                    </div>
                    <div className="h-[280px] px-3 pb-1">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={barCharts[1].data as Array<{ label: string; value: number }>}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
                                <XAxis dataKey="label" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                                <YAxis tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                                <Tooltip contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12, color: 'var(--popover-foreground)' }} labelStyle={{ color: 'var(--popover-foreground)' }} itemStyle={{ color: 'var(--popover-foreground)' }} />
                                <Bar dataKey="value" name="Value" radius={[6, 6, 0, 0]}>
                                    {(barCharts[1].data as Array<{ label: string; value: number }>).map((_, i) => (
                                        <Cell key={i} fill={SEG_COLORS[i % SEG_COLORS.length]} />
                                    ))}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            )}

            {segmentation.scatter_data && segmentation.scatter_data.length > 0 && (
                <div className="mt-6 bg-card border border-border rounded-2xl overflow-hidden">
                    <div className="px-6 pt-5 pb-2">
                        <h3 className="text-base font-bold text-foreground">Cluster Scatter Plot (PCA)</h3>
                    </div>
                    <div className="h-[320px] px-3 pb-1">
                        <ResponsiveContainer width="100%" height="100%">
                            <ScatterChart>
                                <CartesianGrid stroke="var(--border)" />
                                <XAxis type="number" dataKey="x" name="PC1" tick={{ fill: '#9ca3af', fontSize: 10 }} />
                                <YAxis type="number" dataKey="y" name="PC2" tick={{ fill: '#9ca3af', fontSize: 10 }} />
                                <Tooltip
                                    contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12, color: 'var(--popover-foreground)' }} labelStyle={{ color: 'var(--popover-foreground)' }} itemStyle={{ color: 'var(--popover-foreground)' }}
                                    formatter={(_val: unknown, _name: any, props: { payload?: { entity?: string; cluster?: number } }) => {
                                        const p = props?.payload
                                        return p ? [`Cluster ${(p.cluster ?? 0) + 1}`, p.entity || ''] : []
                                    }}
                                />
                                <Scatter data={segmentation.scatter_data} fill="#06b6d4">
                                    {segmentation.scatter_data.map((pt, i) => (
                                        <Cell key={i} fill={SEG_COLORS[pt.cluster % SEG_COLORS.length]} />
                                    ))}
                                </Scatter>
                            </ScatterChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            )}

            {segmentation.segments.length > 0 && (
                <div className="mt-6 overflow-x-auto rounded-2xl border border-border">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="bg-card text-left">
                                <th className="px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Segment</th>
                                <th className="px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Count</th>
                                <th className="px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">%</th>
                                <th className="px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Key Metrics</th>
                                <th className="px-5 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Top Entities</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-border">
                            {segmentation.segments.map((seg, si) => (
                                <tr key={si} className="hover:bg-muted/60 transition-colors">
                                    <td className="px-5 py-3">
                                        <div className="flex items-center gap-2">
                                            <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: SEG_COLORS[si % SEG_COLORS.length] }}></div>
                                            <span className="font-medium text-foreground">{seg.name}</span>
                                        </div>
                                    </td>
                                    <td className="px-5 py-3 text-foreground">{seg.size.toLocaleString()}</td>
                                    <td className="px-5 py-3 text-foreground">{seg.percentage}%</td>
                                    <td className="px-5 py-3 text-muted-foreground">
                                        <div className="flex flex-wrap gap-2">
                                            {Object.entries(seg.avg_metrics).slice(0, 3).map(([k, v]) => (
                                                <span key={k} className="px-2 py-0.5 rounded-md bg-muted text-xs">
                                                    {prettify(k)}: {v != null ? compactNumber(v) : '-'}
                                                </span>
                                            ))}
                                        </div>
                                    </td>
                                    <td className="px-5 py-3 text-muted-foreground text-xs max-w-[200px] truncate" title={seg.top_entities.join(', ')}>
                                        {seg.top_entities.slice(0, 3).join(', ') || '-'}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {segmentation.insights.length > 0 && (
                <div className="mt-6 space-y-4">
                    {segmentation.insights.map((insight, ii) => (
                        <div key={ii} className="bg-gradient-to-br from-[#5A5AF6]/10 to-[#7c3aed]/5 border border-primary/20 rounded-2xl p-6">
                            <div className="flex items-center gap-3 mb-3">
                                <div className="w-9 h-9 bg-primary/15 rounded-lg flex items-center justify-center">
                                    <i className="fa-solid fa-lightbulb text-primary text-sm"></i>
                                </div>
                                <h3 className="text-base font-bold text-foreground">{insight.title}</h3>
                            </div>
                            <div className="text-foreground leading-relaxed text-sm space-y-2 pl-1">
                                {insight.content.split('\n').map((p, pi) =>
                                    p.trim() ? <p key={pi}>{p}</p> : null
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

function ChartCard({ spec, rows, columnsByKey, aggData = [] }: {
    spec: ChartSpec
    rows: DatasetRow[]
    columnsByKey: Map<string, ColumnMeta>
    aggData?: AggregatedPoint[]
}) {
    const chartType = spec.chart_type

    // Group the many backend chart types into the handful of render shapes.
    const isPieFamily = chartType === 'pie' || chartType === 'donut' || chartType === 'treemap' || chartType === 'funnel'
    const isBarFamily = chartType === 'bar' || chartType === 'horizontal_bar' || chartType === 'stacked_bar' || chartType === 'histogram' || chartType === 'radial'

    const data = useMemo(() => {
        const collapsePie = (pts: { name: string; value: number }[]) => {
            const d = pts.filter(p => p.value > 0)
            if (d.length > 10) {
                const head = d.slice(0, 9)
                const rest = d.slice(9).reduce((a, c) => a + c.value, 0)
                return [...head, { name: 'Others', value: rest }]
            }
            return d
        }

        // Prefer pre-aggregated backend data (full dataset via Parquet)
        if (aggData.length > 0) {
            if (isPieFamily) {
                return collapsePie(aggData.map(p => ({ name: p.name ?? p.label ?? '', value: p.value ?? 0 })))
            }
            if (chartType === 'scatter') {
                return aggData.filter(p => p.x != null && p.y != null).map(p => ({ x: p.x!, y: p.y! }))
            }
            if (chartType === 'combo') {
                return aggData.map(p => ({ label: p.label ?? '', value: p.value ?? 0, line: p.line ?? 0 }))
            }
            if (chartType === 'pareto') {
                return aggData.map(p => ({ label: p.label ?? '', value: p.value ?? 0, cumulative: p.cumulative ?? 0 }))
            }
            // bar / line / area / horizontal_bar / stacked_bar / histogram / radial
            return aggData.map(p => ({ label: p.label ?? p.name ?? '', value: p.value ?? 0 }))
        }

        // Fallback: client-side aggregation on sample rows
        if (isBarFamily || chartType === 'line' || chartType === 'area' || chartType === 'combo' || chartType === 'pareto') {
            return buildGrouped(rows, spec.x_axis, spec.y_axis, columnsByKey).map(d => ({ label: d.label, value: d.value }))
        }
        if (isPieFamily) {
            return collapsePie(
                buildGrouped(rows, spec.x_axis, spec.y_axis, columnsByKey, true).map(d => ({ name: d.label, value: d.value }))
            )
        }
        if (chartType === 'scatter') {
            if (!spec.x_axis || !spec.y_axis) return []
            const pts: { x: number; y: number }[] = []
            for (const r of rows) {
                const x = parseNumeric(r.row_data[spec.x_axis])
                const y = parseNumeric(r.row_data[spec.y_axis!])
                if (x != null && y != null) pts.push({ x, y })
            }
            return pts
        }
        return []
    }, [aggData, rows, spec, columnsByKey, chartType, isPieFamily, isBarFamily])

    if (!data.length) return null

    const yLabel = spec.y_axis ? prettify(spec.y_axis) : 'Value'

    return (
        <div className="bg-card border border-border rounded-2xl overflow-hidden hover:border-primary/20 transition-colors">
            <div className="px-6 pt-5 pb-2">
                <h3 className="text-base font-bold text-foreground">{spec.title}</h3>
            </div>
            <div className="h-[280px] px-3 pb-1">
                <ResponsiveContainer width="100%" height="100%">
                    {isBarFamily ? (
                        <BarChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
                            <XAxis dataKey="label" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                            <YAxis tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                            <Tooltip contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12, color: 'var(--popover-foreground)' }} labelStyle={{ color: 'var(--popover-foreground)' }} itemStyle={{ color: 'var(--popover-foreground)' }} />
                            <Legend />
                            <Bar dataKey="value" name={yLabel} radius={[6, 6, 0, 0]}>
                                {(data as { label: string; value: number }[]).map((_, i) => (
                                    <Cell key={i} fill={COLORS[i % COLORS.length]} />
                                ))}
                            </Bar>
                        </BarChart>
                    ) : chartType === 'line' ? (
                        <LineChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                            <XAxis dataKey="label" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                            <YAxis tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                            <Tooltip contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12, color: 'var(--popover-foreground)' }} labelStyle={{ color: 'var(--popover-foreground)' }} itemStyle={{ color: 'var(--popover-foreground)' }} />
                            <Legend />
                            <Line type="monotone" dataKey="value" name={yLabel} stroke="#22c55e" strokeWidth={2.5} dot={false} />
                        </LineChart>
                    ) : chartType === 'area' ? (
                        <AreaChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                            <XAxis dataKey="label" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                            <YAxis tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                            <Tooltip contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12, color: 'var(--popover-foreground)' }} labelStyle={{ color: 'var(--popover-foreground)' }} itemStyle={{ color: 'var(--popover-foreground)' }} />
                            <Legend />
                            <Area type="monotone" dataKey="value" name={yLabel} stroke={COLORS[0]} fill={COLORS[0]} fillOpacity={0.2} strokeWidth={2.5} />
                        </AreaChart>
                    ) : chartType === 'combo' ? (
                        <ComposedChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
                            <XAxis dataKey="label" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                            <YAxis yAxisId="left" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                            <YAxis yAxisId="right" orientation="right" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                            <Tooltip contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12, color: 'var(--popover-foreground)' }} labelStyle={{ color: 'var(--popover-foreground)' }} itemStyle={{ color: 'var(--popover-foreground)' }} />
                            <Legend />
                            <Bar yAxisId="left" dataKey="value" name={yLabel} radius={[6, 6, 0, 0]} fill={COLORS[0]} />
                            <Line yAxisId="right" type="monotone" dataKey="line" name="Trend" stroke={COLORS[2]} strokeWidth={2.5} dot={false} />
                        </ComposedChart>
                    ) : chartType === 'pareto' ? (
                        <ComposedChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
                            <XAxis dataKey="label" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                            <YAxis yAxisId="left" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                            <YAxis yAxisId="right" orientation="right" domain={[0, 100]} unit="%" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                            <Tooltip contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12, color: 'var(--popover-foreground)' }} labelStyle={{ color: 'var(--popover-foreground)' }} itemStyle={{ color: 'var(--popover-foreground)' }} />
                            <Legend />
                            <Bar yAxisId="left" dataKey="value" name={yLabel} radius={[6, 6, 0, 0]} fill={COLORS[0]} />
                            <Line yAxisId="right" type="monotone" dataKey="cumulative" name="Cumulative %" stroke={COLORS[3]} strokeWidth={2.5} dot={false} />
                        </ComposedChart>
                    ) : isPieFamily ? (
                        <PieChart>
                            <Pie data={data} dataKey="value" nameKey="name" outerRadius={95} innerRadius={55}>
                                {(data as { name: string; value: number }[]).map((_, i) => (
                                    <Cell key={i} fill={COLORS[i % COLORS.length]} />
                                ))}
                            </Pie>
                            <Tooltip contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12, color: 'var(--popover-foreground)' }} labelStyle={{ color: 'var(--popover-foreground)' }} itemStyle={{ color: 'var(--popover-foreground)' }} />
                            <Legend />
                        </PieChart>
                    ) : chartType === 'scatter' ? (
                        <ScatterChart>
                            <CartesianGrid stroke="var(--border)" />
                            <XAxis type="number" dataKey="x" name={spec.x_axis ? prettify(spec.x_axis) : 'X'} tick={{ fill: '#9ca3af', fontSize: 10 }} />
                            <YAxis type="number" dataKey="y" name={yLabel} tick={{ fill: '#9ca3af', fontSize: 10 }} />
                            <Tooltip contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12, color: 'var(--popover-foreground)' }} labelStyle={{ color: 'var(--popover-foreground)' }} itemStyle={{ color: 'var(--popover-foreground)' }} cursor={{ strokeDasharray: '3 3' }} />
                            <Scatter data={data} fill="#06b6d4" />
                        </ScatterChart>
                    ) : <BarChart data={[]}><Bar dataKey="value" /></BarChart>}
                </ResponsiveContainer>
            </div>
            <div className="px-6 py-3 border-t border-border">
                <p className="text-xs text-muted-foreground italic">{spec.reason}</p>
            </div>
        </div>
    )
}
