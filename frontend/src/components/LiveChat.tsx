import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { useDisclosurePreference } from '../useDisclosurePreference'
import { BookmarkPlus, Copy, Download, Ellipsis, FileText, Folder, PanelRight, Pencil, Trash2 } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { api } from '@/api/client'
import { ApiError } from '@/api/stream'
import type { Citation, Conversation, Message, Page, Scope } from '@/api/types'
import { allPages, chatModelError, errorMessage, safeExternalUrl } from '@/api/ui'
import { downloadMarkdownFile, formatConversationMarkdown, sanitizeMarkdownFilename, scopeSummaryText } from '@/conversationActions'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { DropdownMenu } from '@/components/arc/dropdown-menu/dropdown-menu'
import { EmptyState } from '@/components/arc/empty-state/empty-state'
import { Textarea } from '@/components/arc/textarea/textarea'
import { useWorkspace } from '../WorkspaceData'
import { Composer, type ComposerHandle } from './Composer'
import { ConversationDialogs, type ConversationDialogTarget } from './ConversationDialogs'
import { MarkdownContent } from './MarkdownContent'
import { AssistantActivity, activityLabels } from './AssistantActivity'
import { SaveChatNoteDialog } from './SaveChatNoteDialog'
import { SourceReaderDrawer, type ReaderActionContext, type SourceReaderTarget } from './SourceReaderDrawer'

const time = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' })
const statusText = { pending: '等待生成…', streaming: '正在生成…', stopped: '已停止', failed: '回答失败', completed: '' }
const mobileSourcesQuery = '(max-width: 760px)'

function mergeMessages(saved: Message[], live: Message[], accepted: Message[], activeTurn?: string) {
  const rows = new Map(saved.map((message) => [message.id, message]))
  for (const message of live) {
    const stored = rows.get(message.id)
    const generating = activeTurn === message.turn_id
    const storedTerminal = stored && !['pending', 'streaming'].includes(stored.status)
    if (!storedTerminal || generating) rows.set(message.id, message)
  }
  for (const message of accepted) rows.set(message.id, message)
  return [...rows.values()].filter((message) => message.role !== 'system_summary')
    .sort((a, b) => a.sequence_no - b.sequence_no)
}

const ChatAnswerMarkdown = memo(function ChatAnswerMarkdown({ message, onSelectCitation }: {
  message: Message
  onSelectCitation: (messageId: string, citation: Citation) => void
}) {
  return (
    <MarkdownContent citations={message.citations} onSelectCitation={(citation) => onSelectCitation(message.id, citation)}>
      {message.content}
    </MarkdownContent>
  )
})

