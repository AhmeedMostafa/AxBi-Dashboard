import { useState, useEffect, useRef, useCallback, lazy, Suspense } from 'react'
import { createPortal } from 'react-dom'
import { supabase } from '../../supabase-client'
import { LiveClient, type LiveStatus, type LiveAction } from '../../lib/liveClient'
import { mergeLiveTranscript, mergeAssistantTranscript } from '../../lib/liveTranscript'
import AudioWaveform from './AudioWaveform'
import ChatChartRenderer from '../Conversation/ChatChart'
import { useGeneratedAssets, type GeneratedAsset } from '../../context/GeneratedAssetsContext'
import { LogoSpinner } from '../ui/LogoSpinner'

const Visual3D = lazy(() => import('../Conversation/Visual3D'))

// Hard cap on a single convo session (client side). The proxy enforces its own
// server-side cap too (LIVE_MAX_SESSION_SECONDS) to protect audio quota.
const MAX_SESSION_SECONDS = 300
const LAST_DATASET_ID_KEY = 'bi_dashboard_last_dataset_id'

interface Props {
  onClose: () => void
  language: string
  onLanguageChange?: (lang: string) => void
  // Navigation/refresh actions from the voice agent.
  onAction?: (action: LiveAction) => void
  // Render inline within the current page (no full-screen portal overlay).
  embedded?: boolean
}

