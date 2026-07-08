import { useState, useCallback, useRef, useEffect } from 'react'
import { arrayMove } from '@dnd-kit/sortable'
import { sendChatMessageStream } from '../api'
import type { ChatChart, Chat3DVisual } from './useChat'

export interface MetricCard {
  label: string
  value: number
  formatted: string
  agg: 'sum' | 'mean' | 'count'
  column: string | null
}

export interface MetricsAsset {
  title: string
  cards: MetricCard[]
}

export type Span = 4 | 6 | 8 | 12
export type HeightPreset = 'normal' | 'tall'

export type AgentWidget =
  | { id: string; kind: 'metric'; span: Span; height: HeightPreset; metric: MetricCard; createdAt: number }
  | { id: string; kind: 'chart'; span: Span; height: HeightPreset; chart: ChatChart; createdAt: number }
  | { id: string; kind: '3d'; span: Span; height: HeightPreset; visual3d: Chat3DVisual; createdAt: number }
  | { id: string; kind: 'note'; span: Span; height: HeightPreset; text: string; createdAt: number }
  | { id: string; kind: 'query'; span: Span; height: HeightPreset; text: string; createdAt: number }

interface StreamEvent {
  type: string
  text?: string
  data?: unknown
  conversation_id?: string
  message?: string
}

interface UseAgentBoardReturn {
  widgets: AgentWidget[]
  order: string[]
  loading: boolean
  error: string | null
  runPrompt: (text: string) => Promise<void>
  stop: () => void
  addChart: (chart: ChatChart) => void
  add3D: (visual: Chat3DVisual) => void
  addMetrics: (metrics: MetricsAsset) => void
  addNote: (text: string) => void
  addQuery: (text: string) => void
  streamQuery: (text: string) => void
  streamNote: (text: string) => void
  endQuery: (finalText?: string) => void
  endNote: (finalText?: string) => void
  removeWidget: (id: string) => void
  clearBoard: () => void
  reorder: (activeId: string, overId: string) => void
  setSpan: (id: string, span: Span) => void
  setHeight: (id: string, height: HeightPreset) => void
}

const CONV_ID_PREFIX = 'axbi-agent-conv:'
const BOARD_PREFIX = 'axbi-agent-widgets:'
const MAX_WIDGETS = 40

function datasetKey(): string {
  return localStorage.getItem('bi_dashboard_last_dataset_id') || 'default'
}

function boardKey(): string {
  return `${BOARD_PREFIX}${datasetKey()}`
}

interface PersistShape {
  widgets: AgentWidget[]
  order: string[]
}

function loadBoard(): PersistShape {
  try {
    const raw = localStorage.getItem(boardKey())
    if (!raw) return { widgets: [], order: [] }
    const parsed = JSON.parse(raw) as Partial<PersistShape>
    const widgets = Array.isArray(parsed.widgets) ? parsed.widgets : []
    const order = Array.isArray(parsed.order) ? parsed.order : widgets.map(w => w.id)
    return { widgets, order }
  } catch {
    return { widgets: [], order: [] }
  }
}

