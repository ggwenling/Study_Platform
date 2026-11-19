import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ChevronRight, FileText, HelpCircle, Image, Layers, MessageCircle, PanelRight } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { animate } from 'motion/mini'
import { Link, useLocation } from 'react-router'
import { Button } from '@/components/arc/button/button'
import { Composer } from './Composer'
import type { ComposerHandle } from './Composer'
import { useWorkspace } from '../WorkspaceData'
import { ActivityHeatmap } from '@/components/arc/activity-heatmap/activity-heatmap'
import { useDisclosurePreference } from '../useDisclosurePreference'

const shortcuts = [
  { label: '基于资料提问', description: '从知识库查找答案', icon: MessageCircle },
  { label: '总结与提炼', description: '整理重点，提炼要点', icon: FileText },
  { label: '上传图片并识别', description: '提取图片中的文字', icon: Image },
]

function HomeOrbit() {
  const orbit = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = orbit.current!
    const canAnimate = window.matchMedia('(min-width: 761px) and (prefers-reduced-motion: no-preference)')
    let visible = false
    let animation: ReturnType<typeof animate> | undefined

    function sync() {
      if (!canAnimate.matches) {
        animation?.cancel()
        animation = undefined
      } else if (visible && document.visibilityState === 'visible') {
        animation ??= animate(node, {
          transform: ['perspective(1100px) rotateY(-4deg)', 'perspective(1100px) rotateY(4deg)'],
        }, { duration: 14, repeat: Infinity, repeatType: 'reverse', ease: 'easeInOut' })
        animation.play()
      } else {
        animation?.pause()
      }
    }

    const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; sync() })
    observer.observe(node)
    canAnimate.addEventListener('change', sync)
    document.addEventListener('visibilitychange', sync)
    return () => {
      observer.disconnect()
      canAnimate.removeEventListener('change', sync)
      document.removeEventListener('visibilitychange', sync)
      animation?.cancel()
    }
  }, [])

  return <div ref={orbit} className="orbit-light home-orbit" aria-hidden="true" />
}

export function Home() {
  const reduced = useReducedMotion()
  const location = useLocation()
  const navState = (location.state ?? null) as { preferredLibrary?: string; initialDraft?: string } | null
  const [summary, setSummary] = useState(false)
  const composer = useRef<ComposerHandle>(null)
  const { overview, user, loading, requireLogin } = useWorkspace()
  const [panelOpen, setPanelOpen] = useDisclosurePreference(`knowloop:${user?.id ?? 'guest'}:overview`, true)
  const panelContent = useRef<HTMLDivElement>(null)
  const [panelWidth, setPanelWidth] = useState(224)
  useLayoutEffect(() => {
    const node = panelContent.current!
    const measure = () => setPanelWidth(node.getBoundingClientRect().width)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  return (
    <div className="home" data-overview={panelOpen}>
      <div className="home-hero">
        <HomeOrbit />
        <div className="home-content">
          <header className="welcome">
            <h1>欢迎使用 知循</h1>
            <p>你的个人多知识库 RAG 工具，基于自己的资料获得更可靠的答案与洞察。</p>
          </header>
          <Composer
            ref={composer}
            summary={summary}
            preferredLibrary={navState?.preferredLibrary}
            initialDraft={navState?.initialDraft}
          />
          <div className="shortcuts">
            {shortcuts.map(({ label, description, icon: Icon }, index) => (
              <Button key={label} variant="secondary" className="shortcut" onClick={() => {
                if (!user) return requireLogin()
                if (index === 2) composer.current?.chooseImage()
                else {
                  const nextSummary = index === 1
                  setSummary(nextSummary)
                  composer.current?.setSummaryMode(nextSummary)
                  composer.current?.focus()
                }
              }}>
                <span className="shortcut-content">
                  <Icon size={24} strokeWidth={1.6} />
                  <span>{label}</span>
                  <small>{description}</small>
                </span>
              </Button>
            ))}
          </div>
        </div>
      </div>
      <motion.aside initial={false} animate={{ width: panelOpen ? panelWidth : 36 }} transition={{ duration: reduced ? 0 : .28, ease: [.22, 1, .36, 1] }} className="home-panel" aria-label="资料概况">
        <Button className="overview-toggle" variant="ghost" size="sm" aria-label={panelOpen ? '收起资料概况' : '展开资料概况'} aria-expanded={panelOpen} aria-controls="home-overview" onClick={() => setPanelOpen(!panelOpen)}><PanelRight size={18} /></Button>
        <motion.div ref={panelContent} className="home-panel-inner" id="home-overview" inert={!panelOpen} aria-hidden={!panelOpen} initial={false} animate={{ opacity: panelOpen ? 1 : 0, x: panelOpen ? 0 : 12 }} transition={{ duration: reduced ? 0 : .2 }}>
        <section>
          <h2><Link to="/history">资料活动 <ChevronRight size={16} /></Link></h2>
          {overview?.activity.some((day) => day.count > 0)
            ? <ActivityHeatmap days={overview.activity.slice(-84)} label="资料活动" period="最近 12 周" unit={{ one: '次活动', other: '次活动' }} weekStartsOn={1} locale="zh-CN" />
            : <div className="activity-empty"><MessageCircle size={23} strokeWidth={1.5} /><span>{loading ? '正在读取活动…' : overview ? '暂无资料活动' : user ? '暂时无法读取活动' : '登录后查看资料活动'}</span></div>}
        </section>
        <section>
          <h2>资料概况</h2>
          {user && !loading && overview && overview.knowledge_base_count === 0 && (
            <p className="home-onboarding">
              <Link to="/knowledge">创建首个知识库</Link>
              ，上传资料后即可在本页提问。
            </p>
          )}
          <Link className="overview-row" to="/knowledge"><Layers size={20} />知识库 <span>{overview?.knowledge_base_count}</span><ChevronRight size={15} /></Link>
          <Link className="overview-row" to="/knowledge"><FileText size={20} />资料 <span>{overview?.material_count}</span><ChevronRight size={15} /></Link>
          <Link className="overview-row" to="/history"><HelpCircle size={20} />问答 <span>{overview?.completed_qa_count}</span><ChevronRight size={15} /></Link>
        </section>
        </motion.div>
      </motion.aside>
    </div>
  )
}
