import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import { allPages, errorMessage } from '@/api/ui'
import type { Material } from '@/api/types'
import { Accordion } from '@/components/arc/accordion/accordion'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { useWorkspace } from '../WorkspaceData'

const phases: Record<string, string> = {
  queued: '等待处理', parsing: '解析中', ocr: '识别文字中', indexing: '建立索引中',
  ready: '可问答', failed: '处理失败', cancelled: '已取消',
}
const terminal = (row: Material) => ['ready', 'failed', 'cancelled'].includes(row.status)

// Keyed by account and library at the composer: old requests cannot populate a new scope.
export function CurrentMaterials({ libraryId, name, reveal = 0 }: { libraryId: string; name: string; reveal?: number }) {
  const { revision, refresh, report, notify } = useWorkspace()
  const [rows, setRows] = useState<Material[] | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<Material | null>(null)
  const [open, setOpen] = useState(-1)
  useEffect(() => { if (reveal) setOpen(0) }, [reveal])
  useEffect(() => {
    const controller = new AbortController()
    let timer: number | undefined
    async function load() {
      try {
        const items = await allPages((cursor) => api.materials(libraryId, cursor, controller.signal))
        if (controller.signal.aborted) return
        setRows(items)
        setError('')
        if (items.some((item) => !terminal(item))) timer = window.setTimeout(load, 2200)
      } catch (failure) {
        if (controller.signal.aborted) return
        setError(errorMessage(failure))
        report(failure)
      }
    }
    void load()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [libraryId, revision, attempt])

  const ready = rows?.filter((row) => row.status === 'ready').length ?? 0
  const pending = rows?.filter((row) => !terminal(row)).length ?? 0
  const failed = rows?.filter((row) => ['failed', 'cancelled'].includes(row.status)).length ?? 0
  return <section className="current-materials" aria-label={`当前知识库资料：${name}`}>
    <Accordion openIndex={open} onOpenChange={setOpen} items={[{
      title: <span className="current-materials-title">当前库资料 · {name}<small role="status">{error ? '状态读取失败' : rows ? `${ready} 份可问答${pending ? ` · ${pending} 份处理中` : ''}${failed ? ` · ${failed} 份需处理` : ''}` : '正在读取…'}</small></span>,
      content: <div className="current-materials-body">
        {error ? <div role="alert"><p>{error}</p>{rows && <p className="muted">下方保留上次读取结果，当前状态尚未确认。</p>}<Button type="button" size="sm" variant="secondary" onClick={() => setAttempt((value) => value + 1)}>重新读取状态</Button></div>
          : rows === null ? <p role="status">正在读取资料…</p>
            : !rows.length ? <p>这个知识库还没有资料，请从输入区添加附件或图片。</p>
              : <p className="muted">{ready && (pending || failed) ? '本次提问仅使用已就绪资料，其余资料仍在处理或需重试。' : ready ? '资料已就绪，可以继续提问。' : pending ? '暂无可问答资料，请等待处理完成。' : '暂无可问答资料，请重试处理或重新上传。'}</p>}
        {rows && rows.length > 0 && <ul className="current-materials-list">{rows.map((row) => <li key={row.id}>
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
