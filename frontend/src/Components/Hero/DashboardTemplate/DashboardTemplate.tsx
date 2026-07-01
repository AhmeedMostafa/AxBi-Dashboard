import { useEffect, useMemo, useState, lazy, Suspense } from "react";
import { DndContext, closestCenter, PointerSensor, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { SortableContext, arrayMove, rectSortingStrategy } from "@dnd-kit/sortable";
import { aggregateCharts, updateDatasetCategory, detectDatasetCategory, customizeChart } from "../../../api.js";
import { LoadingSkeleton } from "../../ui/LoadingSkeleton";
import { LogoSpinner } from "../../ui/LogoSpinner";
import { useSpeechSynthesis } from "../../../hooks/useSpeechSynthesis";
import AudioOverviewButton from "../../common/AudioOverviewButton";
import ChatChartRenderer from "../../Conversation/ChatChart";
import InteractiveChartCard, { type Field } from "./InteractiveChartCard";
import SortableChartCard from "./SortableChartCard";
import EditableTitle from "./EditableTitle";
import AddChartModal from "./AddChartModal";
import DateRangeControl from "./DateRangeControl";
import { rangeIsActive, resolveRange } from "./dateRange";
import { useDashboardConfig, type GlobalRange } from "./useDashboardConfig";
import { useGeneratedAssets } from "../../../context/GeneratedAssetsContext";
import { CHART_PRIMARY, formatCompact, CHART_CARD_CLASS } from "../../../lib/chartTheme";
import toast from "react-hot-toast";

const PinnedVisual3D = lazy(() => import("../../Conversation/Visual3D"));

type ChartType = "bar" | "horizontal_bar" | "line" | "area" | "pie" | "donut" | "treemap" | "stacked_bar" | "funnel" | "kpi_card" | "radial" | "histogram" | "pareto" | "combo";

type ChartSpec = {
    chart_type: ChartType;
    title: string;
    x_axis: string | null;
    y_axis: string | null;
    y_axis_secondary?: string | null;
    columns: string[];
    reason: string;
};

type TechnicalStats = {
    top_5_samples?: Array<string | number>;
    min?: number;
    max?: number;
    mean?: number;
    std_dev?: number;
};

type AiProfile = {
    role?: string;
};

type ColumnMeta = {
    clean_name?: string;
    original_name?: string;
    column_key?: string;
    display_name?: string;
    data_type?: string;
    technical_stats?: TechnicalStats | string | null;
    ai_profile?: AiProfile | string | null;
};

type CategoryDetection = {
    resolved_category?: string;
    detected_category?: string;
    user_category?: string;
    confidence?: number;
    overridden?: boolean;
    mismatch_warning?: boolean;
    user_confirmed?: boolean;
    threshold?: number;
};

type DashboardPayload = {
    dataset_id?: string;
    data?: {
        category_hint?: string;
        global_context?: {
            step7?: {
                suggested_title?: string;
                suggested_charts?: ChartSpec[];
            };
            category_detection?: CategoryDetection;
        };
    };
    columns?: ColumnMeta[];
};

type DashboardTemplateProps = {
    data: DashboardPayload | null;
};

type AggregatedPoint = {
    label?: string;
    value?: number;
    name?: string;
    x?: number;
    y?: number;
    cumulative?: number;
    line?: number;
    line_name?: string;
    line_is_pct?: boolean;
    max?: number;
    unit?: string;
};

type AggregatedChartResult = {
    chart_type: string;
    x_axis: string | null;
    y_axis: string | null;
    data: AggregatedPoint[];
};

type ChartCardProps = {
    chart: ChartSpec;
    columnsByKey: Map<string, ColumnMeta>;
    labelByKey: Map<string, string>;
    aggData: AggregatedPoint[];
};

function prettifyName(value: string): string {
    const tokens = value.replace(/[-\s]+/g, "_").split("_").filter(Boolean);
    if (tokens.length === 0) return value;
    return tokens.map((token) => token.charAt(0).toUpperCase() + token.slice(1)).join(" ");
}

function KpiCard({ chart, labelByKey, aggData, datasetId, globalRange, agg = "sum", autoFetch = false, onTitleChange }: ChartCardProps & { datasetId?: string; globalRange?: GlobalRange | null; agg?: string; autoFetch?: boolean; onTitleChange?: (next: string) => void }) {
    const targetKey = chart.y_axis ?? chart.columns[0] ?? null;
    const isCount = agg === "count";
    const label = isCount ? "Rows" : (targetKey ? (labelByKey.get(targetKey) ?? prettifyName(targetKey)) : "Primary KPI");
    const [value, setValue] = useState<number>(aggData[0]?.value ?? 0);
    const [loading, setLoading] = useState(false);

    useEffect(() => { if (!autoFetch) setValue(aggData[0]?.value ?? 0); }, [aggData, autoFetch]);

    const active = rangeIsActive(globalRange);
    const rangeKey = JSON.stringify(globalRange ?? null);
    useEffect(() => {
        const needFetch = autoFetch || active;
        if (!needFetch || !datasetId || (!targetKey && !isCount)) {
            if (!autoFetch) setValue(aggData[0]?.value ?? 0);
            return;
        }
        let cancelled = false;
        const { from, to } = resolveRange(globalRange);
        setLoading(true);
        customizeChart(datasetId, {
            chart_type: "kpi", y_axis: isCount ? null : targetKey, agg,
            date_field: globalRange?.field, date_from: from, date_to: to,
        })
            .then((res: { value?: number }) => { if (!cancelled && typeof res?.value === "number") setValue(res.value); })
            .catch(() => { /* keep previous value */ })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [rangeKey, datasetId, targetKey, agg, autoFetch, isCount]);

    return (
        <div className={`relative ${CHART_CARD_CLASS} overflow-hidden`}>
            <span className="absolute left-0 top-0 h-full w-1" style={{ background: CHART_PRIMARY }} />
            <EditableTitle value={chart.title} onChange={onTitleChange} className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider" />
            <h3 className={`text-3xl font-bold text-foreground mt-2 transition-opacity ${loading ? "opacity-50" : ""}`}>{formatCompact(value)}</h3>
            <p className="text-sm text-muted-foreground mt-2">{label}</p>
            <p className="text-xs text-muted-foreground mt-3 italic">{chart.reason}</p>
        </div>
    );
}

function asChartType(value: string): ChartType | null {
    const normalized = value.toLowerCase().replace(/[\s-]/g, '_');
    const valid: ChartType[] = ["bar", "horizontal_bar", "line", "area", "pie", "donut", "treemap", "stacked_bar", "funnel", "kpi_card", "radial", "histogram", "pareto", "combo"];
    if (valid.includes(normalized as ChartType)) return normalized as ChartType;
    if (normalized === "area_chart") return "area";
    if (normalized === "doughnut") return "donut";
    if (normalized === "hbar" || normalized === "bar_horizontal") return "horizontal_bar";
    if (normalized === "gauge") return "radial";
    if (normalized === "combo_chart" || normalized === "bar_line") return "combo";
    // scatter / dot charts are no longer supported → skip
    if (normalized === "scatter" || normalized === "dot" || normalized === "bubble") return null;
    return null;
}

export default function DashboardTemplate({ data }: DashboardTemplateProps) {
    const step7 = data?.data?.global_context?.step7;
    const charts = Array.isArray(step7?.suggested_charts) ? step7.suggested_charts : [];
    const columns = Array.isArray(data?.columns) ? data.columns : [];
    const datasetId = typeof data?.dataset_id === "string" ? data.dataset_id : "";

    const [aggResults, setAggResults] = useState<AggregatedChartResult[]>([]);
    const [rowsLoading, setRowsLoading] = useState<boolean>(false);
    const [rowsError, setRowsError] = useState<string | null>(null);
    const [catBannerDismissed, setCatBannerDismissed] = useState(false);
    const [catUpdating, setCatUpdating] = useState(false);
    const [confirmChoice, setConfirmChoice] = useState<string>("");
    const [catDetection, setCatDetection] = useState<CategoryDetection | undefined>(
        data?.data?.global_context?.category_detection
    );

    const { isSupported: ttsSupported } = useSpeechSynthesis();
    const { pinnedForDataset, unpinAsset } = useGeneratedAssets();
    const pinnedAssets = pinnedForDataset(datasetId);

    const { columnsByKey, labelByKey } = useMemo(() => {
        const columnsMap = new Map<string, ColumnMeta>();
        const labelsMap = new Map<string, string>();
        for (const column of columns) {
            const key = column.column_key ?? column.clean_name ?? column.original_name;
            if (!key) continue;
            columnsMap.set(key, column);
            labelsMap.set(key, column.display_name ?? prettifyName(key));
        }
        return { columnsByKey: columnsMap, labelByKey: labelsMap };
    }, [columns]);

    // Classified field list for the interactive chart customizer
    const fields = useMemo<Field[]>(() => {
        const out: Field[] = [];
        for (const [key, col] of columnsByKey) {
            const dt = (col.data_type ?? "").toLowerCase();
            let role = "";
            const ap = col.ai_profile;
            if (ap && typeof ap === "object") role = (ap.role ?? "").toLowerCase();
            else if (typeof ap === "string") { try { role = (JSON.parse(ap)?.role ?? "").toLowerCase(); } catch { /* ignore */ } }
            let kind: Field["kind"] = "other";
            if (dt === "numeric") kind = "numeric";
            else if (dt === "datetime" || dt === "date" || role === "date") kind = "date";
            else if (dt === "categorical" || dt === "text" || dt === "boolean" || role === "dimension" || role === "categorical" || role === "geographic") kind = "dim";
            out.push({ key, label: labelByKey.get(key) ?? prettifyName(key), kind });
        }
        return out;
    }, [columnsByKey, labelByKey]);

    const kpiCharts    = charts.filter((c) => c.chart_type === "kpi_card");
    const nonKpiCharts = charts.filter((c) => c.chart_type !== "kpi_card");

    // ── Layout / custom-chart / date-range config (persisted per dataset) ──
    const baseIds = useMemo(
        () => nonKpiCharts.filter((c) => asChartType(c.chart_type)).map((c) => `s${charts.indexOf(c)}`),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [charts]
    );
    const {
        order, customCharts, globalRange, titles, getLayout, reorder,
        setSpan, setHeight, addChart, removeChart, setGlobalRange, setTitle, resetLayout,
    } = useDashboardConfig(datasetId, baseIds);

    const [editMode, setEditMode] = useState(false);
    const [showAdd, setShowAdd] = useState(false);
    const hasDateField = fields.some((f) => f.kind === "date");

    const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
    const heightPx = (h: "normal" | "tall") => (h === "tall" ? 440 : 290);

    const handleDragEnd = (event: DragEndEvent) => {
        const { active, over } = event;
        if (!over || active.id === over.id) return;
        const oldIndex = order.indexOf(String(active.id));
        const newIndex = order.indexOf(String(over.id));
        if (oldIndex < 0 || newIndex < 0) return;
        reorder(arrayMove(order, oldIndex, newIndex));
    };

    // Auto-detect category for datasets processed before detection was added
    useEffect(() => {
        if (!datasetId || catDetection) return;
        detectDatasetCategory(datasetId)
            .then((result: { category_detection?: CategoryDetection }) => {
                if (result?.category_detection) {
                    setCatDetection(result.category_detection);
                }
            })
            .catch(() => { /* silent */ });
    }, [datasetId, catDetection]);

    useEffect(() => {
        if (!datasetId || charts.length === 0) return;

        let cancelled = false;
        const load = async () => {
            setRowsLoading(true);
            setRowsError(null);
            setAggResults([]);
            try {
                const res = await aggregateCharts(datasetId, charts);
                if (cancelled) return;
                setAggResults(Array.isArray(res?.results) ? res.results : []);
            } catch (err: unknown) {
                if (cancelled) return;
                const e = err as { response?: { data?: { error?: string } }; message?: string };
                setRowsError(e?.response?.data?.error || e?.message || "Failed to load chart data.");
            } finally {
                if (!cancelled) setRowsLoading(false);
            }
        };

        void load();
        return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [datasetId]);

    const getAggData = (index: number): AggregatedPoint[] =>
        aggResults[index]?.data ?? [];

    const handleCategoryChange = async (newCategory: string) => {
        if (!datasetId || catUpdating) return;
        setCatUpdating(true);
        try {
            await updateDatasetCategory(datasetId, newCategory);
            toast.success(`Category updated to ${newCategory}`);
            setCatBannerDismissed(true);
            setCatDetection((prev) => prev ? { ...prev, user_confirmed: true, mismatch_warning: false, resolved_category: newCategory } : prev);
        } catch {
            toast.error("Failed to update category. Please try again.");
        } finally {
            setCatUpdating(false);
        }
    };

    const showOverrideBanner = !catBannerDismissed && catDetection?.overridden;
    const showMismatchBanner = !catBannerDismissed && !catDetection?.overridden
        && catDetection?.mismatch_warning && !catDetection?.user_confirmed;

    const resolvedIsGeneric =
        (catDetection?.resolved_category ?? "").toLowerCase() === "business";
    const needsCategoryConfirm =
        !!catDetection
        && !catDetection.user_confirmed
        && (catDetection.mismatch_warning || resolvedIsGeneric);

    return (
        <div className="p-6 min-h-screen font-sans">
            {needsCategoryConfirm && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
                    <div className="w-full max-w-md rounded-2xl bg-card border border-primary/40 p-6">
                        <h3 className="text-lg font-bold text-foreground mb-2">
                            Confirm dataset category
                        </h3>
                        <p className="text-sm text-muted-foreground mb-4">
                            We couldn't confidently match this data to a department.
                            Pick the best fit, or continue with a General dashboard.
                        </p>
                        <select
                            className="w-full rounded-xl px-3 py-2.5 bg-card border border-border text-foreground text-sm mb-4"
                            value={confirmChoice}
                            onChange={(e) => setConfirmChoice(e.target.value)}
                        >
                            <option value="">Select a category…</option>
                            <option value="Sales">Sales</option>
                            <option value="HR">HR</option>
                            <option value="Operations">Operations</option>
                            <option value="Marketing">Marketing</option>
                            <option value="Business">General (Business)</option>
                        </select>
                        <div className="flex gap-3 justify-end">
                            <button
                                disabled={catUpdating}
                                onClick={() => void handleCategoryChange("Business")}
                                className="px-4 py-2 text-sm rounded-xl border border-border text-muted-foreground hover:text-foreground disabled:opacity-50"
                            >
                                Continue as General
                            </button>
                            <button
                                disabled={!confirmChoice || catUpdating}
                                onClick={() => void handleCategoryChange(confirmChoice)}
                                className="px-4 py-2 text-sm rounded-xl bg-primary text-primary-foreground disabled:opacity-50"
                            >
                                Confirm
                            </button>
                        </div>
                    </div>
                </div>
            )}
            <div className="mb-7 flex items-center justify-between gap-4 flex-wrap">
                <h2 className="text-2xl font-bold text-foreground">
                    {step7?.suggested_title || "AI Dashboard"}
                </h2>
                <div className="flex items-center gap-2 flex-wrap">
                    {charts.length > 0 && hasDateField && (
                        <DateRangeControl
                            fields={fields}
                            value={globalRange}
                            onChange={(r: GlobalRange) => setGlobalRange(r)}
                        />
                    )}
                    {charts.length > 0 && (
                        <>
                            <button
                                onClick={() => setShowAdd(true)}
                                className="flex items-center gap-1.5 text-sm px-3 py-2 rounded-xl bg-primary hover:bg-primary text-primary-foreground font-medium transition-colors"
                            >
                                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg>
                                Add graph
                            </button>
                            <button
                                onClick={() => setEditMode((e) => !e)}
                                className={`flex items-center gap-1.5 text-sm px-3 py-2 rounded-xl border transition-colors ${editMode ? "bg-card text-foreground border-border" : "border-border text-muted-foreground hover:text-foreground hover:border-border"}`}
                                title="Drag to reorder and resize charts"
                            >
                                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M5 9l-3 3 3 3M9 5l3-3 3 3M15 19l-3 3-3-3M19 9l3 3-3 3M2 12h20M12 2v20" /></svg>
                                {editMode ? "Done" : "Edit layout"}
                            </button>
                            {editMode && (
                                <button
                                    onClick={resetLayout}
                                    className="text-sm px-3 py-2 rounded-xl border border-border text-muted-foreground hover:text-foreground hover:border-border transition-colors"
                                    title="Reset to default sizes and order"
                                >
                                    Reset
                                </button>
                            )}
                        </>
                    )}
                    {ttsSupported && (
                        <AudioOverviewButton
                            size="sm"
                            datasetId={datasetId || undefined}
                            downloadName={(step7?.suggested_title || 'dashboard').toString().toLowerCase().replace(/\s+/g, '-').slice(0, 40)}
                            text={() => {
                                const title = step7?.suggested_title || "Dashboard";
                                const chartSummaries = charts.slice(0, 5).map(c => c.title).join(", ");
                                const kpiSummary = kpiCharts.map(c => c.title).join(", ");
                                return `${title}. This dashboard contains ${charts.length} charts. ${kpiSummary ? `Key metrics include ${kpiSummary}.` : ""} ${chartSummaries ? `Charts show ${chartSummaries}.` : ""}`;
                            }}
                        />
                    )}
                </div>
            </div>

            {showAdd && (
                <AddChartModal
                    fields={fields}
                    onCreate={(chart) => { addChart(chart); toast.success("Graph added"); }}
                    onClose={() => setShowAdd(false)}
                />
            )}

            {/* ── Pinned AI-generated visuals ── */}
            {pinnedAssets.length > 0 && (
                <div className="mb-8">
                    <div className="flex items-center gap-2 mb-3">
                        <span className="w-6 h-6 rounded-md bg-gradient-to-br from-[#5A5AF6] to-[#a855f7] flex items-center justify-center text-xs">✨</span>
                        <h3 className="text-sm font-semibold text-foreground">AI-Generated</h3>
                        <span className="text-[11px] text-muted-foreground">pinned by you · {pinnedAssets.length}</span>
                    </div>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        {pinnedAssets.map((asset) => (
                            asset.kind === 'chart' && asset.chart ? (
                                <div key={asset.id} className="relative">
                                    <button
                                        onClick={() => unpinAsset(asset.id)}
                                        className="absolute top-2.5 right-2.5 z-10 text-[11px] text-muted-foreground hover:text-red-600 px-2 py-1 rounded-md bg-card/95 hover:bg-red-500/10 border border-border shadow-sm transition-colors flex items-center gap-1 shrink-0"
                                        title="Unpin from dashboard"
                                    >
                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
                                        Unpin
                                    </button>
                                    <ChatChartRenderer chart={asset.chart} height={300} />
                                </div>
                            ) : asset.kind === '3d' && asset.visual3d ? (
                                <div key={asset.id} className="rounded-2xl border border-primary/25 bg-card overflow-hidden">
                                    <div className="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-border">
                                        <span className="text-sm font-semibold text-foreground truncate flex items-center gap-1.5">
                                            <span>🧊</span>{asset.title}
                                        </span>
                                        <button
                                            onClick={() => unpinAsset(asset.id)}
                                            className="text-[11px] text-muted-foreground hover:text-destructive px-2 py-1 rounded-md hover:bg-red-500/10 transition-colors flex items-center gap-1 shrink-0"
                                            title="Unpin from dashboard"
                                        >
                                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
                                            Unpin
                                        </button>
                                    </div>
                                    <div className="p-3">
                                        <Suspense fallback={<div className="h-[280px] flex items-center justify-center text-muted-foreground text-xs">Loading 3D…</div>}>
                                            <PinnedVisual3D visual={asset.visual3d} height={280} />
                                        </Suspense>
                                    </div>
                                </div>
                            ) : null
                        ))}
                    </div>
                </div>
            )}

            {/* ── Category auto-overridden info banner ── */}
            {showOverrideBanner && catDetection && (
                <div className="mb-6 flex items-start gap-4 bg-card border border-primary/40 rounded-2xl px-5 py-4">
                    <div className="w-9 h-9 bg-primary/15 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5">
                        <i className="fa-solid fa-wand-magic-sparkles text-primary text-sm"></i>
                    </div>
                    <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-foreground mb-1">
                            Category auto-corrected to <span className="text-primary">{catDetection.resolved_category}</span>
                        </p>
                        <p className="text-xs text-muted-foreground leading-relaxed">
                            You uploaded this as <span className="text-foreground font-medium">{prettifyName(catDetection.user_category ?? '')}</span>, but our AI
                            identified it as a <span className="text-foreground font-medium">{catDetection.detected_category}</span> dataset
                            with <span className="text-foreground font-medium">{Math.round((catDetection.confidence ?? 0) * 100)}%</span> confidence.
                            All analysis (segmentation, recommendations) will use the corrected category.
                        </p>
                    </div>
                    <button
                        onClick={() => handleCategoryChange(catDetection.user_category?.toLowerCase() === catDetection.detected_category?.toLowerCase()
                            ? catDetection.user_category ?? ''
                            : catDetection.user_category ?? '')}
                        className="text-xs text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap flex-shrink-0 cursor-pointer"
                        title="Revert to your original selection"
                    >
                        Revert to {prettifyName(catDetection.user_category ?? '')}
                    </button>
                    <button onClick={() => setCatBannerDismissed(true)} className="text-muted-foreground hover:text-muted-foreground cursor-pointer flex-shrink-0">
                        <i className="fa-solid fa-xmark text-sm"></i>
                    </button>
                </div>
            )}

            {/* ── Category mismatch warning banner (low confidence) ── */}
            {showMismatchBanner && catDetection && (
                <div className="mb-6 bg-muted border border-amber-500/40 rounded-2xl px-5 py-4">
                    <div className="flex items-start gap-4">
                        <div className="w-9 h-9 bg-amber-500/15 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5">
                            <i className="fa-solid fa-triangle-exclamation text-warning text-sm"></i>
                        </div>
                        <div className="flex-1 min-w-0">
                            <p className="text-sm font-semibold text-warning mb-1">
                                Possible category mismatch detected
                            </p>
                            <p className="text-xs text-amber-200/70 leading-relaxed mb-4">
                                You selected <span className="font-semibold text-amber-200">{prettifyName(catDetection.user_category ?? '')}</span> but our AI
                                suspects this may be a <span className="font-semibold text-amber-200">{catDetection.detected_category}</span> dataset
                                ({Math.round((catDetection.confidence ?? 0) * 100)}% confidence).
                                Using the wrong category may affect segmentation and recommendation quality.
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
                                    className="flex items-center gap-2 px-4 py-2 bg-card hover:bg-accent border border-amber-500/30 text-warning text-xs font-semibold rounded-lg transition-colors disabled:opacity-50 cursor-pointer"
                                >
                                    <i className="fa-solid fa-forward"></i> Keep {prettifyName(catDetection.user_category ?? '')} (may reduce accuracy)
                                </button>
                            </div>
                        </div>
                        <button onClick={() => setCatBannerDismissed(true)} className="text-muted-foreground hover:text-muted-foreground cursor-pointer flex-shrink-0">
                            <i className="fa-solid fa-xmark text-sm"></i>
                        </button>
                    </div>
                </div>
            )}

            {charts.length === 0 && (
                <div className="mb-8 p-6 rounded-xl border border-border bg-card">
                    <h2 className="text-foreground text-xl font-bold">No chart blueprint available yet.</h2>
                    <p className="text-muted-foreground mt-2 text-sm">Run a completed analysis to receive Step 7 chart specifications.</p>
                </div>
            )}

            {rowsLoading && (
                <div className="space-y-6 mb-6">
                    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
                        <LoadingSkeleton variant="kpi" count={4} />
                    </div>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <LoadingSkeleton variant="chart" count={4} />
                    </div>
                </div>
            )}

            {!rowsLoading && rowsError && (
                <div className="mb-6 p-4 rounded-xl border border-red-800 bg-muted text-red-200 text-sm">
                    {rowsError}
                </div>
            )}

            {!rowsLoading && !rowsError && kpiCharts.length > 0 && (
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 mb-8">
                    {kpiCharts.map((chart, index) => {
                        const kid = `k${index}`;
                        const effTitle = titles[kid] ?? chart.title;
                        return (
                            <div key={`kpi-${index}`}>
                                <KpiCard
                                    chart={effTitle === chart.title ? chart : { ...chart, title: effTitle }}
                                    columnsByKey={columnsByKey}
                                    labelByKey={labelByKey}
                                    aggData={getAggData(charts.indexOf(chart))}
                                    datasetId={datasetId}
                                    globalRange={globalRange}
                                    onTitleChange={(t) => setTitle(kid, t)}
                                />
                            </div>
                        );
                    })}
                </div>
            )}

            {!rowsLoading && !rowsError && (
                <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                    <SortableContext items={order} strategy={rectSortingStrategy}>
                        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
                            {order.map((id) => {
                                const isCustom = id.startsWith("c");
                                const layout = getLayout(id);
                                let child: React.ReactNode;

                                if (isCustom) {
                                    const cc = customCharts.find((c) => c.id === id);
                                    if (!cc) return null;
                                    const effTitle = titles[id] ?? cc.title;
                                    if (cc.chart_type === "kpi") {
                                        const kpiSpec: ChartSpec = {
                                            chart_type: "kpi_card", title: effTitle, x_axis: null,
                                            y_axis: cc.y_axis, columns: cc.y_axis ? [cc.y_axis] : [],
                                            reason: "Custom KPI",
                                        };
                                        child = (
                                            <KpiCard
                                                chart={kpiSpec}
                                                columnsByKey={columnsByKey}
                                                labelByKey={labelByKey}
                                                aggData={[]}
                                                datasetId={datasetId}
                                                globalRange={globalRange}
                                                agg={cc.agg}
                                                autoFetch
                                                onTitleChange={(t) => setTitle(id, t)}
                                            />
                                        );
                                    } else {
                                        const yLabel = cc.y_axis ? (labelByKey.get(cc.y_axis) ?? prettifyName(cc.y_axis)) : "Value";
                                        child = (
                                            <InteractiveChartCard
                                                datasetId={datasetId} title={effTitle} reason="Custom graph"
                                                initialType={cc.chart_type} initialX={cc.x_axis} initialY={cc.y_axis}
                                                initialData={[]} fields={fields} yLabel={yLabel} globalRange={globalRange}
                                                storageId={cc.id}
                                                initialOptions={{
                                                    agg: cc.agg, breakdown: cc.breakdown,
                                                    time_grain: cc.time_grain, top_n: cc.top_n,
                                                    stacked: cc.chart_type === "stacked_bar",
                                                    cumulative: cc.cumulative ?? false,
                                                }}
                                                height={heightPx(layout.height)}
                                                onTitleChange={(t) => setTitle(id, t)}
                                            />
                                        );
                                    }
                                } else {
                                    const idx = Number(id.slice(1));
                                    const chart = charts[idx];
                                    if (!chart) return null;
                                    const chartType = asChartType(chart.chart_type);
                                    if (!chartType) return null;
                                    const yLabel = chart.y_axis ? (labelByKey.get(chart.y_axis) ?? prettifyName(chart.y_axis)) : "Value";
                                    child = (
                                        <InteractiveChartCard
                                            datasetId={datasetId} title={titles[id] ?? chart.title} reason={chart.reason}
                                            initialType={chartType} initialX={chart.x_axis} initialY={chart.y_axis}
                                            initialData={getAggData(idx)} fields={fields} yLabel={yLabel} globalRange={globalRange}
                                            storageId={id}
                                            height={heightPx(layout.height)}
                                            onTitleChange={(t) => setTitle(id, t)}
                                        />
                                    );
                                }

                                return (
                                    <SortableChartCard
                                        key={id}
                                        id={id}
                                        span={layout.span}
                                        height={layout.height}
                                        editMode={editMode}
                                        isCustom={isCustom}
                                        onSpan={(s) => setSpan(id, s)}
                                        onHeight={(h) => setHeight(id, h)}
                                        onRemove={isCustom ? () => removeChart(id) : undefined}
                                    >
                                        {child}
                                    </SortableChartCard>
                                );
                            })}
                        </div>
                    </SortableContext>
                </DndContext>
            )}
        </div>
    );
}
