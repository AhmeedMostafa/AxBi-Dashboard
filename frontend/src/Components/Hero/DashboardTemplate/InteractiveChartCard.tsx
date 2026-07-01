import { useEffect, useMemo, useRef, useState } from "react";
import {
    Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart,
    Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis, LabelList,
    Treemap, FunnelChart, Funnel, ComposedChart, RadialBarChart, RadialBar, PolarAngleAxis,
} from "recharts";
import { customizeChart } from "../../../api.js";
import {
    CHART_PALETTE, CHART_PRIMARY,
    ChartTooltip, formatCompact, showDataLabels, colorAt,
    useChartTheme, CHART_CARD_CLASS, CHART_CARD_TITLE_CLASS, CHART_SELECT_CLASS,
    type ChartThemeTokens,
} from "../../../lib/chartTheme";
import DateRangeControl from "./DateRangeControl";
import EditableTitle from "./EditableTitle";
import { LogoSpinner } from "../../ui/LogoSpinner";
import { resolveRange } from "./dateRange";
import type { GlobalRange } from "./useDashboardConfig";

const CUSTOMIZABLE = new Set(["line", "area", "bar", "horizontal_bar", "stacked_bar", "pie", "donut", "treemap", "funnel"]);

export type FieldKind = "numeric" | "date" | "dim" | "other";
export interface Field { key: string; label: string; kind: FieldKind; }

type SeriesDef = { key: string; label: string };

interface Opts {
    type: string;
    x_axis: string | null;
    y_axis: string | null;
    agg: string;
    breakdown: string | null;
    time_grain: string | null;
    top_n: number;
    sort: string;
    stacked: boolean;
    cumulative: boolean;         // running total over the (time-ordered) x-axis
    range: GlobalRange | null;   // per-graph date-range override; null = inherit global
}

interface Props {
    datasetId: string;
    title: string;
    reason: string;
    initialType: string;
    initialX: string | null;
    initialY: string | null;
    initialData: any[];
    fields: Field[];
    yLabel?: string;
    height?: number;
    globalRange?: GlobalRange | null;
    initialOptions?: Partial<Opts>;
    storageId?: string;
    onTitleChange?: (next: string) => void;
}

