import { useState, useEffect, useRef, useCallback } from 'react'
import AudioWaveform from './AudioWaveform'

interface Props {
  onTranscript: (text: string) => void
  onInterimTranscript?: (text: string) => void
  disabled?: boolean
  className?: string
  lang?: string
  // 'circle' renders a large round button that morphs into a live sound-wave
  // while listening (used on the agent page).
  variant?: 'inline' | 'circle'
}

const SpeechRecognition =
  (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition

export default function VoiceInput({ onTranscript, onInterimTranscript, disabled, className, lang = 'en-US', variant = 'inline' }: Props) {
  const [listening, setListening] = useState(false)
  const [supported] = useState(() => !!SpeechRecognition)
  const recognitionRef = useRef<any>(null)
  const silenceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const stop = useCallback(() => {
    if (recognitionRef.current) {
      recognitionRef.current.stop()
      recognitionRef.current = null
    }
    if (silenceTimer.current) {
      clearTimeout(silenceTimer.current)
      silenceTimer.current = null
    }
    setListening(false)
  }, [])

  const start = useCallback(() => {
    if (!supported || disabled) return

    const recognition = new SpeechRecognition()
    recognition.continuous = true
    recognition.interimResults = true
    recognition.lang = lang === 'ar-EG' ? 'ar-EG' : 'en-US'

    recognition.onresult = (event: any) => {
      let interim = ''
      let final = ''

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript
        if (event.results[i].isFinal) {
          final += transcript
        } else {
          interim += transcript
        }
      }

      if (interim && onInterimTranscript) {
        onInterimTranscript(interim)
      }

      if (final) {
        onTranscript(final.trim())
        stop()
      }

      if (silenceTimer.current) clearTimeout(silenceTimer.current)
      silenceTimer.current = setTimeout(() => {
        stop()
      }, 3000)
    }

    recognition.onerror = (event: any) => {
      if (event.error !== 'aborted') {
        console.warn('Speech recognition error:', event.error)
      }
      stop()
    }

    recognition.onend = () => {
      setListening(false)
    }

    recognitionRef.current = recognition
    recognition.start()
    setListening(true)
  }, [supported, disabled, onTranscript, onInterimTranscript, stop, lang])

  const toggle = useCallback(() => {
    if (listening) {
      stop()
    } else {
      start()
    }
  }, [listening, start, stop])

  useEffect(() => {
    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.stop()
      }
      if (silenceTimer.current) {
        clearTimeout(silenceTimer.current)
      }
    }
  }, [])

  if (!supported) {
    return (
      <button
        type="button"
        disabled
        className={`text-muted-foreground cursor-not-allowed ${
          variant === 'circle' ? 'w-12 h-12 rounded-full bg-muted flex items-center justify-center' : ''
        } ${className || ''}`}
        title="Voice input not supported in this browser"
        aria-label="Voice input not supported"
      >
        <i className={`fa-solid fa-microphone-slash ${variant === 'circle' ? 'text-lg' : 'text-sm'}`} />
      </button>
    )
  }

  // Circle variant: the button itself becomes a sound-wave while listening.
  if (variant === 'circle') {
    return (
      <button
        type="button"
        onClick={toggle}
        disabled={disabled}
        title={listening ? 'Stop listening' : 'Talk to your data'}
        aria-label={listening ? 'Stop voice input' : 'Start voice input'}
        className={`shrink-0 w-12 h-12 rounded-full flex items-center justify-center shadow-md transition-all overflow-hidden ${
          listening
            ? 'bg-red-500 text-white shadow-lg shadow-red-500/30 ring-2 ring-red-400/50'
            : 'bg-gradient-to-br from-[#5A5AF6] to-[#8B5CF6] text-primary-foreground shadow-primary/30 hover:scale-105 active:scale-95'
        } ${disabled ? 'opacity-40 cursor-not-allowed' : ''} ${className || ''}`}
      >
        {listening
          ? <AudioWaveform active size="small" className="text-white" />
          : <i className="fa-solid fa-microphone text-lg" />}
      </button>
    )
  }

  return (
    <div className={`flex items-center ${className || ''}`}>
      {listening && <AudioWaveform active={listening} size="small" className="text-primary" />}
      <button
        type="button"
        onClick={toggle}
        disabled={disabled}
        className={`relative flex items-center justify-center w-8 h-8 rounded-full transition-all ${
          listening
            ? 'bg-red-500/20 text-destructive hover:bg-red-500/30'
            : 'text-muted-foreground hover:text-foreground hover:bg-primary/15'
        } ${disabled ? 'opacity-40 cursor-not-allowed' : ''}`}
        title={listening ? 'Stop listening' : 'Voice input'}
        aria-label={listening ? 'Stop voice input' : 'Start voice input'}
      >
        <i className={`fa-solid ${listening ? 'fa-stop' : 'fa-microphone'} text-sm`} />
      </button>
    </div>
  )
}
