import { createContext, useContext, useEffect, useState, createElement, type ReactNode } from 'react'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'bi_theme'

/** Read the stored theme, defaulting to light (the product default). */
export function getStoredTheme(): Theme {
  if (typeof window === 'undefined') return 'light'
  const saved = localStorage.getItem(STORAGE_KEY)
  return saved === 'dark' ? 'dark' : 'light'
}

/** Apply a theme to <html> by toggling the `dark` class. */
export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark')
}

/**
 * Set the initial theme class before React renders, so there is no flash of the
 * wrong theme on first paint. Call once from main.tsx.
 */
export function initTheme(): void {
  applyTheme(getStoredTheme())
}

type ThemeContextValue = {
  theme: Theme
  setTheme: (theme: Theme) => void
  toggle: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

/** Single shared theme state for the whole app (toggle + logo + charts stay in sync). */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(getStoredTheme)

  useEffect(() => {
    applyTheme(theme)
    localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY && (e.newValue === 'light' || e.newValue === 'dark')) {
        setThemeState(e.newValue)
      }
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const toggle = () => setThemeState((t) => (t === 'dark' ? 'light' : 'dark'))

  return createElement(
    ThemeContext.Provider,
    { value: { theme, setTheme: setThemeState, toggle } },
    children,
  )
}

/** Theme state hook: current theme + a toggle that persists the choice. */
export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) {
    throw new Error('useTheme must be used within ThemeProvider')
  }
  return ctx
}
