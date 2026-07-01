import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import type { Field } from "./InteractiveChartCard";
import type { CustomChart } from "./useDashboardConfig";

const CHART_TYPES: { value: string; label: string }[] = [
    { value: "kpi", label: "KPI (single value)" },
    { value: "line", label: "Line" },
    { value: "area", label: "Area" },
    { value: "bar", label: "Column" },
    { value: "horizontal_bar", label: "Bar (horizontal)" },
    { value: "stacked_bar", label: "Stacked bar" },
    { value: "pie", label: "Pie" },
    { value: "donut", label: "Donut" },
    { value: "treemap", label: "Treemap" },
    { value: "funnel", label: "Funnel" },
];
const KPI_TITLE_PREFIX: Record<string, string> = {
    sum: "Total", avg: "Average", count: "Count", min: "Min", max: "Max", median: "Median",
};
const AGGS = [
    { value: "sum", label: "Sum" },
    { value: "avg", label: "Average" },
    { value: "count", label: "Count" },
    { value: "min", label: "Min" },
    { value: "max", label: "Max" },
    { value: "median", label: "Median" },
];
const GRAINS = [
    { value: "", label: "Auto" },
    { value: "day", label: "Day" },
    { value: "week", label: "Week" },
    { value: "month", label: "Month" },
    { value: "quarter", label: "Quarter" },
    { value: "year", label: "Year" },
];
const PART_TO_WHOLE = new Set(["pie", "donut", "treemap", "funnel"]);

interface Props {
    fields: Field[];
    onCreate: (chart: Omit<CustomChart, "id">) => void;
    onClose: () => void;
}

