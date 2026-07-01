import { useTheme } from '../../hooks/useTheme'
import { brandLogoForTheme, logoDisplayBox } from '../../lib/brandLogos'

interface Props {
  /** Pixel size of the mark. */
  size?: number
  className?: string
}

/**
 * Small inline loading indicator that animates the AxBi logo shape.
 * Drop-in replacement for `fa-spinner fa-spin` / border spinners so every
 * loading state across the app uses the brand mark.
 */
export function LogoSpinner({ size = 20, className = '' }: Props) {
  const { theme } = useTheme()
  const logoShape = brandLogoForTheme(theme, 'shape')
  const box = logoDisplayBox(size, 'shape')

  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center overflow-hidden ${className}`}
      style={{ width: box.width, height: box.height }}
    >
      <img
        key={logoShape}
        src={logoShape}
        alt="Loading"
        width={box.width}
        height={box.height}
        className="block h-full w-full object-contain will-change-transform select-none"
        style={{ animation: 'logo-fade 1.2s ease-in-out infinite' }}
        draggable={false}
      />
    </span>
  )
}

export default LogoSpinner
