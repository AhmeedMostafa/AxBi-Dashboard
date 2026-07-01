import { useState, useRef, useEffect, useCallback } from 'react'
import toast from 'react-hot-toast'
import { translateForTTS, generateDatasetAudioOverview, getTTSWithUsage } from '../../api'
import { LogoSpinner } from '../ui/LogoSpinner'
import { supabase } from '../../supabase-client'
import AudioPlayer from './AudioPlayer'

type Lang = 'en' | 'ar-EG'
type VoicePreset = 'warm-female' | 'anchor-female' | 'calm-female' | 'radio-male' | 'narrator-male' | 'strong-male' | 'news-male'

interface Props {
  /**
   * Fallback source text — used when datasetId is not provided, or as a backup
   * if the dataset overview generation fails.
   */
  text: string | (() => string)
  /** Visible label when idle (defaults to "Audio Overview"). */
  label?: string
  /** Compact / pill style. */
  size?: 'sm' | 'md'
  /** Tailwind classes for the button container. */
  className?: string
  /** Optional aria-label override. */
  ariaLabel?: string
  /** If true (default), prepend the listen icon. */
  showIcon?: boolean
  /**
   * If provided, the component will request a full analytical overview from
   * /api/datasets/<id>/audio-overview/ (uses real data + numbers + insights)
   * instead of just translating the fallback text.
   */
  datasetId?: string
  /** Display name for the downloaded file (default "audio-overview"). */
  downloadName?: string
}

const LANG_LABELS: Record<Lang, { short: string; full: string; flag: string }> = {
  'en':    { short: 'EN',  full: 'English',         flag: '🇺🇸' },
  'ar-EG': { short: 'عر', full: 'Egyptian Arabic', flag: '🇪🇬' },
}

const VOICE_OPTIONS: { id: VoicePreset; label: string; tag: string; icon: string }[] = [
  { id: 'radio-male',    label: 'Radio Anchor',  tag: 'Deep, broadcast male', icon: 'fa-microphone' },
  { id: 'news-male',     label: 'News Anchor',   tag: 'Clear professional male', icon: 'fa-tv' },
  { id: 'narrator-male', label: 'Narrator',      tag: 'Warm storyteller male', icon: 'fa-book-open' },
  { id: 'anchor-female', label: 'News Anchor',   tag: 'Sharp female anchor',   icon: 'fa-tv' },
  { id: 'warm-female',   label: 'Warm Female',   tag: 'Friendly female',       icon: 'fa-heart' },
  { id: 'calm-female',   label: 'Calm Female',   tag: 'Soft soothing female',  icon: 'fa-leaf' },
]

const LANG_KEY = 'bi-voice-language'
const VOICE_KEY = 'bi-voice-preset'

type AudioCacheEntry = {
  blob: Blob
  playerTitle: string
  lang: Lang
  voice: VoicePreset
  datasetId?: string
  /** Fingerprint for text-only (non-dataset) mode. */
  textKey?: string
}

function fallbackTextKey(text: string): string {
  const t = text.trim()
  if (!t) return ''
  return `${t.length}:${t.slice(0, 160)}`
}

