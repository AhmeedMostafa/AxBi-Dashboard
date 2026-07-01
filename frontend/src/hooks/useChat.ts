import { useState, useCallback, useRef } from 'react'
import { sendChatMessageStream } from '../api'

export interface ChatChart {
  type: 'bar' | 'horizontal_bar' | 'line' | 'area' | 'pie' | 'treemap' | 'funnel'
  title: string
  data: Record<string, any>[]
  xKey: string
  yKey: string
}

export interface Chat3DVisual {
  type: 'scatter3d' | 'bar3d' | 'globe'
  title: string
  data: Record<string, any>[]
  config: Record<string, any>
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  chart?: ChatChart
  visual3d?: Chat3DVisual
}

export interface ChatAction {
  type: 'navigate' | 'toast' | 'refresh'
  payload: Record<string, any>
}

interface UseChatReturn {
  messages: ChatMessage[]
  loading: boolean
  error: string | null
  sendMessage: (text: string) => Promise<ChatAction | null>
  stopGenerating: () => void
  clearChat: () => void
  addAssistantMessage: (content: string, chart?: ChatChart, visual3d?: Chat3DVisual) => void
}

const STORAGE_KEY = 'bi-chat-messages'
const CONV_ID_KEY = 'bi-chat-conversation-id'

function loadMessages(): ChatMessage[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveMessages(msgs: ChatMessage[]) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(msgs))
  } catch { /* quota exceeded */ }
}

export function useChat(): UseChatReturn {
  const [messages, setMessages] = useState<ChatMessage[]>(loadMessages)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const idCounter = useRef(Date.now())
  const conversationId = useRef<string | null>(sessionStorage.getItem(CONV_ID_KEY))
  const abortRef = useRef<AbortController | null>(null)

  const stopGenerating = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setLoading(false)
    setError(null)
  }, [])

  const sendMessage = useCallback(async (text: string): Promise<ChatAction | null> => {
    const trimmed = text.trim()
    if (!trimmed) return null

    const userMsg: ChatMessage = {
      id: String(++idCounter.current),
      role: 'user',
      content: trimmed,
      timestamp: Date.now(),
    }

    const assistantMsgId = String(++idCounter.current)

    const withUser = [...messages, userMsg]
    setMessages([...withUser, { id: assistantMsgId, role: 'assistant', content: '', timestamp: Date.now() }])
    setLoading(true)
    setError(null)

    const controller = new AbortController()
    abortRef.current = controller

    const apiMessages = withUser.map(m => ({ role: m.role === 'assistant' ? 'assistant' : 'user', content: m.content }))
    let actionResult: ChatAction | null = null

    try {
      const datasetId = localStorage.getItem('bi_dashboard_last_dataset_id') || null

      await sendChatMessageStream(
        apiMessages,
        datasetId,
        conversationId.current,
        controller.signal,
        (event: any) => {
          if (controller.signal.aborted) return

          if (event.type === 'chunk') {
            setMessages(prev => prev.map(m =>
              m.id === assistantMsgId ? { ...m, content: m.content + event.text } : m
            ))
          } else if (event.type === 'chart') {
            setMessages(prev => prev.map(m =>
              m.id === assistantMsgId ? { ...m, chart: event.data } : m
            ))
          } else if (event.type === 'visual3d') {
            setMessages(prev => prev.map(m =>
              m.id === assistantMsgId ? { ...m, visual3d: event.data } : m
            ))
          } else if (event.type === 'done') {
            if (event.conversation_id) {
              conversationId.current = event.conversation_id
              sessionStorage.setItem(CONV_ID_KEY, event.conversation_id)
            }
            actionResult = event.action || null
          } else if (event.type === 'error') {
            setError(event.message || 'An error occurred')
          }
        },
      )

      if (!controller.signal.aborted) {
        setMessages(prev => {
          const final = prev.map(m =>
            m.id === assistantMsgId && !m.content && !m.chart && !m.visual3d
              ? { ...m, content: 'No response received.' }
              : m
          )
          saveMessages(final)
          return final
        })
      }

      return actionResult
    } catch (e: any) {
      if (e?.name === 'AbortError' || controller.signal.aborted) return null
      const msg = e?.message || 'Failed to send message'
      setError(msg)
      setMessages(prev => {
        const cleaned = prev.filter(m => m.id !== assistantMsgId || m.content.length > 0)
        saveMessages(cleaned)
        return cleaned
      })
      return null
    } finally {
      abortRef.current = null
      setLoading(false)
    }
  }, [messages])

  const clearChat = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setMessages([])
    sessionStorage.removeItem(STORAGE_KEY)
    sessionStorage.removeItem(CONV_ID_KEY)
    conversationId.current = null
    setLoading(false)
    setError(null)
  }, [])

  // Append an assistant message from outside the normal stream — used by voice
  // conversation mode to drop charts / 3D visuals it generated into the thread.
  const addAssistantMessage = useCallback((content: string, chart?: ChatChart, visual3d?: Chat3DVisual) => {
    setMessages(prev => {
      const next: ChatMessage[] = [...prev, {
        id: `live-${idCounter.current++}`,
        role: 'assistant',
        content,
        timestamp: Date.now(),
        chart,
        visual3d,
      }]
      saveMessages(next)
      return next
    })
  }, [])

  return { messages, loading, error, sendMessage, stopGenerating, clearChat, addAssistantMessage }
}
