import { useMemo } from "react";
import type { Field } from "./InteractiveChartCard";
import type { GlobalRange } from "./useDashboardConfig";

const PRESETS: { value: string; label: string }[] = [
    { value: "all", label: "All time" },
    { value: "last7", label: "Last 7 days" },
    { value: "last30", label: "Last 30 days" },
    { value: "last90", label: "Last 90 days" },
    { value: "ytd", label: "Year to date" },
    { value: "last12m", label: "Last 12 months" },
    { value: "custom", label: "Custom range" },
];

interface Props {
    fields: Field[];
    value: GlobalRange;
    onChange: (next: GlobalRange) => void;
    compact?: boolean;
    /** label shown before the controls (global header only) */
    title?: string;
}

export default function DateRangeControl({ fields, value, onChange, compact, title }: Props) {
    const dateFields = useMemo(() => fields.filter((f) => f.kind === "date"), [fields]);
    if (dateFields.length === 0) return null;

    const field = value.field ?? dateFields[0].key;
    const isCustom = value.preset === "custom";

    const selectCls = compact
        ? "text-xs bg-card border border-border rounded-md px-2 py-1.5 text-foreground focus:outline-none focus:border-info/40"
        : "text-sm bg-card border border-border rounded-lg px-3 py-2 text-foreground focus:outline-none focus:border-info/40";

    return (
        <div className={`flex items-center gap-2 flex-wrap ${compact ? "" : "bg-card rounded-xl px-3 py-2 border border-border shadow-sm"}`}>
            {title && <span className="text-xs font-semibold text-muted-foreground mr-1">{title}</span>}
            {!compact && (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-muted-foreground"><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4M8 2v4M3 10h18" /></svg>
            )}
            {dateFields.length > 1 && (
                <select
                    className={selectCls}
                    value={field}
                    onChange={(e) => onChange({ ...value, field: e.target.value })}
                    title="Date field"
                >
                    {dateFields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
                </select>
            )}
            <select
                className={selectCls}
                value={value.preset}
                onChange={(e) => onChange({ ...value, field, preset: e.target.value })}
            >
                {PRESETS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
            {isCustom && (
                <>
                    <input
                        type="date"
                        className={selectCls}
                        value={value.from ?? ""}
                        onChange={(e) => onChange({ ...value, field, from: e.target.value || null })}
                    />
                    <span className="text-muted-foreground text-xs">to</span>
                    <input
                        type="date"
                        className={selectCls}
                        value={value.to ?? ""}
                        onChange={(e) => onChange({ ...value, field, to: e.target.value || null })}
                    />
                </>
            )}
        </div>
    );
}
