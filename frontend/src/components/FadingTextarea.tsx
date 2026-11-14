import { useEffect, useRef, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { Textarea } from '@/components/arc/textarea/textarea'
import type { TextareaProps } from '@/components/arc/textarea/textarea'

// The native control owns editing. Only a short, appended run is mirrored while it fades.
export function FadingTextarea({ value, onChange, ...props }: TextareaProps & { value: string }) {
  const reduced = useReducedMotion()
  const composing = useRef(false)
  const compositionStart = useRef('')
  const previous = useRef(value)
  const sequence = useRef(0)
  const [run, setRun] = useState<{ value: string; start: number; key: number } | null>(null)
  const [scroll, setScroll] = useState({ top: 0, left: 0 })
  useEffect(() => { previous.current = value }, [value])
  const mirror = !reduced && run?.value === value && !composing.current
  function reveal(before: string, after: string, allowed: boolean) {
    if (allowed && after.startsWith(before) && after.length > before.length && after.length - before.length <= 32) {
      setRun({ value: after, start: before.length, key: ++sequence.current })
    } else setRun(null)
  }
  return <div className="fading-input" data-mirroring={mirror || undefined}>
    <Textarea {...props} value={value} onChange={(event) => {
      const next = event.target.value
      const input = event.nativeEvent as InputEvent
      if (next !== previous.current) reveal(previous.current, next, !composing.current && !input.isComposing && input.inputType === 'insertText')
      previous.current = next
      onChange?.(event)
    }} onCompositionStart={(event) => {
      composing.current = true; compositionStart.current = event.currentTarget.value; setRun(null)
    }} onCompositionEnd={(event) => {
      composing.current = false
      reveal(compositionStart.current, event.currentTarget.value, true)
      previous.current = event.currentTarget.value
    }} onScroll={(event) => setScroll({ top: event.currentTarget.scrollTop, left: event.currentTarget.scrollLeft })} />
    {mirror && <div className="fading-mirror" aria-hidden="true"><div style={{ transform: `translate(${-scroll.left}px, ${-scroll.top}px)` }}>
      {value.slice(0, run.start)}<motion.span key={run.key} initial={{ opacity: .25 }} animate={{ opacity: 1 }} transition={{ duration: .14, ease: 'easeOut' }} onAnimationComplete={() => setRun((current) => current?.key === run.key ? null : current)}>{value.slice(run.start)}</motion.span>{'\u200b'}
    </div></div>}
  </div>
}
