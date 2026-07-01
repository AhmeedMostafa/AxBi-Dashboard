import { useEffect, useRef, useState, useCallback } from 'react'

interface Props {
  /** Blob to play (MP3 / audio/*). */
  blob: Blob
  /** Auto-start playback when blob changes. */
  autoPlay?: boolean
  /** Optional title shown above the controls. */
  title?: string
  /** Tailwind classes for the outer container. */
  className?: string
  /** Called when the user clicks the close (×) button. If omitted, no close button is shown. */
  onClose?: () => void
  /** Called when playback ends naturally. */
  onEnded?: () => void
  /** Optional download filename. If provided, a download button is shown. */
  downloadName?: string
}

const SPEED_OPTIONS = [0.75, 1, 1.25, 1.5, 1.75, 2]

function fmt(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60).toString().padStart(2, '0')
  return `${m}:${s}`
}

export default function AudioPlayer({
  blob,
  autoPlay = true,
  title,
  className = '',
  onClose,
  onEnded,
  downloadName,
}: Props) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const urlRef = useRef<string | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [duration, setDuration] = useState(0)
  const [currentTime, setCurrentTime] = useState(0)
  const [speed, setSpeed] = useState(1)
  const [volume, setVolume] = useState(1)
  const [muted, setMuted] = useState(false)
  const [showSpeedMenu, setShowSpeedMenu] = useState(false)

  // (Re)load the audio element when the blob changes.
  useEffect(() => {
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current)
      urlRef.current = null
    }
    const url = URL.createObjectURL(blob)
    urlRef.current = url
    const audio = new Audio(url)
    audio.preload = 'metadata'
    audio.playbackRate = speed
    audio.volume = muted ? 0 : volume
    audioRef.current = audio

    const onLoaded = () => setDuration(audio.duration || 0)
    const onTime = () => setCurrentTime(audio.currentTime || 0)
    const onPlay = () => setIsPlaying(true)
    const onPause = () => setIsPlaying(false)
    const onEnd = () => {
      setIsPlaying(false)
      if (onEnded) onEnded()
    }

    audio.addEventListener('loadedmetadata', onLoaded)
    audio.addEventListener('timeupdate', onTime)
    audio.addEventListener('play', onPlay)
    audio.addEventListener('pause', onPause)
    audio.addEventListener('ended', onEnd)

    if (autoPlay) {
      audio.play().catch(() => setIsPlaying(false))
    }

    return () => {
      audio.removeEventListener('loadedmetadata', onLoaded)
      audio.removeEventListener('timeupdate', onTime)
      audio.removeEventListener('play', onPlay)
      audio.removeEventListener('pause', onPause)
      audio.removeEventListener('ended', onEnd)
      try { audio.pause() } catch {}
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current)
        urlRef.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blob])

  // Keep volume / speed in sync with state
  useEffect(() => {
    if (audioRef.current) audioRef.current.playbackRate = speed
  }, [speed])
  useEffect(() => {
    if (audioRef.current) audioRef.current.volume = muted ? 0 : volume
  }, [volume, muted])

  const togglePlay = useCallback(() => {
    const a = audioRef.current
    if (!a) return
    if (a.paused) a.play().catch(() => {})
    else a.pause()
  }, [])

  const skip = useCallback((delta: number) => {
    const a = audioRef.current
    if (!a) return
    a.currentTime = Math.max(0, Math.min(a.duration || 0, a.currentTime + delta))
  }, [])

  const onScrub = (e: React.ChangeEvent<HTMLInputElement>) => {
    const a = audioRef.current
    if (!a) return
    const v = parseFloat(e.target.value)
    a.currentTime = v
    setCurrentTime(v)
  }

  const handleDownload = () => {
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = (downloadName || 'audio-overview') + '.mp3'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(url), 1500)
  }

  const progressPct = duration > 0 ? (currentTime / duration) * 100 : 0

  return (
    <div
      className={`bg-gradient-to-br from-card to-card border border-border rounded-xl shadow-2xl backdrop-blur-sm ${className}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 pt-3 pb-2">
        <div className="flex items-center gap-2 min-w-0">
          <div className={`w-2 h-2 rounded-full ${isPlaying ? 'bg-[#7c3aed] animate-pulse' : 'bg-slate-500'}`} />
          <span className="text-xs text-foreground font-semibold truncate">
            {title || 'Audio Overview'}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {downloadName && (
            <button
              onClick={handleDownload}
              aria-label="Download as MP3"
              title="Download MP3"
              className="w-7 h-7 flex items-center justify-center rounded-md text-success hover:bg-emerald-500/15 transition-colors"
            >
              <i className="fa-solid fa-download text-[10px]" />
            </button>
          )}
          {onClose && (
            <button
              onClick={onClose}
              aria-label="Close player"
              title="Close"
              className="w-7 h-7 flex items-center justify-center rounded-md text-muted-foreground hover:bg-red-500/15 hover:text-destructive transition-colors"
            >
              <i className="fa-solid fa-xmark text-xs" />
            </button>
          )}
        </div>
      </div>

      {/* Scrub bar with custom progress fill */}
      <div className="px-4 pb-2">
        <div className="relative h-1.5 bg-muted rounded-full overflow-hidden">
          <div
            className="absolute top-0 left-0 h-full bg-gradient-to-r from-[#5A5AF6] to-[#7c3aed] rounded-full transition-all"
            style={{ width: `${progressPct}%` }}
          />
        </div>
        <input
          type="range"
          min={0}
          max={duration || 0}
          step={0.1}
          value={currentTime}
          onChange={onScrub}
          aria-label="Scrub audio"
          className="w-full -mt-2 h-2 appearance-none bg-transparent cursor-pointer opacity-0"
          style={{ marginTop: '-12px' }}
        />
        <div className="flex justify-between text-[10px] text-muted-foreground mt-1 font-mono">
          <span>{fmt(currentTime)}</span>
          <span>{fmt(duration)}</span>
        </div>
      </div>

      {/* Transport row */}
      <div className="flex items-center justify-between px-4 pb-3">
        {/* Left — speed */}
        <div className="relative">
          <button
            onClick={() => setShowSpeedMenu(v => !v)}
            aria-label="Playback speed"
            title={`Speed: ${speed}x`}
            className="px-2 py-1 rounded-md text-[11px] font-bold text-muted-foreground bg-muted border border-border hover:bg-muted hover:text-foreground transition-colors min-w-[44px]"
          >
            {speed}x
          </button>
          {showSpeedMenu && (
            <div className="absolute bottom-full left-0 mb-1 bg-card border border-border rounded-md shadow-xl overflow-hidden z-10">
              {SPEED_OPTIONS.map(s => (
                <button
                  key={s}
                  onClick={() => { setSpeed(s); setShowSpeedMenu(false) }}
                  className={`block w-full text-left px-3 py-1.5 text-[11px] font-medium transition-colors ${
                    speed === s ? 'bg-primary/15 text-primary-foreground' : 'text-muted-foreground hover:bg-muted'
                  }`}
                >
                  {s}x
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Center — transport controls */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => skip(-10)}
            aria-label="Skip back 10 seconds"
            title="Back 10s"
            className="w-8 h-8 flex items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          >
            <i className="fa-solid fa-backward-step text-xs" />
          </button>
          <button
            onClick={togglePlay}
            aria-label={isPlaying ? 'Pause' : 'Play'}
            title={isPlaying ? 'Pause' : 'Play'}
            className="w-10 h-10 flex items-center justify-center rounded-full bg-gradient-to-br from-[#5A5AF6] to-[#7c3aed] text-primary-foreground shadow-lg hover:shadow-[#5A5AF6]/40 hover:scale-105 transition-all"
          >
            <i className={`fa-solid ${isPlaying ? 'fa-pause' : 'fa-play'} text-sm`} />
          </button>
          <button
            onClick={() => skip(10)}
            aria-label="Skip forward 10 seconds"
            title="Forward 10s"
            className="w-8 h-8 flex items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          >
            <i className="fa-solid fa-forward-step text-xs" />
          </button>
        </div>

        {/* Right — volume */}
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setMuted(m => !m)}
            aria-label={muted ? 'Unmute' : 'Mute'}
            title={muted ? 'Unmute' : 'Mute'}
            className="w-8 h-8 flex items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          >
            <i className={`fa-solid ${
              muted || volume === 0 ? 'fa-volume-xmark' :
              volume < 0.5 ? 'fa-volume-low' :
              'fa-volume-high'
            } text-xs`} />
          </button>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={muted ? 0 : volume}
            onChange={(e) => { setMuted(false); setVolume(parseFloat(e.target.value)) }}
            aria-label="Volume"
            className="w-16 h-1 accent-[#5A5AF6] cursor-pointer"
          />
        </div>
      </div>
    </div>
  )
}
