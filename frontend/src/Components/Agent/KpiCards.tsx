import type { MetricCard, MetricsAsset } from '../../hooks/useAgentBoard'
import { CHART_CARD_CLASS } from '../../lib/chartTheme'

const AGG_LABEL: Record<string, string> = {
  sum: 'Total',
  mean: 'Average',
  count: 'Count',
}

const AGG_ICON: Record<string, string> = {
  sum: 'fa-arrow-up-wide-short',
  mean: 'fa-chart-line',
  count: 'fa-hashtag',
}

// Standalone KPI card — uses theme tokens so dark mode matches the dashboard.
export function MetricCardView({ card, height = 130 }: { card: MetricCard; height?: number }) {
  return (
    <div
      className={`relative overflow-hidden ${CHART_CARD_CLASS} flex flex-col justify-center`}
      style={{ minHeight: height }}
    >
      <div className="absolute -right-4 -top-4 h-16 w-16 rounded-full bg-primary/10" />
      <div className="flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">
        <i className={`fa-solid ${AGG_ICON[card.agg] || 'fa-gauge'} text-primary/70 text-[10px]`} />
        <span className="truncate" title={card.label}>{card.label}</span>
      </div>
      <div className="mt-2 text-3xl font-extrabold text-foreground leading-tight tabular-nums">
        {card.formatted}
      </div>
      <div className="mt-1 text-[11px] text-muted-foreground">
        {AGG_LABEL[card.agg] || card.agg}
      </div>
    </div>
  )
}

export default function KpiCards({ metrics }: { metrics: MetricsAsset }) {
  if (!metrics?.cards?.length) return null
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
      {metrics.cards.map((c, i) => (
        <MetricCardView key={`${c.label}-${i}`} card={c} />
      ))}
    </div>
  )
}
