import { useEffect, useEffectEvent, useId, useRef, useState } from 'react'
import { FileText, FolderOpen, Layers, MessageCircle, NotebookPen, Search, SquarePen } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { api } from '@/api/client'
import { ApiError } from '@/api/stream'
import type { Page, SearchItem } from '@/api/types'
import { errorMessage, locatorLabel } from '@/api/ui'
import { Button } from '@/components/arc/button/button'
import { Input } from '@/components/arc/input/input'
import { useWorkspace } from '../WorkspaceData'

const kind = {
  knowledge_base: { label: '知识库', icon: Layers },
  material: { label: '资料', icon: FileText },
  fragment: { label: '资料正文', icon: FileText },
  note: { label: '研究笔记', icon: NotebookPen },
  conversation: { label: '对话', icon: MessageCircle },
}

const shortcuts = [
  { id: 'new', title: '新对话', path: '/', icon: SquarePen },
  { id: 'library', title: '打开知识库', path: '/knowledge', icon: FolderOpen },
]

type SearchOption = { id: string; title: string; path: string; icon: typeof Search; details?: string; excerpt?: string | null; matchedTerms?: string[] }

type SearchState = { query: string; page?: Page<SearchItem>; loadingMore?: boolean; error?: string }

function highlightMatches(text: string, terms: string[] = []) {
  const uniqueTerms = [...new Set(terms.filter(Boolean))].sort((left, right) => right.length - left.length)
  if (!uniqueTerms.length) return text
  const escaped = uniqueTerms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const parts = text.split(new RegExp(`(${escaped.join('|')})`, 'gi'))
  const matched = new Set(uniqueTerms.map((term) => term.toLowerCase()))
  return parts.map((part, index) => matched.has(part.toLowerCase())
    ? <mark className="search-highlight" key={`${index}-${part}`}>{part}</mark>
    : part)
}

