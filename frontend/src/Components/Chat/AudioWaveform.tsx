import { useEffect, useRef, useState } from 'react'

interface Props {
  active: boolean
  size?: 'small' | 'large'
  className?: string
}

export default function AudioWaveform({ active, size = 'small', className }: Props) {
  const isSmall = size === 'small'
  const BARS = isSmall ? 7 : 15

  const [levels, setLevels] = useState<number[]>(() => new Array(BARS).fill(0))
  const animFrameRef = useRef<number>(0)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const contextRef = useRef<AudioContext | null>(null)

  useEffect(() => {
    if (!active) {
      setLevels(new Array(BARS).fill(0))
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(t => t.stop())
        streamRef.current = null
      }
      if (contextRef.current) {
        contextRef.current.close()
        contextRef.current = null
      }
      analyserRef.current = null
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current)
      return
    }

    let cancelled = false

    navigator.mediaDevices.getUserMedia({ audio: true })
      .then(stream => {
        if (cancelled) { stream.getTracks().forEach(t => t.stop()); return }

        streamRef.current = stream
        const ctx = new AudioContext()
        contextRef.current = ctx
        const source = ctx.createMediaStreamSource(stream)
        const analyser = ctx.createAnalyser()
        analyser.fftSize = 64
        analyser.smoothingTimeConstant = 0.78
        source.connect(analyser)
        analyserRef.current = analyser

        const dataArray = new Uint8Array(analyser.frequencyBinCount)

        const tick = () => {
          if (cancelled) return
          analyser.getByteFrequencyData(dataArray)
          // Reflect the real speech spectrum (lower ~70%) directly, with only a
          // small gain — no decorative shaping — so the wave is accurate.
          const newLevels = Array.from({ length: BARS }, (_, i) => {
            const bin = Math.floor((i / BARS) * dataArray.length * 0.7)
            const v = (dataArray[bin] || 0) / 255
            return Math.min(1, v * 1.15)
          })
          setLevels(newLevels)
          animFrameRef.current = requestAnimationFrame(tick)
        }
        tick()
      })
      .catch(() => {
        // Mic unavailable — show a faint flat baseline.
        setLevels(new Array(BARS).fill(0.12))
      })

    return () => {
      cancelled = true
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current)
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(t => t.stop())
        streamRef.current = null
      }
      if (contextRef.current) {
        contextRef.current.close()
        contextRef.current = null
      }
    }
  }, [active, BARS])

  const barWidth = isSmall ? 3 : 5
  const barGap = isSmall ? 3 : 5
  const maxHeight = isSmall ? 18 : 64
  const minHeight = barWidth
  const containerH = isSmall ? 24 : 80
  const containerW = BARS * barWidth + (BARS - 1) * barGap

  if (!active) return null

  return (
    <div
      className={`flex items-center justify-center ${className || ''}`}
      style={{ width: containerW, height: containerH, gap: barGap }}
      aria-label="Voice activity indicator"
    >
      {levels.map((level, i) => {
        const h = Math.max(minHeight, level * maxHeight)
        return (
          <div
            key={i}
            style={{
              width: barWidth,
              height: h,
              borderRadius: barWidth,
              background: 'currentColor',
              opacity: 0.2 + level * 0.45,
              transition: 'height 100ms ease-out, opacity 140ms ease-out',
            }}
          />
        )
      })}
    </div>
  )
}
