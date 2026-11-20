import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useDisclosurePreference } from '../useDisclosurePreference'
import { BookmarkPlus, Copy, Download, Ellipsis, FileText, Folder, PanelRight, Pencil, Trash2 } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { api } from '@/api/client'
import type { Citation, Conversation, Message, Scope } from '@/api/types'
import { allPages, errorMessage } from '@/api/ui'
import { downloadMarkdownFile, formatConversationMarkdown, sanitizeMarkdownFilename, scopeSummaryText } from '@/conversationActions'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { DropdownMenu } from '@/components/arc/dropdown-menu/dropdown-menu'
import { EmptyState } from '@/components/arc/empty-state/empty-state'
import { useWorkspace } from '../WorkspaceData'
import { Composer } from './Composer'
import { ConversationDialogs, type ConversationDialogTarget } from './ConversationDialogs'
import { MarkdownContent } from './MarkdownContent'
import { DownloadMaterial, MaterialPage } from './ProtectedMaterial'
import { AssistantActivity, activityLabels } from './AssistantActivity'
import { SaveChatNoteDialog } from './SaveChatNoteDialog'

const time = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' })
const statusText = { pending: '等待生成…', streaming: '正在生成…', stopped: '已停止', failed: '回答失败', completed: '' }

export default function LiveChat({ id, searchButton }: { id: string; searchButton?: ReactNode }) {
  const { user, chat, revision, refresh, notify, report, requireLogin } = useWorkspace()
  const navigate = useNavigate()
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [saved, setSaved] = useState<Message[]>([])
  const [error, setError] = useState('')
  const [dialogTarget, setDialogTarget] = useState<ConversationDialogTarget | null>(null)
  const sourcesDefault = typeof window.matchMedia === 'function' ? window.matchMedia('(min-width: 769px)').matches : true
  const [panel, setPanel] = useDisclosurePreference(`knowloop:${user?.id ?? 'guest'}:chat-sources`, sourcesDefault)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [sourceId, setSourceId] = useState<string | null>(null)
  const [source, setSource] = useState<Citation | null>(null)
  const [sourceError, setSourceError] = useState('')
  const [noteTarget, setNoteTarget] = useState<{ message: Message; scope: Scope; title: string } | null>(null)
  const scroll = useRef<HTMLDivElement>(null)
  const follow = useRef(true)
  const live = chat.cached[id] ?? []
  const rows = new Map(saved.filter((message) => message.conversation_id === id).map((message) => [message.id, message]))
  for (const message of live) {
    const stored = rows.get(message.id)
    const generating = chat.active?.conversationId === id && chat.active.turnId === message.turn_id
    const storedTerminal = stored && !['pending', 'streaming'].includes(stored.status)
    if (!storedTerminal || generating) rows.set(message.id, message)
  }
  const messages = [...rows.values()].filter((message) => message.role !== 'system_summary')
    .sort((a, b) => a.sequence_no - b.sequence_no)
  const latest = messages.at(-1)
  const focused = messages.find((message) => message.id === selectedId) ?? [...messages].reverse().find((message) => message.role === 'assistant')
  const activeHere = chat.active?.conversationId === id
  const recovering = [...new Set(messages.filter((message) => !chat.busy && message.role === 'assistant'
    && ['pending', 'streaming'].includes(message.status) && message.turn_id !== chat.active?.turnId
    && !chat.recoveryErrors[message.turn_id!]).map((message) => message.turn_id).filter(Boolean))].join(',')

  useEffect(() => {
    if (chat.active?.conversationId === id) {
      // Retry reuses message ids. Drop the previous attempt's saved terminal
      // snapshot before the active attempt finishes and loses its live priority.
      setSaved((current) => current.filter((message) => message.role !== 'assistant' || message.turn_id !== chat.active?.turnId))
    }
  }, [id, chat.active?.conversationId, chat.active?.turnId])

  useEffect(() => {
    if (!user || !recovering) return
    const cleanups = recovering.split(',').map((turnId) => chat.recover(id, turnId))
    return () => { for (const cleanup of cleanups) cleanup() }
  }, [id, user, recovering, chat.recover])

  useEffect(() => {
    if (!user) return
    let mounted = true
    void Promise.all([api.conversation(id), allPages((cursor) => api.messages(id, cursor))]).then(
      ([conversation, messages]) => {
        if (!mounted) return
        setConversation(conversation)
        setSaved(messages)
        setError('')
      },
      (error) => {
        if (!mounted) return
        setError(errorMessage(error))
        report(error)
      },
    )
    return () => { mounted = false }
  }, [id, user, revision])

  useEffect(() => {
    if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight
  }, [messages.length, latest?.content, latest?.status])

  useEffect(() => {
    const node = scroll.current
    if (!node || typeof ResizeObserver === 'undefined') return
    // The animated materials panel changes the message viewport height on every frame.
    const observer = new ResizeObserver(() => {
      if (follow.current) node.scrollTop = node.scrollHeight
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [user, error])

  useEffect(() => {
    if (!sourceId) return
    let mounted = true
    void api.source(sourceId).then(
      (citation) => { if (mounted) setSource(citation) },
      (error) => { if (mounted) { setSourceError(errorMessage(error)); report(error) } },
    )
    return () => { mounted = false }
  }, [sourceId])

  function openSource(citation: Citation) {
    setSource(null)
    setSourceError('')
    setSourceId(citation.id)
  }

  const displayTitle = conversation?.title || live.find((item) => item.role === 'user')?.content.slice(0, 80) || '对话'

  if (!user || (error && !conversation && !live.length)) {
    return (
      <div className="placeholder-page">
        <EmptyState title={user ? '无法打开会话' : '登录后查看会话'} description={error || '登录后可查看你的私有对话。'}
          action={user ? <Link className="return-link" to="/">返回首页</Link> : <Button onClick={requireLogin}>登录</Button>} />
      </div>
    )
  }

  return (
    <div className="chat live-chat" data-sources={panel}>
      <section className="chat-main" aria-label="会话">
        <header className="chat-header">
          <h1>{displayTitle}</h1>
          <span className="pill">仅限个人使用</span>
          {searchButton}
          <DropdownMenu
            label="会话操作"
            icon={<Ellipsis size={18} />}
            iconOnly
            items={[
              { label: '重命名会话', icon: <Pencil size={15} />, disabled: !conversation, onSelect: () => {
                if (conversation) setDialogTarget({ mode: 'rename', conversation })
              } },
              { label: '导出 Markdown', icon: <Download size={15} />, disabled: !messages.length, onSelect: () => {
                downloadMarkdownFile(sanitizeMarkdownFilename(displayTitle), formatConversationMarkdown(displayTitle, messages))
                notify('已导出 Markdown 文件。')
              } },
              { label: '删除会话', icon: <Trash2 size={15} />, destructive: true, separatorBefore: true, onSelect: () => {
                setDialogTarget({ mode: 'delete', conversation: conversation ?? { id, title: displayTitle } })
              } },
            ]}
          />
          <Button variant="ghost" className="icon-button" aria-label={panel ? '收起引用与来源' : '展开引用与来源'} aria-expanded={panel} aria-controls="live-sources" onClick={() => setPanel(!panel)}><PanelRight size={18} /></Button>
        </header>
        <div className="conversation" ref={scroll} tabIndex={0} aria-label="消息" onScroll={(event) => {
          const node = event.currentTarget
          follow.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 8
        }}>
          <div className="conversation-inner">
            {!messages.length && <p className="muted">{conversation ? '还没有消息。选好知识库后开始提问。' : '正在读取会话…'}</p>}
            {messages.map((message) => {
              const isUser = message.role === 'user'
              const stopped = message.status === 'stopped' || message.status === 'failed'
              const phase = !isUser && ['pending', 'streaming'].includes(message.status)
                && chat.active?.assistantId === message.id ? chat.active.phase : null
              const status = phase ? activityLabels[phase] : ['pending', 'streaming'].includes(message.status)
                ? '正在确认生成状态…' : statusText[message.status]
              return (
                <article key={message.id} className={`message ${isUser ? 'user-message' : 'ai-message'}`} aria-label={isUser ? '用户消息' : 'AI 消息'}>
                  <div className="message-meta">
                    {isUser ? <span className="message-avatar">你</span> : <AssistantActivity phase={phase} />}
                    <span>{isUser ? '你' : '知循'}</span><time dateTime={message.created_at}>{time.format(new Date(message.created_at))}</time>
                  </div>
                  {message.scope && <span className="pill"><Folder size={13} />{scopeSummaryText(message.scope)}</span>}
                  {isUser ? <div className="user-bubble">{message.content}</div> : (
                    <>
                      <div className="answer">
                        {message.content && (
                          <MarkdownContent
                            citations={message.citations}
                            onSelectCitation={(citation) => {
                              setSelectedId(message.id)
                              setPanel(true)
                              openSource(citation)
                            }}
                          >
                            {message.content}
                          </MarkdownContent>
                        )}
                        {message.turn_id && chat.recoveryErrors[message.turn_id] && ['pending', 'streaming'].includes(message.status)
                          ? <p className="muted" role="status">暂时无法确认生成状态。<Button size="sm" variant="secondary" onClick={() => chat.recover(id, message.turn_id!)}>重新查询状态</Button></p>
                          : <p className="muted activity-status" role="status" aria-live="polite">{status}</p>}
                      </div>
                      <div className="citation-list">
                        {message.citations.map((citation) => (
                          <Button key={citation.id} size="sm" variant="secondary" className="citation" onClick={() => { setSelectedId(message.id); setPanel(true); openSource(citation) }}>
                            <FileText size={15} />{citation.title} · {citation.location_label}
                          </Button>
                        ))}
                        {message.content && <Button className="icon-button" variant="ghost" aria-label="复制回答" onClick={async () => {
                          try { await navigator.clipboard.writeText(message.content); notify('已复制回答。') } catch { notify('无法访问剪贴板。') }
                        }}><Copy size={16} /></Button>}
                        {message.status === 'completed' && message.content && message.scope && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              if (!message.scope) return
                              const promptRow = messages.find((m) => m.role === 'user' && m.turn_id === message.turn_id)
                              const title = (promptRow?.content.trim().slice(0, 60) || displayTitle || '问答沉淀笔记').replace(/\s+/g, ' ').trim()
                              setNoteTarget({ message, scope: message.scope, title })
                            }}
                          >
                            <BookmarkPlus size={15} />存为笔记
                          </Button>
                        )}
                        {message === latest && message.turn_id && stopped && <Button size="sm" variant="secondary" disabled={chat.busy} onClick={() => void chat.retry(id, message.turn_id!)}>重试本轮</Button>}
                        {message === latest && message.turn_id && message.status === 'streaming' && !activeHere && <Button size="sm" variant="secondary" onClick={() => void chat.stop(id, message.turn_id!)}>停止生成</Button>}
                      </div>
                    </>
                  )}
                </article>
              )
            })}
          </div>
        </div>
        <div className="chat-footer"><Composer conversationId={id} preferredLibrary={latest?.scope?.knowledge_base_id ?? conversation?.project_knowledge_base_id ?? undefined} /></div>
      </section>
      <aside className="source-panel" id="live-sources" aria-label="引用与来源" inert={!panel} aria-hidden={!panel}>
        <div className="source-panel-inner">
          <h2>引用与来源</h2>
          {focused?.scope && (
            <section className="source-section">
              <span className="muted">本轮回答范围</span>
              <h3>{focused.scope.knowledge_base_name}</h3>
              <p>
                {[
                  focused.scope.intent === 'summary' ? '摘要提炼' : null,
                  focused.scope.selected_material_ids?.length ? `限定 ${focused.scope.selected_material_ids.length} 份资料` : null,
                  focused.scope.answer_mode === 'with_general' ? '含通用知识补充' : '仅知识库',
                ].filter(Boolean).join(' · ')}
              </p>
            </section>
          )}
          <section className="source-section">
            <h3>已使用的资料</h3>
            {focused?.citations.length ? <div className="source-cards">
              {focused.citations.map((citation) => <Button key={citation.id} variant="secondary" className="source-card" onClick={() => openSource(citation)}>
                <span className="source-card-content">
                  <span className="source-card-title"><span className="source-badge">{citation.source_label}</span><FileText size={16} /><span>{citation.title}</span></span>
                  {citation.availability === 'available' && citation.quote && <span className="source-excerpt">{citation.quote}</span>}
                  <small>{citation.availability === 'available' ? citation.location_label : '来源已不可用'}
                    {citation.availability === 'available' && citation.quote_status === 'unavailable' && ' · 历史引文不可用'}
                  </small>
                </span>
              </Button>)}
            </div> : <p>这轮还没有可查看的资料引用。</p>}
          </section>
        </div>
      </aside>
      <Dialog open={Boolean(sourceId)} onOpenChange={(open) => { if (!open) setSourceId(null) }}>
        <DialogContent title={source?.title || '资料来源'} description={source?.location_label || '查看当前引用。'}>
          {sourceError ? <p className="error-text" role="alert">{sourceError}</p> : source?.availability === 'unavailable'
            ? <p>此来源已不可访问。</p>
            : source ? <div className="source-detail">
              {source.quote_status === 'unavailable' ? <p>历史引文不可用。</p> : <p>{source.quote}</p>}
              {source.preview_url?.includes('/pages/') && <MaterialPage key={source.preview_url} version={source.material_version_id} page={Number(source.preview_url.split('/').at(-1))} title={`${source.title} · ${source.location_label}`} />}
              {source.content_url && <DownloadMaterial version={source.material_version_id} title={source.title} />}
            </div>
              : <p role="status">正在核实来源…</p>}
        </DialogContent>
      </Dialog>
      {noteTarget && <SaveChatNoteDialog
        key={noteTarget.message.id}
        libraryId={noteTarget.scope.knowledge_base_id}
        libraryName={noteTarget.scope.knowledge_base_name}
        titleSuggestion={noteTarget.title}
        content={noteTarget.message.content}
        onClose={() => setNoteTarget(null)}
        onSave={async (request) => {
          const kbId = noteTarget.scope.knowledge_base_id
          const source = { content_md: noteTarget.message.content, message_id: noteTarget.message.id }
          const note = 'noteId' in request
            ? await api.studio.appendNote(request.noteId, { ...source, section_heading: request.sectionHeading || undefined })
            : await api.studio.createNote(kbId, { ...source, title: request.title, note_type: 'summary' })
          setNoteTarget(null)
          notify(`已保存至「${note.title}」研究笔记。`, {
            action: {
              label: '查看笔记',
              onClick: () => void navigate(`/knowledge?${new URLSearchParams({ id: kbId, note: note.id })}`),
            },
          })
        }}
      />}
      <ConversationDialogs
        target={dialogTarget}
        onClose={() => setDialogTarget(null)}
        onRenamed={(updated) => { setConversation(updated); refresh?.() }}
        onBeforeDelete={(deletedId) => chat?.evictConversation?.(deletedId)}
        onDeleted={(deletedId) => { chat?.evictConversation?.(deletedId); refresh?.(); void navigate('/', { replace: true }) }}
        report={report}
        notify={notify}
      />
    </div>
  )
}
