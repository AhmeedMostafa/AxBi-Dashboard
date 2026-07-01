import { useRef, useMemo, useState, useEffect, type ErrorInfo, Component, type ReactNode } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { OrbitControls, Text } from '@react-three/drei'
import type { Chat3DVisual } from '../../hooks/useChat'
import { LogoSpinner } from '../ui/LogoSpinner'

interface Props {
  visual: Chat3DVisual
  height?: number
  onExpand?: () => void
}

function normalize(values: number[], scale: number = 5): number[] {
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  return values.map(v => ((v - min) / range) * scale)
}

class Canvas3DErrorBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { hasError: boolean }> {
  state = { hasError: false }
  static getDerivedStateFromError() { return { hasError: true } }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.warn('3D render failed:', error, info)
  }
  render() {
    if (this.state.hasError) return this.props.fallback
    return this.props.children
  }
}

function Scatter3DScene({ data, config }: { data: Record<string, any>[]; config: Record<string, any> }) {
  const { xKey, yKey, zKey, labelKey } = config
  const groupRef = useRef<any>(null)

  useFrame((_, delta) => {
    if (groupRef.current) groupRef.current.rotation.y += delta * 0.1
  })

  const points = useMemo(() => {
    const xs = normalize(data.map(d => Number(d[xKey]) || 0))
    const ys = normalize(data.map(d => Number(d[yKey]) || 0))
    const zs = normalize(data.map(d => Number(d[zKey]) || 0))
    return data.map((d, i) => ({
      x: xs[i] - 2.5,
      y: ys[i] - 2.5,
      z: zs[i] - 2.5,
      label: labelKey ? String(d[labelKey] || '') : '',
    }))
  }, [data, xKey, yKey, zKey, labelKey])

  return (
    <group ref={groupRef}>
      {points.map((p, i) => (
        <group key={i} position={[p.x, p.y, p.z]}>
          <mesh>
            <sphereGeometry args={[0.12, 8, 8]} />
            <meshStandardMaterial color={`hsl(${(i / points.length) * 240 + 220}, 70%, 60%)`} />
          </mesh>
          {p.label && (
            <Text position={[0, 0.25, 0]} fontSize={0.15} color="#e2e8f0" anchorX="center">
              {p.label.slice(0, 10)}
            </Text>
          )}
        </group>
      ))}
      <Text position={[3, 0, 0]} fontSize={0.3} color="#94a3b8">{xKey}</Text>
      <Text position={[0, 3, 0]} fontSize={0.3} color="#94a3b8">{yKey}</Text>
      <Text position={[0, 0, 3]} fontSize={0.3} color="#94a3b8">{zKey}</Text>
    </group>
  )
}

function Bar3DScene({ data, config }: { data: Record<string, any>[]; config: Record<string, any> }) {
  const { categoryKey, valueKey } = config
  const groupRef = useRef<any>(null)

  useFrame((_, delta) => {
    if (groupRef.current) groupRef.current.rotation.y += delta * 0.08
  })

  const bars = useMemo(() => {
    const values = data.map(d => Number(d[valueKey]) || 0)
    const heights = normalize(values, 4)
    return data.map((d, i) => ({
      label: String(d[categoryKey] || i),
      height: Math.max(heights[i], 0.1),
      x: (i - data.length / 2) * 0.8,
    }))
  }, [data, categoryKey, valueKey])

  return (
    <group ref={groupRef}>
      {bars.map((bar, i) => (
        <group key={i} position={[bar.x, bar.height / 2, 0]}>
          <mesh>
            <boxGeometry args={[0.5, bar.height, 0.5]} />
            <meshStandardMaterial color={`hsl(${240 + i * 15}, 70%, 55%)`} />
          </mesh>
          <Text position={[0, -bar.height / 2 - 0.3, 0]} fontSize={0.18} color="#94a3b8" anchorY="top">
            {bar.label.slice(0, 6)}
          </Text>
        </group>
      ))}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.01, 0]}>
        <planeGeometry args={[10, 3]} />
        <meshStandardMaterial color="#1e293b" transparent opacity={0.4} />
      </mesh>
    </group>
  )
}

