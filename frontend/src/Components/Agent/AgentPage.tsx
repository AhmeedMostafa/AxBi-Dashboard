import { useState, useRef, useEffect, useMemo, useCallback, lazy, Suspense, type RefObject } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import {
  DndContext, closestCenter, PointerSensor, useSensor, useSensors, type DragEndEvent,
} from '@dnd-kit/core'
import { SortableContext, rectSortingStrategy } from '@dnd-kit/sortable'
import { supabase } from '../../supabase-client'
import { listDatasets, getDatasetDashboard } from '../../api'
import AudioWaveform from '../Chat/AudioWaveform'
import { LiveClient, type LiveStatus, type LiveAction } from '../../lib/liveClient'
import { mergeLiveTranscript, mergeAssistantTranscript, captionDir, normalizeTranscriptText } from '../../lib/liveTranscript'
import AxBiLogo from '../ui/AxBiLogo'
import { MetricCardView } from './KpiCards'
import { LogoSpinner } from '../ui/LogoSpinner'
import InteractiveChartCard, { type Field } from '../Hero/DashboardTemplate/InteractiveChartCard'
import SortableChartCard from '../Hero/DashboardTemplate/SortableChartCard'
import { useAgentBoard, type AgentWidget } from '../../hooks/useAgentBoard'
import type { ChatChart, Chat3DVisual } from '../../hooks/useChat'

type VoiceState = 'idle' | LiveStatus

const Visual3D = lazy(() => import('../Conversation/Visual3D'))

const PROJECT_NAME_KEY = 'axbi_project_name'
const DATASET_ID_KEY = 'bi_dashboard_last_dataset_id'

const GENERIC_SUGGESTIONS = [
  { icon: 'fa-gauge-high', text: 'Show me the key metrics for my data' },
  { icon: 'fa-magnifying-glass-chart', text: 'Generate a chart of my top metrics' },
  { icon: 'fa-arrow-trend-up', text: 'Show my data over time' },
  { icon: 'fa-circle-question', text: 'What questions should I be asking my data?' },
]

