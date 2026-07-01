import { useTheme, type Theme } from '../../hooks/useTheme'
import { brandLogoForTheme, logoDisplayBox } from '../../lib/brandLogos'

type Variant = 'full' | 'shape'

interface Props {
  /** Tailwind height class on the logo box, e.g. `h-7`. Default display size reference. */
  className?: string
  variant?: Variant
  /** Logo height in px (overrides className height when set). */
  size?: number
  alt?: string
  /** Force a specific theme's asset (e.g. dark logo on an always-dark panel). */
  forceTheme?: Theme
}

/** Parse `h-7`, `h-12`, etc. to pixel height (Tailwind 4px scale). */
function heightFromClass(className: string, fallback = 28): number {
  const match = className.match(/\bh-(\d+(?:\.\d+)?)\b/)
  if (!match) return fallback
  return Number(match[1]) * 4
}

/**
 * Theme-aware AxBi logo. Both light and dark assets render inside the same
 * bounding box derived from the light-mode aspect ratio so size never jumps
 * when toggling theme. Pass `forceTheme` for surfaces with a fixed background
 * (e.g. the login hero panel is always dark).
 */
export function AxBiLogo({
  className = 'h-7',
  variant = 'full',
  size,
  alt = 'AxBi',
  forceTheme,
}: Props) {
  const { theme } = useTheme()
  const effectiveTheme = forceTheme ?? theme
  const src = brandLogoForTheme(effectiveTheme, variant)
  const heightPx = size ?? heightFromClass(className)
  const box = logoDisplayBox(heightPx, variant)

  return (
    <span
      className="inline-flex shrink-0 items-center justify-start overflow-hidden"
      style={{ width: box.width, height: box.height }}
      aria-hidden={alt === ''}
    >
      <img
        key={src}
        src={src}
        alt={alt}
        width={box.width}
        height={box.height}
        className="block h-full w-full object-contain object-left"
        draggable={false}
      />
    </span>
  )
}

export default AxBiLogo
