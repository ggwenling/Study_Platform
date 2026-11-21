import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Check, CircleDashed, Ellipsis, FileText, Search, TriangleAlert } from 'lucide-react'
import { api } from '@/api/client'
import type { Fragment, FragmentPage } from '@/api/types'
import { errorMessage, locatorLabel } from '@/api/ui'
import { Button } from '@/components/arc/button/button'
import { DatePicker } from '@/components/arc/date-picker/date-picker'
import { DropdownMenu } from '@/components/arc/dropdown-menu/dropdown-menu'
import { Input } from '@/components/arc/input/input'
import { Select } from '@/components/arc/select/select'
import { SortableDataTable } from '@/components/arc/sortable-data-table/sortable-data-table'
import type { DataColumn } from '@/components/arc/sortable-data-table/sortable-data-table'
import { Switch } from '@/components/arc/switch/switch'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { Textarea } from '@/components/arc/textarea/textarea'
import { useWorkspace } from '../WorkspaceData'

export const number = new Intl.NumberFormat('zh-CN')

const mimeTypes = [
  { value: 'all', label: '文件类型' },
  { value: 'application/pdf', label: 'PDF' },
  { value: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', label: 'Word' },
  { value: 'text/markdown', label: 'Markdown' },
  { value: 'text/plain', label: '文本' },
  { value: 'image/', label: '图片' },
]
const statuses = [
  { value: 'all', label: '处理状态' },
  { value: 'succeeded', label: '已完成' },
  { value: 'running', label: '处理中' },
  { value: 'failed', label: '失败' },
  { value: 'queued', label: '等待处理' },
  { value: 'cancelled', label: '已取消' },
]

function dateKey(value?: Date) {
  if (!value) return undefined
  return [value.getFullYear(), String(value.getMonth() + 1).padStart(2, '0'), String(value.getDate()).padStart(2, '0')].join('-')
}

type Scope = { material_id?: string; section_id?: string; label?: string }

export function LibraryFragments({ id, tags, day, setDay, scope, clearScope, revision, initialPage, onOpenSource }: {
  id: string; tags: string[]; day?: string; setDay: (day?: string) => void; scope: Scope;
  clearScope: () => void; revision: number;
  initialPage?: FragmentPage;
  onOpenSource: (fragment: Fragment) => void;
}) {
  const { report, notify } = useWorkspace()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [fileType, setFileType] = useState('all')
  const [status, setStatus] = useState('all')
  const [tag, setTag] = useState('all')
  const filterKey = JSON.stringify([debounced, fileType, status, tag, day, scope.material_id, scope.section_id])
  const [paging, setPaging] = useState({ filterKey, page: 1 })
  const [selection, setSelection] = useState<{ key: string; ids: string[] }>({ key: '', ids: [] })
  const page = filterKey === paging.filterKey ? paging.page : 1
  if (filterKey !== paging.filterKey) {
    setPaging({ filterKey, page: 1 })
    setSelection({ key: '', ids: [] })
  }
  const resultKey = `${filterKey}:${page}`
  const selected = selection.key === resultKey ? selection.ids : []
  const [view, setView] = useState<{ key: string; data?: FragmentPage; error?: string }>(() => ({ key: resultKey, data: initialPage }))
  const prefetchedKey = useRef(initialPage ? resultKey : null)
  const [reload, setReload] = useState(0)
  const [updating, setUpdating] = useState(false)
  const [editingFragment, setEditingFragment] = useState<Fragment | null>(null)
  const data = view.key === resultKey ? view.data : undefined

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query), 230)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    if (prefetchedKey.current === resultKey && revision === 0 && reload === 0) return
    prefetchedKey.current = null
    const controller = new AbortController()
    api.fragments(id, {
      q: debounced || undefined, file_type: fileType === 'all' ? undefined : fileType,
      status: status === 'all' ? undefined : status, tag: tag === 'all' ? undefined : tag.slice(4), day,
      material_id: scope.material_id, section_id: scope.section_id, page: String(page),
    }, controller.signal).then(
      (result) => {
        if (controller.signal.aborted) return
        if (!result.items.length && page > 1) {
          setPaging({ filterKey, page: Math.max(1, Math.ceil(result.total / result.page_size)) })
          return
        }
        setView({ key: resultKey, data: result })
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setView({ key: resultKey, error: errorMessage(error) })
      },
    )
    return () => controller.abort()
  }, [id, debounced, fileType, status, tag, day, scope.material_id, scope.section_id, page, filterKey, resultKey, revision, reload])

  async function update(ids: string[], enabled: boolean) {
    if (updating || !ids.length) return
    setUpdating(true)
    try {
      await api.setFragmentRetrieval(id, ids, enabled)
      setView((current) => current.data ? { ...current, data: {
        ...current.data, items: current.data.items.map((row) => ids.includes(row.id) ? { ...row, retrieval_enabled: enabled } : row),
      } } : current)
      notify(enabled ? '已启用片段检索。' : '已停用片段检索。')
    } catch (error) { report(error) } finally {
      setReload((value) => value + 1)
      setUpdating(false)
    }
  }

  const columns: DataColumn<Fragment>[] = [
    { key: 'text', label: '片段内容', width: '35%', sortable: false,
      render: (_, row) => <button type="button" className="fragment-text" onClick={() => onOpenSource(row)}>{row.text}</button> },
    { key: 'title', label: '来源文档', width: '22%', sortable: false,
      render: (_, row) => <button type="button" className="fragment-source" title={row.title} onClick={() => onOpenSource(row)}><FileText size={15} /><span>{row.title}</span></button> },
    { key: 'locator', label: '原文定位', width: '10%', sortable: false,
      render: (_, row) => locatorLabel(row.locator) },
    { key: 'estimated_tokens', label: '估算 Token', width: '10%', sortable: false,
      render: (_, row) => number.format(row.estimated_tokens) },
    { key: 'retrieval_enabled', label: '参与检索', width: '10%', sortable: false,
      render: (_, row) => <Switch aria-label={`片段参与检索，${row.title}，${locatorLabel(row.locator)}`} checked={row.retrieval_enabled} disabled={updating} onCheckedChange={(enabled) => void update([row.id], enabled)} /> },
    { key: 'status', label: '处理状态', width: '10%', sortable: false,
      render: (_, row) => {
        const Icon = row.status === 'succeeded' ? Check : row.status === 'failed' ? TriangleAlert : CircleDashed
        return <span className="library-status"><Icon size={13} />{statuses.find((option) => option.value === row.status)?.label ?? row.status}</span>
      } },
    { key: 'actions', label: '操作', width: '48px', sortable: false,
      render: (_, row) => <DropdownMenu label="片段操作" icon={<Ellipsis size={16} />} iconOnly items={[
        { label: '查看原文', onSelect: () => onOpenSource(row) },
        { label: '编辑片段', onSelect: () => setEditingFragment(row) },
        { label: '打开原文', onSelect: () => onOpenSource(row) },
      ]} /> },
  ]
  const visibleSelection = selected.filter((key) => data?.items.some((row) => row.id === key))
  return <>
    <div className="library-filters">
      <div className="library-search"><Search size={16} /><Input label="搜索片段" placeholder="搜索片段内容、来源文档或标签…" maxLength={200} value={query} onChange={(event) => setQuery(event.target.value)} /></div>
      <Select label="文件类型" value={fileType} onValueChange={setFileType} options={mimeTypes} />
      <Select label="处理状态" value={status} onValueChange={setStatus} options={statuses} />
      <Select label="标签" value={tag} onValueChange={setTag} options={[{ value: 'all', label: '标签' }, ...tags.map((value) => ({ value: `tag:${value}`, label: value }))]} />
      <DatePicker label="索引日期" placeholder="日期" locale="zh-CN" value={day ? new Date(`${day}T12:00:00`) : undefined} onChange={(value) => setDay(dateKey(value))} />
      <span className="library-total numeric">{data ? `共 ${number.format(data.total)} 条片段` : '正在读取…'}</span>
    </div>
    {(scope.label || day || visibleSelection.length > 0) && <div className="library-selection">
      {scope.label && <Button size="sm" variant="ghost" onClick={clearScope}>清除来源筛选 · {scope.label}</Button>}
      {day && <Button size="sm" variant="ghost" onClick={() => setDay(undefined)}>清除日期 · {day}</Button>}
      {visibleSelection.length > 0 && <>
        <span>已选 {visibleSelection.length} 条</span>
        <Button size="sm" variant="secondary" disabled={updating} onClick={() => void update(visibleSelection, true)}>启用检索</Button>
        <Button size="sm" variant="secondary" disabled={updating} onClick={() => void update(visibleSelection, false)}>停用检索</Button>
      </>}
    </div>}
    {view.key === resultKey && view.error ? <p role="alert" className="library-empty">{view.error} <Button size="sm" onClick={() => setReload((value) => value + 1)}>重试</Button></p>
      : <SortableDataTable rows={data?.items ?? []} columns={columns} rowKey="id" caption="知识库片段"
          selectable scrollTable showSelectionFooter={false} selectedKeys={visibleSelection} onSelectionChange={(ids) => setSelection({ key: resultKey, ids })}
          emptyMessage={data ? '没有匹配的片段。' : '正在读取片段…'} />}
    <FragmentEditDialog
      kbId={id}
      target={editingFragment ? { id: editingFragment.id, title: editingFragment.title, text: editingFragment.text } : null}
      onClose={() => setEditingFragment(null)}
      onSaved={(updated) => {
        setView((current) => current.data ? {
          ...current,
          data: {
            ...current.data,
            items: current.data.items.map((row) => row.id === updated.id ? updated : row),
          },
        } : current)
        setEditingFragment(null)
      }}
    />
    {data && data.total > data.page_size && <div className="library-pagination">
      <Button size="sm" variant="ghost" disabled={page === 1} onClick={() => { setSelection({ key: '', ids: [] }); setPaging({ filterKey, page: page - 1 }) }}>上一页</Button>
      <span className="numeric">{page} / {Math.ceil(data.total / data.page_size)}</span>
      <Button size="sm" variant="ghost" disabled={page * data.page_size >= data.total} onClick={() => { setSelection({ key: '', ids: [] }); setPaging({ filterKey, page: page + 1 }) }}>下一页</Button>
    </div>}
  </>
}


