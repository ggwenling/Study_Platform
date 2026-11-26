import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ChevronRight, FileText, HelpCircle, Image, Layers, MessageCircle, PanelRight, RotateCcw } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { animate } from 'motion/mini'
import { Link, useLocation, useNavigate } from 'react-router'
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
  const navigate = useNavigate()
  const navState = (location.state ?? null) as { preferredLibrary?: string; initialDraft?: string; initialMaterialIds?: string[] } | null
  const consumeInitialDraft = useCallback(() => {
    void navigate(location.pathname + location.search + location.hash, { replace: true, state: null })
  }, [location.pathname, location.search, location.hash, navigate])
  const composer = useRef<ComposerHandle>(null)
  const [modelId, setModelId] = useState<string | null>(null)
  const { overview, user, capabilities, loading, requireLogin } = useWorkspace()
  const [panelOpen, setPanelOpen] = useDisclosurePreference(`knowloop:${user?.id ?? 'guest'}:overview`, true)
  const [showModelSetup, setShowModelSetup] = useDisclosurePreference(`knowloop:${user?.id ?? 'guest'}:model-setup`, true)
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
          {user && capabilities?.has_personal_models === false && showModelSetup && <section className="home-model-setup" aria-labelledby="home-model-setup-title">
            <div><h2 id="home-model-setup-title">接入你的模型</h2><p>从供应商预设开始，配置聊天、向量、视觉和重排模型。</p></div>
            <div><Link to="/settings?configure=models">开始配置</Link>
              <Button type="button" size="sm" variant="ghost" onClick={() => setShowModelSetup(false)}>暂时跳过</Button></div>
          </section>}
          <Composer
            ref={composer}
            modelId={modelId}
            onModelChange={setModelId}
            preferredLibrary={navState?.preferredLibrary}
            initialDraft={navState?.initialDraft}
            initialMaterialIds={navState?.initialMaterialIds}
            onInitialDraftApplied={consumeInitialDraft}
          />
          <div className="shortcuts">
            {shortcuts.map(({ label, description, icon: Icon }, index) => (
              <Button key={label} variant="secondary" className="shortcut" onClick={() => {
                if (!user) return requireLogin()
                if (index === 2) composer.current?.chooseImage()
                else {
                  composer.current?.setSummaryMode(index === 1)
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
          {user && !loading && overview && <>
            <Link className="overview-row" to="/learning" state={{ tab: 'flashcards', startDueQueue: true }}>
              <Layers size={20} />待复习闪卡 <span>{overview.due_flashcard_count ?? 0}</span><ChevronRight size={15} />
            </Link>
            <Link className="overview-row" to="/learning" state={{ tab: 'wrong', wrongFilter: 'due' }}>
              <RotateCcw size={20} />待重练错题 <span>{overview.due_wrong_question_count ?? 0}</span><ChevronRight size={15} />
            </Link>
          </>}
        </section>
        </motion.div>
      </motion.aside>
    </div>
  )
}
