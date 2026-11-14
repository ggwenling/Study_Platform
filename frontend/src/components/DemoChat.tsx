import { useState } from 'react'
import { ArrowUpRight, BookOpen, Copy, FileText, Folder, PanelRight } from 'lucide-react'
import { ActivityHeatmap } from '@/components/arc/activity-heatmap/activity-heatmap'
import { Button } from '@/components/arc/button/button'
import { Tooltip } from '@/components/arc/tooltip/tooltip'
import { Composer } from './Composer'
import { MarkdownContent } from './MarkdownContent'

const sources = [
  { name: '产品需求说明.pdf', location: '第 8 页', version: 'v2', excerpt: '文字 OCR 支持 PNG、JPEG 与扫描 PDF 的文字提取，并保留识别文本和原始文件。' },
  { name: '产品定位说明.docx', location: '第 3 节', excerpt: '识别后的文字可用于检索。图表语义理解属于后续范围。' },
]

const answer = `根据示例资料，首版图片 OCR 有三个要求：

- **支持格式**：PNG、JPEG，以及需要文字识别的扫描 PDF。
- **识别预览**：提供识别文字和原图对照，保留文件与版本信息。
- **异常处理**：识别失败时提示重试，无法识别的内容不进入检索。

当前只按识别到的文字检索。以下是示例格式清单：

\`\`\`json
{ "image": ["PNG", "JPEG"], "document": ["扫描 PDF"] }
\`\`\``

const demoDays = Array.from({ length: 147 }, (_, index) => ({
  date: new Date(Date.UTC(2026, 5, 22 + index)).toISOString().slice(0, 10),
  count: index % 47 === 0 ? 8 : index % 13 === 0 ? 4 : index % 5 === 0 ? 1 : 0,
}))

export default function DemoChat({ notify }: { notify: (message: string) => void }) {
  const [sourcesOpen, setSourcesOpen] = useState(true)
  const sourceNotice = () => notify('来源查看尚未接入。')

  async function copyAnswer() {
    try {
      await navigator.clipboard.writeText(answer)
      notify('已复制示例回答。')
    } catch {
      notify('无法访问剪贴板，请选中回答后手动复制。')
    }
  }

  return (
    <div className="chat" data-sources={sourcesOpen}>
      <section className="chat-main" aria-label="示例会话">
        <header className="chat-header">
          <h1>产品需求讨论</h1>
          <span className="pill">示例会话</span>
          <Tooltip content={sourcesOpen ? '收起引用与来源' : '展开引用与来源'}>
            <Button variant="ghost" className="icon-button" aria-label={sourcesOpen ? '收起引用与来源' : '展开引用与来源'} aria-expanded={sourcesOpen} aria-controls={sourcesOpen ? 'chat-sources' : undefined} onClick={() => setSourcesOpen(!sourcesOpen)}>
              <PanelRight size={18} />
            </Button>
          </Tooltip>
        </header>
        <div className="conversation" tabIndex={0} aria-label="消息">
          <div className="conversation-inner">
            <article className="message user-message" aria-label="用户消息">
              <div className="message-meta"><time>14:27</time><span>你</span><span className="message-avatar">你</span></div>
              <div className="user-bubble">根据我上传的资料，首版对图片 OCR 有哪些具体要求？</div>
              <span className="pill"><Folder size={13} /> 产品资料库 · 仅知识库</span>
            </article>
            <article className="message ai-message" aria-label="AI 消息">
              <div className="message-meta"><span className="message-avatar"><BookOpen size={17} /></span><span>知循</span><time>14:27</time></div>
              <span className="pill"><Folder size={13} /> 本轮依据 · 产品资料库</span>
              <div className="answer"><MarkdownContent>{answer}</MarkdownContent></div>
              <div className="citation-list">
                {sources.map(({ name, location }) => (
                  <Button key={name} variant="secondary" size="sm" className="citation" onClick={sourceNotice}>
                    <FileText size={15} />{name} · {location}<ArrowUpRight size={12} />
                  </Button>
                ))}
              </div>
              <Tooltip content="复制回答">
                <Button variant="ghost" className="icon-button copy-button" aria-label="复制回答" onClick={copyAnswer}><Copy size={16} /></Button>
              </Tooltip>
            </article>
          </div>
        </div>
        <div className="chat-footer"><Composer demo /></div>
      </section>
      <aside className="source-panel" id="chat-sources" aria-label="引用与来源" inert={!sourcesOpen} aria-hidden={!sourcesOpen}>
        <div className="source-panel-inner">
          <h2>引用与来源</h2>
          <section className="source-section">
            <span className="muted">本轮回答范围</span>
            <h3>产品资料库</h3>
            <p>仅知识库 · 示例资料</p>
          </section>
          <section className="source-section">
            <h3>已使用的资料 <span>2 条示例引用</span></h3>
            <div className="source-cards">
              {sources.map(({ name, location, version, excerpt }) => (
                <Button key={name} className="source-card" variant="secondary" onClick={sourceNotice}>
                  <span className="source-card-content">
                    <span className="source-card-title"><FileText size={16} />{name}<ArrowUpRight size={15} /></span>
                    <span className="source-excerpt">“{excerpt}”</span>
                    <small>{location}{version && ` · ${version}`}</small>
                  </span>
                </Button>
              ))}
            </div>
          </section>
          <section className="source-section">
            <h3>使用活动</h3>
            <ActivityHeatmap days={demoDays} label="示例使用活动" period="最近 21 周" unit={{ one: '次活动', other: '次活动' }} weekStartsOn={1} locale="zh-CN" />
          </section>
          <p className="source-disclaimer">示例内容仅用于展示界面。</p>
        </div>
      </aside>
    </div>
  )
}
