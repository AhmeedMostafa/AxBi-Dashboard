import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { getColumnCorrelations } from '../../api'
import { LogoSpinner } from '../ui/LogoSpinner'

interface ColumnNode {
  id: string
  label: string
  type: string
  role: string
  is_numeric: boolean
}

interface ColumnEdge {
  source: string
  target: string
  weight: number
  method: string
}

interface Props {
  datasetId: string
  targetColumn?: string
  onSelectFeature?: (column: string) => void
}

const TYPE_COLORS: Record<string, string> = {
  numeric: '#5A5AF6',
  datetime: '#f59e0b',
  text: '#10b981',
  boolean: '#ec4899',
  unknown: '#6b7280',
}

const ROLE_ICONS: Record<string, string> = {
  measure: 'fa-chart-line',
  dimension: 'fa-layer-group',
  date: 'fa-calendar',
  id: 'fa-fingerprint',
  geographic: 'fa-globe',
  descriptive: 'fa-align-left',
  boolean: 'fa-toggle-on',
  unknown: 'fa-question',
}

function ColumnNodeComponent({ data }: NodeProps) {
  const d = data as any
  const borderColor = d.isTarget ? '#f59e0b' : TYPE_COLORS[d.colType] || '#6b7280'
  const roleIcon = ROLE_ICONS[d.role] || 'fa-question'

  return (
    <div
      className="px-3 py-2 rounded-xl border-2 bg-card shadow-lg min-w-[130px] cursor-pointer hover:shadow-xl transition-shadow"
      style={{ borderColor }}
    >
      <Handle type="target" position={Position.Top} className="!bg-slate-500 !w-2 !h-2" />
      <div className="flex items-center gap-2">
        <i className={`fa-solid ${roleIcon} text-xs`} style={{ color: borderColor }} />
        <span className="text-xs font-semibold text-foreground truncate max-w-[100px]">{d.label}</span>
      </div>
      <div className="flex items-center gap-1.5 mt-1">
        <span
          className="text-[10px] px-1.5 py-0.5 rounded font-medium"
          style={{ backgroundColor: `${TYPE_COLORS[d.colType] || '#6b7280'}25`, color: TYPE_COLORS[d.colType] || '#6b7280' }}
        >
          {d.colType}
        </span>
        <span className="text-[10px] text-muted-foreground">{d.role}</span>
      </div>
      {d.isTarget && (
        <div className="mt-1 text-[10px] text-warning font-medium flex items-center gap-1">
          <i className="fa-solid fa-crosshairs text-[8px]" /> Target
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="!bg-slate-500 !w-2 !h-2" />
    </div>
  )
}

const nodeTypes = { column: ColumnNodeComponent }

export default function ColumnMindMap({ datasetId, targetColumn, onSelectFeature }: Props) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null)

  useEffect(() => {
    if (!datasetId) return
    setLoading(true)
    setError(null)

    getColumnCorrelations(datasetId)
      .then((data: { nodes: ColumnNode[]; edges: ColumnEdge[] }) => {
        const columnNodes = data.nodes || []
        const columnEdges = data.edges || []

        const angleStep = (2 * Math.PI) / Math.max(columnNodes.length, 1)
        const radius = Math.max(200, columnNodes.length * 40)

        const targetIdx = columnNodes.findIndex(n => n.id === targetColumn)
        const flowNodes: Node[] = columnNodes.map((n, i) => {
          const isTarget = n.id === targetColumn
          const angle = angleStep * (isTarget && targetIdx >= 0 ? 0 : i)
          return {
            id: n.id,
            type: 'column',
            position: {
              x: isTarget ? radius : radius + radius * Math.cos(angle),
              y: isTarget ? radius : radius + radius * Math.sin(angle),
            },
            data: {
              label: n.label,
              colType: n.type,
              role: n.role,
              isTarget,
              isNumeric: n.is_numeric,
            },
          }
        })

        const flowEdges: Edge[] = columnEdges.map((e, i) => {
          const absWeight = Math.abs(e.weight)
          const isPositive = e.weight > 0
          return {
            id: `e-${i}`,
            source: e.source,
            target: e.target,
            type: 'default',
            style: {
              stroke: e.method === 'mutual_info' ? '#a78bfa' : isPositive ? '#34d399' : '#f87171',
              strokeWidth: Math.max(1, absWeight * 4),
              opacity: 0.7,
            },
            label: `${e.weight > 0 ? '+' : ''}${e.weight.toFixed(2)}`,
            labelStyle: { fontSize: 10, fill: '#94a3b8' },
            animated: absWeight > 0.7,
          }
        })

        setNodes(flowNodes)
        setEdges(flowEdges)
      })
      .catch((err: any) => {
        setError(err?.response?.data?.error || 'Failed to load column correlations')
      })
      .finally(() => setLoading(false))
  }, [datasetId, targetColumn, setNodes, setEdges])

  const onNodeClick = useCallback((_: any, node: Node) => {
    if (onSelectFeature && node.id !== targetColumn) {
      onSelectFeature(node.id)
    }
  }, [onSelectFeature, targetColumn])

  const legend = useMemo(() => [
    { color: '#34d399', label: 'Positive correlation' },
    { color: '#f87171', label: 'Negative correlation' },
    { color: '#a78bfa', label: 'Mutual information' },
  ], [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        <div className="flex items-center gap-2">
          <LogoSpinner size={16} />
          <span className="text-sm">Computing column relationships...</span>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-sm text-destructive">{error}</p>
      </div>
    )
  }

  if (!nodes.length) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-sm text-muted-foreground">No column relationship data available</p>
      </div>
    )
  }

  return (
    <div className="relative">
      <div className="flex items-center gap-4 mb-3 px-1">
        {legend.map(l => (
          <div key={l.color} className="flex items-center gap-1.5">
            <div className="w-3 h-0.5 rounded" style={{ backgroundColor: l.color }} />
            <span className="text-[10px] text-muted-foreground">{l.label}</span>
          </div>
        ))}
        <span className="text-[10px] text-muted-foreground ml-auto">Click a node to select it as a feature</span>
      </div>
      <div className="h-[420px] rounded-xl border border-border overflow-hidden bg-background">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          nodeTypes={nodeTypes}
          fitView
          minZoom={0.3}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#1b1f38" gap={20} />
          <Controls className="!bg-card !border-border !rounded-lg [&>button]:!bg-card [&>button]:!border-border [&>button]:!text-foreground [&>button:hover]:!bg-muted" />
          <MiniMap
            className="!bg-card !border-border"
            nodeColor="#5A5AF6"
            maskColor="rgba(0,0,0,0.6)"
          />
        </ReactFlow>
      </div>
    </div>
  )
}
