// Shared chart theme for dashboard cards, ChatChart, and pinned visuals.
// Card surfaces use Tailwind `bg-card`; axis/grid/tooltip follow light/dark via useChartTheme().

import { useMemo } from 'react'
import { useTheme } from '../hooks/useTheme'

/** Tailwind classes for dashboard chart/KPI tiles (respects dark mode). */
export const CHART_CARD_CLASS =
  'p-5 rounded-2xl border border-border bg-card shadow-sm'

export const CHART_CARD_TITLE_CLASS = 'text-base font-semibold text-foreground'

export const CHART_SELECT_CLASS =
  'text-xs bg-card border border-border rounded-md px-2 py-1.5 text-foreground focus:outline-none focus:border-info/40 min-w-[96px]'

export const CHART_PALETTE = [
  '#2E6BE6', // blue (primary)
  '#17B0A6', // teal
  '#F2A23A', // amber
  '#7A5AF8', // violet
  '#E8568A', // pink
  '#3AA0F0', // sky
  '#E2654A', // coral
  '#54B435', // green
]

export const CHART_PRIMARY = '#2E6BE6'
export const CHART_GRID = '#EDF0F4'
export const CHART_AXIS = '#64748B'
export const CHART_AXIS_STRONG = '#475569'

/** Common Recharts axis tick style (light fallback). */
export const axisTick = { fill: CHART_AXIS, fontSize: 12 } as const
export const axisTickSmall = { fill: CHART_AXIS, fontSize: 11 } as const

export type ChartThemeTokens = {
  grid: string
  axis: string
  axisStrong: string
  axisTick: { fill: string; fontSize: number }
  axisTickSmall: { fill: string; fontSize: number }
  legendStyle: { fontSize: number; color: string }
  dataLabelStyle: { fill: string; fontSize: number; fontWeight: number }
  radialBg: string
  pieStroke: string
}

export function useChartTheme(): ChartThemeTokens {
  const { theme } = useTheme()
  return useMemo(() => {
    const isDark = theme === 'dark'
    const axis = isDark ? '#9AA1B5' : '#64748B'
    const axisStrong = isDark ? '#C7CBDA' : '#475569'
    return {
      grid: isDark ? '#232842' : '#EDF0F4',
      axis,
      axisStrong,
      axisTick: { fill: axis, fontSize: 12 },
      axisTickSmall: { fill: axis, fontSize: 11 },
      legendStyle: { fontSize: 12, color: axis },
      dataLabelStyle: { fill: axisStrong, fontSize: 11, fontWeight: 600 },
      radialBg: isDark ? '#1A1F33' : '#EEF1F6',
      pieStroke: isDark ? '#12162A' : '#ffffff',
    }
  }, [theme])
}

export function colorAt(i: number): string {
  return CHART_PALETTE[i % CHART_PALETTE.length]
}

/** 1.2K / 3.4M style numbers for axes and labels. */
export function formatCompact(v: number): string {
  if (!Number.isFinite(v)) return '-'
  return Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(v)
}

/** Full grouped number for tooltips. */
export function formatFull(v: number): string {
  if (!Number.isFinite(v)) return '-'
  return v.toLocaleString('en', { maximumFractionDigits: 2 })
}

/**
 * "Auto" data-label rule: only show labels when the series is small enough that
 * they won't collide. Tune thresholds per chart kind.
 */
export function showDataLabels(count: number, kind: 'bar' | 'line' | 'scatter' = 'bar'): boolean {
  if (kind === 'bar') return count > 0 && count <= 12
  if (kind === 'line') return count > 0 && count <= 8
  return false // scatter: never (too noisy)
}

export const dataLabelStyle = { fill: CHART_AXIS_STRONG, fontSize: 11, fontWeight: 600 } as const

/** Tooltip card — adapts to light/dark. Use via `content={<ChartTooltip/>}`. */
export function ChartTooltip({ active, payload, label }: any) {
  const { theme } = useTheme()
  const isDark = theme === 'dark'
  if (!active || !payload || !payload.length) return null
  return (
    <div
      style={{
        background: isDark ? '#1A2035' : '#ffffff',
        border: isDark ? '1px solid #232842' : '1px solid #E5E8EE',
        borderRadius: 10,
        boxShadow: isDark ? '0 8px 22px rgba(0,0,0,0.45)' : '0 8px 22px rgba(16,24,40,0.12)',
        padding: '8px 11px',
        fontSize: 12,
        minWidth: 120,
      }}
    >
      {label !== undefined && label !== '' && (
        <div style={{ color: isDark ? '#E8EAF2' : '#0F172A', fontWeight: 700, marginBottom: 6 }}>{label}</div>
      )}
      {payload.map((p: any, i: number) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 7, marginTop: i ? 3 : 0 }}>
          <span style={{ width: 9, height: 9, borderRadius: 3, background: p.color || p.fill || CHART_PRIMARY, display: 'inline-block' }} />
          <span style={{ color: isDark ? '#9AA1B5' : '#64748B' }}>{p.name}</span>
          <span style={{ color: isDark ? '#E8EAF2' : '#0F172A', fontWeight: 700, marginLeft: 'auto' }}>
            {typeof p.value === 'number' ? formatFull(p.value) : p.value}
          </span>
        </div>
      ))}
    </div>
  )
}

/** Clean legend text style for light cards. */
export const legendStyle = { fontSize: 12, color: CHART_AXIS } as const
