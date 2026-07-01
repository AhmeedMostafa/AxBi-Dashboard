import { useEffect, useState, useRef, type FormEvent, lazy, Suspense } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import toast from 'react-hot-toast'
import { getConversation, shareConversation, sendChatMessageStream } from '../../api'
import ChatChartRenderer from './ChatChart'
import type { ChatChart, Chat3DVisual } from '../../hooks/useChat'
import VoiceInput from '../Chat/VoiceInput'
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

const Visual3D = lazy(() => import('./Visual3D'))

// Tracks which conversations have already auto-sent their initial message, so a
// React StrictMode double-mount (dev) doesn't fire the first prompt twice.
const sentInitialFor = new Set<string>()

interface Message {
  id: string
  role: string
  content: string
  chart_data?: ChatChart | null
  visual_3d?: Chat3DVisual | null
  created_at: string
}

interface Conversation {
  id: string
  title: string
  created_at: string
  share_token?: string | null
}

export default function ConversationPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [input, setInput] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!id) return
    loadConversation()
  }, [id])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  // Auto-send a prompt passed from the AI Agent landing (router state).
  useEffect(() => {
    if (loading || !id) return
    const initial = (location.state as { initialMessage?: string } | null)?.initialMessage
    if (!initial || sentInitialFor.has(id) || messages.length > 0) return
    sentInitialFor.add(id)
    // Clear router state so a refresh doesn't resend the same prompt.
    window.history.replaceState({}, '')
    sendText(initial)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, id])

  const loadConversation = async () => {
    try {
      const data = await getConversation(id!)
      setConversation(data.conversation)
      const msgs = (data.messages || []).map((m: any) => ({
        ...m,
        chart_data: typeof m.chart_data === 'string' ? JSON.parse(m.chart_data) : m.chart_data,
        visual_3d: typeof m.visual_3d === 'string' ? JSON.parse(m.visual_3d) : m.visual_3d,
      }))
      setMessages(msgs)
    } catch {
      toast.error('Failed to load conversation')
    } finally {
      setLoading(false)
    }
  }

  const handleShare = async () => {
    if (!id) return
    try {
      const data = await shareConversation(id)
      const url = `${window.location.origin}${data.share_url}`
      await navigator.clipboard.writeText(url)
      toast.success('Share link copied to clipboard!')
    } catch {
      toast.error('Failed to generate share link')
    }
  }

  const stopGenerating = () => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setSending(false)
  }

  const handleSend = async (e: FormEvent) => {
    e.preventDefault()
    if (!input.trim() || sending || !id) return
    const text = input.trim()
    setInput('')
    await sendText(text)
  }

  const sendText = async (rawText: string) => {
    const text = rawText.trim()
    if (!text || sending || !id) return
    setSending(true)

    const controller = new AbortController()
    abortRef.current = controller

    const userMsg: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    }
    const assistantMsgId = (Date.now() + 1).toString()
    const assistantMsg: Message = {
      id: assistantMsgId,
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
    }

    setMessages(prev => [...prev, userMsg, assistantMsg])

    try {
      const apiMsgs = [...messages, userMsg].map(m => ({ role: m.role, content: m.content }))

      await sendChatMessageStream(
        apiMsgs,
        null,
        id,
        controller.signal,
        (event: any) => {
          if (controller.signal.aborted) return
          if (event.type === 'chunk') {
            setMessages(prev => prev.map(m =>
              m.id === assistantMsgId ? { ...m, content: m.content + event.text } : m
            ))
          } else if (event.type === 'chart') {
            setMessages(prev => prev.map(m =>
              m.id === assistantMsgId ? { ...m, chart_data: event.data } : m
            ))
          } else if (event.type === 'visual3d') {
            setMessages(prev => prev.map(m =>
              m.id === assistantMsgId ? { ...m, visual_3d: event.data } : m
            ))
          } else if (event.type === 'done' && event.action?.type === 'toast') {
            toast.success(event.action.payload.message)
          } else if (event.type === 'error') {
            toast.error(event.message || 'AI error')
          }
        },
      )
    } catch (e: any) {
      if (e?.name === 'AbortError' || controller.signal.aborted) return
      toast.error(e?.message || 'Failed to send message')
      setMessages(prev => prev.filter(m => m.id !== assistantMsgId || m.content.length > 0))
    } finally {
      abortRef.current = null
      setSending(false)
    }
  }

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-[60vh]">
        <LogoSpinner size={44} />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)]">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate(-1)} className="text-muted-foreground hover:text-foreground transition-colors">
            <i className="fa-solid fa-arrow-left" />
          </button>
          <div>
            <h2 className="text-base font-semibold">{conversation?.title || 'Conversation'}</h2>
            <p className="text-xs text-muted-foreground">{messages.length} messages</p>
          </div>
        </div>
        <button
          onClick={handleShare}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-primary/10 border border-primary/30 text-primary text-xs font-medium hover:bg-primary/15 transition-colors"
        >
          <i className="fa-solid fa-share-nodes" />
          Share
        </button>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[70%] ${msg.role !== 'user' ? 'w-full max-w-2xl' : ''}`}>
              <div className={`rounded-xl px-4 py-3 text-sm leading-relaxed ${
                msg.role === 'user'
                  ? 'bg-primary text-primary-foreground rounded-br-sm'
                  : 'bg-card text-foreground border border-border rounded-bl-sm'
              }`}>
                {msg.role === 'assistant'
                  ? <MarkdownText text={msg.content} />
                  : <div className="whitespace-pre-wrap">{msg.content}</div>
                }
              </div>
              {msg.chart_data && <ChatChartRenderer chart={msg.chart_data} height={280} />}
              {msg.visual_3d && (
                <Suspense fallback={<div className="mt-2 h-[300px] bg-background rounded-lg border border-border flex items-center justify-center text-muted-foreground text-xs">Loading 3D...</div>}>
                  <Visual3D visual={msg.visual_3d} height={400} />
                </Suspense>
              )}
            </div>
          </div>
        ))}
        {sending && (
          <div className="flex justify-start">
            <div className="bg-card border border-border rounded-xl px-4 py-3 flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-primary animate-bounce" />
              <span className="w-2 h-2 rounded-full bg-primary animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-2 h-2 rounded-full bg-primary animate-bounce" style={{ animationDelay: '300ms' }} />
              <button
                onClick={stopGenerating}
                className="ml-2 w-6 h-6 rounded-md bg-red-500/20 border border-red-500/40 flex items-center justify-center hover:bg-red-500/40 transition-colors"
                title="Stop generating"
              >
                <i className="fa-solid fa-stop text-destructive text-[9px]" />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <form onSubmit={handleSend} className="shrink-0 px-6 pb-4 pt-3 border-t border-border">
        {sending ? (
          <button
            type="button"
            onClick={stopGenerating}
            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-card border border-red-500/30 text-destructive text-sm font-medium hover:bg-red-500/10 hover:border-red-500/50 transition-colors"
          >
            <i className="fa-solid fa-stop text-xs" />
            Stop generating
          </button>
        ) : (
          <div className="flex items-center gap-3 bg-card border border-border rounded-xl px-4 py-2.5 focus-within:border-primary/50 transition-colors">
            <VoiceInput
              onTranscript={(text) => {
                setInput(prev => (prev ? prev + ' ' + text : text))
              }}
              onInterimTranscript={(text) => {
                setInput(text)
              }}
              disabled={sending}
              lang={localStorage.getItem('bi-voice-language') || 'en'}
            />
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Continue the conversation..."
              disabled={sending}
              className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={sending || !input.trim()}
              className="w-8 h-8 rounded-lg bg-primary hover:bg-primary disabled:opacity-30 flex items-center justify-center transition-colors shrink-0"
            >
              <i className="fa-solid fa-paper-plane text-foreground text-xs" />
            </button>
          </div>
        )}
      </form>
    </div>
  )
}