export function FragmentEditDialog({
  kbId,
  target,
  onClose,
  onSaved,
}: {
  kbId: string
  target: { id: string; title: string; text: string } | null
  onClose: () => void
  onSaved: (updated: Fragment) => void
}) {
  const { notify, report } = useWorkspace()
  const [saving, setSaving] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!target || saving || !api.workshop?.updateFragment) return
    const form = new FormData(event.currentTarget)
    const nextText = String(form.get('text') ?? '').trim()
    if (!nextText) return
    setSaving(true)
    try {
      const updated = await api.workshop.updateFragment(kbId, target.id, nextText)
      notify('片段已更新并重新生成向量索引。')
      onSaved(updated)
    } catch (error) {
      report(error)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={Boolean(target)} onOpenChange={(open) => { if (!open && !saving) onClose() }}>
      {target && (
        <DialogContent
          title="编辑片段内容"
          description={`来源：${target.title} · 保存后将自动更新分词与 1024 维向量索引`}
          closeLabel="关闭编辑片段"
        >
          <form key={target.id} className="library-dialog-stack" onSubmit={(event) => void submit(event)}>
            <Textarea
              label="片段文本"
              name="text"
              defaultValue={target.text}
              rows={8}
              maxLength={8000}
              required
            />
            <div className="form-actions">
              <Button type="submit" loading={saving} disabled={saving}>保存并重建向量</Button>
              <Button type="button" variant="ghost" disabled={saving} onClick={onClose}>取消</Button>
            </div>
          </form>
        </DialogContent>
      )}
    </Dialog>
  )
}
