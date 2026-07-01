import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react'
import type { ChatChart, Chat3DVisual } from '../hooks/useChat'

export type AssetKind = 'chart' | '3d'

export interface GeneratedAsset {
  id: string
  kind: AssetKind
  title: string
  chart?: ChatChart
  visual3d?: Chat3DVisual
  source: 'voice' | 'chat'
  datasetId: string | null
  createdAt: number
}

export type NewAsset = Omit<GeneratedAsset, 'id' | 'createdAt'> & { id?: string }

interface GeneratedAssetsCtx {
  /** Everything the AI generated this session (the Canvas gallery). */
  assets: GeneratedAsset[]
  /** Assets the user pinned onto the dashboard (persisted). */
  pinned: GeneratedAsset[]
  canvasOpen: boolean
  addAsset: (a: NewAsset) => string
  removeAsset: (id: string) => void
  clearAssets: () => void
  openCanvas: () => void
  closeCanvas: () => void
  toggleCanvas: () => void
  pinAsset: (asset: GeneratedAsset) => void
  unpinAsset: (id: string) => void
  isPinned: (id: string) => boolean
  pinnedForDataset: (datasetId: string | null | undefined) => GeneratedAsset[]
}

const ASSETS_KEY = 'bi-generated-assets'   // session — cleared when the tab closes
const PINNED_KEY = 'bi-pinned-assets'      // local — survives across sessions
const MAX_ASSETS = 30

const Ctx = createContext<GeneratedAssetsCtx | null>(null)

function loadSession(): GeneratedAsset[] {
  try {
    const raw = sessionStorage.getItem(ASSETS_KEY)
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

function loadPinned(): GeneratedAsset[] {
  try {
    const raw = localStorage.getItem(PINNED_KEY)
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

export function GeneratedAssetsProvider({ children }: { children: ReactNode }) {
  const [assets, setAssets] = useState<GeneratedAsset[]>(loadSession)
  const [pinned, setPinned] = useState<GeneratedAsset[]>(loadPinned)
  const [canvasOpen, setCanvasOpen] = useState(false)

  useEffect(() => {
    try { sessionStorage.setItem(ASSETS_KEY, JSON.stringify(assets)) } catch { /* quota */ }
  }, [assets])

  useEffect(() => {
    try { localStorage.setItem(PINNED_KEY, JSON.stringify(pinned)) } catch { /* quota */ }
  }, [pinned])

  const addAsset = useCallback((a: NewAsset): string => {
    const id = a.id || `ga-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
    setAssets(prev => {
      if (prev.some(x => x.id === id)) return prev
      return [...prev, { ...a, id, createdAt: Date.now() }].slice(-MAX_ASSETS)
    })
    return id
  }, [])

  const removeAsset = useCallback((id: string) => {
    setAssets(prev => prev.filter(a => a.id !== id))
  }, [])

  const clearAssets = useCallback(() => setAssets([]), [])

  const openCanvas = useCallback(() => setCanvasOpen(true), [])
  const closeCanvas = useCallback(() => setCanvasOpen(false), [])
  const toggleCanvas = useCallback(() => setCanvasOpen(o => !o), [])

  const pinAsset = useCallback((asset: GeneratedAsset) => {
    setPinned(prev => (prev.some(p => p.id === asset.id) ? prev : [...prev, asset]))
  }, [])

  const unpinAsset = useCallback((id: string) => {
    setPinned(prev => prev.filter(p => p.id !== id))
  }, [])

  const isPinned = useCallback((id: string) => pinned.some(p => p.id === id), [pinned])

  const pinnedForDataset = useCallback(
    (datasetId: string | null | undefined) =>
      pinned.filter(p => !datasetId || !p.datasetId || p.datasetId === datasetId),
    [pinned],
  )

  return (
    <Ctx.Provider
      value={{
        assets, pinned, canvasOpen,
        addAsset, removeAsset, clearAssets,
        openCanvas, closeCanvas, toggleCanvas,
        pinAsset, unpinAsset, isPinned, pinnedForDataset,
      }}
    >
      {children}
    </Ctx.Provider>
  )
}

export function useGeneratedAssets(): GeneratedAssetsCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useGeneratedAssets must be used within <GeneratedAssetsProvider>')
  return ctx
}