const INTERACTIVE_TYPES: { value: string; label: string }[] = [
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
const AGGS = [
    { value: "sum", label: "Sum" },
    { value: "avg", label: "Average" },
    { value: "count", label: "Count" },
    { value: "min", label: "Min" },
    { value: "max", label: "Max" },
    { value: "median", label: "Median" },
];
const GRAINS = [
    { value: "day", label: "Day" },
    { value: "week", label: "Week" },
    { value: "month", label: "Month" },
    { value: "quarter", label: "Quarter" },
    { value: "year", label: "Year" },
];
const SORTS = [
    { value: "value_desc", label: "Value ↓" },
    { value: "value_asc", label: "Value ↑" },
    { value: "label_asc", label: "Label A–Z" },
    { value: "label_desc", label: "Label Z–A" },
];
const PART_TO_WHOLE = new Set(["pie", "donut", "treemap", "funnel"]);
const CAT_AXIS_TYPES = new Set(["bar", "horizontal_bar", "stacked_bar"]);

function sumValues(rows: { value?: number }[]): number {
    return rows.reduce((s, r) => s + (Number(r.value) || 0), 0);
}

/** Drop corrupted per-chart overrides saved in older builds. */
function sanitizeSavedOpts(saved: Opts, defaults: Opts, fields: Field[]): Opts {
    const next = { ...saved };
    const CAT = CAT_AXIS_TYPES;

    if (CAT.has(next.type) && next.x_axis) {
        const xKind = fields.find((f) => f.key === next.x_axis)?.kind;
        if (xKind === "numeric") {
            next.x_axis =
                defaults.x_axis && fields.find((f) => f.key === defaults.x_axis)?.kind !== "numeric"
                    ? defaults.x_axis
                    : fields.find((f) => f.kind === "dim")?.key ?? next.x_axis;
        }
    }

    if (next.y_axis) {
        const yKind = fields.find((f) => f.key === next.y_axis)?.kind;
        if (yKind !== "numeric") {
            next.y_axis = defaults.y_axis ?? fields.find((f) => f.kind === "numeric")?.key ?? null;
        }
    } else if (defaults.y_axis) {
        next.y_axis = defaults.y_axis;
    }

    if (!next.y_axis) next.agg = "count";
    else if (defaults.y_axis && next.y_axis === defaults.y_axis) next.agg = defaults.agg;

    // Per-chart date overrides often zero-out historical datasets (e.g. last-30-days on 2021 data).
    next.range = null;
    next.breakdown = null;

    return next;
}

export default function InteractiveChartCard({
    datasetId, title, reason, initialType, initialX, initialY, initialData, fields, yLabel, height = 290, globalRange, initialOptions, storageId, onTitleChange,
}: Props) {
    const storageKey = `bi:chartopts:v2:${datasetId}:${storageId ?? title}`;
    const customizable = CUSTOMIZABLE.has(initialType);
    const initialOptsKey = JSON.stringify(initialOptions ?? null);

    const defaults: Opts = useMemo(() => ({
        type: initialType,
        x_axis: initialX,
        y_axis: initialY,
        agg: "sum",
        breakdown: null,
        time_grain: null,
        top_n: 12,
        sort: "value_desc",
        stacked: initialType === "stacked_bar",
        cumulative: false,
        range: null,
        ...(initialOptions ?? {}),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }), [initialType, initialX, initialY, initialOptsKey]);

    const baseline = useMemo(() => JSON.stringify(defaults), [defaults]);

    const [opts, setOpts] = useState<Opts>(() => {
        try {
            const saved = localStorage.getItem(storageKey);
            if (saved) return { ...defaults, ...JSON.parse(saved) };
        } catch { /* ignore */ }
        return defaults;
    });

    const numericFields = fields.filter((f) => f.kind === "numeric");
    const dateFields = fields.filter((f) => f.kind === "date");
    const dimFields = fields.filter((f) => f.kind === "dim");
    const xFields = [...dimFields, ...dateFields];

    // Repair bad saved customizations once column metadata is available.
    useEffect(() => {
        if (!customizable || fields.length === 0) return;
        setOpts((prev) => {
            const next = sanitizeSavedOpts(prev, defaults, fields);
            if (JSON.stringify(next) === JSON.stringify(prev)) return prev;
            try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* ignore */ }
            return next;
        });
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [customizable, fields.length, datasetId, storageId, baseline]);

    const [data, setData] = useState<any[]>(initialData);
    const [series, setSeries] = useState<SeriesDef[]>([{ key: "value", label: initialY || "Value" }]);
    const [mode, setMode] = useState<"single" | "multi">("single");
    const [loading, setLoading] = useState(false);
    const [open, setOpen] = useState(false);
    const reqId = useRef(0);

    const xIsDate = dateFields.some((f) => f.key === opts.x_axis);
    const isPTW = PART_TO_WHOLE.has(opts.type);
    const canStack = opts.breakdown != null && ["bar", "stacked_bar", "area", "horizontal_bar"].includes(opts.type);
    const canCumulate = opts.type === "line" || opts.type === "area";

    // Effective date range = per-graph override, else inherited global.
    const effectiveRange = opts.range ?? globalRange ?? null;
    const effectiveDateField = effectiveRange?.field ?? (xIsDate ? opts.x_axis : null);
    const resolved = resolveRange(effectiveRange);
    const rangeActive = Boolean(effectiveDateField && (resolved.from || resolved.to));
    const globalKey = JSON.stringify(globalRange ?? null);

    useEffect(() => {
        if (!customizable) return;
        try { localStorage.setItem(storageKey, JSON.stringify(opts)); } catch { /* ignore */ }

        const atBaseline = JSON.stringify(opts) === baseline;
        const coreMatch =
            opts.x_axis === defaults.x_axis &&
            opts.y_axis === defaults.y_axis &&
            opts.agg === defaults.agg &&
            !opts.breakdown &&
            !opts.range;

        if ((atBaseline || coreMatch) && !rangeActive && initialData.length > 0) {
            setData(initialData);
            setSeries([{ key: "value", label: initialY || "Value" }]);
            setMode("single");
            setLoading(false);
            return;
        }

        const myId = ++reqId.current;
        setLoading(true);
        const payload = {
            chart_type: opts.type,
            x_axis: opts.x_axis,
            y_axis: opts.y_axis,
            agg: opts.y_axis ? opts.agg : "count",
            breakdown: isPTW ? null : opts.breakdown,
            time_grain: xIsDate ? opts.time_grain : null,
            top_n: opts.top_n,
            sort: opts.sort,
            cumulative: canCumulate ? opts.cumulative : false,
            date_field: rangeActive ? effectiveDateField : null,
            date_from: rangeActive ? resolved.from : null,
            date_to: rangeActive ? resolved.to : null,
        };
        customizeChart(datasetId, payload)
            .then((res: any) => {
                if (myId !== reqId.current) return;
                if (res?.mode === "multi") {
                    setMode("multi");
                    setSeries(Array.isArray(res.series) ? res.series : []);
                    setData(Array.isArray(res.data) ? res.data : []);
                } else {
                    const rows = Array.isArray(res?.data) ? res.data : [];
                    const apiTotal = sumValues(rows);
                    const seedTotal = sumValues(initialData);
                    const isCatBar = CAT_AXIS_TYPES.has(opts.type);
                    const notBaseline = JSON.stringify(opts) !== baseline;
                    // A saved customization that yields all-zero bars is almost always
                    // stale/corrupt (bad measure or an out-of-range date). Revert to the
                    // AI's default chart so the card self-heals instead of showing zeros.
                    if (isCatBar && notBaseline && rows.length > 0 && apiTotal === 0) {
                        try { localStorage.removeItem(storageKey); } catch { /* ignore */ }
                        setMode("single");
                        setSeries([{ key: "value", label: initialY || "Value" }]);
                        setData(seedTotal > 0 ? initialData : rows);
                        setOpts(defaults);
                        return;
                    }
                    setMode("single");
                    setSeries([{ key: "value", label: res?.y_label || opts.y_axis || "Value" }]);
                    setData(rows);
                }
            })
            .catch(() => { if (myId === reqId.current) setData([]); })
            .finally(() => { if (myId === reqId.current) setLoading(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [opts, baseline, globalKey]);

    const set = (patch: Partial<Opts>) => setOpts((p) => ({ ...p, ...patch }));
    const reset = () => {
        try { localStorage.removeItem(storageKey); } catch { /* ignore */ }
        setOpts(defaults);
    };
    const ct = useChartTheme();
    const dirty = JSON.stringify(opts) !== baseline;

    // Specialized read-only charts (combo / histogram / pareto / radial) — no toolbar.
    if (!customizable) {
        return (
            <div className={CHART_CARD_CLASS}>
                <div className="mb-3"><EditableTitle value={title} onChange={onTitleChange} className={CHART_CARD_TITLE_CLASS} /></div>
                <div className="h-[290px] w-full" style={{ height }}>
                    {initialData.length === 0
                        ? <div className="h-full flex items-center justify-center text-sm text-muted-foreground">No data.</div>
                        : initialType === "radial"
                            ? <RadialGauge data={initialData} label={yLabel || initialY || "Metric"} ct={ct} />
                            : <ResponsiveContainer width="100%" height="100%">{renderSpecial(initialType, initialData, yLabel || initialY || "Value", ct)}</ResponsiveContainer>}
                </div>
                <div className="mt-3 pt-3 border-t border-border"><p className="text-xs text-muted-foreground italic">{reason}</p></div>
            </div>
        );
    }

    return (
        <div className={CHART_CARD_CLASS}>
            <div className="mb-3 flex items-start justify-between gap-2">
                <EditableTitle value={title} onChange={onTitleChange} className={CHART_CARD_TITLE_CLASS} />
                <button
                    onClick={() => setOpen((o) => !o)}
                    className={`shrink-0 flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border transition-colors ${open || dirty ? "border-info/40 text-info bg-info/10" : "border-border text-muted-foreground hover:text-foreground hover:border-border"}`}
                    title="Customize chart"
                >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>
                    Customize
                </button>
            </div>

            {open && (
                <div className="mb-3 p-3 rounded-xl bg-muted border border-border flex flex-wrap gap-x-4 gap-y-2.5">
                    <Ctrl label="Chart">
                        <Select value={opts.type} onChange={(v) => set({ type: v, stacked: v === "stacked_bar" ? true : opts.stacked })} options={INTERACTIVE_TYPES} />
                    </Ctrl>
                    <Ctrl label={isPTW ? "Category" : "X axis"}>
                        <Select value={opts.x_axis ?? ""} onChange={(v) => set({ x_axis: v || null })} options={xFields.map((f) => ({ value: f.key, label: f.label }))} />
                    </Ctrl>
                    <Ctrl label="Measure">
                        <Select value={opts.y_axis ?? ""} onChange={(v) => set({ y_axis: v || null })} options={numericFields.map((f) => ({ value: f.key, label: f.label }))} allowEmpty emptyLabel="Count of rows" />
                    </Ctrl>
                    <Ctrl label="Aggregate">
                        <Select value={opts.agg} onChange={(v) => set({ agg: v })} options={AGGS} />
                    </Ctrl>
                    {!isPTW && (
                        <Ctrl label="Break down by">
                            <Select value={opts.breakdown ?? ""} onChange={(v) => set({ breakdown: v || null })} options={dimFields.filter((f) => f.key !== opts.x_axis).map((f) => ({ value: f.key, label: f.label }))} allowEmpty emptyLabel="None" />
                        </Ctrl>
                    )}
                    {xIsDate && (
                        <Ctrl label="Interval">
                            <Select value={opts.time_grain ?? ""} onChange={(v) => set({ time_grain: v || null })} options={GRAINS} allowEmpty emptyLabel="Auto" />
                        </Ctrl>
                    )}
                    <Ctrl label="Top N">
                        <Select value={String(opts.top_n)} onChange={(v) => set({ top_n: Number(v) })} options={[5, 8, 10, 15, 20, 30, 50].map((n) => ({ value: String(n), label: String(n) }))} />
                    </Ctrl>
                    {!xIsDate && (
                        <Ctrl label="Sort">
                            <Select value={opts.sort} onChange={(v) => set({ sort: v })} options={SORTS} />
                        </Ctrl>
                    )}
                    {canStack && (
                        <label className="flex items-end gap-1.5 text-xs text-muted-foreground pb-1 cursor-pointer select-none">
                            <input type="checkbox" checked={opts.stacked} onChange={(e) => set({ stacked: e.target.checked })} className="accent-[#2E6BE6]" />
                            Stacked
                        </label>
                    )}
                    {canCumulate && (
                        <label className="flex items-end gap-1.5 text-xs text-muted-foreground pb-1 cursor-pointer select-none" title="Running total — the line rises until it reaches the all-time total">
                            <input type="checkbox" checked={opts.cumulative} onChange={(e) => set({ cumulative: e.target.checked })} className="accent-[#2E6BE6]" />
                            Running total
                        </label>
                    )}
                    {dateFields.length > 0 && (
                        <Ctrl label="Date range">
                            <div className="flex items-center gap-2">
                                {opts.range == null ? (
                                    <button
                                        onClick={() => set({ range: { field: globalRange?.field ?? dateFields[0].key, preset: "last30", from: null, to: null } })}
                                        className="text-xs px-2 py-1.5 rounded-md border border-border text-muted-foreground hover:text-foreground hover:border-border"
                                        title="Override the dashboard date range for this chart"
                                    >
                                        {globalRange && globalRange.preset !== "all" ? "Inheriting global · Override" : "Override"}
                                    </button>
                                ) : (
                                    <>
                                        <DateRangeControl fields={fields} value={opts.range} onChange={(r) => set({ range: r })} compact />
                                        <button onClick={() => set({ range: null })} className="text-[11px] text-muted-foreground hover:text-red-600 underline decoration-dotted" title="Use global date range">Clear</button>
                                    </>
                                )}
                            </div>
                        </Ctrl>
                    )}
                    {dirty && (
                        <button onClick={reset} className="self-end text-xs text-muted-foreground hover:text-red-600 pb-1 underline decoration-dotted">Reset</button>
                    )}
                </div>
            )}

            <div className="relative h-[290px] w-full" style={{ height }}>
                {loading && (
                    <div className="absolute inset-0 z-10 flex items-center justify-center bg-card/70 rounded-lg">
                        <LogoSpinner size={32} />
                    </div>
                )}
                {data.length === 0 && !loading ? (
                    <div className="h-full flex items-center justify-center text-sm text-muted-foreground">No data for this selection.</div>
                ) : (
                    <ResponsiveContainer width="100%" height="100%">
                        {renderChart(opts, mode, data, series, ct)}
                    </ResponsiveContainer>
                )}
            </div>

            <div className="mt-3 pt-3 border-t border-border">
                <p className="text-xs text-muted-foreground italic">{reason}</p>
            </div>
        </div>
    );
}

function renderChart(opts: Opts, mode: "single" | "multi", data: any[], series: SeriesDef[], ct: ChartThemeTokens) {
    const type = opts.type;
    const many = data.length > 7;
    const catX = {
        dataKey: "label",
        tick: many ? ct.axisTickSmall : ct.axisTick,
        tickLine: false,
        axisLine: { stroke: ct.grid },
        interval: many ? ("preserveStartEnd" as const) : (0 as const),
        ...(many && !["line", "area"].includes(type) ? { angle: -28, textAnchor: "end" as const, height: 60 } : {}),
    };
    const numY = { tick: ct.axisTick, tickLine: false, axisLine: false as const, tickFormatter: formatCompact, width: 48 };
    const keys = mode === "multi" ? series : [{ key: "value", label: series[0]?.label || "Value" }];
    const showLabels = mode === "single" && showDataLabels(data.length, "bar");

    if (type === "line") {
        return (
            <LineChart data={data} margin={{ top: 12, right: 16, bottom: 4, left: 0 }}>
                <CartesianGrid vertical={false} stroke={ct.grid} />
                <XAxis {...catX} />
                <YAxis {...numY} />
                <Tooltip content={<ChartTooltip />} cursor={{ stroke: ct.grid }} />
                {mode === "multi" && <Legend wrapperStyle={ct.legendStyle} />}
                {keys.map((s, i) => (
                    <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={mode === "multi" ? colorAt(i) : CHART_PRIMARY} strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} />
                ))}
            </LineChart>
        );
    }
    if (type === "area") {
        return (
            <AreaChart data={data} margin={{ top: 12, right: 16, bottom: 4, left: 0 }}>
                <CartesianGrid vertical={false} stroke={ct.grid} />
                <XAxis {...catX} />
                <YAxis {...numY} />
                <Tooltip content={<ChartTooltip />} cursor={{ stroke: ct.grid }} />
                {mode === "multi" && <Legend wrapperStyle={ct.legendStyle} />}
                {keys.map((s, i) => (
                    <Area key={s.key} type="monotone" dataKey={s.key} name={s.label}
                        stackId={opts.stacked ? "s" : undefined}
                        stroke={mode === "multi" ? colorAt(i) : CHART_PRIMARY}
                        fill={mode === "multi" ? colorAt(i) : CHART_PRIMARY} fillOpacity={0.22} strokeWidth={2.5} />
                ))}
            </AreaChart>
        );
    }
    if (type === "horizontal_bar") {
        return (
            <BarChart layout="vertical" data={data} margin={{ top: 4, right: 24, bottom: 4, left: 8 }}>
                <CartesianGrid horizontal={false} stroke={ct.grid} />
                <XAxis type="number" tick={ct.axisTick} tickLine={false} axisLine={{ stroke: ct.grid }} tickFormatter={formatCompact} />
                <YAxis type="category" dataKey="label" tick={ct.axisTickSmall} tickLine={false} axisLine={false} width={130} />
                <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(46,107,230,0.06)" }} />
                {mode === "multi" && <Legend wrapperStyle={ct.legendStyle} />}
                {keys.map((s, i) => (
                    <Bar key={s.key} dataKey={s.key} name={s.label} stackId={opts.stacked ? "s" : undefined}
                        radius={[0, 4, 4, 0]} maxBarSize={30} fill={mode === "multi" ? colorAt(i) : CHART_PRIMARY}>
                        {showLabels && <LabelList dataKey={s.key} position="right" formatter={(v: any) => formatCompact(Number(v))} style={ct.dataLabelStyle} />}
                    </Bar>
                ))}
            </BarChart>
        );
    }
    if (type === "bar" || type === "stacked_bar") {
        const stacked = opts.stacked || type === "stacked_bar";
        return (
            <BarChart data={data} margin={{ top: showLabels ? 20 : 8, right: 16, bottom: 4, left: 0 }}>
                <CartesianGrid vertical={false} stroke={ct.grid} />
                <XAxis {...catX} />
                <YAxis {...numY} />
                <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(46,107,230,0.06)" }} />
                {mode === "multi" && <Legend wrapperStyle={ct.legendStyle} />}
                {keys.map((s, i) => (
                    <Bar key={s.key} dataKey={s.key} name={s.label} stackId={stacked && mode === "multi" ? "s" : undefined}
                        radius={stacked && mode === "multi" ? [0, 0, 0, 0] : [6, 6, 0, 0]} maxBarSize={56}
                        fill={mode === "multi" ? colorAt(i) : CHART_PRIMARY}>
                        {mode === "single" && <>{data.map((_, idx) => <Cell key={idx} fill={CHART_PRIMARY} />)}</>}
                        {showLabels && <LabelList dataKey={s.key} position="top" formatter={(v: any) => formatCompact(Number(v))} style={ct.dataLabelStyle} />}
                    </Bar>
                ))}
            </BarChart>
        );
    }
    if (type === "treemap") {
        const tmData = data.map((d, i) => ({ name: d.name ?? d.label ?? "", value: Number(d.value) || 0, __fill: colorAt(i) }));
        return (
            <Treemap data={tmData} dataKey="value" nameKey="name" stroke={ct.pieStroke} content={<TreemapCell stroke={ct.pieStroke} />} isAnimationActive={false}>
                <Tooltip content={<ChartTooltip />} />
            </Treemap>
        );
    }
    if (type === "funnel") {
        const fData = data.map((d, i) => ({ name: d.name ?? d.label ?? "", value: Number(d.value) || 0, fill: colorAt(i) }));
        return (
            <FunnelChart margin={{ top: 8, right: 90, bottom: 8, left: 8 }}>
                <Tooltip content={<ChartTooltip />} />
                <Funnel dataKey="value" nameKey="name" data={fData} isAnimationActive>
                    <LabelList position="right" fill={ct.axisStrong} stroke="none" dataKey="name" style={{ fontSize: 12, fontWeight: 600 }} />
                    <LabelList position="inside" fill="#ffffff" stroke="none" dataKey="value" formatter={(v: any) => formatCompact(Number(v))} style={{ fontSize: 11, fontWeight: 700 }} />
                </Funnel>
            </FunnelChart>
        );
    }
    // pie / donut
    const isDonut = type === "donut";
    return (
        <PieChart>
            <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={102} innerRadius={isDonut ? 64 : 0}
                paddingAngle={2} stroke={ct.pieStroke} strokeWidth={2}
                label={({ percent }: any) => (percent > 0.05 ? `${Math.round(percent * 100)}%` : "")} labelLine={false}>
                {data.map((_, i) => <Cell key={i} fill={colorAt(i)} />)}
            </Pie>
            <Tooltip content={<ChartTooltip />} />
            <Legend wrapperStyle={ct.legendStyle} />
        </PieChart>
    );
}

function TreemapCell(props: any) {
    const { x, y, width, height, name, value, __fill, index, stroke = "#ffffff" } = props;
    const fill = __fill || colorAt(index ?? 0);
    return (
        <g>
            <rect x={x} y={y} width={width} height={height} fill={fill} stroke={stroke} strokeWidth={2} rx={3} />
            {width > 64 && height > 30 && (
                <>
                    <text x={x + 8} y={y + 18} fill="#ffffff" fontSize={12} fontWeight={600}>{String(name ?? "").slice(0, 20)}</text>
                    <text x={x + 8} y={y + 34} fill="#ffffff" fillOpacity={0.85} fontSize={11}>{formatCompact(Number(value) || 0)}</text>
                </>
            )}
        </g>
    );
}

function renderSpecial(type: string, data: any[], yLabel: string, ct: ChartThemeTokens) {
    if (type === "combo") {
        const lineIsPct = data[0]?.line_is_pct ?? true;
        const lineName = data[0]?.line_name ?? (lineIsPct ? "Growth %" : "Secondary");
        const many = data.length > 7;
        return (
            <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
                <CartesianGrid vertical={false} stroke={ct.grid} />
                <XAxis dataKey="label" tick={many ? ct.axisTickSmall : ct.axisTick} tickLine={false} axisLine={{ stroke: ct.grid }} interval={many ? "preserveStartEnd" : 0} {...(many ? { angle: -28, textAnchor: "end" as const, height: 56 } : {})} />
                <YAxis yAxisId="left" tick={ct.axisTick} tickLine={false} axisLine={false} tickFormatter={formatCompact} width={48} />
                <YAxis yAxisId="right" orientation="right" tick={ct.axisTick} tickLine={false} axisLine={false} tickFormatter={(v: number) => (lineIsPct ? `${v}%` : formatCompact(v))} width={44} />
                <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(46,107,230,0.06)" }} />
                <Legend wrapperStyle={ct.legendStyle} />
                <Bar yAxisId="left" dataKey="value" name={yLabel} fill={CHART_PRIMARY} radius={[6, 6, 0, 0]} maxBarSize={48} />
                <Line yAxisId="right" type="monotone" dataKey="line" name={lineName} stroke={CHART_PALETTE[2]} strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} />
            </ComposedChart>
        );
    }
    if (type === "histogram") {
        return (
            <BarChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                <CartesianGrid vertical={false} stroke={ct.grid} />
                <XAxis dataKey="label" tick={ct.axisTickSmall} tickLine={false} axisLine={{ stroke: ct.grid }} interval={0} angle={-30} textAnchor="end" height={56} />
                <YAxis tick={ct.axisTick} tickLine={false} axisLine={false} tickFormatter={formatCompact} width={48} />
                <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(46,107,230,0.06)" }} />
                <Bar dataKey="value" name="Count" fill={CHART_PALETTE[3]} radius={[5, 5, 0, 0]} />
            </BarChart>
        );
    }
    if (type === "pareto") {
        return (
            <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
                <CartesianGrid vertical={false} stroke={ct.grid} />
                <XAxis dataKey="label" tick={ct.axisTickSmall} tickLine={false} axisLine={{ stroke: ct.grid }} interval={0} angle={-30} textAnchor="end" height={56} />
                <YAxis yAxisId="left" tick={ct.axisTick} tickLine={false} axisLine={false} tickFormatter={formatCompact} width={48} />
                <YAxis yAxisId="right" orientation="right" domain={[0, 100]} tick={ct.axisTick} tickLine={false} axisLine={false} tickFormatter={(v: number) => `${v}%`} width={42} />
                <Tooltip content={<ChartTooltip />} />
                <Legend wrapperStyle={ct.legendStyle} />
                <Bar yAxisId="left" dataKey="value" name="Value" fill={CHART_PRIMARY} radius={[5, 5, 0, 0]} maxBarSize={48} />
                <Line yAxisId="right" type="monotone" dataKey="cumulative" name="Cumulative %" stroke={CHART_PALETTE[2]} strokeWidth={2.5} dot={false} />
            </ComposedChart>
        );
    }
    return <div />;
}

function RadialGauge({ data, label, ct }: { data: any[]; label: string; ct: ChartThemeTokens }) {
    const point = data[0] ?? {};
    const value = Number(point.value) || 0;
    const max = Number(point.max) || 0;
    const unit = point.unit ?? "";
    const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
    const gauge = [{ name: label, value: pct, fill: CHART_PRIMARY }];
    return (
        <div className="relative h-full w-full">
            <ResponsiveContainer width="100%" height="100%">
                <RadialBarChart innerRadius="68%" outerRadius="100%" data={gauge} startAngle={210} endAngle={-30}>
                    <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
                    <RadialBar background={{ fill: ct.radialBg }} dataKey="value" cornerRadius={12} fill={CHART_PRIMARY} />
                </RadialBarChart>
            </ResponsiveContainer>
            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                <span className="text-3xl font-bold text-foreground">{formatCompact(value)}{unit}</span>
                <span className="text-[11px] text-muted-foreground mt-1">{max > 0 ? `${Math.round(pct)}% of ${formatCompact(max)}${unit}` : label}</span>
            </div>
        </div>
    );
}

function Ctrl({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <label className="flex flex-col gap-1">
            <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">{label}</span>
            {children}
        </label>
    );
}

function Select({ value, onChange, options, allowEmpty, emptyLabel }: {
    value: string; onChange: (v: string) => void;
    options: { value: string; label: string }[]; allowEmpty?: boolean; emptyLabel?: string;
}) {
    return (
        <select
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className={CHART_SELECT_CLASS}
        >
            {allowEmpty && <option value="">{emptyLabel ?? "None"}</option>}
            {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
    );
}
