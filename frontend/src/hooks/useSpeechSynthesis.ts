import { useState, useCallback, useRef, useEffect } from 'react'
import { getTTS } from '../api'
import { confirmLanguageBeforeTTS } from '../utils/languageDetect'

function stripMarkdown(text: string): string {
  return text
    .replace(/#{1,6}\s/g, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/`(.+?)`/g, '$1')
    .replace(/\[(.+?)\]\(.+?\)/g, '$1')
    .replace(/^[-*]\s/gm, '')
    .replace(/^\d+\.\s/gm, '')
    .trim()
}

interface SpeakOptions {
  voice?: string
  language?: string
  /**
   * If true (default), the hook detects the text language and shows a confirm
   * prompt when it doesn't match the selected voice language. Pass false to
   * skip the check (e.g. for streaming sentences inside ConversationMode).
   */
  confirmLanguageMismatch?: boolean
}

interface UseSpeechSynthesisReturn {
  speak: (text: string, options?: SpeakOptions) => void
  stop: () => void
  isSpeaking: boolean
  isSupported: boolean
  isLoading: boolean
}

export function useSpeechSynthesis(): UseSpeechSynthesisReturn {
  const [isSpeaking, setIsSpeaking] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const urlRef = useRef<string | null>(null)
  const abortRef = useRef(false)

  const cleanup = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current)
      urlRef.current = null
    }
  }, [])

  const stop = useCallback(() => {
    abortRef.current = true
    cleanup()
    setIsSpeaking(false)
    setIsLoading(false)
  }, [cleanup])

  const speak = useCallback(async (text: string, options?: SpeakOptions) => {
    if (!text.trim()) return

    stop()
    abortRef.current = false

    const cleaned = stripMarkdown(text)
    const voice = options?.voice || ''
    const requestedLang = options?.language || localStorage.getItem('bi-voice-language') || 'en'
    const wantConfirm = options?.confirmLanguageMismatch !== false

    // Detect text language vs selected voice and prompt user if they differ.
    let language: string | null = requestedLang
    if (wantConfirm) {
      language = await confirmLanguageBeforeTTS(cleaned, requestedLang)
      if (!language || abortRef.current) {
        setIsLoading(false)
        return
      }
    }

    setIsLoading(true)

    getTTS(cleaned, voice, language)
      .then((blob: Blob) => {
        if (abortRef.current) return

        const url = URL.createObjectURL(blob)
        urlRef.current = url

        const audio = new Audio(url)
        audioRef.current = audio

        audio.onplay = () => {
          setIsLoading(false)
          setIsSpeaking(true)
        }
        audio.onended = () => {
          setIsSpeaking(false)
          cleanup()
        }
        audio.onerror = () => {
          setIsSpeaking(false)
          setIsLoading(false)
          cleanup()
        }

        audio.play().catch(() => {
          setIsSpeaking(false)
          setIsLoading(false)
          cleanup()
        })
      })
      .catch(() => {
        if (!abortRef.current) {
          setIsLoading(false)
        }
      })
  }, [stop, cleanup])

  useEffect(() => {
    return () => {
      cleanup()
    }
  }, [cleanup])

  return { speak, stop, isSpeaking, isSupported: true, isLoading }
}
