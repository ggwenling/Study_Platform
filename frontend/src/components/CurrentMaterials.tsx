import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from '@/api/client'
import { allPages, errorMessage } from '@/api/ui'
import type { Material } from '@/api/types'
import { Accordion } from '@/components/arc/accordion/accordion'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { useWorkspace } from '../WorkspaceData'

const phases: Record<string, string> = {
  queued: '等待处理', parsing: '解析中', ocr: '识别文字中', vision: '视觉解析中', indexing: '建立索引中',
  ready: '可问答', failed: '处理失败', cancelled: '已取消',
}
const terminal = (row: Material) => ['ready', 'failed', 'cancelled'].includes(row.status)
const emptySelection: string[] = []

export function CurrentMaterials({
  libraryId,
  name,
  reveal = 0,
  summaryMode = false,
  selectedIds = emptySelection,
  onSelectedChange,
  onScopeStatus,
  onUseAll,
  scoped = false,
}: {
  libraryId: string
  name: string
  reveal?: number
  summaryMode?: boolean
  selectedIds?: string[]
  onSelectedChange?: (ids: string[]) => void
  onScopeStatus?: (libraryId: string, ids: string[], valid: boolean) => void
  onUseAll?: () => void
  scoped?: boolean
}) {
  const { revision, refresh, report, notify } = useWorkspace()
  const [data, setData] = useState<{ libraryId: string; rows: Material[] | null; error: string }>({ libraryId, rows: null, error: '' })
  const [attempt, setAttempt] = useState(0)
  const [checking, setChecking] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<Material | null>(null)
  const [open, setOpen] = useState(-1)
  const bodyRef = useRef<HTMLDivElement>(null)
  const [bodyHeight, setBodyHeight] = useState(0)
  const { rows, error } = data
  // Reset the scoped content before rendering the new library, while keeping the header mounted.
  if (data.libraryId !== libraryId) {
    setData({ libraryId, rows: null, error: '' })
    setConfirmDelete(null)
    setOpen(-1)
  }
  useEffect(() => { if (reveal) setOpen(0) }, [reveal])
  useLayoutEffect(() => {
    const body = bodyRef.current
    if (open < 0 || !body) return
    const measure = () => setBodyHeight(body.offsetHeight)
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(body)
    return () => observer.disconnect()
  }, [open])
  useEffect(() => {
    const controller = new AbortController()
    let timer: number | undefined
    async function load() {
      setChecking(true)
      try {
        const items = await allPages((cursor) => api.materials(libraryId, cursor, controller.signal))
        if (controller.signal.aborted) return
        setData((current) => current.libraryId === libraryId ? { libraryId, rows: items, error: '' } : current)
        setChecking(false)
        if (items.some((item) => !terminal(item))) timer = window.setTimeout(load, 2200)
      } catch (failure) {
        if (controller.signal.aborted) return
        setData((current) => current.libraryId === libraryId ? { ...current, error: errorMessage(failure) } : current)
        setChecking(false)
        report(failure)
      }
    }
    void load()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [libraryId, revision, attempt])

  const readyIds = new Set(rows?.filter((row) => row.status === 'ready').map((row) => row.id))
  const invalidIds = rows ? selectedIds.filter((id) => !readyIds.has(id)) : []
  const validScope = Boolean(rows && !error && !checking && !invalidIds.length && selectedIds.length > 0 && selectedIds.length <= 50)
  useLayoutEffect(() => {
    onScopeStatus?.(libraryId, selectedIds, validScope)
  }, [libraryId, selectedIds, validScope, onScopeStatus])

  const ready = rows?.filter((row) => row.status === 'ready').length ?? 0
  const pending = rows?.filter((row) => !terminal(row)).length ?? 0
  const failed = rows?.filter((row) => ['failed', 'cancelled'].includes(row.status)).length ?? 0
  const statusSummary = error
    ? '状态读取失败'
    : rows
      ? `${ready} 份可问答${pending ? ` · ${pending} 份处理中` : ''}${failed ? ` · ${failed} 份需处理` : ''}`
      : '正在读取…'

  const scopeSummary = scoped && (invalidIds.length > 0 || error) ? '已选资料不可用或状态读取失败，请检查范围'
    : selectedIds.length
    ? `本轮${summaryMode ? '摘要' : '提问'}已限定 ${selectedIds.length} 份资料`
    : scoped ? '资料范围未确认 · 请重新选择或使用全库'
    : summaryMode ? '摘要模式 · 请至少勾选 1 份资料' : statusSummary
  const clearScopeLabel = summaryMode ? '清除选择' : '使用全库'

  function toggleMaterial(id: string, checked: boolean) {
    if (!onSelectedChange) return
    if (checked) {
      if (!selectedIds.includes(id) && selectedIds.length < 50) onSelectedChange([...selectedIds, id])
    } else {
      onSelectedChange(selectedIds.filter((item) => item !== id))
    }
  }

  return <section className="current-materials" aria-label={`当前知识库资料：${name}`}>
    <Accordion openIndex={open} onOpenChange={setOpen} items={[{
      title: <span className="current-materials-title">
        <span>当前库资料 · {name}</span>
        <small role="status" title={scopeSummary} data-loading={!rows && !error && !summaryMode ? '' : undefined}>
          <span key={scopeSummary}>{scopeSummary}</span>
        </small>
      </span>,
      headerAction: scoped && <Button type="button" size="sm" variant="ghost" className="clear-material-scope"
        aria-label={clearScopeLabel} onClick={() => onUseAll?.()}>{clearScopeLabel}</Button>,
      // Keep the outgoing height while the accordion closes, even when a library switch replaces the rows with loading text.
      content: <div ref={bodyRef} className="current-materials-body" style={open < 0 ? { minBlockSize: bodyHeight } : undefined}>
        {error ? <div role="alert"><p>{error}</p>{rows && <p className="muted">下方保留上次读取结果，当前状态尚未确认。</p>}<Button type="button" size="sm" variant="secondary" onClick={() => setAttempt((value) => value + 1)}>重新读取状态</Button></div>
          : rows === null ? <p role="status">正在读取资料…</p>
            : !rows.length ? <p>这个知识库还没有资料，请从输入区添加附件或图片。</p>
              : !scoped && <p className="muted">{ready && (pending || failed) ? '本次提问仅使用已就绪资料，其余资料仍在处理或需重试。' : ready ? '资料已就绪，可以继续提问（可勾选特定资料聚焦范围）。' : pending ? '暂无可问答资料，请等待处理完成。' : '暂无可问答资料，请重试处理或重新上传。'}</p>}
        {scoped && <p className="muted">最多选择 50 份资料。{invalidIds.length > 0 ? '部分已选资料不可用，请移除或等待恢复。' : !selectedIds.length ? (summaryMode ? '请重新选择摘要资料。' : '请重新选择资料，或选择使用全库。') : ''}</p>}
        {invalidIds.map((id) => <p key={id} role="alert">已选资料不可用：{rows?.find((row) => row.id === id)?.title ?? '已移除的资料'} <Button type="button" size="sm" variant="ghost" onClick={() => toggleMaterial(id, false)}>移除</Button></p>)}
        {rows && rows.length > 0 && <ul className="current-materials-list">{rows.map((row) => <li key={row.id}>
          {onSelectedChange && row.status === 'ready' && (
            <input
              type="checkbox"
              className="material-scope-checkbox"
              aria-label={`限定资料：${row.title}`}
              checked={selectedIds.includes(row.id)}
              disabled={!selectedIds.includes(row.id) && selectedIds.length >= 50}
              onChange={(event) => toggleMaterial(row.id, event.target.checked)}
            />
          )}
          <div><strong>{row.title}</strong><span>{phases[row.status] ?? row.status}</span>{row.error_message && <p className="error-text">{row.error_message}</p>}</div>
          <div className="current-materials-actions">
            {!terminal(row) && row.job_id && <Button type="button" size="sm" variant="secondary" disabled={busy !== null} loading={busy === `cancel:${row.id}`} onClick={async () => {
              if (busy) return
              setBusy(`cancel:${row.id}`)
              try { await api.cancelJob(row.job_id!); refresh(); notify?.('已取消资料处理。') } catch (failure) { report(failure) } finally { setBusy(null) }
            }}>取消处理</Button>}
            {['failed', 'cancelled'].includes(row.status) && <Button type="button" size="sm" variant="secondary" disabled={busy !== null} loading={busy === row.id} onClick={async () => {
              if (busy) return
              setBusy(row.id)
              try { await api.retryMaterial(row.id); refresh() } catch (failure) { report(failure) } finally { setBusy(null) }
            }}>重试处理</Button>}
            <Button type="button" size="sm" variant="ghost" disabled={busy !== null} onClick={() => setConfirmDelete(row)}>删除</Button>
          </div>
        </li>)}</ul>}
      </div>,
    }]} />
    <Dialog open={Boolean(confirmDelete)} onOpenChange={(next) => { if (!next && !busy) setConfirmDelete(null) }}>
      {confirmDelete && (
        <DialogContent title="删除资料" description="删除后该资料不再参与检索，历史引用将显示为不可用。" closeLabel="关闭删除资料">
          <div className="stack-form">
            <p>确认删除资料「{confirmDelete.title}」吗？</p>
            <div className="form-actions">
              <Button type="button" variant="danger" loading={busy === `delete:${confirmDelete.id}`} disabled={busy !== null && busy !== `delete:${confirmDelete.id}`} onClick={async () => {
                if (busy) return
                setBusy(`delete:${confirmDelete.id}`)
                try {
                  await api.deleteMaterial(confirmDelete.id)
                  setConfirmDelete(null)
                  refresh()
                  notify?.('资料已删除。')
                } catch (failure) { report(failure) } finally { setBusy(null) }
              }}>确认删除</Button>
              <Button type="button" variant="ghost" disabled={busy !== null} onClick={() => setConfirmDelete(null)}>取消</Button>
            </div>
          </div>
        </DialogContent>
      )}
    </Dialog>
  </section>
}
