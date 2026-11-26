import { useCallback, useState } from 'react'

export function useDisclosurePreference(key: string, initial: boolean) {
  const [open, setOpen] = useState(() => {
    try { const saved = localStorage.getItem(key); return saved === null ? initial : saved === 'true' } catch { return initial }
  })
  const change = useCallback((next: boolean) => {
    setOpen(next)
    try { localStorage.setItem(key, String(next)) } catch { /* Storage may be unavailable. */ }
  }, [key])
  return [open, change] as const
}
