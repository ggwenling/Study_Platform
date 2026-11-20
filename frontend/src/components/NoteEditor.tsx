import { memo, useDeferredValue, useEffect, useState, type KeyboardEvent } from 'react'
import type { Citation } from '@/api/types'
import { Button } from '@/components/arc/button/button'
import { Textarea } from '@/components/arc/textarea/textarea'
import { MarkdownContent } from './MarkdownContent'

const mobileQuery = '(max-width: 760px)'
const maxLength = 200000
const views = [
  { value: 'edit', label: '编辑' },
  { value: 'split', label: '分栏预览' },
  { value: 'preview', label: '预览' },
] as const
const MarkdownPreview = memo(MarkdownContent)

export function NoteEditor({ content, onContentChange, citations, onSelectCitation }: {
  content: string
  onContentChange: (content: string) => void
  citations?: Citation[]
  onSelectCitation?: (citation: Citation) => void
}) {
  const [view, setView] = useState<typeof views[number]['value']>('split')
  const [mobile, setMobile] = useState(() => window.matchMedia?.(mobileQuery).matches ?? false)
  const displayed = mobile && view === 'split' ? 'edit' : view
  const preview = useDeferredValue(content)

  useEffect(() => {
    const media = window.matchMedia?.(mobileQuery)
    if (!media) return
    const change = () => setMobile(media.matches)
    media.addEventListener('change', change)
    return () => media.removeEventListener('change', change)
  }, [])

  function indent(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Tab' || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return
    event.preventDefault()
    const field = event.currentTarget
    const { selectionStart, selectionEnd } = field
    const next = `${content.slice(0, selectionStart)}  ${content.slice(selectionEnd)}`
    if (next.length > maxLength) return
    onContentChange(next)
    requestAnimationFrame(() => {
      if (field.value === next) field.setSelectionRange(selectionStart + 2, selectionStart + 2)
    })
  }

  return <div className="note-editor">
    <div className="note-editor-toolbar" role="group" aria-label="笔记编辑视图">
      {views.filter(({ value }) => value !== 'split' || !mobile).map(({ value, label }) => (
        <Button key={value} type="button" size="sm" variant={displayed === value ? 'secondary' : 'ghost'}
          aria-pressed={displayed === value} onClick={() => setView(value)}>{label}</Button>
      ))}
    </div>
    <div className="note-editor-body" data-view={displayed}>
      {displayed !== 'preview' && <div className="note-editor-input">
        <Textarea label="Markdown 内容" name="content_md" value={content} rows={12}
          maxLength={maxLength} placeholder="支持 Markdown 与 $...$ 数学公式…"
          onChange={(event) => onContentChange(event.target.value)} onKeyDown={indent} />
        <small className="muted">{content.length.toLocaleString('zh-CN')} / 200,000 字</small>
      </div>}
      {displayed !== 'edit' && <section className="note-editor-preview answer" aria-label="Markdown 预览">
        {preview.trim()
          ? <div className="markdown"><MarkdownPreview citations={citations} onSelectCitation={onSelectCitation}>{preview}</MarkdownPreview></div>
          : <p className="muted">输入内容后在这里预览。</p>}
      </section>}
    </div>
  </div>
}
