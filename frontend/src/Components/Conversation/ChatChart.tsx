import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  AreaChart, Area, Treemap, FunnelChart, Funnel,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LabelList,
} from 'recharts'
import type { ChatChart } from '../../hooks/useChat'
import {
  CHART_PRIMARY,
  ChartTooltip, formatCompact, showDataLabels, colorAt,
  useChartTheme, CHART_CARD_CLASS,
} from '../../lib/chartTheme'

interface Props {
  chart: ChatChart
  height?: number
}

export default function ChatChartRenderer({ chart, height = 220 }: Props) {
  const { type, title, data, xKey, yKey } = chart
  const ct = useChartTheme()

  if (!data || data.length === 0) return null

  const many = data.length > 6
  const catXAxis = {
    dataKey: xKey,
    tick: ct.axisTick,
    tickLine: false,
    axisLine: { stroke: ct.grid },
    ...(many ? { angle: -28, textAnchor: 'end' as const, height: 56, interval: 0 } : {}),
  }
  const numYAxis = {
    tick: ct.axisTick,
    tickLine: false,
    axisLine: false as const,
    tickFormatter: formatCompact,
    width: 44,
  }
  const gradId = `cc-area-${xKey}-${yKey}`.replace(/[^a-zA-Z0-9-]/g, '')
  const showBarLabels = showDataLabels(data.length, 'bar')
  const palettized = data.map((d, i) => ({ ...d, __fill: colorAt(i) }))

  return (
    <div className={`mt-2 ${CHART_CARD_CLASS} !p-3`}>
      {title && <p className="text-[13px] text-foreground mb-2 font-semibold">{title}</p>}
      <ResponsiveContainer width="100%" height={height}>
        {type === 'line' ? (
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: many ? 0 : 4, left: 0 }}>
            <CartesianGrid vertical={false} stroke={ct.grid} />
            <XAxis {...catXAxis} interval={many ? 'preserveStartEnd' : 0} angle={0} textAnchor="middle" height={28} />
            <YAxis {...numYAxis} />
            <Tooltip content={<ChartTooltip />} cursor={{ stroke: ct.grid }} />
            <Line type="monotone" dataKey={yKey} stroke={CHART_PRIMARY} strokeWidth={2.5} dot={false} activeDot={{ r: 4 }}>
              {showDataLabels(data.length, 'line') && <LabelList dataKey={yKey} position="top" formatter={(v: any) => formatCompact(Number(v))} style={ct.dataLabelStyle} />}
            </Line>
          </LineChart>
        ) : type === 'area' ? (
          <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={CHART_PRIMARY} stopOpacity={0.35} />
                <stop offset="100%" stopColor={CHART_PRIMARY} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke={ct.grid} />
            <XAxis {...catXAxis} interval={many ? 'preserveStartEnd' : 0} angle={0} textAnchor="middle" height={28} />
            <YAxis {...numYAxis} />
            <Tooltip content={<ChartTooltip />} cursor={{ stroke: ct.grid }} />
            <Area type="monotone" dataKey={yKey} stroke={CHART_PRIMARY} strokeWidth={2.5} fill={`url(#${gradId})`} />
          </AreaChart>
        ) : type === 'horizontal_bar' ? (
          <BarChart layout="vertical" data={data} margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
            <CartesianGrid horizontal={false} stroke={ct.grid} />
            <XAxis type="number" tick={ct.axisTick} tickLine={false} axisLine={{ stroke: ct.grid }} tickFormatter={formatCompact} />
            <YAxis type="category" dataKey={xKey} tick={ct.axisTick} tickLine={false} axisLine={false} width={110} />
            <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(46,107,230,0.06)' }} />
            <Bar dataKey={yKey} fill={CHART_PRIMARY} radius={[0, 6, 6, 0]} maxBarSize={28}>
              {showBarLabels && <LabelList dataKey={yKey} position="right" formatter={(v: any) => formatCompact(Number(v))} style={ct.dataLabelStyle} />}
            </Bar>
          </BarChart>
        ) : type === 'treemap' ? (
          <Treemap data={palettized} dataKey={yKey} nameKey={xKey} stroke={ct.pieStroke} content={<TreemapCell stroke={ct.pieStroke} />}>
            <Tooltip content={<ChartTooltip />} />
          </Treemap>
        ) : type === 'funnel' ? (
          <FunnelChart>
            <Tooltip content={<ChartTooltip />} />
            <Funnel dataKey={yKey} nameKey={xKey} data={palettized} isAnimationActive>
              <LabelList position="right" fill={ct.axisStrong} stroke="none" dataKey={xKey} style={{ fontSize: 11, fontWeight: 600 }} />
              {palettized.map((_, i) => <Cell key={i} fill={colorAt(i)} />)}
            </Funnel>
          </FunnelChart>
        ) : type === 'pie' ? (
          <PieChart>
            <Pie
              data={data}
              dataKey={yKey}
              nameKey={xKey}
              cx="50%"
              cy="50%"
              outerRadius={Math.min(96, height / 2 - 8)}
              innerRadius={Math.min(58, height / 3)}
              paddingAngle={2}
              label={({ percent }: any) => (percent > 0.05 ? `${Math.round(percent * 100)}%` : '')}
              labelLine={false}
              stroke={ct.pieStroke}
              strokeWidth={2}
            >
              {data.map((_, i) => <Cell key={i} fill={colorAt(i)} />)}
            </Pie>
            <Tooltip content={<ChartTooltip />} />
          </PieChart>
        ) : (
          <BarChart data={data} margin={{ top: showBarLabels ? 18 : 8, right: 12, bottom: many ? 0 : 4, left: 0 }}>
            <CartesianGrid vertical={false} stroke={ct.grid} />
            <XAxis {...catXAxis} />
            <YAxis {...numYAxis} />
            <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(46,107,230,0.06)' }} />
            <Bar dataKey={yKey} fill={CHART_PRIMARY} radius={[6, 6, 0, 0]} maxBarSize={64}>
              {showBarLabels && <LabelList dataKey={yKey} position="top" formatter={(v: any) => formatCompact(Number(v))} style={ct.dataLabelStyle} />}
            </Bar>
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  )
}

function TreemapCell(props: any) {
  const { x, y, width, height, name, __fill, index, stroke = '#ffffff' } = props
  const fill = __fill || colorAt(index ?? 0)
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={fill} stroke={stroke} strokeWidth={2} rx={3} />
      {width > 56 && height > 22 && (
        <text x={x + 6} y={y + 16} fill="#ffffff" fontSize={11} fontWeight={600}>
          {String(name ?? '').slice(0, 18)}
        </text>
      )}
    </g>
  )
}