function prettify(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function buildFields(columns: Array<Record<string, unknown>>): Field[] {
  const out: Field[] = []
  for (const col of columns || []) {
    const key = (col.column_key ?? col.clean_name ?? col.original_name) as string | undefined
    if (!key) continue
    const dt = String(col.data_type ?? '').toLowerCase()
    let role = ''
    const ap = col.ai_profile
    if (ap && typeof ap === 'object') role = String((ap as Record<string, unknown>).role ?? '').toLowerCase()
    else if (typeof ap === 'string') { try { role = String(JSON.parse(ap)?.role ?? '').toLowerCase() } catch { /* ignore */ } }
    let kind: Field['kind'] = 'other'
    if (dt === 'numeric') kind = 'numeric'
    else if (dt === 'datetime' || dt === 'date' || role === 'date') kind = 'date'
    else if (dt === 'categorical' || dt === 'text' || dt === 'boolean' || role === 'dimension' || role === 'categorical' || role === 'geographic') kind = 'dim'
    out.push({ key, label: (col.display_name as string) ?? prettify(key), kind })
  }
  return out
}

function buildSuggestions(fields: Field[]): { icon: string; text: string }[] {
  if (!fields.length) return GENERIC_SUGGESTIONS
  const numeric = fields.filter((f) => f.kind === 'numeric')
  const dates = fields.filter((f) => f.kind === 'date')
  const dims = fields.filter((f) => f.kind === 'dim')
  const out: { icon: string; text: string }[] = [{ icon: 'fa-gauge-high', text: 'Show me the key metrics for my data' }]
  if (numeric[0] && dates[0]) out.push({ icon: 'fa-arrow-trend-up', text: `Show ${numeric[0].label} over time` })
  if (numeric[0] && dims[0]) out.push({ icon: 'fa-chart-column', text: `Compare ${numeric[0].label} by ${dims[0].label}` })
  const totalCol = numeric[1] || numeric[0]
  if (totalCol) out.push({ icon: 'fa-magnifying-glass-chart', text: `Show total ${totalCol.label}` })
  while (out.length < 4) {
    const fill = GENERIC_SUGGESTIONS[out.length]
    if (!fill || out.some((s) => s.text === fill.text)) break
    out.push(fill)
  }
  return out.slice(0, 4)
}

function toInteractiveData(chart: ChatChart): Array<Record<string, unknown>> {
  return (chart.data || []).map((r) => ({
    label: String(r[chart.xKey]),
    name: String(r[chart.xKey]),
    value: Number(r[chart.yKey]),
  }))
}

const heightPx = (h: 'normal' | 'tall') => (h === 'tall' ? 440 : 290)

interface PromptBarProps {
  input: string
  setInput: (v: string | ((p: string) => string)) => void
  loading: boolean
  hasBoard: boolean
  onSubmit: (text: string) => void
  onStop: () => void
  voiceState: VoiceState
  onVoiceToggle: () => void
  voiceLang: string
  onLangToggle: () => void
  inputRef?: RefObject<HTMLTextAreaElement | null>
}

function PromptBar({ input, setInput, loading, hasBoard, onSubmit, onStop, voiceState, onVoiceToggle, voiceLang, onLangToggle, inputRef }: PromptBarProps) {
  const voiceActive = voiceState !== 'idle'
  const voiceTitle = voiceActive
    ? (voiceState === 'connecting' ? 'Connecting…' : 'Listening — tap to end')
    : 'Talk to your data'
  return (
    <form onSubmit={(e) => { e.preventDefault(); onSubmit(input) }} className="w-full">
      <div className="flex items-end gap-2.5">
        <div className="flex-1 flex items-end gap-2 bg-card border border-border rounded-2xl px-4 py-3 focus-within:border-primary/60 transition-colors shadow-sm">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                onSubmit(input)
              }
            }}
            rows={1}
            placeholder={hasBoard ? 'Ask for another metric, chart or insight…' : 'Ask anything about your data...'}
            disabled={loading}
            className="flex-1 bg-transparent text-sm text-foreground placeholder-muted-foreground outline-none resize-none max-h-40 py-1.5 disabled:opacity-50"
          />
          {loading ? (
            <button type="button" onClick={onStop} className="w-9 h-9 rounded-xl bg-muted hover:bg-muted/80 flex items-center justify-center transition-colors shrink-0" title="Stop">
              <i className="fa-solid fa-stop text-foreground text-xs" />
            </button>
          ) : (
            <button type="submit" disabled={!input.trim()} className="w-9 h-9 rounded-xl bg-primary hover:bg-primary/90 disabled:opacity-30 flex items-center justify-center transition-colors shrink-0" title="Send">
              <i className="fa-solid fa-paper-plane text-primary-foreground text-xs" />
            </button>
          )}
        </div>
        <button
          type="button"
          onClick={onLangToggle}
          title={`Voice language: ${voiceLang === 'ar-EG' ? 'Arabic' : 'English'} — tap to switch`}
          aria-label="Switch voice language"
          className={`shrink-0 h-12 px-3 rounded-full border text-xs font-semibold transition-colors ${
            voiceLang === 'ar-EG'
              ? 'border-primary/60 text-primary bg-primary/10'
              : 'border-border text-muted-foreground hover:text-foreground hover:border-primary/50'
          }`}
        >
          {voiceLang === 'ar-EG' ? 'عربي' : 'EN'}
        </button>
        <button
          type="button"
          onClick={onVoiceToggle}
          title={voiceTitle}
          aria-label={voiceTitle}
          className={`shrink-0 rounded-full flex items-center justify-center shadow-md transition-all duration-300 overflow-hidden ${
            voiceActive
              ? 'w-16 h-16 bg-gradient-to-br from-emerald-400 to-teal-600 text-white shadow-lg shadow-emerald-500/40 ring-4 ring-emerald-400/30'
              : 'w-12 h-12 bg-gradient-to-br from-[#5A5AF6] to-[#8B5CF6] text-primary-foreground shadow-primary/30 hover:scale-105 active:scale-95'
          }`}
        >
          {voiceActive
            ? <AudioWaveform active size="small" className="text-white" />
            : <i className="fa-solid fa-microphone text-lg" />}
        </button>
      </div>
    </form>
  )
}