function fmtTime(totalSec: number): string {
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

export default function ConversationMode({ onClose, language, onLanguageChange, onAction, embedded = false }: Props) {
  const { addAsset, pinAsset, unpinAsset, isPinned, openCanvas } = useGeneratedAssets()
  const [activeLang, setActiveLang] = useState(language === 'ar-EG' ? 'ar-EG' : 'en-US')
  const [status, setStatus] = useState<LiveStatus>('connecting')
  const [userText, setUserText] = useState('')
  const [assistantText, setAssistantText] = useState('')
  const [errorMsg, setErrorMsg] = useState('')
  const [ended, setEnded] = useState<{ shown: boolean; reason: string }>({ shown: false, reason: '' })
  const [elapsed, setElapsed] = useState(0)
  // Visuals the agent produced during THIS call (also stored in the Canvas).
  const [liveVisuals, setLiveVisuals] = useState<GeneratedAsset[]>([])
  const [activeIdx, setActiveIdx] = useState(0)

  const clientRef = useRef<LiveClient | null>(null)
  const lastSpeakerRef = useRef<'user' | 'assistant' | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const activeLangRef = useRef(activeLang)
  useEffect(() => { activeLangRef.current = activeLang }, [activeLang])
  const liveCountRef = useRef(0)

  // Keep the latest action handler in a ref so the (memoized) Live callbacks
  // always dispatch to the current handler without restarting the session.
  const onActionRef = useRef(onAction)
  const addAssetRef = useRef(addAsset)
  useEffect(() => {
    onActionRef.current = onAction
    addAssetRef.current = addAsset
  })

  const finish = useCallback((reason: string) => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
    try { clientRef.current?.close() } catch { /* noop */ }
    clientRef.current = null
    if (reason) setEnded({ shown: true, reason })
  }, [])

  const buildCallbacks = useCallback(() => ({
    onStatus: (s: LiveStatus) => {
      setStatus(s)
      if (s === 'closed') {
        setEnded((prev) => prev.shown ? prev : { shown: true, reason: 'Conversation ended.' })
      }
    },
    onUserTranscript: (t: string) => {
      if (!t) return
      setUserText((prev) => (
        lastSpeakerRef.current === 'user' ? mergeLiveTranscript(prev, t) : t
      ))
      if (lastSpeakerRef.current !== 'user') setAssistantText('')
      lastSpeakerRef.current = 'user'
    },
    onAssistantTranscript: (t: string) => {
      if (!t) return
      setAssistantText((prev) => (
        lastSpeakerRef.current === 'assistant' ? mergeAssistantTranscript(prev, t) : t
      ))
      lastSpeakerRef.current = 'assistant'
    },
    onError: (m: string) => setErrorMsg(m),
    onClose: (reason: string) => finish(reason || 'Connection closed.'),
    onAction: (action: LiveAction) => {
      onActionRef.current?.(action)
    },
    onChart: (chart: any) => {
      const datasetId = localStorage.getItem(LAST_DATASET_ID_KEY)
      const id = addAssetRef.current({ kind: 'chart', title: chart?.title || 'Chart', chart, source: 'voice', datasetId })
      const asset: GeneratedAsset = { id, kind: 'chart', title: chart?.title || 'Chart', chart, source: 'voice', datasetId, createdAt: Date.now() }
      const idx = liveCountRef.current
      liveCountRef.current += 1
      setLiveVisuals(prev => [...prev, asset])
      setActiveIdx(idx)
    },
    onVisual3D: (visual: any) => {
      const datasetId = localStorage.getItem(LAST_DATASET_ID_KEY)
      const id = addAssetRef.current({ kind: '3d', title: visual?.title || '3D Visual', visual3d: visual, source: 'voice', datasetId })
      const asset: GeneratedAsset = { id, kind: '3d', title: visual?.title || '3D Visual', visual3d: visual, source: 'voice', datasetId, createdAt: Date.now() }
      const idx = liveCountRef.current
      liveCountRef.current += 1
      setLiveVisuals(prev => [...prev, asset])
      setActiveIdx(idx)
    },
  }), [finish])

  const startSession = useCallback(async (lang: string) => {
    setErrorMsg('')
    try {
      const { data } = await supabase.auth.getSession()
      const token = data?.session?.access_token
      if (!token) { setErrorMsg('Not authenticated. Please log in again.'); return }
      const datasetId = localStorage.getItem('bi_dashboard_last_dataset_id') || null

      const client = new LiveClient(
        { token, lang, datasetId },
        buildCallbacks(),
      )
      clientRef.current = client
      await client.start()
    } catch (e: any) {
      const msg = e?.name === 'NotAllowedError'
        ? 'Microphone permission denied. Allow mic access and reopen.'
        : (e?.message || 'Failed to start the voice session.')
      setErrorMsg(msg)
      setStatus('error')
    }
  }, [buildCallbacks])

  useEffect(() => {
    // StrictMode (dev) mounts twice. A naive async start would leave the first
    // (discarded) mount's Live session running alongside the second — causing
    // TWO overlapping voice replies. The `cancelled` flag ensures any session
    // started by a mount that gets torn down is closed, even if start() is
    // still mid-flight when cleanup runs.
    let cancelled = false

    ;(async () => {
      setErrorMsg('')
      try {
        const { data } = await supabase.auth.getSession()
        if (cancelled) return
        const token = data?.session?.access_token
        if (!token) { setErrorMsg('Not authenticated. Please log in again.'); return }
        const datasetId = localStorage.getItem('bi_dashboard_last_dataset_id') || null

        const client = new LiveClient(
          { token, lang: activeLangRef.current, datasetId },
          buildCallbacks(),
        )
        if (cancelled) return
        clientRef.current = client
        await client.start()
        if (cancelled) {
          client.close()
          if (clientRef.current === client) clientRef.current = null
        }
      } catch (e: any) {
        if (cancelled) return
        const msg = e?.name === 'NotAllowedError'
          ? 'Microphone permission denied. Allow mic access and reopen.'
          : (e?.message || 'Failed to start the voice session.')
        setErrorMsg(msg)
        setStatus('error')
      }
    })()

    timerRef.current = setInterval(() => {
      setElapsed((prev) => {
        const next = prev + 1
        if (next >= MAX_SESSION_SECONDS) {
          finish('Session limit reached (5 min). Reopen to continue.')
        }
        return next
      })
    }, 1000)

    return () => {
      cancelled = true
      if (timerRef.current) clearInterval(timerRef.current)
      try { clientRef.current?.close() } catch { /* noop */ }
      clientRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleLangToggle = async () => {
    const newLang = activeLang === 'ar-EG' ? 'en-US' : 'ar-EG'
    setActiveLang(newLang)
    activeLangRef.current = newLang
    localStorage.setItem('bi-voice-language', newLang === 'ar-EG' ? 'ar-EG' : 'en')
    onLanguageChange?.(newLang === 'ar-EG' ? 'ar-EG' : 'en')
    setUserText('')
    setAssistantText('')
    lastSpeakerRef.current = null
    setStatus('connecting')
    // Tear down + reopen with the new language/system instruction.
    try {
      if (clientRef.current) {
        await clientRef.current.switchLanguage(newLang)
      } else {
        await startSession(newLang)
      }
    } catch (e: any) {
      setErrorMsg(e?.message || 'Failed to switch language.')
    }
  }

  const handleClose = () => {
    finish('')
    onClose()
  }

  const isAr = activeLang === 'ar-EG'

  const orbClass = {
    connecting: 'scale-100 opacity-40',
    listening: 'scale-110 opacity-80',
    speaking: 'scale-125 opacity-100',
    closed: 'scale-90 opacity-30',
    error: 'scale-90 opacity-40',
  }[status]

  const statusLabel = {
    connecting: isAr ? 'بنتوصل...' : 'Connecting...',
    listening: isAr ? 'بسمعك...' : 'Listening...',
    speaking: isAr ? 'بتكلم...' : 'Speaking...',
    closed: isAr ? 'خلصت' : 'Ended',
    error: isAr ? 'في مشكلة' : 'Error',
  }[status]

  const hasVisuals = liveVisuals.length > 0
  const activeVisual = hasVisuals ? liveVisuals[Math.min(activeIdx, liveVisuals.length - 1)] : null
  const activePinned = activeVisual ? isPinned(activeVisual.id) : false

  // Embedded mode renders inline inside the current page (relative container with
  // a fixed height) so the conversation lives on the same page rather than a
  // full-screen takeover. The inner layout uses absolute positioning relative to
  // this container, so it works for both the relative and the fixed wrappers.
  const containerClass = embedded
    ? "relative w-full h-[440px] rounded-2xl border border-border bg-gradient-to-b from-background via-card to-background overflow-hidden shadow-2xl"
    : "fixed inset-0 z-[100] bg-gradient-to-b from-background via-card to-background overflow-hidden"

  const tree = (
    <div className={containerClass}>
      {/* Ambient background glow */}
      <div className="absolute inset-0 pointer-events-none">
        <div
          className={`absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] rounded-full transition-all duration-1000 ${
            status === 'speaking'
              ? 'bg-gradient-to-br from-[#5A5AF6]/20 via-[#7c3aed]/15 to-transparent scale-110'
              : status === 'listening'
              ? 'bg-gradient-to-br from-emerald-500/15 via-cyan-500/10 to-transparent scale-100'
              : 'bg-gradient-to-br from-[#5A5AF6]/8 to-transparent scale-90'
          }`}
          style={{ filter: 'blur(80px)' }}
        />
      </div>

      {/* Header */}
      <div className="absolute top-0 left-0 right-0 px-6 py-4 flex items-center justify-between z-10">
        <div className="flex items-center gap-3">
          <div className={`w-2 h-2 rounded-full ${
            status === 'listening' ? 'bg-emerald-400 animate-pulse' :
            status === 'speaking' ? 'bg-[#7c3aed] animate-pulse' :
            status === 'connecting' ? 'bg-amber-400 animate-pulse' :
            'bg-slate-500'
          }`} />
          <span className="text-xs text-muted-foreground uppercase tracking-wider font-medium">
            {statusLabel}
          </span>
          {/* Secure / live badge + session timer */}
          <div
            className="hidden sm:flex items-center gap-1.5 text-[10px] text-muted-foreground bg-card/70 backdrop-blur-sm px-2.5 py-1 rounded-md border border-border"
            title="Real-time Gemini Live session (audio streamed securely through the server). Auto-ends at 5 minutes."
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            <span>Live · {fmtTime(elapsed)}<span className="text-muted-foreground">/{fmtTime(MAX_SESSION_SECONDS)}</span></span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleLangToggle}
            className="flex items-center gap-1.5 text-xs text-foreground bg-card/80 backdrop-blur-sm px-3 py-1.5 rounded-lg border border-border hover:bg-accent hover:border-primary/60 transition-all cursor-pointer font-medium"
            title={isAr ? 'اضغط للتبديل للإنجليزية' : 'Click to switch to Arabic'}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
            {isAr ? 'عربي' : 'English'}
          </button>
          <button
            onClick={handleClose}
            className="px-4 py-1.5 rounded-lg bg-red-500/20 backdrop-blur-sm border border-red-500/40 text-red-200 text-xs font-medium hover:bg-red-500/30 transition-all"
          >
            {isAr ? 'إنهاء' : 'End'}
          </button>
        </div>
      </div>

      {/* Content row: voice on the left, generated visuals on the right when present */}
      <div className="absolute inset-0 pt-16 pb-4 flex">
        {/* Voice column */}
        <div className={`relative flex flex-col items-center justify-center px-6 transition-all duration-500 ${
          hasVisuals ? 'w-[42%] min-w-[340px] max-w-[560px] border-r border-border/60' : 'flex-1'
        }`}>
      {/* Central Orb */}
      <div className={`relative flex items-center justify-center z-10 ${hasVisuals ? 'mb-8' : 'mb-12'}`}>
        <div
          className={`w-40 h-40 rounded-full bg-gradient-to-br from-[#5A5AF6] via-[#7c3aed] to-[#a855f7] transition-all duration-500 ${orbClass}`}
          style={{ boxShadow: '0 0 80px rgba(124, 58, 237, 0.5), inset 0 0 40px rgba(255,255,255,0.1)' }}
        />
        {status === 'listening' && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none text-white/90">
            <AudioWaveform active={true} size="large" />
          </div>
        )}
        {status === 'connecting' && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <LogoSpinner size={36} />
          </div>
        )}
        {status === 'speaking' && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="w-24 h-24 rounded-full border-2 border-white/30 animate-ping" />
          </div>
        )}
      </div>

      {/* Live assistant transcript */}
      {assistantText && (
        <div className="mb-3 px-6 py-2 bg-primary/10 backdrop-blur-sm border border-primary/30 rounded-xl max-w-lg z-10">
          <p className="text-sm text-indigo-100 text-center" dir={isAr ? 'rtl' : 'ltr'}>
            {assistantText}
          </p>
        </div>
      )}

      {/* Live user transcript */}
      {userText && (
        <div className="mb-2 max-w-md z-10">
          <p className="text-xs text-muted-foreground text-center" dir={isAr ? 'rtl' : 'ltr'}>
            {isAr ? 'إنت قلت: ' : 'You: '}
            <span className="text-muted-foreground italic">{userText}</span>
          </p>
        </div>
      )}

      {/* Error banner */}
      {errorMsg && (
        <div className="mb-2 px-5 py-2 bg-red-500/10 border border-red-500/30 rounded-xl max-w-md z-10">
          <p className="text-xs text-red-200 text-center">{errorMsg}</p>
        </div>
      )}

          {/* Hint */}
          <p className="absolute bottom-3 text-xs text-muted-foreground z-10 text-center px-6">
            {isAr
              ? 'اتكلم عادي — ممكن تقاطع المساعد في أي وقت'
              : 'Speak naturally — you can interrupt the assistant any time'}
          </p>
        </div>{/* end voice column */}

        {/* Generated visuals panel (split view) */}
        {hasVisuals && activeVisual && (
          <div className="flex-1 min-w-0 flex flex-col p-4 sm:p-5 z-10">
            {/* Panel header */}
            <div className="flex items-center justify-between gap-2 mb-3">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-foreground truncate flex items-center gap-1.5">
                  <span>{activeVisual.kind === '3d' ? '🧊' : '📊'}</span>
                  {activeVisual.title}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  {isAr ? 'اتعمل دلوقتي بالصوت' : 'Generated just now by voice'}
                  {liveVisuals.length > 1 && ` · ${activeIdx + 1}/${liveVisuals.length}`}
                </div>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                <button
                  onClick={() => activePinned ? unpinAsset(activeVisual.id) : pinAsset(activeVisual)}
                  className={`text-[11px] px-2.5 py-1.5 rounded-lg border transition-colors flex items-center gap-1 ${
                    activePinned
                      ? 'text-warning bg-amber-500/10 border-amber-500/40'
                      : 'text-foreground bg-card/80 border-border hover:border-primary/60'
                  }`}
                  title={activePinned ? (isAr ? 'مثبّت ع الداشبورد' : 'Pinned to dashboard') : (isAr ? 'ثبّت ع الداشبورد' : 'Pin to dashboard')}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill={activePinned ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 17v5M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg>
                  {activePinned ? (isAr ? 'مثبّت' : 'Pinned') : (isAr ? 'تثبيت' : 'Pin')}
                </button>
                <button
                  onClick={openCanvas}
                  className="text-[11px] px-2.5 py-1.5 rounded-lg border border-border bg-card/80 text-foreground hover:border-primary/60 transition-colors"
                  title={isAr ? 'افتح الكانفس' : 'Open Canvas'}
                >
                  {isAr ? 'الكل' : 'Canvas'}
                </button>
              </div>
            </div>

            {/* Active visual */}
            <div className="flex-1 min-h-0 rounded-2xl border border-border bg-background/70 backdrop-blur-sm p-3 overflow-hidden flex items-center justify-center">
              {activeVisual.kind === 'chart' && activeVisual.chart && (
                <div className="w-full"><ChatChartRenderer chart={activeVisual.chart} height={Math.min(460, window.innerHeight - 320)} /></div>
              )}
              {activeVisual.kind === '3d' && activeVisual.visual3d && (
                <Suspense fallback={<div className="text-muted-foreground text-xs">{isAr ? 'بحمّل الثري دي...' : 'Loading 3D…'}</div>}>
                  <div className="w-full"><Visual3D visual={activeVisual.visual3d} height={Math.min(460, window.innerHeight - 320)} /></div>
                </Suspense>
              )}
            </div>

            {/* Carousel nav when more than one */}
            {liveVisuals.length > 1 && (
              <div className="flex items-center justify-center gap-1.5 mt-3 flex-wrap">
                {liveVisuals.map((v, i) => (
                  <button
                    key={v.id}
                    onClick={() => setActiveIdx(i)}
                    className={`px-2 py-1 rounded-md text-[10px] border transition-colors ${
                      i === activeIdx
                        ? 'bg-primary/15 border-primary/50 text-indigo-200'
                        : 'bg-card/60 border-border text-muted-foreground hover:text-foreground'
                    }`}
                    title={v.title}
                  >
                    {v.kind === '3d' ? '🧊' : '📊'} {i + 1}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>{/* end content row */}

      {/* Session ended overlay */}
      {ended.shown && (
        <div className="absolute inset-0 z-20 bg-black/70 backdrop-blur-sm flex items-center justify-center px-6">
          <div className="max-w-md bg-card border border-amber-500/40 rounded-xl px-6 py-5 text-center">
            <div className="text-warning text-sm font-semibold mb-1">Conversation ended</div>
            <div className="text-muted-foreground text-xs mb-4">{ended.reason}</div>
            <button
              onClick={onClose}
              className="px-4 py-1.5 rounded-lg bg-primary/15 border border-primary/40 text-indigo-200 text-xs font-medium hover:bg-primary/30 transition-all"
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  )

  // Inline: render in place. Overlay: portal to <body> so the full-screen overlay
  // isn't trapped inside a transformed (translate-x) parent that would confine
  // position:fixed.
  return embedded ? tree : createPortal(tree, document.body)
}
