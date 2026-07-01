import type { GlobalRange } from "./useDashboardConfig";

function iso(d: Date): string {
    return d.toISOString().slice(0, 10);
}

/** Resolve a GlobalRange to concrete from/to ISO dates (recomputed each call). */
export function resolveRange(range: GlobalRange | null | undefined): { from: string | null; to: string | null } {
    if (!range || range.preset === "all") return { from: null, to: null };
    if (range.preset === "custom") return { from: range.from, to: range.to };
    const now = new Date();
    const to = iso(now);
    const start = new Date(now);
    if (range.preset === "last7") start.setDate(now.getDate() - 7);
    else if (range.preset === "last30") start.setDate(now.getDate() - 30);
    else if (range.preset === "last90") start.setDate(now.getDate() - 90);
    else if (range.preset === "last12m") start.setMonth(now.getMonth() - 12);
    else if (range.preset === "ytd") { start.setMonth(0); start.setDate(1); }
    return { from: iso(start), to };
}

export function rangeIsActive(range: GlobalRange | null | undefined): boolean {
    if (!range || !range.field) return false;
    const { from, to } = resolveRange(range);
    return Boolean(from || to);
}