function WidgetView({ widget, datasetId, fields }: { widget: AgentWidget; datasetId: string; fields: Field[] }) {
  if (widget.kind === 'query') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-gradient-to-br from-[#5A5AF6] to-[#8B5CF6] text-white px-4 py-2.5 shadow-sm">
          <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-white/70 mb-0.5">
            <i className="fa-solid fa-user" /> You asked
          </div>
          <p
            className="text-sm whitespace-pre-wrap leading-relaxed"
            dir={captionDir(widget.text)}
            style={{ unicodeBidi: 'plaintext' }}
          >
            {widget.text}
          </p>
        </div>
      </div>
    )
  }
  if (widget.kind === 'metric') {
    return <MetricCardView card={widget.metric} height={widget.height === 'tall' ? 220 : 140} />
  }
  if (widget.kind === 'note') {
    return (
      <div
        className="h-full rounded-2xl border border-border bg-card p-4 shadow-sm overflow-auto border-l-4 border-l-primary/50"
        style={{ minHeight: widget.height === 'tall' ? 300 : 160 }}
      >
        <div className="flex items-center gap-1.5 text-[11px] font-semibold text-primary/80 uppercase tracking-wide mb-2">
          <i className="fa-solid fa-note-sticky" /> Note
        </div>
        <p
          className="text-sm text-foreground whitespace-pre-wrap leading-relaxed"
          dir={captionDir(widget.text)}
          style={{ unicodeBidi: 'plaintext' }}
        >
          {widget.text}
        </p>
      </div>
    )
  }
  if (widget.kind === 'chart') {
    return (
      <InteractiveChartCard
        datasetId={datasetId}
        title={widget.chart.title || 'Chart'}
        reason=""
        initialType={widget.chart.type}
        initialX={widget.chart.xKey}
        initialY={widget.chart.yKey}
        initialData={toInteractiveData(widget.chart)}
        fields={fields}
        height={heightPx(widget.height)}
        storageId={widget.id}
      />
    )
  }
  return (
    <Suspense fallback={<div className="h-[300px] bg-card rounded-2xl border border-border flex items-center justify-center text-muted-foreground text-xs">Loading 3D...</div>}>
      <Visual3D visual={widget.visual3d} height={heightPx(widget.height)} />
    </Suspense>
  )
}

