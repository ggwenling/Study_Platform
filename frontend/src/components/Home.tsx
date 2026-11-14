import { useRef, useState } from 'react'
import { ChevronRight, FileText, HelpCircle, Image, Layers, MessageCircle } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { Link } from 'react-router'
import { Button } from '@/components/arc/button/button'
import { Composer } from './Composer'
import { useWorkspace } from '../WorkspaceData'
import { ActivityHeatmap } from '@/components/arc/activity-heatmap/activity-heatmap'

const shortcuts = [
  { label: '基于资料提问', description: '从知识库查找答案', icon: MessageCircle },
  { label: '总结与提炼', description: '整理重点，提炼要点', icon: FileText },
  { label: '上传图片并识别', description: '提取图片中的文字', icon: Image },
]

export function Home() {
  const reduced = useReducedMotion()
  const [summary, setSummary] = useState(false)
  const composer = useRef<HTMLDivElement>(null)
  const { overview, user, loading, requireLogin } = useWorkspace()

  return (
    <div className="home">
      <div className="home-hero">
        <motion.div
          aria-hidden="true"
          className="orbit-light"
          animate={reduced ? { opacity: .8, x: 0, y: 0 } : { opacity: [.78, 1, .78], x: [0, 15, 0], y: [0, -8, 0] }}
          transition={reduced ? { duration: 0 } : { duration: 18, repeat: Infinity, ease: 'easeInOut' }}
        />
        <div className="home-content">
          <header className="welcome">
            <h1>欢迎使用 知循</h1>
            <p>你的个人多知识库 RAG 工具，基于自己的资料获得更可靠的答案与洞察。</p>
          </header>
          <div ref={composer}><Composer summary={summary} /></div>
          <div className="shortcuts">
            {shortcuts.map(({ label, description, icon: Icon }, index) => (
              <Button key={label} variant="secondary" className="shortcut" onClick={() => {
                if (!user) return requireLogin()
                if (index === 2) composer.current?.querySelector<HTMLButtonElement>('button[aria-label="添加图片"]')?.click()
                else {
                  setSummary(index === 1)
                  composer.current?.querySelector('textarea')?.focus()
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
      <aside className="home-panel" aria-label="资料概况">
        <section>
          <h2><Link to="/history">资料活动 <ChevronRight size={16} /></Link></h2>
          {overview?.activity.some((day) => day.count > 0)
            ? <ActivityHeatmap days={overview.activity.slice(-84)} label="资料活动" period="最近 12 周" unit={{ one: '次活动', other: '次活动' }} weekStartsOn={1} locale="zh-CN" />
            : <div className="activity-empty"><MessageCircle size={23} strokeWidth={1.5} /><span>{loading ? '正在读取活动…' : overview ? '暂无资料活动' : user ? '暂时无法读取活动' : '登录后查看资料活动'}</span></div>}
        </section>
        <section>
          <h2>资料概况</h2>
          <Link className="overview-row" to="/knowledge"><Layers size={20} />知识库 <span>{overview?.knowledge_base_count}</span><ChevronRight size={15} /></Link>
          <Link className="overview-row" to="/knowledge"><FileText size={20} />资料 <span>{overview?.material_count}</span><ChevronRight size={15} /></Link>
          <Link className="overview-row" to="/history"><HelpCircle size={20} />问答 <span>{overview?.completed_qa_count}</span><ChevronRight size={15} /></Link>
        </section>
      </aside>
    </div>
  )
}
