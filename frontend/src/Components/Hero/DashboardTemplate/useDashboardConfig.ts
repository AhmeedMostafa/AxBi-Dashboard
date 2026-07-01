import { useCallback, useEffect, useState } from "react";

export type Span = 4 | 6 | 8 | 12;
export type HeightPreset = "normal" | "tall";

export interface LayoutItem {
    span: Span;
    height: HeightPreset;
}

export interface GlobalRange {
    field: string | null;
    preset: string;          // 'all' | 'last7' | 'last30' | 'last90' | 'ytd' | 'last12m' | 'custom'
    from: string | null;     // ISO yyyy-mm-dd
    to: string | null;       // ISO yyyy-mm-dd
}

export interface CustomChart {
    id: string;              // 'c{timestamp}'
    title: string;
    chart_type: string;
    x_axis: string | null;
    y_axis: string | null;
    agg: string;
    breakdown: string | null;
    time_grain: string | null;
    top_n: number;
    cumulative?: boolean;
}

export interface DashboardConfig {
    order: string[];
    layout: Record<string, LayoutItem>;
    customCharts: CustomChart[];
    globalRange: GlobalRange;
    titles: Record<string, string>;   // per-card title overrides, keyed by card id
}

export const DEFAULT_RANGE: GlobalRange = { field: null, preset: "all", from: null, to: null };

const DEFAULT_SPAN: Span = 6;
const DEFAULT_HEIGHT: HeightPreset = "normal";

function emptyConfig(): DashboardConfig {
    return { order: [], layout: {}, customCharts: [], globalRange: { ...DEFAULT_RANGE }, titles: {} };
}

function storageKey(datasetId: string) {
    return `bi:dashlayout:${datasetId}`;
}

function loadConfig(datasetId: string): DashboardConfig {
    if (!datasetId) return emptyConfig();
    try {
        const raw = localStorage.getItem(storageKey(datasetId));
        if (!raw) return emptyConfig();
        const parsed = JSON.parse(raw) as Partial<DashboardConfig>;
        return {
            order: Array.isArray(parsed.order) ? parsed.order : [],
            layout: parsed.layout && typeof parsed.layout === "object" ? parsed.layout : {},
            customCharts: Array.isArray(parsed.customCharts) ? parsed.customCharts : [],
            globalRange: parsed.globalRange ? { ...DEFAULT_RANGE, ...parsed.globalRange } : { ...DEFAULT_RANGE },
            titles: parsed.titles && typeof parsed.titles === "object" ? parsed.titles : {},
        };
    } catch {
        return emptyConfig();
    }
}

/**
 * Per-dataset dashboard layout state (order, sizing, custom charts, global date range)
 * persisted in localStorage. `baseIds` are the ids of the AI-suggested charts; they are
 * merged into `order` so newly-added suggested charts always appear.
 */
export function useDashboardConfig(datasetId: string, baseIds: string[]) {
    const [config, setConfig] = useState<DashboardConfig>(() => loadConfig(datasetId));

    // Reload when switching datasets.
    useEffect(() => {
        setConfig(loadConfig(datasetId));
    }, [datasetId]);

    // Persist on change.
    useEffect(() => {
        if (!datasetId) return;
        try { localStorage.setItem(storageKey(datasetId), JSON.stringify(config)); } catch { /* ignore */ }
    }, [datasetId, config]);

    // Merge base (suggested) + custom ids into a stable order, dropping stale ids.
    const allIds = [...baseIds, ...config.customCharts.map((c) => c.id)];
    const order = [
        ...config.order.filter((id) => allIds.includes(id)),
        ...allIds.filter((id) => !config.order.includes(id)),
    ];

    const getLayout = useCallback((id: string): LayoutItem => {
        return config.layout[id] ?? { span: DEFAULT_SPAN, height: DEFAULT_HEIGHT };
    }, [config.layout]);

    const reorder = useCallback((next: string[]) => {
        setConfig((c) => ({ ...c, order: next }));
    }, []);

    const setSpan = useCallback((id: string, span: Span) => {
        setConfig((c) => ({
            ...c,
            layout: { ...c.layout, [id]: { ...(c.layout[id] ?? { span: DEFAULT_SPAN, height: DEFAULT_HEIGHT }), span } },
        }));
    }, []);

    const setHeight = useCallback((id: string, height: HeightPreset) => {
        setConfig((c) => ({
            ...c,
            layout: { ...c.layout, [id]: { ...(c.layout[id] ?? { span: DEFAULT_SPAN, height: DEFAULT_HEIGHT }), height } },
        }));
    }, []);

    const addChart = useCallback((chart: Omit<CustomChart, "id">) => {
        const id = `c${Date.now()}`;
        const span: Span = chart.chart_type === "kpi" ? 4 : DEFAULT_SPAN;
        setConfig((c) => ({
            ...c,
            customCharts: [...c.customCharts, { ...chart, id }],
            order: [...c.order, id],
            layout: { ...c.layout, [id]: { span, height: DEFAULT_HEIGHT } },
        }));
        return id;
    }, []);

    const removeChart = useCallback((id: string) => {
        setConfig((c) => {
            const { [id]: _drop, ...restLayout } = c.layout;
            const { [id]: _dropTitle, ...restTitles } = c.titles;
            void _drop; void _dropTitle;
            return {
                ...c,
                customCharts: c.customCharts.filter((x) => x.id !== id),
                order: c.order.filter((x) => x !== id),
                layout: restLayout,
                titles: restTitles,
            };
        });
        try { localStorage.removeItem(`bi:chartopts:${datasetId}:${id}`); } catch { /* ignore */ }
    }, [datasetId]);

    const setTitle = useCallback((id: string, title: string) => {
        setConfig((c) => ({ ...c, titles: { ...c.titles, [id]: title } }));
    }, []);

    const setGlobalRange = useCallback((range: GlobalRange) => {
        setConfig((c) => ({ ...c, globalRange: range }));
    }, []);

    const resetLayout = useCallback(() => {
        setConfig((c) => ({ ...c, order: [], layout: {} }));
    }, []);

    return {
        order,
        customCharts: config.customCharts,
        globalRange: config.globalRange,
        titles: config.titles,
        getLayout,
        reorder,
        setSpan,
        setHeight,
        addChart,
        removeChart,
        setGlobalRange,
        setTitle,
        resetLayout,
    };
}