export default function AudioOverviewButton({
  text,
  label = 'Audio Overview',
  size = 'md',
  className = '',
  ariaLabel,
  showIcon = true,
  datasetId,
  downloadName = 'audio-overview',
}: Props) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [translating, setTranslating] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [activeLang, setActiveLang] = useState<Lang>(() => {
    const stored = (typeof window !== 'undefined' ? localStorage.getItem(LANG_KEY) : null) || 'en'
    return stored.toLowerCase().startsWith('ar') ? 'ar-EG' : 'en'
  })
  const [activeVoice, setActiveVoice] = useState<VoicePreset>(() => {
    const stored = typeof window !== 'undefined' ? localStorage.getItem(VOICE_KEY) : null
    return (stored as VoicePreset) || 'radio-male'
  })
  const [lastBlob, setLastBlob] = useState<Blob | null>(null)
  const [showPlayer, setShowPlayer] = useState(false)
  const [userName, setUserName] = useState<string>('')
  const [playerTitle, setPlayerTitle] = useState<string>('')

  const menuRef = useRef<HTMLDivElement>(null)
  const audioCacheRef = useRef<AudioCacheEntry | null>(null)

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    if (menuOpen) document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [menuOpen])

  // Fetch user name once for personalized overviews
  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => {
      const meta = data?.user?.user_metadata || {}
      const name = (meta.name || meta.full_name || meta.display_name || '').toString().trim()
      if (name) setUserName(name)
    }).catch(() => {})
  }, [])

  const resolveFallbackText = () => (typeof text === 'function' ? text() : text)

  const cacheMatches = useCallback((lang: Lang, voice: VoicePreset): boolean => {
    const cached = audioCacheRef.current
    if (!cached) return false
    if (cached.lang !== lang || cached.voice !== voice) return false
    if ((cached.datasetId || '') !== (datasetId || '')) return false
    if (!datasetId) {
      const key = fallbackTextKey(resolveFallbackText())
      if (cached.textKey !== key) return false
    }
    return true
  }, [datasetId, text])

  const replayCached = useCallback((cached: AudioCacheEntry) => {
    setLastBlob(cached.blob)
    setPlayerTitle(cached.playerTitle)
    setShowPlayer(true)
    setPlaying(true)
  }, [])

  const stopPlayback = useCallback(() => {
    setShowPlayer(false)
    setPlaying(false)
  }, [])

  const playInLang = async (lang: Lang, voice?: VoicePreset) => {
    setMenuOpen(false)
    setActiveLang(lang)
    localStorage.setItem(LANG_KEY, lang)
    const usedVoice = voice || activeVoice

    // Replay instantly if nothing changed (e.g. user stopped mid-playback).
    if (cacheMatches(lang, usedVoice) && audioCacheRef.current) {
      stopPlayback()
      replayCached(audioCacheRef.current)
      return
    }

    stopPlayback()

    let narration = ''
    let overviewSession: Record<string, unknown> | null = null

    // Path 1 — full analytical overview from dataset
    if (datasetId) {
      setGenerating(true)
      const tId = toast.loading(
        lang === 'ar-EG'
          ? `بنحضّرلك نظرة تحليلية ${userName ? '، يا ' + userName.split(' ')[0] : ''}...`
          : `Generating analytical overview${userName ? ' for ' + userName.split(' ')[0] : ''}...`,
        { position: 'top-center' },
      )
      try {
        const result = await generateDatasetAudioOverview(datasetId, {
          language: lang,
          style: 'formal',
          durationSeconds: 75,
          userName,
          skipVoiceLog: true,
        })
        narration = (result?.text || '').trim()
        overviewSession = {
          dataset_id: datasetId,
          style: result?.style || 'formal',
          duration_seconds: result?.duration_seconds ?? 75,
          user_name: userName || '',
          overview_model: result?.model || '',
        }
        toast.dismiss(tId)
      } catch (err: any) {
        toast.dismiss(tId)
        toast.error(`Overview failed: ${err?.response?.data?.error || err?.message || err}`)
        setGenerating(false)
        narration = ''
      }
      setGenerating(false)
    }

    // Path 2 — fallback: take the provided text and translate it if needed
    if (!narration) {
      narration = (resolveFallbackText() || '').trim()
      if (!narration) {
        toast.error('Nothing to read')
        return
      }
      // Detect mismatch: if text is in different language than target, translate
      // (cheap heuristic — count arabic vs latin chars)
      const arabicChars = (narration.match(/[\u0600-\u06FF]/g) || []).length
      const latinChars = (narration.match(/[A-Za-z]/g) || []).length
      const looksArabic = arabicChars > latinChars
      const needsTranslate =
        (lang === 'ar-EG' && !looksArabic) || (lang === 'en' && looksArabic)

      if (needsTranslate) {
        setTranslating(true)
        const tId = toast.loading(
          lang === 'ar-EG' ? 'بنحوّلك النص للمصري الراقي...' : 'Translating to English...',
          { position: 'top-center' },
        )
        try {
          const result = await translateForTTS(narration, lang, { style: 'formal' })
          narration = result?.text || narration
          toast.dismiss(tId)
        } catch (err: any) {
          toast.dismiss(tId)
          toast.error(`Translation failed: ${err?.message || err}`)
          setTranslating(false)
          return
        }
        setTranslating(false)
      }
    }

    // Generate TTS audio with Gemini 3.1 Flash TTS Preview (high-quality
    // narration with a natural-language tone prompt). The prompt steers the
    // voice's delivery — confident, warm, broadcast-quality.
    const ttsPrompt = lang === 'ar-EG'
      ? (
          'اقرأ النص باللهجة المصرية الراقية بأسلوب محلل أعمال محترف. '
          + 'صوت واثق ودافئ ومتحكَّم، إيقاع طبيعي وواضح، نبرة احترافية ولكن ودودة، '
          + 'كأنك تقدم تقريراً تنفيذياً في برنامج اقتصادي مصري.'
        )
      : (
          'Read aloud as a confident senior business analyst delivering an '
          + 'executive briefing. Warm yet professional, articulate, '
          + 'broadcast-quality narration with natural pacing.'
        )

    const ttsToast = toast.loading(
      lang === 'ar-EG' ? 'بنسجّل الصوت بجودة HD...' : 'Generating HD audio...',
      { position: 'top-center' },
    )
    try {
      const { blob, usage } = await getTTSWithUsage(narration, usedVoice, lang, {
        model: 'gemini',
        prompt: ttsPrompt,
        ...(overviewSession ? { audioOverview: overviewSession } : {}),
      })
      toast.dismiss(ttsToast)
      const voiceLabel = VOICE_OPTIONS.find(v => v.id === usedVoice)?.label
        || usage?.voice
        || ''
      const title =
        lang === 'ar-EG'
          ? `نظرة تحليلية · ${voiceLabel}`
          : `Audio Overview · ${voiceLabel}`
      setLastBlob(blob)
      setPlayerTitle(title)
      audioCacheRef.current = {
        blob,
        playerTitle: title,
        lang,
        voice: usedVoice,
        datasetId,
        textKey: datasetId ? undefined : fallbackTextKey(resolveFallbackText()),
      }
      setShowPlayer(true)
      setPlaying(true)
    } catch (err: any) {
      toast.dismiss(ttsToast)
      toast.error(`TTS failed: ${err?.response?.data?.error || err?.message || err}`)
    }
  }

  const handlePrimary = () => {
    if (playing) {
      stopPlayback()
      return
    }
    // Same lang/voice as last run → replay cached audio (no API wait).
    if (cacheMatches(activeLang, activeVoice) && audioCacheRef.current) {
      replayCached(audioCacheRef.current)
      return
    }
    playInLang(activeLang, activeVoice)
  }

  const handleDownload = () => {
    if (!lastBlob) {
      toast.error('Generate an overview first, then download')
      return
    }
    const url = URL.createObjectURL(lastBlob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${downloadName}-${activeLang}-${activeVoice}-${Date.now()}.mp3`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(url), 1500)
  }

  const pickVoice = (v: VoicePreset) => {
    setActiveVoice(v)
    localStorage.setItem(VOICE_KEY, v)
  }

  const busy = generating || translating
  const sizeClasses = size === 'sm' ? 'px-3 py-1.5 text-xs' : 'px-4 py-2 text-sm'
  const voiceInfo = VOICE_OPTIONS.find(v => v.id === activeVoice)

  return (
    <>
    {/* Floating audio player anchored bottom-right when active */}
    {showPlayer && lastBlob && (
      <div className="fixed bottom-6 right-6 z-[80] w-[360px] max-w-[90vw]">
        <AudioPlayer
          blob={lastBlob}
          title={playerTitle}
          downloadName={downloadName}
          onClose={() => { setShowPlayer(false); setPlaying(false) }}
          onEnded={() => setPlaying(false)}
        />
      </div>
    )}

    <div ref={menuRef} className={`relative inline-flex items-stretch ${className}`}>
      {/* Primary action */}
      <button
        onClick={handlePrimary}
        disabled={busy && !playing}
        aria-label={ariaLabel || `${playing ? 'Stop' : label} in ${LANG_LABELS[activeLang].full}`}
        className={`${sizeClasses} font-medium rounded-l-lg border-r-0 transition-all flex items-center gap-2 whitespace-nowrap ${
          playing
            ? 'bg-red-500/20 border border-red-500/40 text-destructive hover:bg-red-500/30'
            : busy
            ? 'bg-card border border-border text-muted-foreground cursor-wait'
            : 'bg-primary/15 border border-primary/40 text-primary hover:bg-primary/25 hover:border-primary/60'
        }`}
      >
        {showIcon && (
          (translating || busy) && !playing && !generating
            ? <LogoSpinner size={14} />
            : <i className={`fa-solid ${
                playing ? 'fa-stop' :
                generating ? 'fa-brain fa-beat' :
                'fa-volume-high'
              } text-xs`} />
        )}
        <span>
          {playing ? 'Stop'
            : generating ? 'Analyzing…'
            : translating ? 'Translating…'
            : label}
        </span>
        <span className="text-[10px] opacity-70 font-semibold">
          · {LANG_LABELS[activeLang].short}
        </span>
      </button>

      {/* Language/voice picker chevron */}
      <button
        onClick={(e) => { e.stopPropagation(); setMenuOpen(o => !o) }}
        disabled={busy}
        aria-label="Choose audio language and voice"
        title="Choose language and voice"
        className={`${size === 'sm' ? 'px-2 py-1.5' : 'px-2.5 py-2'} border border-l border-r-0 transition-all ${
          playing
            ? 'bg-red-500/20 border-red-500/40 text-destructive hover:bg-red-500/30'
            : busy
            ? 'bg-card border-border text-muted-foreground'
            : 'bg-primary/15 border-primary/40 text-primary hover:bg-primary/25 hover:border-primary/60'
        }`}
      >
        <i className={`fa-solid fa-chevron-down text-[10px] transition-transform ${menuOpen ? 'rotate-180' : ''}`} />
      </button>

      {/* Download button */}
      <button
        onClick={handleDownload}
        disabled={!lastBlob || busy}
        aria-label="Download last generated audio as MP3"
        title={lastBlob ? 'Download last audio as MP3' : 'Generate an overview first to download'}
        className={`${size === 'sm' ? 'px-2 py-1.5' : 'px-2.5 py-2'} rounded-r-lg border transition-all ${
          !lastBlob
            ? 'bg-card border-border text-muted-foreground cursor-not-allowed'
            : 'bg-card border-border text-success hover:bg-emerald-500/15 hover:border-emerald-500/40'
        }`}
      >
        <i className="fa-solid fa-download text-[10px]" />
      </button>

      {/* Dropdown panel */}
      {menuOpen && (
        <div className="absolute top-full right-0 mt-1 w-72 bg-card border border-border rounded-lg shadow-2xl overflow-hidden z-50 backdrop-blur-sm">
          {/* Language section */}
          <div className="px-3 py-2 text-[10px] uppercase tracking-wider text-muted-foreground font-semibold border-b border-border">
            Audio language
          </div>
          {(['en', 'ar-EG'] as Lang[]).map((l) => (
            <button
              key={l}
              onClick={() => playInLang(l)}
              className={`w-full text-left px-3 py-2 flex items-center justify-between gap-2 text-sm transition-colors ${
                activeLang === l
                  ? 'bg-primary/15 text-primary-foreground'
                  : 'text-muted-foreground hover:bg-muted'
              }`}
            >
              <span className="flex items-center gap-2">
                <span className="text-base">{LANG_LABELS[l].flag}</span>
                <span>{LANG_LABELS[l].full}</span>
              </span>
              {activeLang === l && <i className="fa-solid fa-check text-[10px] text-success" />}
            </button>
          ))}

          {/* Voice section */}
          <div className="px-3 py-2 text-[10px] uppercase tracking-wider text-muted-foreground font-semibold border-b border-t border-border">
            Voice
          </div>
          <div className="max-h-64 overflow-y-auto">
            {VOICE_OPTIONS.map((v) => (
              <button
                key={v.id}
                onClick={() => pickVoice(v.id)}
                className={`w-full text-left px-3 py-2 flex items-center justify-between gap-2 text-sm transition-colors ${
                  activeVoice === v.id
                    ? 'bg-primary/15 text-primary-foreground'
                    : 'text-muted-foreground hover:bg-muted'
                }`}
              >
                <span className="flex items-center gap-2 min-w-0">
                  <i className={`fa-solid ${v.icon} text-[10px] w-3 text-muted-foreground`} />
                  <span className="flex flex-col min-w-0">
                    <span className="text-xs font-medium truncate">{v.label}</span>
                    <span className="text-[10px] text-muted-foreground truncate">{v.tag}</span>
                  </span>
                </span>
                {activeVoice === v.id && <i className="fa-solid fa-check text-[10px] text-success" />}
              </button>
            ))}
          </div>

          <div className="px-3 py-2 text-[10px] text-muted-foreground border-t border-border leading-relaxed">
            {datasetId
              ? 'Generates an analytical narration with real numbers + insights from your dataset.'
              : 'Rewrites the source text in your chosen language and voice.'}
          </div>
          <div className="px-3 py-2 border-t border-border flex items-center gap-2 bg-muted">
            <div className="text-[10px] text-muted-foreground">
              Current: <span className="text-foreground font-medium">{LANG_LABELS[activeLang].full}</span>
              <span className="text-muted-foreground"> · </span>
              <span className="text-foreground font-medium">{voiceInfo?.label || activeVoice}</span>
            </div>
          </div>
        </div>
      )}
    </div>
    </>
  )
}
