import { createContext, useContext, useEffect, useLayoutEffect, useState, type ReactNode } from 'react'
import type { ThemePreference } from '@/components/arc/user-menu/user-menu'

type Theme = { theme: ThemePreference; resolvedTheme: 'light' | 'dark'; setTheme: (theme: ThemePreference) => void }
const ThemeContext = createContext<Theme | null>(null)

function savedTheme(): ThemePreference {
  try {
    const saved = localStorage.getItem('knowloop-theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch { /* Storage may be unavailable. */ }
  return 'system'
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState(savedTheme)
  const [systemDark, setSystemDark] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches)
  const resolvedTheme = theme === 'system' ? systemDark ? 'dark' : 'light' : theme

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const update = () => setSystemDark(media.matches)
    media.addEventListener('change', update)
    update()
    return () => media.removeEventListener('change', update)
  }, [])

  useLayoutEffect(() => { document.documentElement.dataset.theme = resolvedTheme }, [resolvedTheme])
  useEffect(() => {
    try { localStorage.setItem('knowloop-theme', theme) } catch { /* Storage may be unavailable. */ }
  }, [theme])

  return <ThemeContext.Provider value={{ theme, resolvedTheme, setTheme }}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const theme = useContext(ThemeContext)
  if (!theme) throw new Error('ThemeProvider is required')
  return theme
}
