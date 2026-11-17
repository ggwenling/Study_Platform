import { useCallback, useRef, type ReactNode } from 'react'
import { BEND, MetalFx, useMetalBend } from 'metal-fx'
import { useReducedMotion } from 'motion/react'
import { useTheme } from '../Theme'

const staticBend = { ...BEND, enabled: false }

export function ComposerMetalSend({
  children,
  reflectionTarget,
}: {
  children: ReactNode
  reflectionTarget: React.RefObject<HTMLButtonElement | null>
}) {
  const metalRef = useRef<HTMLDivElement>(null)
  const { resolvedTheme } = useTheme()
  const reducedMotion = useReducedMotion() ?? false
  const reflections = reducedMotion ? undefined : [{ ref: reflectionTarget, strength: 0.35 as const }]
  useMetalBend(metalRef, useCallback(() => (reducedMotion ? staticBend : BEND), [reducedMotion]))
  return (
    <MetalFx
      ref={metalRef}
      className="composer-send"
      variant="circle"
      preset="chromatic"
      theme={resolvedTheme}
      innerShadow
      ringCssPx={2.5}
      glowGain={0.55}
      reflectionTargets={reflections}
      paused={reducedMotion}
      normalizeHostStyles={false}
    >
      {children}
    </MetalFx>
  )
}
