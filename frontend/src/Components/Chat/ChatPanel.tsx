import { useState, useRef, useEffect, type FormEvent, lazy, Suspense } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { useChat, type ChatAction, type ChatChart, type Chat3DVisual } from '../../hooks/useChat'
import type { LiveAction } from '../../lib/liveClient'
import { listConversations, deleteConversation } from '../../api'
import ChatChartRenderer from '../Conversation/ChatChart'
import VoiceInput from './VoiceInput'
import { useSpeechSynthesis } from '../../hooks/useSpeechSynthesis'
import ConversationMode from './ConversationMode'
import { useGeneratedAssets } from '../../context/GeneratedAssetsContext'
import { LogoSpinner } from '../ui/LogoSpinner'

function MarkdownText({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <div className="space-y-1">
      {lines.map((line, i) => {
        if (/^#{1,3}\s/.test(line)) {
          const content = line.replace(/^#{1,3}\s/, '')
          return <p key={i} className="font-semibold text-foreground mt-1">{renderInline(content)}</p>
        }
        if (/^[-*]\s/.test(line)) {
          return <div key={i} className="flex gap-2"><span className="text-primary shrink-0 mt-0.5">•</span><span>{renderInline(line.slice(2))}</span></div>
        }
        if (/^\d+\.\s/.test(line)) {
          const [num, ...rest] = line.split(/\.\s/)
          return <div key={i} className="flex gap-2"><span className="text-primary shrink-0 font-semibold">{num}.</span><span>{renderInline(rest.join('. '))}</span></div>
        }
        if (line.trim() === '') return <div key={i} className="h-1" />
        return <p key={i}>{renderInline(line)}</p>
      })}
    </div>
  )
}

function renderInline(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/)
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={i} className="text-foreground font-semibold">{part.slice(2, -2)}</strong>
    if (part.startsWith('*') && part.endsWith('*')) return <em key={i} className="italic">{part.slice(1, -1)}</em>
    if (part.startsWith('`') && part.endsWith('`')) return <code key={i} className="bg-card text-primary px-1 py-0.5 rounded text-[11px] font-mono">{part.slice(1, -1)}</code>
    return part
  })
}

const Visual3D = lazy(() => import('../Conversation/Visual3D'))

interface Props {
  open: boolean
  onClose: () => void
}

interface ExpandedVisual {
  type: 'chart' | '3d'
  chart?: ChatChart
  visual3d?: Chat3DVisual
}

function ExpandedModal({ visual, onClose }: { visual: ExpandedVisual; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div
        className="relative w-[90vw] max-w-4xl h-[70vh] bg-background rounded-2xl border border-border shadow-2xl p-4 flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-foreground">
            {visual.type === 'chart' ? visual.chart?.title || 'Chart' : visual.visual3d?.title || '3D Visualization'}
          </h3>
          <button onClick={onClose} className="w-8 h-8 rounded-lg hover:bg-card flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors">
            <i className="fa-solid fa-xmark" />
          </button>
        </div>
        <div className="flex-1 rounded-xl overflow-hidden">
          {visual.type === 'chart' && visual.chart && (
            <ChatChartRenderer chart={visual.chart} height={500} />
          )}
          {visual.type === '3d' && visual.visual3d && (
            <Suspense fallback={<div className="h-full flex items-center justify-center text-muted-foreground text-sm">Loading 3D...</div>}>
              <Visual3D visual={visual.visual3d} height={500} />
            </Suspense>
          )}
        </div>
      </div>
    </div>
  )
}