export function SearchPanel({ onSelect }: { onSelect: () => void }) {
  const { libraries, recent, report } = useWorkspace()
  const navigate = useNavigate()
  const listId = useId()
  const input = useRef<HTMLInputElement>(null)
  const request = useRef<AbortController | null>(null)
  const [query, setQuery] = useState('')
  const [composing, setComposing] = useState(false)
  const [state, setState] = useState<SearchState | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [active, setActive] = useState(0)
  const term = composing ? '' : query.trim()
  const idle = !query.trim()
  const current = state?.query === term ? state : null

  const firstPageFailure = useEffectEvent((error: unknown, submitted: string) => {
    if (error instanceof ApiError && error.status === 401) {
      onSelect()
      report(error)
    } else {
      setState({ query: submitted, error: errorMessage(error) })
    }
  })

  useEffect(() => {
    if (!term) return
    const controller = new AbortController()
    request.current = controller
    const timer = window.setTimeout(() => {
      void api.search(term, undefined, true, controller.signal).then(
        (page) => { if (!controller.signal.aborted) setState({ query: term, page }) },
        (error) => { if (!controller.signal.aborted) firstPageFailure(error, term) },
      )
    }, 180)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [term, attempt])

  async function loadMore(button: HTMLButtonElement) {
    const controller = request.current
    const page = current?.page
    if (!page?.next_cursor || !controller || controller.signal.aborted || current?.loadingMore) return
    setState({ query: term, page, loadingMore: true })
    try {
      const next = await api.search(term, page.next_cursor, true, controller.signal)
      if (!controller.signal.aborted) {
        if (!next.next_cursor && document.activeElement === button) input.current?.focus({ preventScroll: true })
        setState({ query: term, page: { ...next, items: [...page.items, ...next.items] } })
      }
    } catch (error) {
      if (controller.signal.aborted) return
      if (error instanceof ApiError && error.status === 401) { onSelect(); report(error) }
      else setState({ query: term, page, error: errorMessage(error) })
    }
  }

  const options: SearchOption[] = idle ? [...shortcuts, ...recent.slice(0, 5).map((item) => ({
    id: item.id, title: item.title || '未命名对话', path: `/chat/${item.id}`, icon: MessageCircle,
    details: libraries.find((library) => library.id === item.project_knowledge_base_id)?.name,
  }))] : (current?.page?.items ?? []).map((item) => {
    const params = new URLSearchParams({ id: item.knowledge_base_id ?? item.id })
    if (item.type === 'fragment' || item.type === 'note') params.set(item.type, item.id)
    return {
      id: `${item.type}-${item.id}`,
      title: item.title,
      path: item.type === 'conversation' ? `/chat/${item.id}` : `/knowledge?${params}`,
      icon: kind[item.type].icon,
      matchedTerms: item.matched_terms,
      details: [
        kind[item.type].label,
        item.type !== 'knowledge_base' && libraries.find((library) => library.id === item.knowledge_base_id)?.name,
        item.locator && locatorLabel(item.locator),
        item.retrieval_enabled === false && '已关闭 AI 检索',
      ].filter(Boolean).join(' · '),
      excerpt: item.excerpt,
    }
  })
  const selected = Math.min(active, options.length - 1)

  return <div className="search-panel">
    <div className="workspace-search-field">
      <Search size={18} aria-hidden="true" />
      <Input ref={input} label="搜索关键词" role="combobox" aria-autocomplete="list" aria-expanded={options.length > 0}
        aria-controls={listId} aria-activedescendant={selected >= 0 ? `${listId}-${selected}` : undefined}
        value={query} maxLength={200} placeholder="搜索术语、编号或标题…" autoComplete="off" spellCheck={false}
        onChange={(event) => {
          const next = event.target.value
          if (next.trim() !== query.trim()) { setState(null); setActive(0) }
          setQuery(next)
        }}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={() => setComposing(false)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || composing || selected < 0) return
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault()
            const next = (selected + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length
            setActive(next)
            document.getElementById(`${listId}-${next}`)?.scrollIntoView({ block: 'nearest' })
          } else if (event.key === 'Enter') {
            event.preventDefault()
            void navigate(options[selected].path)
            onSelect()
          }
        }} />
      <kbd>Ctrl K</kbd>
    </div>
    <ul id={listId} className="workspace-search-options" role="listbox" aria-label={idle ? '快捷操作和最近对话' : '搜索结果'} hidden={!options.length}>
      {options.map(({ id, title, path, icon: Icon, details, excerpt, matchedTerms }, index) => <li key={id} role="presentation">
        {idle && index === 0 && <div className="search-group-label" aria-hidden="true">快捷操作</div>}
        {idle && index === shortcuts.length && <div className="search-group-label" aria-hidden="true">最近对话</div>}
        <Link id={`${listId}-${index}`} role="option" aria-selected={selected === index} tabIndex={-1}
          className="search-result" to={path} onPointerMove={() => setActive(index)} onClick={onSelect}>
          <Icon size={17} aria-hidden="true" />
          <span className="search-result-text"><strong>{highlightMatches(title, matchedTerms)}</strong>
            {details && <small>{details}</small>}
            {excerpt && <small className="search-excerpt">{highlightMatches(excerpt, matchedTerms)}</small>}
          </span>
        </Link>
      </li>)}
    </ul>
    {term && !current && <p className="search-status" role="status">正在搜索…</p>}
    {current?.page && !current.page.items.length && <p className="search-status" role="status">暂无匹配结果。</p>}
    {current?.error && <p className="search-status error-text" role="alert">{current.error}</p>}
    {current?.page?.next_cursor
      ? <Button variant="secondary" size="sm" loading={current.loadingMore} onClick={(event) => void loadMore(event.currentTarget)}>{current.error ? '重试加载' : '加载更多'}</Button>
      : current?.error && <Button variant="secondary" size="sm" onClick={() => { setState(null); setAttempt((value) => value + 1) }}>重试搜索</Button>}
  </div>
}
