import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { ArrowDownToLine, ChevronLeft, ChevronRight, ExternalLink, MessageCircle, NotebookPen } from 'lucide-react'
import { api } from '@/api/client'
import type { MaterialReader, MaterialReaderChunk } from '@/api/types'
import { errorMessage, locatorLabel, safeExternalUrl } from '@/api/ui'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { DownloadMaterial, MaterialPage } from './ProtectedMaterial'
import { SaveChatNoteDialog, type SaveChatNoteRequest } from './SaveChatNoteDialog'
import { useWorkspace } from '../WorkspaceData'
import './SourceReaderDrawer.css'

export type SourceReaderTarget = {
  materialVersionId: string
  materialId?: string
  knowledgeBaseId?: string
  knowledgeBaseName?: string
  chunkId?: string | null
  title?: string
  quote?: string | null
  locator?: Record<string, unknown>
  locationLabel?: string
  sourceUrl?: string | null
}

export type ReaderActionContext = {
  knowledgeBaseId: string
  materialId: string
  materialVersionId: string
  chunkId?: string
  title: string
  locationLabel: string
  excerpt: string
}

function highlightedText(text: string, quote: string) {
  const matchAt = quote ? text.indexOf(quote) : -1
  if (matchAt < 0) return text
  return <>{text.slice(0, matchAt)}<mark>{text.slice(matchAt, matchAt + quote.length)}</mark>{text.slice(matchAt + quote.length)}</>
}

function extractionLabel(locator: Record<string, unknown>, sourceUrl?: string | null) {
  if (locator.extraction === 'vlm') return '视觉解析（VLM）'
  if (locator.ocr === true || locator.extraction === 'ocr') return 'OCR'
  return sourceUrl ? '网页剪藏' : '文字提取'
}

function currentLocation(chunk: MaterialReaderChunk | undefined, target: SourceReaderTarget) {
  return chunk?.location_label || target.locationLabel || locatorLabel(chunk?.locator ?? target.locator ?? {})
}

