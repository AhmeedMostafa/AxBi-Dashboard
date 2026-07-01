import { type ReactNode } from 'react'

type SkeletonVariant = 'card' | 'chart' | 'table-row' | 'text-line' | 'kpi'

interface LoadingSkeletonProps {
  variant: SkeletonVariant
  count?: number
}

function WaveBlock({ className, delay = 0 }: { className?: string; delay?: number }) {
  return (
    <div className={`relative overflow-hidden rounded bg-muted ${className ?? ''}`}>
      <div
        className="absolute inset-0 bg-gradient-to-r from-transparent via-[#5A5AF6]/8 to-transparent will-change-transform"
        style={{ animation: `wave-slim 2s ${delay}ms infinite linear` }}
      />
    </div>
  )
}

function CardSkeleton({ index = 0 }: { index?: number }) {
  const d = index * 80
  return (
    <div
      className="bg-card border border-border rounded-xl p-4 flex flex-col gap-2 will-change-transform"
      style={{ animation: `float-tight 2.5s ${d}ms infinite ease-in-out` }}
    >
      <div className="flex items-center gap-2.5">
        <div
          className="w-9 h-9 rounded-lg bg-gradient-to-br from-primary/15 to-muted"
          style={{ animation: `pulse-opacity 2s ${d + 100}ms infinite` }}
        />
        <div className="flex-1 space-y-1.5">
          <WaveBlock className="h-3.5 w-3/4 rounded" delay={d + 50} />
          <WaveBlock className="h-2.5 w-1/2 rounded" delay={d + 120} />
        </div>
      </div>
      <div className="flex gap-2 mt-1">
        <WaveBlock className="h-2.5 w-14 rounded" delay={d + 180} />
        <WaveBlock className="h-2.5 w-10 rounded" delay={d + 240} />
      </div>
      <div className="flex items-center justify-between pt-2 border-t border-border">
        <WaveBlock className="h-4 w-16 rounded-full" delay={d + 300} />
        <WaveBlock className="h-3 w-12 rounded" delay={d + 360} />
      </div>
    </div>
  )
}

function ChartSkeleton({ index = 0 }: { index?: number }) {
  const d = index * 120
  const bars = [38, 60, 42, 78, 52, 68, 33, 55]

  return (
    <div
      className="p-4 rounded-xl border border-border bg-card will-change-transform"
      style={{ animation: `tilt-3d 5s ${d}ms infinite ease-in-out` }}
    >
      <WaveBlock className="h-4 w-36 rounded mb-3" delay={d} />
      <div className="h-[200px] w-full flex items-end gap-1 px-2 pt-4 pb-2">
        {bars.map((h, i) => (
          <div
            key={i}
            className="flex-1 rounded-t bg-gradient-to-t from-[#5A5AF6]/25 to-[#5A5AF6]/5 will-change-transform"
            style={{
              height: `${h}%`,
              animation: `bar-grow 1.8s ${d + i * 100}ms infinite ease-in-out`,
              transformOrigin: 'bottom',
            }}
          />
        ))}
      </div>
      <div className="mt-2 flex gap-2">
        <WaveBlock className="h-2.5 w-1/3 rounded" delay={d + 200} />
        <WaveBlock className="h-2.5 w-1/4 rounded" delay={d + 260} />
      </div>
    </div>
  )
}

function KpiSkeleton({ index = 0 }: { index?: number }) {
  const d = index * 60
  return (
    <div
      className="p-4 rounded-lg border border-border bg-card will-change-transform"
      style={{ animation: `float-tight 2.8s ${d}ms infinite ease-in-out` }}
    >
      <WaveBlock className="h-2.5 w-20 rounded mb-2" delay={d} />
      <div
        className="h-7 w-28 rounded bg-gradient-to-r from-[#5A5AF6]/12 to-transparent mb-1.5"
        style={{ animation: `pulse-opacity 2s ${d + 100}ms infinite` }}
      />
      <WaveBlock className="h-3 w-16 rounded mb-2" delay={d + 150} />
      <WaveBlock className="h-2.5 w-32 rounded" delay={d + 220} />
    </div>
  )
}

function TableRowSkeleton({ index = 0 }: { index?: number }) {
  const d = index * 60
  return (
    <div
      className="flex items-center gap-3 px-4 py-3 bg-card border border-border rounded-lg"
      style={{ animation: `slide-in-up 0.4s ${d}ms backwards ease-out` }}
    >
      <WaveBlock className="h-3.5 w-24 rounded" delay={d} />
      <WaveBlock className="h-3.5 w-14 rounded" delay={d + 60} />
      <WaveBlock className="h-3.5 w-14 rounded" delay={d + 120} />
      <WaveBlock className="h-3.5 w-18 rounded" delay={d + 180} />
      <WaveBlock className="h-3.5 w-20 rounded ml-auto" delay={d + 240} />
    </div>
  )
}

function TextLineSkeleton() {
  return <WaveBlock className="h-3.5 w-full rounded" />
}

export function LoadingSkeleton({ variant, count = 1 }: LoadingSkeletonProps) {
  const items: ReactNode[] = []
  for (let i = 0; i < count; i++) {
    switch (variant) {
      case 'card':
        items.push(<CardSkeleton key={i} index={i} />)
        break
      case 'chart':
        items.push(<ChartSkeleton key={i} index={i} />)
        break
      case 'kpi':
        items.push(<KpiSkeleton key={i} index={i} />)
        break
      case 'table-row':
        items.push(<TableRowSkeleton key={i} index={i} />)
        break
      case 'text-line':
        items.push(<TextLineSkeleton key={i} />)
        break
    }
  }
  return <>{items}</>
}

export default LoadingSkeleton