export default function LiveChat({ id, searchButton }: { id: string; searchButton?: ReactNode }) {
  const { user, libraries = [], chat, revisions, refresh, notify, report, requireLogin } = useWorkspace()
  const navigate = useNavigate()
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [savingModel, setSavingModel] = useState(false)
  const modelSave = useRef({ pending: false, revision: 0 })
  const [history, setHistory] = useState<Page<Message> | null>(null)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [olderError, setOlderError] = useState('')
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState('')
  const [dialogTarget, setDialogTarget] = useState<ConversationDialogTarget | null>(null)
  const [mobile, setMobile] = useState(() => window.matchMedia?.(mobileSourcesQuery).matches ?? false)
  const [panel, setPanel] = useDisclosurePreference(`knowloop:${user?.id ?? 'guest'}:chat-sources`, true)
  const [drawer, setDrawer] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [readerTarget, setReaderTarget] = useState<SourceReaderTarget | null>(null)
  const [noteTarget, setNoteTarget] = useState<{ message: Message; scope: Scope; title: string } | null>(null)
  const [editing, setEditing] = useState<{ id: string; content: string } | null>(null)
  const composer = useRef<ComposerHandle>(null)
  const sourceTrigger = useRef<HTMLElement | null>(null)
  const scroll = useRef<HTMLDivElement>(null)
  const follow = useRef(true)
  const olderRequest = useRef<AbortController | null>(null)
  const exportRequest = useRef<AbortController | null>(null)
  const scrollAnchor = useRef<{ element: HTMLElement; top: number; trigger?: HTMLButtonElement } | null>(null)
  const active = chat.activeFor(id)
  const busy = chat.isBusy(id)
  const cached = chat.cached[id] ?? []
  const accepted = chat.accepted[id] ?? []
  const oldest = history?.next_cursor ? history.items.at(-1)?.sequence_no : undefined
  const live = oldest === undefined ? cached : cached.filter((message) =>
    message.sequence_no >= oldest || message.turn_id === active?.turnId)
  const messages = mergeMessages(history?.items ?? [], live, accepted, active?.turnId)
  const latest = messages.at(-1)
  const focused = messages.find((message) => message.id === selectedId) ?? [...messages].reverse().find((message) => message.role === 'assistant')
  const latestIsTerminal = latest?.role === 'assistant' && ['completed', 'failed', 'stopped'].includes(latest.status)
  const recovering = [...new Set(messages.filter((message) => !busy && message.role === 'assistant'
    && ['pending', 'streaming'].includes(message.status) && message.turn_id !== active?.turnId
    && !chat.recoveryErrors[message.turn_id!]).map((message) => message.turn_id).filter(Boolean))].join(',')

  useEffect(() => {
    if (!user || !recovering) return
    const cleanups = recovering.split(',').map((turnId) => chat.recover(id, turnId))
    return () => { for (const cleanup of cleanups) cleanup() }
  }, [id, user, recovering, chat.recover])

  useEffect(() => {
    if (!user) return
    const controller = new AbortController()
    const revision = modelSave.current.revision
    void Promise.all([api.conversation(id), api.messages(id, undefined, 'desc', controller.signal)]).then(
      ([conversation, page]) => {
        if (controller.signal.aborted) return
        setConversation((current) => current?.id === id && revision !== modelSave.current.revision
          ? { ...conversation, llm_connection_id: current.llm_connection_id } : conversation)
        setHistory((current) => {
          const oldest = page.items.at(-1)
          if (!page.next_cursor || !oldest || !current?.items.some((message) => message.id === oldest.id)) return page
          const earlier = current.items.filter((message) => message.sequence_no < oldest.sequence_no)
          return { items: [...page.items, ...earlier], next_cursor: current.next_cursor }
        })
        chat.acknowledgeSaved(id, page.items)
        setError('')
      },
      (error) => {
        if (controller.signal.aborted) return
        setError(errorMessage(error))
        report(error)
      },
    )
    return () => controller.abort()
  }, [id, user, revisions.messages])

  async function chooseModel(connectionId: string | null) {
    if (!conversation || modelSave.current.pending || (conversation.llm_connection_id ?? null) === connectionId) return
    modelSave.current.pending = true
    setSavingModel(true)
    try {
      const updated = await api.updateConversation(id, { llm_connection_id: connectionId })
      modelSave.current.revision += 1
      setConversation(updated)
      refresh('conversations')
    } catch (failure) {
      report(failure)
      refresh('capabilities')
    } finally {
      modelSave.current.pending = false
      setSavingModel(false)
    }
  }

  useEffect(() => () => {
    olderRequest.current?.abort()
    exportRequest.current?.abort()
  }, [])

  async function loadOlder(trigger?: HTMLButtonElement) {
    const cursor = history?.next_cursor
    if (!cursor || olderRequest.current) return
    const controller = new AbortController()
    olderRequest.current = controller
    setLoadingOlder(true)
    setOlderError('')
    try {
      const page = await api.messages(id, cursor, 'desc', controller.signal)
      if (controller.signal.aborted) return
      const node = scroll.current
      if (node && (!follow.current || trigger === document.activeElement)) {
        const viewportTop = node.getBoundingClientRect().top
        const element = [...node.querySelectorAll<HTMLElement>('article.message')]
          .find((row) => row.getBoundingClientRect().bottom > viewportTop)
        if (element) {
          scrollAnchor.current = { element, top: element.getBoundingClientRect().top, trigger }
          follow.current = false
        }
      }
      setHistory((current) => current?.next_cursor === cursor
        ? { items: [...current.items, ...page.items], next_cursor: page.next_cursor }
        : current)
      chat.acknowledgeSaved(id, page.items)
    } catch (failure) {
      if (!controller.signal.aborted) {
        setOlderError(errorMessage(failure))
        if (failure instanceof ApiError && failure.status === 401) report(failure)
      }
    } finally {
      olderRequest.current = null
      if (!controller.signal.aborted) setLoadingOlder(false)
    }
  }

  useLayoutEffect(() => {
    const anchor = scrollAnchor.current
    scrollAnchor.current = null
    if (!anchor?.element.isConnected || !scroll.current) return
    scroll.current.scrollTop += anchor.element.getBoundingClientRect().top - anchor.top
    if (anchor.trigger && (document.activeElement === anchor.trigger || !anchor.trigger.isConnected && document.activeElement === document.body)) {
      anchor.element.focus({ preventScroll: true })
    }
  }, [history, loadingOlder])

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
    const media = window.matchMedia?.(mobileSourcesQuery)
    if (!media) return
    const change = () => {
      setMobile(media.matches)
      if (!media.matches) setDrawer(false)
    }
    media.addEventListener?.('change', change)
    return () => media.removeEventListener?.('change', change)
  }, [])

  const openSource = useCallback((citation: Citation) => {
    if (citation.availability !== 'available') {
      notify('此来源已不可访问。')
      return
    }
    setReaderTarget({
      materialVersionId: citation.material_version_id,
      materialId: citation.material_id,
      knowledgeBaseId: citation.knowledge_base_id,
      knowledgeBaseName: libraries.find((library) => library.id === citation.knowledge_base_id)?.name,
      chunkId: citation.chunk_id,
      title: citation.title,
      quote: citation.quote,
      locator: citation.locator,
      locationLabel: citation.location_label,
      sourceUrl: citation.source_url,
    })
  }, [libraries, notify])

  const openCitations = useCallback((messageId: string, citation: Citation) => {
    setSelectedId(messageId)
    if (mobile) {
      sourceTrigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
      setDrawer(true)
    } else setPanel(true)
    openSource(citation)
  }, [mobile, openSource, setPanel])

  const displayTitle = conversation?.title || live.find((item) => item.role === 'user')?.content.slice(0, 80) || '对话'

  async function exportChat() {
    if (exportRequest.current) return
    const controller = new AbortController()
    exportRequest.current = controller
    setExporting(true)
    try {
      const complete = !history || history.next_cursor
        ? mergeMessages(await allPages((cursor) => api.messages(id, cursor, 'asc', controller.signal)), cached, accepted, active?.turnId)
        : messages
      if (controller.signal.aborted) return
      downloadMarkdownFile(sanitizeMarkdownFilename(displayTitle), formatConversationMarkdown(displayTitle, complete))
      notify('已导出 Markdown 文件。')
    } catch (failure) {
      if (!controller.signal.aborted) report(failure)
    } finally {
      exportRequest.current = null
      if (!controller.signal.aborted) setExporting(false)
    }
  }

  if (!user || (error && !conversation && !live.length)) {
    return (
      <div className="placeholder-page">
        <EmptyState title={user ? '无法打开会话' : '登录后查看会话'} description={error || '登录后可查看你的私有对话。'}
          action={user ? <Link className="return-link" to="/">返回首页</Link> : <Button onClick={requireLogin}>登录</Button>} />
      </div>
    )
  }

  const sources = <>
    {focused?.scope && (
      <section className="source-section">
        <span className="muted">本轮回答范围</span>
        <h3>{focused.scope.knowledge_base_name}</h3>
        <p>{[
          focused.scope.intent === 'summary' ? '摘要提炼' : null,
          focused.scope.selected_material_ids?.length ? `限定 ${focused.scope.selected_material_ids.length} 份资料` : null,
          focused.scope.answer_mode === 'with_general' ? '含通用知识补充' : '仅知识库',
        ].filter(Boolean).join(' · ')}</p>
      </section>
    )}
    <section className="source-section">
      <h3>已使用的资料</h3>
      {focused?.citations.length ? <div className="source-cards">
        {focused.citations.map((citation) => {
          const sourceUrl = safeExternalUrl(citation.source_url)
          return <div key={citation.id} className="source-card-item">
            <Button variant="secondary" className="source-card" onClick={() => openSource(citation)}>
              <span className="source-card-content">
                <span className="source-card-title"><span className="source-badge">{citation.source_label}</span><FileText size={16} /><span>{citation.title}</span></span>
                {citation.availability === 'available' && citation.quote && <span className="source-excerpt">{citation.quote}</span>}
                <small>{citation.availability === 'available' ? citation.location_label : '来源已不可用'}
                  {citation.availability === 'available' && citation.quote_status === 'unavailable' && ' · 历史引文不可用'}
                </small>
              </span>
            </Button>
            {sourceUrl && <a className="source-card-original" href={sourceUrl} target="_blank" rel="noopener noreferrer">打开原网页</a>}
          </div>
        })}
      </div> : <p>这轮还没有可查看的资料引用。</p>}
    </section>
  </>

  return (
    <div className="chat live-chat" data-sources={!mobile && panel}>
      <section className="chat-main" aria-label="会话">
        <header className="chat-header">
          <h1>{displayTitle}</h1>
          <span className="pill chat-privacy">仅限个人使用</span>
          {searchButton}
          <DropdownMenu
            label="会话操作"
            icon={<Ellipsis size={18} />}
            iconOnly
            items={[
              { label: '重命名会话', icon: <Pencil size={15} />, disabled: !conversation, onSelect: () => {
                if (conversation) setDialogTarget({ mode: 'rename', conversation })
              } },
              { label: '导出 Markdown', icon: <Download size={15} />, disabled: !messages.length || exporting, onSelect: () => void exportChat() },
              { label: '删除会话', icon: <Trash2 size={15} />, destructive: true, separatorBefore: true, onSelect: () => {
                setDialogTarget({ mode: 'delete', conversation: conversation ?? { id, title: displayTitle } })
              } },
            ]}
          />
          <Button variant="ghost" className="icon-button" aria-label={mobile ? '引用与来源' : panel ? '收起引用与来源' : '展开引用与来源'}
            aria-expanded={mobile ? drawer : panel} aria-controls={mobile ? 'live-sources-drawer' : 'live-sources'}
            onClick={(event) => {
              if (mobile) { sourceTrigger.current = event.currentTarget; setDrawer(true) }
              else setPanel(!panel)
            }}><PanelRight size={18} /></Button>
        </header>
        <div className="conversation" ref={scroll} tabIndex={0} aria-label="消息" onScroll={(event) => {
          const node = event.currentTarget
          follow.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 8
          if (!follow.current && node.scrollTop <= 96 && !olderError) void loadOlder()
        }}>
          <div className="conversation-inner">
            {history?.next_cursor && <div className="chat-older">
              <Button variant="ghost" size="sm" loading={loadingOlder} onClick={(event) => void loadOlder(event.currentTarget)}>
                {olderError ? '重试加载更早消息' : '加载更早消息'}
              </Button>
              {olderError && <p role="alert">{olderError}</p>}
            </div>}
            {!messages.length && <p className="muted">{conversation ? '还没有消息。选好知识库后开始提问。' : '正在读取会话…'}</p>}
            {messages.map((message) => {
              const isUser = message.role === 'user'
              const stopped = message.status === 'stopped' || message.status === 'failed'
              const modelFailure = message.status === 'failed' ? chatModelError(message.error_code) : undefined
              const phase = !isUser && ['pending', 'streaming'].includes(message.status)
                && active?.assistantId === message.id ? active.phase : null
              const status = phase ? activityLabels[phase] : ['pending', 'streaming'].includes(message.status)
                ? '正在确认生成状态…' : statusText[message.status]
              const isLastTurn = Boolean(message.turn_id && message.turn_id === latest?.turn_id)
              return (
                <article key={message.id} className={`message ${isUser ? 'user-message' : 'ai-message'}`} tabIndex={-1}
                  data-editing={editing?.id === message.id || undefined} aria-label={isUser ? '用户消息' : 'AI 消息'}>
                  <div className="message-meta">
                    {isUser ? <span className="message-avatar">你</span> : <AssistantActivity phase={phase} />}
                    <span>{isUser ? '你' : '知循'}</span><time dateTime={message.created_at}>{time.format(new Date(message.created_at))}</time>
                  </div>
                  {message.scope && <span className="pill"><Folder size={13} />{scopeSummaryText(message.scope)}</span>}
                  {isUser ? (
                    editing?.id === message.id && message.turn_id ? (
                      <form className="user-bubble chat-question-editor" onSubmit={(event) => {
                        event.preventDefault()
                        if (!editing.content.trim() || busy) return
                        void chat.edit(id, message.turn_id!, editing.content.trim(), () => setEditing(null))
                      }}>
                        <Textarea label="编辑问题" rows={3} maxLength={8000} autoFocus
                          value={editing.content} disabled={busy}
                          onChange={(event) => setEditing({ id: message.id, content: event.target.value })} />
                        <div className="form-actions">
                          <Button type="submit" size="sm" disabled={!editing.content.trim()} loading={busy}>保存并重新生成</Button>
                          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(null)}>取消</Button>
                        </div>
                      </form>
                    ) : (
                      <>
                        <div className="user-bubble">{message.content}</div>
                        {isLastTurn ? latestIsTerminal && (
                          <Button type="button" variant="ghost" size="sm" className="chat-question-action" disabled={busy}
                            onClick={() => setEditing({ id: message.id, content: message.content })}><Pencil size={14} /> 编辑并重发</Button>
                        ) : (
                          <Button type="button" variant="ghost" size="sm" className="chat-question-action"
                            onClick={() => composer.current?.editAsNew(message.content, message.scope)}><Pencil size={14} /> 编辑为新提问</Button>
                        )}
                      </>
                    )
                  ) : (
                    <>
                      <div className="answer">
                        {message.content && <ChatAnswerMarkdown message={message} onSelectCitation={openCitations} />}
                        {message.turn_id && chat.recoveryErrors[message.turn_id] && ['pending', 'streaming'].includes(message.status)
                          ? <p className="muted" role="status">暂时无法确认生成状态。<Button size="sm" variant="secondary" onClick={() => chat.recover(id, message.turn_id!)}>重新查询状态</Button></p>
                          : <p className="muted activity-status" role="status" aria-live="polite">{status}{message.status === 'failed' && `。${modelFailure ?? '请检查后重试。'}`}</p>}
                        {message.model_profile && <small className="muted">本轮配置模型 · {message.model_profile}</small>}
                      </div>
                      <div className="citation-list">
                        {message.citations.map((citation) => (
                          <Button key={citation.id} size="sm" variant="secondary" className="citation" onClick={() => openCitations(message.id, citation)}>
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
                            disabled={busy && isLastTurn}
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
                        {message === latest && message.turn_id && !modelFailure && (stopped || message.status === 'completed') && <Button size="sm" variant="secondary" disabled={busy}
                          onClick={() => void chat.retry(id, message.turn_id!)}>{stopped ? '重试本轮' : '重新生成'}</Button>}
                        {message === latest && message.turn_id && modelFailure && <>
                          <Button size="sm" variant="secondary" disabled={busy || savingModel || !conversation}
                            onClick={() => void chat.retry(id, message.turn_id!, true)}>用当前模型重试</Button>
                          <Link to="/settings#model-settings-title">检查模型连接</Link>
                        </>}
                        {message === latest && message.turn_id && message.status === 'streaming' && !active && !busy && <Button size="sm" variant="secondary" onClick={() => void chat.stop(id, message.turn_id!)}>停止生成</Button>}
                      </div>
                    </>
                  )}
                </article>
              )
            })}
          </div>
        </div>
        <div className="chat-footer"><Composer ref={composer} conversationId={id}
          modelId={conversation ? conversation.llm_connection_id ?? null : undefined} savingModel={savingModel}
          onModelChange={(modelId) => void chooseModel(modelId)}
          preferredLibrary={latest?.scope?.knowledge_base_id ?? conversation?.project_knowledge_base_id ?? undefined} /></div>
      </section>
      {mobile ? (
        <Dialog open={drawer} onOpenChange={(open) => { if (!open) setDrawer(false) }}>
          <DialogContent id="live-sources-drawer" className="chat-source-drawer" closeLabel="关闭引用抽屉"
            title="引用与来源"
            description="本轮回答的范围与引用资料。"
            onCloseAutoFocus={(event) => {
              if (!sourceTrigger.current?.isConnected) return
              event.preventDefault()
              sourceTrigger.current.focus()
            }}>
            {sources}
          </DialogContent>
        </Dialog>
      ) : (
        <>
          <aside className="source-panel" id="live-sources" aria-label="引用与来源" inert={!panel} aria-hidden={!panel}>
            <div className="source-panel-inner"><h2>引用与来源</h2>{sources}</div>
          </aside>
        </>
      )}
      <SourceReaderDrawer open={Boolean(readerTarget)} target={readerTarget} onOpenChange={(open) => { if (!open) setReaderTarget(null) }}
        onAskFollowUp={(context: ReaderActionContext) => {
          const prompt = `请根据以下资料摘录回答我的问题。\n\n来源：${context.title} · ${context.locationLabel}\n\n${context.excerpt}\n\n我的问题：`
          void navigate('/', { state: { preferredLibrary: context.knowledgeBaseId, initialDraft: prompt, initialMaterialIds: [context.materialId] } })
        }} />
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
          refresh?.('notes')
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
        onRenamed={(updated) => { setConversation(updated); refresh?.('conversations') }}
        onBeforeDelete={(deletedId) => chat?.evictConversation?.(deletedId)}
        onDeleted={(deletedId) => { chat?.evictConversation?.(deletedId); refresh?.('conversations', 'overview'); void navigate('/', { replace: true }) }}
        report={report}
        notify={notify}
      />
    </div>
  )
}
