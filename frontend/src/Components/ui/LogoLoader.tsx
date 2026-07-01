import { useTheme } from '../../hooks/useTheme'
import { useEffect, useState } from 'react'
import { brandLogoForTheme, logoDisplayBox } from '../../lib/brandLogos'

interface Props {
  message?: string
  /** Render as a fixed full-screen overlay (used for app boot). */
  fullScreen?: boolean
  size?: number
}

export function LogoLoader({ message = 'Loading', fullScreen = false, size = 96 }: Props) {
  const { theme } = useTheme()
  const logoShape = brandLogoForTheme(theme, 'shape')
  const box = logoDisplayBox(size, 'shape')
  const [dots, setDots] = useState('')

  useEffect(() => {
    const interval = setInterval(() => {
      setDots(d => (d.length >= 3 ? '' : d + '.'))
    }, 500)
    return () => clearInterval(interval)
  }, [])

  const inner = (
    <div className="flex flex-col items-center justify-center gap-5">
      <span
        className="inline-flex shrink-0 items-center justify-center overflow-hidden"
        style={{ width: box.width, height: box.height }}
      >
        <img
          key={logoShape}
          src={logoShape}
          alt="AxBi"
          width={box.width}
          height={box.height}
          className="block h-full w-full object-contain will-change-transform"
          style={{ animation: 'logo-fade 1.6s ease-in-out infinite' }}
          draggable={false}
        />
      </span>
      {message && (
        <p className="text-muted-foreground text-sm font-medium tracking-wide">
          {message}{dots}
        </p>
      )}
    </div>
  )

  if (fullScreen) {
    return (
      <div className="fixed inset-0 z-[200] flex items-center justify-center bg-background">
        {inner}
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-[50vh]">
      {inner}
    </div>
  )
}

export default LogoLoader
