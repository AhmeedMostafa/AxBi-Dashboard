# Design

Visual system for AxBi. Light is the default theme; dark is a user toggle (persisted in
`localStorage['bi_theme']`, applied as `.dark` on `<html>`). Every surface reads from CSS variables
in `frontend/src/index.css` — no hardcoded colors in components.

## Theme

Dual theme. Light = soft blue-grey workspace, white surfaces, indigo brand. Dark = deep navy-black
with three lifted surface tiers. Both meet **WCAG AA** (body ≥4.5:1, large ≥3:1).

## Color

Strategy: **Restrained** (product). One indigo accent for primary actions, active nav, focus, and
state; neutrals carry everything else; semantic + department colors used only where they mean
something. All values live as CSS vars; Tailwind classes map via `@theme inline`
(`bg-card`, `text-muted-foreground`, `border-border`, `text-primary`, `bg-sales`, …).

| Role | Token | Light | Dark |
|------|-------|-------|------|
| Page background | `--background` | `#F7F8FB` | `#0B0E1A` |
| Card surface | `--card` | `#FFFFFF` | `#12162A` |
| Elevated (modal/popover) | `--popover` / `--elevated` | `#FFFFFF` | `#1A2035` |
| Primary text | `--foreground` | `#1A1D2B` | `#E8EAF2` |
| Secondary text | `--muted-foreground` | `#5B6172` | `#9AA1B5` |
| Border / divider | `--border` | `#E4E7EF` | `#232842` |
| Brand / primary | `--primary` | `#5A5AF6` | `#6E6EF8` |
| Accent tint (hover bg) | `--accent` | `#EEF0FE` | `#1B1F3A` |

**Semantic:** `--success` `#16a34a`/`#34d399`, `--warning` `#b45309`/`#fbbf24`,
`--info` `#2563eb`/`#60a5fa`, `--destructive` `#DC2626`/`#F87171`.

**Department (badges, file/category accents):** `--sales` (emerald), `--marketing` (pink),
`--operations` (orange), `--hr` (blue) — each with a light + dark value tuned for AA on its surface.

**Charts:** `--chart-1..5` (blue, emerald, amber, violet, cyan), brighter in dark.

## Typography

One family: system sans / Inter stack (product register — familiar, legible at consistent DPI).
Fixed rem scale, not fluid. Weights: 800 page titles, 700 section heads, 600 labels/buttons,
500 nav, 400 body. `--muted-foreground` for secondary text only, never below AA.

## Components

- **Card**: `bg-card border border-border rounded-2xl` + soft shadow. No nested cards.
- **Modal / popover**: `bg-popover border border-border rounded-2xl shadow-xl`.
- **Primary button**: `bg-primary text-primary-foreground rounded-xl`, hover `bg-primary/90`.
- **Ghost button**: `bg-card border border-border text-foreground`.
- **Input**: `bg-background border border-input rounded-xl`, focus ring `--ring`.
- **Badge**: department token at `/12` bg, solid token text, `/30` border, pill.
- **Status**: `text-success` / `text-warning` / `text-destructive` + icon (color never the only signal).
- **Empty state**: `border border-dashed border-border`, teaches the next action.
- States required per interactive element: default, hover, focus-visible, active, disabled, loading.

## Layout

- App shell: fixed sidebar (`--sidebar`) + top bar (theme toggle + user menu) + content.
- Card grids: `repeat(auto-fit, minmax(280px, 1fr))` where counts vary.
- Responsive is structural (sidebar collapses under `md`), not fluid type.

## Motion

150–250ms on state transitions (theme crossfade, hover, nav). State/feedback only, no decorative
or page-load choreography. `prefers-reduced-motion: reduce` → instant/crossfade.

## Theme infra

`frontend/src/hooks/useTheme.ts` (`useTheme`, `initTheme`, `applyTheme`). `main.tsx` calls
`initTheme()` before render to avoid theme flash. Toggle lives in `Sidebar.tsx` top bar.