export function useAgentBoard(onCaption?: (text: string) => void): UseAgentBoardReturn {
  const initial = loadBoard()
  const [widgets, setWidgets] = useState<AgentWidget[]>(initial.widgets)
  const [order, setOrder] = useState<string[]>(initial.order)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const idCounter = useRef(Date.now())
  const conversationId = useRef<string | null>(
    localStorage.getItem(`${CONV_ID_PREFIX}${datasetKey()}`),
  )
  const abortRef = useRef<AbortController | null>(null)
  const captionRef = useRef<(text: string) => void>(onCaption ?? (() => {}))
  captionRef.current = onCaption ?? (() => {})
  // Ids of the in-progress voice caption widgets (updated in place while streaming).
  const liveQueryIdRef = useRef<string | null>(null)
  const liveNoteIdRef = useRef<string | null>(null)

  useEffect(() => {
    try {
      localStorage.setItem(boardKey(), JSON.stringify({ widgets: widgets.slice(-MAX_WIDGETS), order }))
    } catch {
      /* quota */
    }
  }, [widgets, order])

  const addWidget = useCallback((w: AgentWidget) => {
    setWidgets(prev => [...prev, w])
    setOrder(prev => [...prev, w.id])
  }, [])

  const makeId = useCallback(() => `w-${++idCounter.current}`, [])

  // Imperative adders used by the live voice session to drop assets straight
  // onto the board (charts/3D as widgets, assistant text as draggable notes).
  const addChart = useCallback((chart: ChatChart) => {
    addWidget({ id: makeId(), kind: 'chart', span: 6, height: 'normal', chart, createdAt: Date.now() })
  }, [addWidget, makeId])

  const add3D = useCallback((visual: Chat3DVisual) => {
    addWidget({ id: makeId(), kind: '3d', span: 6, height: 'tall', visual3d: visual, createdAt: Date.now() })
  }, [addWidget, makeId])

  const addMetrics = useCallback((metrics: MetricsAsset) => {
    for (const card of metrics.cards || []) {
      addWidget({ id: makeId(), kind: 'metric', span: 4, height: 'normal', metric: card, createdAt: Date.now() })
    }
  }, [addWidget, makeId])

  const addNote = useCallback((text: string) => {
    const t = text.trim()
    if (!t) return
    addWidget({ id: makeId(), kind: 'note', span: 4, height: 'normal', text: t, createdAt: Date.now() })
  }, [addWidget, makeId])

  // User's prompt echoed onto the board as a "You asked …" bubble.
  const addQuery = useCallback((text: string) => {
    const t = text.trim()
    if (!t) return
    addWidget({ id: makeId(), kind: 'query', span: 12, height: 'normal', text: t, createdAt: Date.now() })
  }, [addWidget, makeId])

  // ── Live voice captions: create-or-update a widget in place while streaming ──
  const streamQuery = useCallback((text: string) => {
    if (!text) return
    if (liveQueryIdRef.current) {
      const id = liveQueryIdRef.current
      setWidgets(prev => prev.map(w => (w.id === id && w.kind === 'query') ? { ...w, text } : w))
    } else {
      const id = makeId()
      liveQueryIdRef.current = id
      addWidget({ id, kind: 'query', span: 12, height: 'normal', text, createdAt: Date.now() })
    }
  }, [addWidget, makeId])

  const streamNote = useCallback((text: string) => {
    if (!text) return
    if (liveNoteIdRef.current) {
      const id = liveNoteIdRef.current
      setWidgets(prev => prev.map(w => (w.id === id && w.kind === 'note') ? { ...w, text } : w))
    } else {
      const id = makeId()
      liveNoteIdRef.current = id
      addWidget({ id, kind: 'note', span: 4, height: 'normal', text, createdAt: Date.now() })
    }
  }, [addWidget, makeId])

  // Finalize a live caption. With `finalText`: replace text (or drop the widget if
  // empty). Without an argument: just detach the ref so the next turn starts fresh.
  const endQuery = useCallback((finalText?: string) => {
    const id = liveQueryIdRef.current
    liveQueryIdRef.current = null
    if (!id || finalText === undefined) return
    const t = finalText.trim()
    if (!t) {
      setWidgets(prev => prev.filter(w => w.id !== id))
      setOrder(prev => prev.filter(x => x !== id))
    } else {
      setWidgets(prev => prev.map(w => (w.id === id && w.kind === 'query') ? { ...w, text: t } : w))
    }
  }, [])

  const endNote = useCallback((finalText?: string) => {
    const id = liveNoteIdRef.current
    liveNoteIdRef.current = null
    if (!id || finalText === undefined) return
    const t = finalText.trim()
    if (!t) {
      setWidgets(prev => prev.filter(w => w.id !== id))
      setOrder(prev => prev.filter(x => x !== id))
    } else {
      setWidgets(prev => prev.map(w => (w.id === id && w.kind === 'note') ? { ...w, text: t } : w))
    }
  }, [])

  const stop = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setLoading(false)
  }, [])

  const runPrompt = useCallback(async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || loading) return

    setLoading(true)
    setError(null)

    const controller = new AbortController()
    abortRef.current = controller
    const apiMessages = [{ role: 'user' as const, content: trimmed }]
    let caption = ''
    const nid = () => `w-${++idCounter.current}`

    // Echo the user's prompt onto the board immediately so it isn't lost.
    addWidget({ id: nid(), kind: 'query', span: 12, height: 'normal', text: trimmed, createdAt: Date.now() })

    try {
      const datasetId = localStorage.getItem('bi_dashboard_last_dataset_id') || null

      await sendChatMessageStream(
        apiMessages,
        datasetId,
        conversationId.current,
        controller.signal,
        (event: StreamEvent) => {
          if (controller.signal.aborted) return
          if (event.type === 'chunk') {
            caption += event.text || ''
          } else if (event.type === 'chart') {
            addWidget({ id: nid(), kind: 'chart', span: 6, height: 'normal', chart: event.data as ChatChart, createdAt: Date.now() })
          } else if (event.type === 'visual3d') {
            addWidget({ id: nid(), kind: '3d', span: 6, height: 'tall', visual3d: event.data as Chat3DVisual, createdAt: Date.now() })
          } else if (event.type === 'metrics') {
            const m = event.data as MetricsAsset
            for (const card of m.cards || []) {
              addWidget({ id: nid(), kind: 'metric', span: 4, height: 'normal', metric: card, createdAt: Date.now() })
            }
          } else if (event.type === 'done') {
            if (event.conversation_id) {
              conversationId.current = event.conversation_id
              localStorage.setItem(`${CONV_ID_PREFIX}${datasetKey()}`, event.conversation_id)
            }
          } else if (event.type === 'error') {
            setError(event.message || 'An error occurred')
          }
        },
      )

      if (!controller.signal.aborted && caption.trim()) {
        // Text output becomes a draggable note widget on the board.
        addWidget({ id: nid(), kind: 'note', span: 4, height: 'normal', text: caption.trim(), createdAt: Date.now() })
        captionRef.current(caption.trim())
      }
    } catch (e) {
      const err = e as { name?: string; message?: string }
      if (err?.name === 'AbortError' || controller.signal.aborted) return
      const msg = err?.message || 'Failed to send message'
      setError(msg)
    } finally {
      abortRef.current = null
      setLoading(false)
    }
  }, [loading, addWidget])

  const removeWidget = useCallback((id: string) => {
    setWidgets(prev => prev.filter(w => w.id !== id))
    setOrder(prev => prev.filter(x => x !== id))
  }, [])

  const clearBoard = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setWidgets([])
    setOrder([])
    liveQueryIdRef.current = null
    liveNoteIdRef.current = null
    conversationId.current = null
    try {
      localStorage.removeItem(boardKey())
      localStorage.removeItem(`${CONV_ID_PREFIX}${datasetKey()}`)
    } catch {
      /* ignore */
    }
    setLoading(false)
    setError(null)
  }, [])

  const reorder = useCallback((activeId: string, overId: string) => {
    if (activeId === overId) return
    setOrder(prev => {
      const from = prev.indexOf(activeId)
      const to = prev.indexOf(overId)
      if (from === -1 || to === -1) return prev
      return arrayMove(prev, from, to)
    })
  }, [])

  const setSpan = useCallback((id: string, span: Span) => {
    setWidgets(prev => prev.map(w => (w.id === id ? { ...w, span } : w)))
  }, [])

  const setHeight = useCallback((id: string, height: HeightPreset) => {
    setWidgets(prev => prev.map(w => (w.id === id ? { ...w, height } : w)))
  }, [])

  return { widgets, order, loading, error, runPrompt, stop, addChart, add3D, addMetrics, addNote, addQuery, streamQuery, streamNote, endQuery, endNote, removeWidget, clearBoard, reorder, setSpan, setHeight }
}