export default function AddChartModal({ fields, onCreate, onClose }: Props) {
    const numericFields = useMemo(() => fields.filter((f) => f.kind === "numeric"), [fields]);
    const dateFields = useMemo(() => fields.filter((f) => f.kind === "date"), [fields]);
    const dimFields = useMemo(() => fields.filter((f) => f.kind === "dim"), [fields]);
    const xFields = useMemo(() => [...dimFields, ...dateFields], [dimFields, dateFields]);

    const [title, setTitle] = useState("");
    const [chartType, setChartType] = useState("bar");
    const [xAxis, setXAxis] = useState<string>(xFields[0]?.key ?? "");
    const [yAxis, setYAxis] = useState<string>(numericFields[0]?.key ?? "");
    const [agg, setAgg] = useState("sum");
    const [breakdown, setBreakdown] = useState<string>("");
    const [timeGrain, setTimeGrain] = useState<string>("");
    const [topN, setTopN] = useState(12);
    const [cumulative, setCumulative] = useState(false);

    const isKpi = chartType === "kpi";
    const isPTW = PART_TO_WHOLE.has(chartType);
    const canCumulate = chartType === "line" || chartType === "area";
    const xIsDate = dateFields.some((f) => f.key === xAxis);
    const labelOf = (key: string) => fields.find((f) => f.key === key)?.label ?? key;

    const canCreate = isKpi
        ? (agg === "count" || Boolean(yAxis))
        : (Boolean(xAxis) && xFields.length > 0);

    const submit = () => {
        if (!canCreate) return;
        const autoTitle = isKpi
            ? (agg === "count" ? "Row count" : `${KPI_TITLE_PREFIX[agg] ?? ""} ${labelOf(yAxis)}`.trim())
            : `${agg === "count" ? "Count" : labelOf(yAxis)} by ${labelOf(xAxis)}`;
        const useCumulative = canCumulate && cumulative;
        onCreate({
            title: title.trim() || (useCumulative ? `Cumulative ${labelOf(yAxis)}` : autoTitle),
            chart_type: chartType,
            x_axis: isKpi ? null : (xAxis || null),
            y_axis: agg === "count" ? null : (yAxis || null),
            agg,
            breakdown: useCumulative ? null : (isKpi || isPTW ? null : (breakdown || null)),
            time_grain: !isKpi && xIsDate ? (timeGrain || null) : null,
            top_n: topN,
            cumulative: useCumulative,
        });
        onClose();
    };

    const selectCls = "w-full text-sm bg-card border border-border rounded-lg px-3 py-2 text-foreground focus:outline-none focus:border-info/40";
    const lbl = "block text-[11px] font-semibold text-muted-foreground uppercase tracking-wide mb-1";

    return createPortal(
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
            <div className="w-full max-w-lg rounded-2xl bg-card border border-border shadow-xl overflow-hidden" onClick={(e) => e.stopPropagation()}>
                <div className="flex items-center justify-between px-5 py-4 border-b border-border">
                    <h3 className="text-base font-semibold text-foreground">Add new graph</h3>
                    <button onClick={onClose} className="text-muted-foreground hover:text-muted-foreground" title="Close">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
                    </button>
                </div>

                <div className="p-5 space-y-4 max-h-[70vh] overflow-y-auto">
                    <div>
                        <label className={lbl}>Title</label>
                        <input className={selectCls} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Auto-generated if left blank" />
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label className={lbl}>Chart type</label>
                            <select className={selectCls} value={chartType} onChange={(e) => setChartType(e.target.value)}>
                                {CHART_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                            </select>
                        </div>
                        {!isKpi && (
                            <div>
                                <label className={lbl}>{isPTW ? "Category" : "X axis"}</label>
                                <select className={selectCls} value={xAxis} onChange={(e) => setXAxis(e.target.value)}>
                                    {xFields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                                </select>
                            </div>
                        )}
                        <div>
                            <label className={lbl}>Measure</label>
                            <select className={selectCls} value={yAxis} onChange={(e) => setYAxis(e.target.value)} disabled={agg === "count"}>
                                {numericFields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                            </select>
                        </div>
                        <div>
                            <label className={lbl}>Aggregate</label>
                            <select className={selectCls} value={agg} onChange={(e) => setAgg(e.target.value)}>
                                {AGGS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
                            </select>
                        </div>
                        {!isKpi && !isPTW && (
                            <div>
                                <label className={lbl}>Break down by</label>
                                <select className={selectCls} value={breakdown} onChange={(e) => setBreakdown(e.target.value)}>
                                    <option value="">None</option>
                                    {dimFields.filter((f) => f.key !== xAxis).map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                                </select>
                            </div>
                        )}
                        {!isKpi && xIsDate && (
                            <div>
                                <label className={lbl}>Interval</label>
                                <select className={selectCls} value={timeGrain} onChange={(e) => setTimeGrain(e.target.value)}>
                                    {GRAINS.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
                                </select>
                            </div>
                        )}
                        {!isKpi && (
                            <div>
                                <label className={lbl}>Top N</label>
                                <select className={selectCls} value={String(topN)} onChange={(e) => setTopN(Number(e.target.value))}>
                                    {[5, 8, 10, 15, 20, 30, 50].map((n) => <option key={n} value={n}>{n}</option>)}
                                </select>
                            </div>
                        )}
                    </div>
                    {canCumulate && (
                        <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer select-none">
                            <input type="checkbox" checked={cumulative} onChange={(e) => setCumulative(e.target.checked)} className="accent-[#2E6BE6]" />
                            Running total (cumulative) — rises over time until it reaches the all-time total
                        </label>
                    )}
                    {isKpi && (
                        <p className="text-xs text-muted-foreground">
                            Shows a single aggregated value (e.g. Total Revenue). Pick a measure and how to aggregate it. It respects the dashboard date filter.
                        </p>
                    )}
                </div>

                <div className="flex justify-end gap-3 px-5 py-4 border-t border-border bg-muted">
                    <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg border border-border text-muted-foreground hover:bg-accent">Cancel</button>
                    <button onClick={submit} disabled={!canCreate} className="px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground font-medium hover:bg-primary/90 disabled:opacity-50">Add graph</button>
                </div>
            </div>
        </div>,
        document.body
    );
}