function GlobeScene({ data }: { data: Record<string, any>[] }) {
  const globeRef = useRef<any>(null)

  useFrame((_, delta) => {
    if (globeRef.current) globeRef.current.rotation.y += delta * 0.15
  })

  const points = useMemo(() => {
    return data.slice(0, 60).map((_, i) => {
      const phi = Math.acos(-1 + (2 * i) / Math.max(data.length, 1))
      const theta = Math.sqrt(data.length * Math.PI) * phi
      const r = 2.1
      return { x: r * Math.cos(theta) * Math.sin(phi), y: r * Math.sin(theta) * Math.sin(phi), z: r * Math.cos(phi) }
    })
  }, [data])

  return (
    <group ref={globeRef}>
      <mesh>
        <sphereGeometry args={[2, 24, 24]} />
        <meshStandardMaterial color="#1e293b" transparent opacity={0.25} wireframe />
      </mesh>
      {points.map((p, i) => (
        <mesh key={i} position={[p.x, p.y, p.z]}>
          <sphereGeometry args={[0.05, 6, 6]} />
          <meshStandardMaterial color="#5A5AF6" emissive="#5A5AF6" emissiveIntensity={0.4} />
        </mesh>
      ))}
    </group>
  )
}

function Fallback3D({ visual, onExpand }: { visual: Chat3DVisual; onExpand?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-4 bg-background">
      <i className="fa-solid fa-cube text-3xl text-primary/50 mb-2" />
      <p className="text-xs text-muted-foreground">3D visualization ({visual.type})</p>
      <p className="text-[10px] text-muted-foreground mt-1">{visual.data.length} data points</p>
      <p className="text-[10px] text-muted-foreground mt-0.5">WebGL unavailable in this view.</p>
      {onExpand && (
        <button
          onClick={onExpand}
          className="mt-2 text-xs text-primary hover:text-foreground border border-primary/30 px-3 py-1 rounded-lg transition-colors"
        >
          <i className="fa-solid fa-expand mr-1" />Open in fullscreen
        </button>
      )}
    </div>
  )
}

export default function Visual3D({ visual, height = 300, onExpand }: Props) {
  const { type, title, data, config } = visual
  const [renderError, setRenderError] = useState(false)
  const [ready, setReady] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const timer = setTimeout(() => {
      if (containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect()
        if (rect.width > 0 && rect.height > 0) {
          setReady(true)
        } else {
          setRenderError(true)
        }
      }
    }, 500)
    return () => clearTimeout(timer)
  }, [])

  if (!data || data.length === 0) return null

  return (
    <div className="mt-2 bg-background rounded-lg border border-border p-2 relative group">
      <div className="flex items-center justify-between mb-1 px-1">
        {title && <p className="text-xs text-muted-foreground font-medium">{title}</p>}
        {onExpand && (
          <button
            onClick={onExpand}
            className="text-[10px] text-primary hover:text-foreground transition-colors flex items-center gap-1"
          >
            <i className="fa-solid fa-expand" />
            Expand
          </button>
        )}
      </div>
      <div ref={containerRef} style={{ height }} className="rounded-lg overflow-hidden">
        {renderError ? (
          <Fallback3D visual={visual} onExpand={onExpand} />
        ) : !ready ? (
          <div className="h-full flex items-center justify-center bg-background">
            <LogoSpinner size={32} />
          </div>
        ) : (
          <Canvas3DErrorBoundary fallback={<Fallback3D visual={visual} onExpand={onExpand} />}>
            <Canvas
              camera={{ position: [5, 4, 5], fov: 50 }}
              style={{ background: '#0B0E1A' }}
              onCreated={({ gl }) => {
                gl.domElement.addEventListener('webglcontextlost', (e) => {
                  e.preventDefault()
                  setRenderError(true)
                })
              }}
              gl={{ antialias: false, powerPreference: 'low-power', alpha: false }}
              dpr={[1, 1.5]}
            >
              <ambientLight intensity={0.5} />
              <directionalLight position={[5, 8, 5]} intensity={0.7} />
              {type === 'scatter3d' && <Scatter3DScene data={data} config={config} />}
              {type === 'bar3d' && <Bar3DScene data={data} config={config} />}
              {type === 'globe' && <GlobeScene data={data} />}
              <OrbitControls enablePan enableZoom enableRotate autoRotate autoRotateSpeed={0.5} />
            </Canvas>
          </Canvas3DErrorBoundary>
        )}
      </div>
    </div>
  )
}