export default function ChatPanel({ open, onClose }: Props) {
  const navigate = useNavigate()
  const { messages, loading, error, sendMessage, stopGenerating, clearChat } = useChat()
  const [input, setInput] = useState('')
  const [expanded, setExpanded] = useState<ExpandedVisual | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [history, setHistory] = useState<{ id: string; title: string; created_at: string }[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const [voiceMode, setVoiceMode] = useState(() => localStorage.getItem('bi-chat-voice-mode') === 'true')
  const [voiceLang, setVoiceLang] = useState(() => localStorage.getItem('bi-voice-language') || 'en')
  const [showConvoMode, setShowConvoMode] = useState(false)
  const { addAsset, openCanvas, assets: canvasAssets } = useGeneratedAssets()
  const syncedVisualIds = useRef<Set<string>>(new Set())
  const { speak, stop: stopSpeech, isSpeaking, isSupported: ttsSupported } = useSpeechSynthesis()

  useEffect(() => {
    if (showHistory) {
      setHistoryLoading(true)
      listConversations()
        .then(data => setHistory(data.conversations || []))
        .catch(() => {})
        .finally(() => setHistoryLoading(false))
    }
  }, [showHistory])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, loading])

  useEffect(() => {
    if (open && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 200)
    }
  }, [open])

  // Collect charts/3D from typed chat replies into the shared Canvas (once each).
  useEffect(() => {
    const datasetId = localStorage.getItem('bi_dashboard_last_dataset_id')
    for (const m of messages) {
      if (m.role !== 'assistant') continue
      if ((m.chart || m.visual3d) && !syncedVisualIds.current.has(m.id)) {
        syncedVisualIds.current.add(m.id)
        if (m.chart) addAsset({ kind: 'chart', title: m.chart.title || 'Chart', chart: m.chart, source: 'chat', datasetId })
        if (m.visual3d) addAsset({ kind: '3d', title: m.visual3d.title || '3D Visual', visual3d: m.visual3d, source: 'chat', datasetId })
      }
    }
  }, [messages, addAsset])

  const prevLoadingRef = useRef(loading)
  useEffect(() => {
    if (prevLoadingRef.current && !loading && voiceMode && messages.length > 0) {
      const last = messages[messages.length - 1]
      if (last.role === 'assistant' && last.content) {
        speak(last.content, { language: voiceLang })
      }
    }
    prevLoadingRef.current = loading
  }, [loading, voiceMode, messages, speak, voiceLang])

  const handleAction = (action: ChatAction | null) => {
    if (!action) return
    switch (action.type) {
      case 'navigate':
        if (action.payload.path?.startsWith('/conversation/')) {
          onClose()
        }
        navigate(action.payload.path)
        break
      case 'toast':
        toast.success(action.payload.message || 'Done')
        break
      case 'refresh':
        window.location.reload()
        break
    }
  }

  // Actions taken by the voice agent in conversation mode. Navigation gets a
  // short delay so the agent's spoken confirmation can play before we leave the
  // overlay; generated charts/visuals are dropped into the chat thread.
  const handleConvoAction = (action: LiveAction) => {
    if (action.type === 'navigate' && action.payload?.path) {
      const path = action.payload.path as string
      toast(voiceLang === 'ar-EG' ? 'بفتحلك الصفحة…' : 'Opening the page…')
      setTimeout(() => { setShowConvoMode(false); navigate(path) }, 1300)
    } else if (action.type === 'toast') {
      toast.success(action.payload?.message || 'Done')
    } else if (action.type === 'refresh') {
      window.location.reload()
    }
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!input.trim() || loading) return
    if (isSpeaking) stopSpeech()
    const text = input
    setInput('')
    const action = await sendMessage(text)
    handleAction(action)
  }

  const handleSuggestion = async (suggestion: string) => {
    if (loading) return
    const action = await sendMessage(suggestion)
    handleAction(action)
  }

  const handleDeleteConversation = async (e: React.MouseEvent, convId: string) => {
    e.stopPropagation()
    setDeletingId(convId)
    try {
      await deleteConversation(convId)
      setHistory(prev => prev.filter(c => c.id !== convId))
      toast.success('Conversation deleted')
    } catch {
      toast.error('Failed to delete')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div
      className={`fixed top-0 right-0 h-full z-50 flex flex-col bg-background border-l border-border shadow-2xl transition-transform duration-300 ease-in-out ${
        open ? 'translate-x-0' : 'translate-x-full'
      }`}
      style={{ width: 400, maxWidth: '100vw' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#5A5AF6] to-[#8B5CF6] flex items-center justify-center">
            <i className="fa-solid fa-robot text-foreground text-sm" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-foreground leading-tight">AxBi Assistant</h3>
            <p className="text-[10px] text-muted-foreground">Powered by Gemini</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => {
              const next = voiceLang === 'en' ? 'ar-EG' : 'en'
              setVoiceLang(next)
              localStorage.setItem('bi-voice-language', next)
            }}
            className="h-7 px-1.5 rounded-md hover:bg-card flex items-center justify-center text-muted-foreground hover:text-muted-foreground transition-colors text-[10px] font-bold"
            title={`Language: ${voiceLang === 'ar-EG' ? 'Egyptian Arabic' : 'English'}`}
            aria-label="Switch voice language"
          >
            {voiceLang === 'ar-EG' ? 'ع' : 'EN'}
          </button>
          <button
            onClick={() => setShowConvoMode(true)}
            className="w-7 h-7 rounded-md hover:bg-card flex items-center justify-center text-muted-foreground hover:text-muted-foreground transition-colors"
            title="Conversation mode"
            aria-label="Open voice conversation mode"
          >
            <i className="fa-solid fa-headset text-xs" />
          </button>
          <button
            onClick={openCanvas}
            className="relative w-7 h-7 rounded-md hover:bg-card flex items-center justify-center text-muted-foreground hover:text-muted-foreground transition-colors"
            title="Canvas — AI-generated charts & visuals"
            aria-label="Open Canvas"
          >
            <i className="fa-solid fa-images text-xs" />
            {canvasAssets.length > 0 && (
              <span className="absolute -top-1 -right-1 min-w-[14px] h-[14px] px-0.5 rounded-full bg-primary text-primary-foreground text-[8px] font-bold flex items-center justify-center">
                {canvasAssets.length}
              </span>
            )}
          </button>
          {ttsSupported && (
            <button
              onClick={() => {
                const next = !voiceMode
                setVoiceMode(next)
                localStorage.setItem('bi-chat-voice-mode', String(next))
                if (!next && isSpeaking) stopSpeech()
              }}
              className={`w-7 h-7 rounded-md hover:bg-card flex items-center justify-center transition-colors ${voiceMode ? 'text-primary' : 'text-muted-foreground hover:text-muted-foreground'}`}
              title={voiceMode ? 'Voice mode ON (auto-reads replies)' : 'Voice mode OFF'}
              aria-label={voiceMode ? 'Disable voice mode' : 'Enable voice mode'}
            >
              <i className={`fa-solid ${voiceMode ? 'fa-volume-high' : 'fa-volume-xmark'} text-xs`} />
            </button>
          )}
          <button
            onClick={() => setShowHistory(!showHistory)}
            className={`w-7 h-7 rounded-md hover:bg-card flex items-center justify-center transition-colors ${showHistory ? 'text-primary' : 'text-muted-foreground hover:text-muted-foreground'}`}
            title="Chat history"
          >
            <i className="fa-solid fa-clock-rotate-left text-xs" />
          </button>
          <button
            onClick={() => { clearChat(); setShowHistory(false) }}
            className="w-7 h-7 rounded-md hover:bg-card flex items-center justify-center text-muted-foreground hover:text-muted-foreground transition-colors"
            title="New chat"
          >
            <i className="fa-solid fa-plus text-xs" />
          </button>
          <button
            onClick={onClose}
            className="w-7 h-7 rounded-md hover:bg-card flex items-center justify-center text-muted-foreground hover:text-muted-foreground transition-colors"
            title="Close"
          >
            <i className="fa-solid fa-xmark text-sm" />
          </button>
        </div>
      </div>

      {/* History Panel */}
      {showHistory ? (
        <div className="flex-1 overflow-y-auto px-3 py-3">
          <div className="flex items-center justify-between mb-3 px-1">
            <p className="text-xs text-muted-foreground font-medium">Recent Conversations</p>
            <button
              onClick={() => { setHistoryLoading(true); listConversations().then(d => setHistory(d.conversations || [])).catch(() => {}).finally(() => setHistoryLoading(false)) }}
              className="text-[10px] text-muted-foreground hover:text-muted-foreground transition-colors"
              title="Refresh"
            >
              <i className="fa-solid fa-arrows-rotate" />
            </button>
          </div>
          {historyLoading ? (
            <div className="flex items-center justify-center py-8">
              <LogoSpinner size={28} />
            </div>
          ) : history.length === 0 ? (
            <div className="text-center py-8">
              <i className="fa-solid fa-inbox text-2xl text-muted-foreground mb-2" />
              <p className="text-xs text-muted-foreground">No conversations yet</p>
            </div>
          ) : (
            <div className="space-y-1">
              {history.map(conv => (
                <div key={conv.id} className="flex items-center gap-1 group/hist">
                  <button
                    onClick={() => { onClose(); navigate(`/conversation/${conv.id}`) }}
                    className="flex-1 min-w-0 text-left px-3 py-2.5 rounded-lg hover:bg-card border border-transparent hover:border-border transition-colors"
                  >
                    <p className="text-xs text-foreground font-medium truncate group-hover/hist:text-foreground">
                      {conv.title || 'Untitled'}
                    </p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">
                      {new Date(conv.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                    </p>
                  </button>
                  <button
                    onClick={(e) => handleDeleteConversation(e, conv.id)}
                    disabled={deletingId === conv.id}
                    className="opacity-0 group-hover/hist:opacity-100 w-6 h-6 rounded-md hover:bg-destructive/10 flex items-center justify-center text-muted-foreground hover:text-destructive transition-all shrink-0"
                    title="Delete conversation"
                  >
                    {deletingId === conv.id
                      ? <LogoSpinner size={12} />
                      : <i className="fa-solid fa-trash text-[10px]" />
                    }
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
      <>
      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
        {messages.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center h-full text-center px-4">
            <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-[#5A5AF6]/20 to-[#8B5CF6]/10 flex items-center justify-center mb-4">
              <i className="fa-solid fa-wand-magic-sparkles text-primary text-xl" />
            </div>
            <p className="text-sm text-muted-foreground font-medium mb-1">How can I help?</p>
            <p className="text-xs text-muted-foreground leading-relaxed">
              Ask me to navigate, query data, generate charts &amp; 3D visuals, run forecasts, detect anomalies, or anything about your data.
            </p>
            <div className="mt-4 space-y-1.5 w-full">
              {[
                'Show me my projects',
                'Generate a bar chart of my data',
                'Create a 3D visualization',
                'Check data quality',
                'How do I use this platform?',
              ].map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => handleSuggestion(suggestion)}
                  disabled={loading}
                  className="w-full text-left text-xs px-3 py-2 rounded-lg bg-card border border-border text-muted-foreground hover:text-foreground hover:border-primary/40 transition-colors disabled:opacity-40"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[90%] ${msg.role === 'user' ? '' : 'w-full'} group/msg`}>
              <div
                className={`rounded-xl px-3 py-2 text-sm leading-relaxed ${
                  msg.role === 'user'
                    ? 'bg-primary text-primary-foreground rounded-br-sm'
                    : 'bg-card text-foreground border border-border rounded-bl-sm'
                }`}
              >
                {msg.role === 'assistant'
                  ? <MarkdownText text={msg.content} />
                  : <div className="whitespace-pre-wrap">{msg.content}</div>
                }
              </div>
              {msg.role === 'assistant' && msg.content && (
                <div className="flex items-center gap-2 mt-1 opacity-0 group-hover/msg:opacity-100 transition-all">
                  <button
                    onClick={() => { navigator.clipboard.writeText(msg.content); toast.success('Copied') }}
                    className="text-[10px] text-muted-foreground hover:text-muted-foreground flex items-center gap-1"
                  >
                    <i className="fa-regular fa-copy" /> Copy
                  </button>
                  {ttsSupported && (
                    <button
                      onClick={() => {
                        if (isSpeaking) { stopSpeech(); return }
                        speak(msg.content, { language: voiceLang })
                      }}
                      className="text-[10px] text-muted-foreground hover:text-muted-foreground flex items-center gap-1"
                      aria-label="Read aloud"
                    >
                      <i className={`fa-solid ${isSpeaking ? 'fa-stop' : 'fa-volume-high'}`} /> {isSpeaking ? 'Stop' : 'Listen'}
                    </button>
                  )}
                </div>
              )}
              {/* Inline chart */}
              {msg.chart && (
                <div className="relative group/chart">
                  <ChatChartRenderer chart={msg.chart} height={180} />
                  <button
                    onClick={() => setExpanded({ type: 'chart', chart: msg.chart })}
                    className="absolute top-3 right-3 opacity-0 group-hover/chart:opacity-100 text-[10px] text-primary bg-background/90 border border-border px-2 py-1 rounded-md hover:text-foreground transition-all"
                  >
                    <i className="fa-solid fa-expand mr-1" />Expand
                  </button>
                </div>
              )}
              {/* Inline 3D */}
              {msg.visual3d && (
                <Suspense fallback={<div className="mt-2 h-[200px] bg-background rounded-lg border border-border flex items-center justify-center text-muted-foreground text-xs">Loading 3D...</div>}>
                  <Visual3D
                    visual={msg.visual3d}
                    height={250}
                    onExpand={() => setExpanded({ type: '3d', visual3d: msg.visual3d })}
                  />
                </Suspense>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-card border border-border rounded-xl rounded-bl-sm px-3 py-2 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
          </div>
        )}

        {error && (
          <div className="text-xs text-destructive bg-destructive/10 border border-red-800/30 rounded-lg px-3 py-2">
            {error}
          </div>
        )}
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="shrink-0 px-3 pb-3 pt-2 border-t border-border">
        {loading ? (
          <button
            type="button"
            onClick={stopGenerating}
            className="w-full flex items-center justify-center gap-2 py-2 rounded-xl bg-card border border-red-500/30 text-destructive text-xs font-medium hover:bg-red-500/10 hover:border-red-500/50 transition-colors"
          >
            <i className="fa-solid fa-stop text-[10px]" />
            Stop generating
          </button>
        ) : (
          <div className="flex items-center gap-2 bg-card border border-border rounded-xl px-3 py-1.5 focus-within:border-primary/50 transition-colors">
            <VoiceInput
              onTranscript={(text) => {
                setInput(prev => (prev ? prev + ' ' + text : text))
              }}
              onInterimTranscript={(text) => {
                setInput(text)
              }}
              disabled={loading}
              lang={voiceLang}
            />
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask anything..."
              disabled={loading}
              className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={loading || !input.trim()}
              className="w-7 h-7 rounded-lg bg-primary hover:bg-primary disabled:opacity-30 disabled:hover:bg-primary flex items-center justify-center transition-colors shrink-0"
            >
              <i className="fa-solid fa-paper-plane text-foreground text-xs" />
            </button>
          </div>
        )}
      </form>
      </>
      )}

      {/* Expanded visual modal */}
      {expanded && <ExpandedModal visual={expanded} onClose={() => setExpanded(null)} />}

      {/* Conversation Mode */}
      {showConvoMode && (
        <ConversationMode
          language={voiceLang}
          onLanguageChange={(lang) => setVoiceLang(lang)}
          onClose={() => setShowConvoMode(false)}
          onAction={handleConvoAction}
        />
      )}
    </div>
  )
}
