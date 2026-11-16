import { ThinkingOrbLazy } from './ThinkingOrbLazy'

/** 无导航入口；仅供移动端 WebGL 烟测与截图。 */
export function VisualOrbSmoke() {
  return (
    <div className="placeholder-page" style={{ minHeight: '60dvh' }}>
      <ThinkingOrbLazy state="working" size={64} theme="auto" aria-hidden="true" />
    </div>
  )
}