export default function AgentPage() {
  const navigate = useNavigate()

  const { widgets, order, loading, error, runPrompt, stop, addChart, add3D, addMetrics, addNote, addQuery, removeWidget, clearBoard, reorder, setSpan, setHeight } =
    useAgentBoard()

  const [input, setInput] = useState('')
  const [projectName] = useState(() => localStorage.getItem(PROJECT_NAME_KEY) || '')
  const [firstName, setFirstName] = useState('')
  const [voiceLang, setVoiceLang] = useState(() => localStorage.getItem('bi-voice-language') || 'en')
  const [voiceState, setVoiceState] = useState<VoiceState>('idle')
  const [editMode, setEditMode] = useState(false)
  const [datasetId, setDatasetId] = useState(() => localStorage.getItem(DATASET_ID_KEY) || '')
  const [fields, setFields] = useState<Field[]>([])
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  // Headless live voice session: the mic circle runs the real Gemini Live agent,
  // whose charts/3D land as board widgets and whose spoken answers become notes.
  const liveRef = useRef<LiveClient | null>(null)
  const assistantBufRef = useRef('')
  const userBufRef = useRef('')
  const lastSpeakerRef = useRef<'user' | 'assistant' | null>(null)

  const widgetById = useMemo(() => {
    const m = new Map<string, AgentWidget>()
    for (const w of widgets) m.set(w.id, w)
    return m
  }, [widgets])

  const hasBoard = order.length > 0
  const suggestions = useMemo(() => buildSuggestions(fields), [fields])

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }))

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => {
      const meta = (data.user?.user_metadata || {}) as Record<string, string>
      const name = (meta.name || meta.full_name || meta.display_name || '').toString().trim()
      setFirstName(name ? name.split(/\s+/)[0] : '')
    })
    setTimeout(() => inputRef.current?.focus(), 200)
  }, [])

  // Resolve the active dataset and load its column fields for chart customization
  // and context-aware suggestions.
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      let id = localStorage.getItem(DATASET_ID_KEY) || ''
      if (!id) {
        try {
          const res = await listDatasets()
          const list: Array<Record<string, unknown>> = res?.datasets || []
          const done = list.find((d) => d.status === 'completed') || list[0]
          if (done?.id) {
            id = String(done.id)
            localStorage.setItem(DATASET_ID_KEY, id)
          }
        } catch { /* ignore */ }
      }
      if (!id || cancelled) return
      setDatasetId(id)
      try {
        const dash = await getDatasetDashboard(id)
        if (cancelled) return
        setFields(buildFields(dash?.columns || []))
      } catch { /* ignore — charts still render baseline data */ }
    }
    load()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (hasBoard) bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [order.length, hasBoard])

  useEffect(() => {
    if (error) toast.error(error)
  }, [error])

  const submit = (text: string) => {
    const prompt = text.trim()
    if (!prompt || loading) return
    setInput('')
    runPrompt(prompt)
  }

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (over && active.id !== over.id) reorder(String(active.id), String(over.id))
  }

  // ── Live voice session ────────────────────────────────────────────────────
  const commitNote = useCallback(() => {
    const t = normalizeTranscriptText(assistantBufRef.current)
    assistantBufRef.current = ''
    if (t) addNote(t)
  }, [addNote])

  const commitQuery = useCallback(() => {
    const t = normalizeTranscriptText(userBufRef.current)
    userBufRef.current = ''
    if (t) addQuery(t)
  }, [addQuery])

  /** Pin the user's spoken question on the board before tool-generated widgets. */
  const flushUserQuery = useCallback(() => {
    commitQuery()
  }, [commitQuery])

  const handleLiveAction = useCallback((action: LiveAction) => {
    if (action.type === 'navigate' && action.payload?.path) {
      toast(voiceLang === 'ar-EG' ? 'بفتحلك الصفحة…' : 'Opening the page…')
      setTimeout(() => navigate(action.payload.path as string), 1200)
    } else if (action.type === 'toast') {
      toast.success(action.payload?.message || 'Done')
    } else if (action.type === 'refresh') {
      window.location.reload()
    }
  }, [navigate, voiceLang])

  const stopVoice = useCallback(() => {
    try { liveRef.current?.close() } catch { /* noop */ }
    liveRef.current = null
    commitQuery()
    commitNote()
    lastSpeakerRef.current = null
    setVoiceState('idle')
  }, [commitNote, commitQuery])

  const startVoice = useCallback(async () => {
    try {
      if (voiceLang !== 'ar-EG') {
        toast(
          'Voice is set to English — tap عربي before speaking in Arabic for accurate captions.',
          { duration: 4500, icon: '🎙️' },
        )
      }
      const { data } = await supabase.auth.getSession()
      const token = data?.session?.access_token
      if (!token) { toast.error('Please log in again to use voice.'); return }
      const dsId = localStorage.getItem(DATASET_ID_KEY) || null
      assistantBufRef.current = ''
      userBufRef.current = ''
      lastSpeakerRef.current = null
      setVoiceState('connecting')

      const client = new LiveClient(
        { token, lang: voiceLang === 'ar-EG' ? 'ar-EG' : 'en-US', datasetId: dsId },
        {
          onStatus: (s: LiveStatus) => setVoiceState(s),
          onUserTranscript: (t: string) => {
            if (!t) return
            if (lastSpeakerRef.current === 'assistant') commitNote()
            userBufRef.current =
              lastSpeakerRef.current === 'user'
                ? mergeLiveTranscript(userBufRef.current, t)
                : t
            lastSpeakerRef.current = 'user'
          },
          onAssistantTranscript: (t: string) => {
            if (!t) return
            flushUserQuery()
            assistantBufRef.current =
              lastSpeakerRef.current === 'assistant'
                ? mergeAssistantTranscript(assistantBufRef.current, t)
                : t
            lastSpeakerRef.current = 'assistant'
          },
          onTurnComplete: () => {
            flushUserQuery()
            commitNote()
          },
          onChart: (chart) => {
            flushUserQuery()
            addChart(chart as ChatChart)
          },
          onVisual3D: (visual) => {
            flushUserQuery()
            add3D(visual as Chat3DVisual)
          },
          onMetrics: (metrics) => {
            flushUserQuery()
            addMetrics(metrics)
          },
          onAction: handleLiveAction,
          onError: (m: string) => toast.error(m),
          onClose: () => { commitQuery(); commitNote(); liveRef.current = null; lastSpeakerRef.current = null; setVoiceState('idle') },
        },
      )
      liveRef.current = client
      await client.start()
    } catch (e) {
      const err = e as { name?: string; message?: string }
      toast.error(err?.name === 'NotAllowedError'
        ? 'Microphone permission denied. Allow mic access and try again.'
        : (err?.message || 'Failed to start the voice session.'))
      liveRef.current = null
      setVoiceState('idle')
    }
  }, [voiceLang, commitNote, commitQuery, flushUserQuery, addChart, add3D, addMetrics, handleLiveAction])

  const toggleVoice = useCallback(() => {
    if (voiceState === 'idle') startVoice()
    else stopVoice()
  }, [voiceState, startVoice, stopVoice])

  const toggleLang = useCallback(() => {
    const next = voiceLang === 'ar-EG' ? 'en' : 'ar-EG'
    setVoiceLang(next)
    localStorage.setItem('bi-voice-language', next)
    // If a session is live, switch language in place (tears down + reopens).
    if (liveRef.current && voiceState !== 'idle') {
      commitNote()
      lastSpeakerRef.current = null
      setVoiceState('connecting')
      liveRef.current.switchLanguage(next === 'ar-EG' ? 'ar-EG' : 'en-US')
        .catch(() => { toast.error('Failed to switch language.'); stopVoice() })
    }
  }, [voiceLang, voiceState, commitNote, stopVoice])

  useEffect(() => {
    return () => { try { liveRef.current?.close() } catch { /* noop */ } liveRef.current = null }
  }, [])

  const greeting = firstName ? `Hi ${firstName}` : 'Welcome'
  const promptBarProps: PromptBarProps = {
    input, setInput, loading, hasBoard, onSubmit: submit, onStop: stop,
    voiceState, onVoiceToggle: toggleVoice, voiceLang, onLangToggle: toggleLang,
  }

  // ── Empty state: centered hero ───────────────────────────────────────────
  if (!hasBoard) {
    return (
      <div className="min-h-[calc(100vh-3.5rem)] flex flex-col items-center justify-center px-6 py-10">
        <div className="w-full max-w-2xl flex flex-col items-center text-center">
          <div className="relative mb-6">
            <div className="absolute inset-0 rounded-full bg-primary/20 blur-2xl" style={{ animation: 'pulse-opacity 3s infinite' }} />
            <AxBiLogo className="h-16" />
          </div>

          <h1 className="text-3xl md:text-4xl font-extrabold mb-2">{greeting}, let&apos;s explore your data</h1>
          <p className="text-muted-foreground text-sm mb-8 max-w-md">
            {projectName
              ? <>Ask AxBi anything about <span className="text-foreground font-semibold">{projectName}</span> — results build a dashboard right here.</>
              : <>Ask AxBi anything about your data — results build a dashboard right here.</>}
          </p>

          <div className="w-full">
            <PromptBar {...promptBarProps} inputRef={inputRef} />
          </div>

          <div className="mt-8 grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full">
            {suggestions.map((s) => (
              <button
                key={s.text}
                onClick={() => submit(s.text)}
                className="flex items-center gap-3 text-left px-4 py-3 rounded-xl bg-card border border-border text-sm text-muted-foreground hover:text-foreground hover:border-primary/40 transition-colors"
              >
                <i className={`fa-solid ${s.icon} text-primary shrink-0`} />
                <span className="truncate">{s.text}</span>
              </button>
            ))}
          </div>

          <div className="mt-8 flex items-center gap-4 text-xs text-muted-foreground">
            <button onClick={() => navigate('/BI-Dashboard')} className="hover:text-foreground transition-colors">
              <i className="fa-solid fa-list-check mr-1" /> View projects
            </button>
            <span className="text-border">|</span>
            <button onClick={() => navigate('/upload')} className="hover:text-foreground transition-colors">
              <i className="fa-solid fa-plus mr-1" /> New project
            </button>
          </div>
        </div>

      </div>
    )
  }

  // ── Board state ──────────────────────────────────────────────────────────
  return (
    <div className="min-h-[calc(100vh-3.5rem)] flex flex-col">
      <div className="sticky top-14 z-20 bg-background/90 backdrop-blur border-b border-border">
        <div className="max-w-6xl mx-auto px-4 md:px-6 py-3">
          <div className="flex items-center justify-between gap-3 mb-2.5">
            <div className="flex items-center gap-2 min-w-0">
              <AxBiLogo className="h-6" />
              <span className="text-sm font-semibold text-foreground truncate">{projectName || 'Your data'}</span>
              <span className="text-[11px] text-muted-foreground hidden sm:inline">· on-demand dashboard</span>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={() => setEditMode((e) => !e)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${editMode ? 'border-primary/50 text-primary bg-primary/10' : 'border-border text-muted-foreground hover:text-foreground hover:bg-card'}`}
                title="Drag, resize and remove widgets"
              >
                <i className="fa-solid fa-table-cells-large" /> <span className="hidden sm:inline">{editMode ? 'Done' : 'Edit layout'}</span>
              </button>
              <button
                onClick={clearBoard}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-muted-foreground hover:text-red-500 hover:bg-red-500/10 border border-border transition-colors"
                title="Clear board"
              >
                <i className="fa-solid fa-trash-can" /> <span className="hidden sm:inline">Clear board</span>
              </button>
            </div>
          </div>
          <div className="max-w-3xl mx-auto">
            <PromptBar {...promptBarProps} />
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto w-full px-4 md:px-6 py-6">
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <SortableContext items={order} strategy={rectSortingStrategy}>
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
              {order.map((id) => {
                const w = widgetById.get(id)
                if (!w) return null
                return (
                  <SortableChartCard
                    key={id}
                    id={id}
                    span={w.span}
                    height={w.height}
                    editMode={editMode}
                    isCustom
                    onSpan={(s) => setSpan(id, s)}
                    onHeight={(h) => setHeight(id, h)}
                    onRemove={() => removeWidget(id)}
                  >
                    <WidgetView widget={w} datasetId={datasetId} fields={fields} />
                  </SortableChartCard>
                )
              })}
            </div>
          </SortableContext>
        </DndContext>

        {loading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground mt-6 justify-center">
            <LogoSpinner size={18} /> Generating…
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
