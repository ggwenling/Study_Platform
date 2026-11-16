import { useEffect, useState } from 'react'

/** True when the primary input is touch / coarse (phones, most tablets). */
export function useCoarsePointer() {
  const [coarse, setCoarse] = useState(() =>
    typeof matchMedia !== 'undefined' ? matchMedia('(pointer: coarse)').matches : false,
  )
  useEffect(() => {
    if (typeof matchMedia === 'undefined') return
    const mq = matchMedia('(pointer: coarse)')
    const sync = () => setCoarse(mq.matches)
    sync()
    mq.addEventListener('change', sync)
    return () => mq.removeEventListener('change', sync)
  }, [])
  return coarse
}