export function SourceReaderDrawer({ open, onOpenChange, target, onAskFollowUp }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  target: SourceReaderTarget | null
  onAskFollowUp?: (context: ReaderActionContext) => void
}) {
  const { libraries = [], notify } = useWorkspace()
  const navigate = useNavigate()
  const [mobile, setMobile] = useState(() => window.matchMedia?.('(max-width: 760px)').matches ?? false)
  const [loaded, setLoaded] = useState<{ key: string; data?: MaterialReader; error?: string; loading: boolean }>({ key: '', loading: false })
  const [viewState, setViewState] = useState<{ key: string; view: 'context' | 'page' }>({ key: '', view: 'context' })
  const [pageState, setPageState] = useState<{ key: string; page: number }>({ key: '', page: 1 })
  const [pageDraft, setPageDraft] = useState<{ key: string; value: string }>({ key: '', value: '' })
  const [selectedText, setSelectedText] = useState('')
  const [loadMoreBusy, setLoadMoreBusy] = useState(false)
  const [loadMoreError, setLoadMoreError] = useState('')
  const [retry, setRetry] = useState(0)
  const [saveNoteOpen, setSaveNoteOpen] = useState(false)
  const chunksRef = useRef(new Map<string, HTMLElement>())
  const copyRef = useRef<HTMLDivElement>(null)
  const contextRequest = useRef<AbortController | null>(null)
  const scrollTargetKey = useRef('')
  const readerKey = open && target ? `${target.materialVersionId}:${target.chunkId ?? ''}` : ''
  const request = loaded.key === readerKey ? loaded : null
  const reader = request?.data
  const readerReady = Boolean(reader)
  const readerTitle = reader?.title || target?.title || '原文阅读'
  const targetChunkId = reader?.target_chunk_id ?? target?.chunkId ?? reader?.chunks.find((chunk) => chunk.is_target)?.id
  const targetChunk = reader?.chunks.find((chunk) => chunk.id === targetChunkId) ?? reader?.chunks.find((chunk) => chunk.is_target)
  const quote = reader?.target_quote || target?.quote || ''
  const locationLabel = target ? currentLocation(targetChunk, target) : '—'
  const sourceUrl = safeExternalUrl(reader?.source_url ?? target?.sourceUrl)
  const view = viewState.key === readerKey ? viewState.view : 'context'
  const maxPages = reader?.preview_page_count ?? reader?.page_count ?? 0
  const locatorPage = typeof target?.locator?.page === 'number' ? target.locator.page : undefined
  const initialPage = reader?.target_page ?? locatorPage ?? 1
  const page = pageState.key === readerKey ? pageState.page : initialPage
  const pageInput = pageDraft.key === readerKey ? pageDraft.value : String(page)
  const excerpt = selectedText || quote || targetChunk?.text || ''
  const knowledgeBaseId = reader?.knowledge_base_id || target?.knowledgeBaseId
  const materialId = reader?.material_id || target?.materialId
  const libraryName = target?.knowledgeBaseName || libraries.find((library) => library.id === knowledgeBaseId)?.name || '目标知识库'
  const currentAction: ReaderActionContext | null = knowledgeBaseId && materialId && target ? {
    knowledgeBaseId,
    materialId,
    materialVersionId: reader?.version_id || target.materialVersionId,
    chunkId: targetChunkId,
    title: readerTitle,
    locationLabel,
    excerpt,
  } : null
  const noteContent = currentAction
    ? `来源：${readerTitle} · ${locationLabel}${sourceUrl ? `\n原网页：${sourceUrl}` : ''}\n\n${excerpt}`
    : ''

  useEffect(() => {
    const media = window.matchMedia?.('(max-width: 760px)')
    if (!media) return
    const change = () => setMobile(media.matches)
    change()
    media.addEventListener?.('change', change)
    return () => media.removeEventListener?.('change', change)
  }, [])

  useEffect(() => {
    if (!open || !target) {
      setLoaded({ key: '', loading: false })
      setSelectedText('')
      return
    }
    const controller = new AbortController()
    setLoaded({ key: readerKey, loading: true })
    setLoadMoreError('')
    void api.materialReader(target.materialVersionId, target.chunkId, 3, controller.signal).then(
      (data) => { if (!controller.signal.aborted) setLoaded({ key: readerKey, data, loading: false }) },
      (error) => { if (!controller.signal.aborted) setLoaded({ key: readerKey, error: errorMessage(error), loading: false }) },
    )
    return () => controller.abort()
  }, [open, readerKey, retry])

  useEffect(() => () => contextRequest.current?.abort(), [readerKey])

  useEffect(() => {
    setLoadMoreBusy(false)
    setLoadMoreError('')
  }, [readerKey])

  useEffect(() => {
    if (!open || view !== 'context' || !readerReady || !targetChunkId) return
    const key = `${readerKey}:${targetChunkId}`
    if (scrollTargetKey.current === key) return
    const targetNode = chunksRef.current.get(targetChunkId)
    if (!targetNode) return
    targetNode.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
    scrollTargetKey.current = key
  }, [open, view, readerKey, readerReady, targetChunkId])

  function rememberSelection() {
    const selection = window.getSelection()
    const text = selection?.toString().trim() ?? ''
    setSelectedText(text && selection?.anchorNode && copyRef.current?.contains(selection.anchorNode) ? text : '')
  }

  async function loadMore(direction: 'prev' | 'next') {
    if (!reader || !target || loadMoreBusy) return
    const boundary = direction === 'prev' ? reader.chunks[0] : reader.chunks.at(-1)
    if (!boundary) return
    const controller = new AbortController()
    contextRequest.current?.abort()
    contextRequest.current = controller
    setLoadMoreBusy(true)
    setLoadMoreError('')
    try {
      const page = await api.materialReader(target.materialVersionId, boundary.id, 3, controller.signal)
      if (controller.signal.aborted) return
      setLoaded((current) => {
        if (current.key !== readerKey || !current.data) return current
        const chunks = new Map([...current.data.chunks, ...page.chunks].map((chunk) => [chunk.id, chunk]))
        const targetId = current.data.target_chunk_id ?? target.chunkId
        const merged = [...chunks.values()].sort((a, b) => a.ordinal - b.ordinal)
          .map((chunk) => ({ ...chunk, is_target: targetId ? chunk.id === targetId : chunk.is_target }))
        return { key: current.key, loading: false, data: {
          ...current.data,
          chunks: merged,
          has_prev: direction === 'prev' ? page.has_prev : current.data.has_prev,
          has_next: direction === 'next' ? page.has_next : current.data.has_next,
        } }
      })
    } catch (error) {
      if (!controller.signal.aborted) setLoadMoreError(errorMessage(error))
    } finally {
      if (!controller.signal.aborted) setLoadMoreBusy(false)
    }
  }

  function choosePage(next: number) {
    if (next < 1 || next > maxPages) return
    setPageState({ key: readerKey, page: next })
    setPageDraft({ key: readerKey, value: String(next) })
  }

  function goToPage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const next = Number(pageInput)
    if (Number.isInteger(next) && next >= 1 && next <= maxPages) choosePage(next)
  }

  async function saveExcerpt(request: SaveChatNoteRequest) {
    if (!currentAction || !noteContent) return
    const note = 'noteId' in request
      ? await api.studio.appendNote(request.noteId, { content_md: noteContent, section_heading: request.sectionHeading || undefined })
      : await api.studio.createNote(knowledgeBaseId!, { title: request.title, content_md: noteContent, note_type: 'summary' })
    setSaveNoteOpen(false)
    notify(`已保存至「${note.title}」研究笔记。`, {
      action: { label: '查看笔记', onClick: () => void navigate(`/knowledge?${new URLSearchParams({ id: knowledgeBaseId!, note: note.id })}`) },
    })
  }

  const sourceLocation = target ? currentLocation(targetChunk, target) : ''
  const extraction = extractionLabel(targetChunk?.locator ?? target?.locator ?? {}, reader?.source_url ?? target?.sourceUrl)
  const launchFrom = mobile
    ? { x: window.innerWidth / 2, y: window.innerHeight }
    : { x: window.innerWidth, y: window.innerHeight / 2 }

  return <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent id="source-reader-dialog" className="source-reader-dialog" title={readerTitle} description={sourceLocation} closeLabel="关闭原文阅读器" launchFrom={launchFrom}>
        <div className="source-reader">
          <div className="source-reader-meta">
            <span className="source-reader-location">{sourceLocation || '资料上下文'}</span>
            <span className="source-reader-extraction">{extraction}</span>
            {sourceUrl && <a href={sourceUrl} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} />打开原网页</a>}
            {reader?.content_url && <DownloadMaterial version={reader.version_id} title={reader.title} />}
          </div>
          {reader?.can_render_pages && maxPages > 0 && <div className="source-reader-tabs" role="tablist" aria-label="原文视图">
            <Button type="button" size="sm" variant={view === 'context' ? 'primary' : 'secondary'} role="tab" aria-selected={view === 'context'} onClick={() => setViewState({ key: readerKey, view: 'context' })}>原文上下文</Button>
            <Button type="button" size="sm" variant={view === 'page' ? 'primary' : 'secondary'} role="tab" aria-selected={view === 'page'} onClick={() => setViewState({ key: readerKey, view: 'page' })}>原页图像</Button>
          </div>}
          {request?.loading && <p className="source-reader-status" role="status">正在读取原文上下文…</p>}
          {request?.error && <p className="source-reader-error" role="alert">{request.error} <Button type="button" size="sm" variant="secondary" onClick={() => setRetry((value) => value + 1)}>重试</Button></p>}
          {reader && view === 'context' && <>
            <div className="source-reader-copy" ref={copyRef} onMouseUp={rememberSelection} onKeyUp={rememberSelection}>
              {reader.has_prev && <Button type="button" size="sm" variant="ghost" loading={loadMoreBusy} disabled={loadMoreBusy} onClick={() => void loadMore('prev')}><ChevronLeft size={15} />加载前文</Button>}
              {reader.chunks.map((chunk) => <article key={chunk.id} ref={(node) => { if (node) chunksRef.current.set(chunk.id, node); else chunksRef.current.delete(chunk.id) }} className="source-reader-chunk" data-target={chunk.id === targetChunkId || undefined}>
                <header><span>{chunk.location_label || locatorLabel(chunk.locator)}</span>{chunk.id === targetChunkId && <strong>命中片段</strong>}</header>
                {chunk.id === targetChunkId && quote && !chunk.text.includes(quote) && <blockquote className="source-reader-citation-snapshot"><small>引用快照</small><p>{quote}</p></blockquote>}
                <p>{chunk.id === targetChunkId ? highlightedText(chunk.text, quote) : chunk.text}</p>
              </article>)}
              {reader.has_next && <Button type="button" size="sm" variant="ghost" loading={loadMoreBusy} disabled={loadMoreBusy} onClick={() => void loadMore('next')}><ChevronRight size={15} />加载后文</Button>}
              {loadMoreError && <p role="alert" className="source-reader-error">{loadMoreError}</p>}
            </div>
            <footer className="source-reader-actions">
              {onAskFollowUp && currentAction && <Button type="button" variant="secondary" onClick={() => onAskFollowUp(currentAction)}><MessageCircle size={16} />针对此段追问</Button>}
              {currentAction && <Button type="button" variant="secondary" disabled={!excerpt} onClick={() => setSaveNoteOpen(true)}><NotebookPen size={16} />摘录到笔记</Button>}
            </footer>
          </>}
          {reader && view === 'page' && <div className="source-reader-page-view">
            <form className="source-reader-page-controls" onSubmit={goToPage}>
              <Button type="button" size="sm" variant="secondary" aria-label="上一页" disabled={page <= 1} onClick={() => choosePage(page - 1)}><ChevronLeft size={16} /></Button>
              <span>第</span>
              <input aria-label="页码" type="number" min={1} max={maxPages} value={pageInput} onChange={(event) => setPageDraft({ key: readerKey, value: event.target.value })} />
              <span>页 / {maxPages} 页</span>
              <Button type="submit" size="sm" variant="secondary">跳转</Button>
              <Button type="button" size="sm" variant="secondary" aria-label="下一页" disabled={page >= maxPages} onClick={() => choosePage(page + 1)}><ChevronRight size={16} /></Button>
            </form>
            <div className="source-reader-page-image"><MaterialPage key={`${reader.version_id}:${page}`} version={reader.version_id} page={page} title={`${reader.title} · 第 ${page} 页`} /></div>
          </div>}
        </div>
      </DialogContent>
    </Dialog>
    {saveNoteOpen && currentAction && <SaveChatNoteDialog
      libraryId={knowledgeBaseId!}
      libraryName={libraryName}
      titleSuggestion={`${readerTitle} · ${locationLabel}`.slice(0, 255)}
      description={`将这段原文摘录保存到「${libraryName}」研究笔记。`}
      contentLabel="原文摘录"
      content={noteContent}
      onClose={() => setSaveNoteOpen(false)}
      onSave={saveExcerpt}
    />}
  </>
}
