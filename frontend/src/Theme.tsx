import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useState, type ReactNode } from 'react'
import type { ThemePreference } from '@/components/arc/user-menu/user-menu'

type Theme = { theme: ThemePreference; resolvedTheme: 'light' | 'dark'; setTheme: (theme: ThemePreference) => void }
const ThemeContext = createContext<Theme | null>(null)
const themeStorageKey = 'knowloop-theme'

function savedTheme(): ThemePreference {
  try {
    const saved = localStorage.getItem(themeStorageKey)
    if (saved === 'light' || saved === 'dark') return saved
  } catch { /* Storage may be unavailable. */ }
  return 'system'
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemePreference] = useState(savedTheme)
  const [systemDark, setSystemDark] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches)
  const resolvedTheme = theme === 'system' ? systemDark ? 'dark' : 'light' : theme

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const update = () => setSystemDark(media.matches)
    media.addEventListener('change', update)
    update()
    return () => media.removeEventListener('change', update)
  }, [])

  useLayoutEffect(() => {
    document.querySelector('meta[name="color-scheme"]')
      ?.setAttribute('content', theme === 'system' ? 'light dark' : theme)
    document.documentElement.dataset.theme = resolvedTheme
  }, [theme, resolvedTheme])

  useEffect(() => {
    const syncTheme = (event: StorageEvent) => {
      if ((event.key === themeStorageKey || event.key === null) && event.storageArea === localStorage) {
        setThemePreference(savedTheme())
      }
    }
    window.addEventListener('storage', syncTheme)
    return () => window.removeEventListener('storage', syncTheme)
  }, [])

  const setTheme = useCallback((nextTheme: ThemePreference) => {
    setThemePreference(nextTheme)
    try {
      if (nextTheme === 'system') localStorage.removeItem(themeStorageKey)
      else localStorage.setItem(themeStorageKey, nextTheme)
    } catch { /* Storage may be unavailable. */ }
  }, [])

  return <ThemeContext.Provider value={{ theme, resolvedTheme, setTheme }}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const theme = useContext(ThemeContext)
  if (!theme) throw new Error('ThemeProvider is required')
  return theme
}
