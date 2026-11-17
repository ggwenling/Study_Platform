import { lazy, Suspense, type ComponentProps } from 'react'
import { BookOpen } from 'lucide-react'

const LazyOrb = lazy(() =>
  import('thinking-orbs').then((m) => ({ default: m.ThinkingOrb })),
)

type OrbProps = ComponentProps<typeof LazyOrb>

export function ThinkingOrbLazy(props: OrbProps) {
  return (
    <Suspense fallback={<BookOpen size={20} aria-hidden="true" />}>
      <LazyOrb {...props} />
    </Suspense>
  )
}