import axbiLogoLight from '../assets/axbi-logo.png'
import axbiLogoDark from '../assets/axbi-logo-darkmode.png'
import logoShapeLight from '../assets/logo-shape.png'
import logoShapeDark from '../assets/logo-shape-darkmode.png'
import type { Theme } from '../hooks/useTheme'

/** Bundled brand marks — light vs dark.
 *  Light: axbi logo1 + logo shape (from AXBI logo/)
 *  Dark:  logo darkmode + logo shape darkmode
 */
export const BRAND_LOGOS = {
  full: { light: axbiLogoLight, dark: axbiLogoDark },
  shape: { light: logoShapeLight, dark: logoShapeDark },
} as const

/** Pixel dimensions of the light-mode masters — used as the canonical display box. */
export const LIGHT_LOGO_SIZE = {
  full: { w: 5308, h: 3200 },
  shape: { w: 4296, h: 3904 },
} as const

export function lightLogoAspect(variant: 'full' | 'shape'): number {
  const s = LIGHT_LOGO_SIZE[variant]
  return s.w / s.h
}

/** Width × height box matching light-mode proportions at a given logo height. */
export function logoDisplayBox(heightPx: number, variant: 'full' | 'shape' = 'full') {
  const aspect = lightLogoAspect(variant)
  return { height: heightPx, width: Math.round(heightPx * aspect) }
}

export function brandLogoForTheme(theme: Theme, variant: 'full' | 'shape' = 'full'): string {
  const set = BRAND_LOGOS[variant]
  return theme === 'dark' ? set.dark : set.light
}
