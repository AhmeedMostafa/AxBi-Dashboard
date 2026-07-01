import type { ReactNode } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { Span, HeightPreset } from "./useDashboardConfig";

interface Props {
    id: string;
    span: Span;
    height: HeightPreset;
    editMode: boolean;
    isCustom: boolean;
    onSpan: (span: Span) => void;
    onHeight: (height: HeightPreset) => void;
    onRemove?: () => void;
    children: ReactNode;
}

const SPANS: { value: Span; label: string; title: string }[] = [
    { value: 4, label: "⅓", title: "Third width" },
    { value: 6, label: "½", title: "Half width" },
    { value: 8, label: "⅔", title: "Two-thirds width" },
    { value: 12, label: "▭", title: "Full width" },
];

export default function SortableChartCard({
    id, span, height, editMode, isCustom, onSpan, onHeight, onRemove, children,
}: Props) {
    const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id, disabled: !editMode });

    const style: React.CSSProperties = {
        gridColumn: `span ${span} / span ${span}`,
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
        zIndex: isDragging ? 20 : undefined,
    };

    const segBtn = (active: boolean) =>
        `px-2 py-1 text-xs rounded-md border transition-colors ${active ? "border-info/40 text-info bg-info/10" : "border-border text-muted-foreground hover:border-border hover:text-foreground"}`;

    return (
        <div ref={setNodeRef} style={style}>
            {editMode && (
                <div className="mb-2 flex items-center gap-2 flex-wrap rounded-lg bg-card border border-border px-2 py-1.5 shadow-sm">
                    <button
                        {...attributes}
                        {...listeners}
                        className="cursor-grab active:cursor-grabbing text-muted-foreground hover:text-muted-foreground px-1"
                        title="Drag to reorder"
                    >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.6" /><circle cx="15" cy="6" r="1.6" /><circle cx="9" cy="12" r="1.6" /><circle cx="15" cy="12" r="1.6" /><circle cx="9" cy="18" r="1.6" /><circle cx="15" cy="18" r="1.6" /></svg>
                    </button>
                    <div className="flex items-center gap-1">
                        {SPANS.map((s) => (
                            <button key={s.value} onClick={() => onSpan(s.value)} className={segBtn(span === s.value)} title={s.title}>{s.label}</button>
                        ))}
                    </div>
                    <div className="flex items-center gap-1">
                        <button onClick={() => onHeight("normal")} className={segBtn(height === "normal")} title="Normal height">Normal</button>
                        <button onClick={() => onHeight("tall")} className={segBtn(height === "tall")} title="Tall">Tall</button>
                    </div>
                    {isCustom && onRemove && (
                        <button onClick={onRemove} className="ml-auto text-xs px-2 py-1 rounded-md border border-destructive/30 text-destructive hover:bg-destructive/10" title="Remove this graph">
                            Delete
                        </button>
                    )}
                </div>
            )}
            <div className={editMode ? "rounded-2xl ring-2 ring-[#2E6BE6]/25" : ""}>
                {children}
            </div>
        </div>
    );
}
