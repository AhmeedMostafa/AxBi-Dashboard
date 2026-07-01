import { useEffect, useRef, useState } from "react";

interface Props {
    value: string;
    onChange?: (next: string) => void;
    className?: string;
    inputClassName?: string;
}

/**
 * Inline-editable title. Renders plain text when `onChange` is omitted;
 * otherwise clicking the title (or its pencil) opens an input. Enter / blur
 * commits, Escape cancels. Empty input falls back to the previous value.
 */
export default function EditableTitle({ value, onChange, className = "", inputClassName = "" }: Props) {
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(value);
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => { setDraft(value); }, [value]);
    useEffect(() => { if (editing) inputRef.current?.select(); }, [editing]);

    if (!onChange) return <span className={className}>{value}</span>;

    if (editing) {
        const commit = () => {
            const next = draft.trim();
            onChange(next || value);
            setEditing(false);
        };
        return (
            <input
                ref={inputRef}
                value={draft}
                autoFocus
                onChange={(e) => setDraft(e.target.value)}
                onBlur={commit}
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => {
                    if (e.key === "Enter") commit();
                    else if (e.key === "Escape") { setDraft(value); setEditing(false); }
                }}
                className={`bg-card border border-info/40 rounded-md px-2 py-0.5 outline-none text-foreground ${inputClassName || className}`}
            />
        );
    }

    return (
        <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setEditing(true); }}
            title="Rename"
            className={`group inline-flex items-center gap-1.5 text-left max-w-full ${className}`}
        >
            <span className="truncate">{value}</span>
            <svg
                width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                className="shrink-0 opacity-0 group-hover:opacity-60 transition-opacity"
            >
                <path d="M12 20h9" />
                <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
            </svg>
        </button>
    );
}
