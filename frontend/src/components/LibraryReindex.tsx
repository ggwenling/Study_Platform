import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { api } from '@/api/client'
import type { KnowledgeBase, ModelConnection, ReindexStatus } from '@/api/types'
import { errorMessage } from '@/api/ui'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { Select } from '@/components/arc/select/select'
import { useWorkspace } from '../WorkspaceData'

export function LibraryReindex({ library, open, onOpenChange, onPublished }: {
  library: KnowledgeBase; open: boolean; onOpenChange: (open: boolean) => void; onPublished: () => void;
}) {
  const { notify, report } = useWorkspace()
  const { id } = library
  const [status, setStatus] = useState<ReindexStatus | null>(null)
  const [target, setTarget] = useState('current')
  const [models, setModels] = useState<ModelConnection[]>([])
  const [modelError, setModelError] = useState('')
  const [busy, setBusy] = useState(false)
  const requestVersion = useRef(0)
  const publishedJobId = useRef<string>(undefined)
  const active = status?.status === 'queued' || status?.status === 'running'
  const publish = useEffectEvent((next: ReindexStatus, wasActive: boolean) => {
    if (publishedJobId.current === next.job_id) return
    if (wasActive) notify('已启用重建后的知识库索引。')
    if (wasActive || next.target_connection_id !== (library.embedding_connection_id ?? null)
      || next.target_model !== library.embedding_binding?.model) {
      publishedJobId.current = next.job_id
      onPublished()
    }
  })

  useEffect(() => {
    let timer: number | undefined
    const controller = new AbortController()
    async function poll() {
      const version = requestVersion.current
      try {
        const next = await api.reindexStatus(id, controller.signal)
        if (controller.signal.aborted || version !== requestVersion.current) return
        setStatus(next)
        if (next?.status === 'succeeded') publish(next, active)
        if (next?.status === 'queued' || next?.status === 'running') timer = window.setTimeout(poll, 1800)
      } catch {
        if (active && !controller.signal.aborted && version === requestVersion.current) timer = window.setTimeout(poll, 1800)
      }
    }
    if (!busy) void poll()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [id, active, busy])

  useEffect(() => {
    if (!open) return
    let live = true
    setTarget('current')
    setModelError('')
    void api.modelConnections().then((result) => {
      if (live) setModels(result.items.filter((model) => model.kind === 'embedding' && model.status === 'ready'))
    }, (error: unknown) => {
      if (live) setModelError(errorMessage(error))
    })
    return () => { live = false }
  }, [open])

  async function act(action: 'start' | 'retry' | 'cancel') {
    requestVersion.current += 1
    setBusy(true)
    try {
      if (action === 'start') {
        const submitted = await api.startReindex(id, target === 'current' ? null : target)
        setStatus({ job_id: submitted.job_id, status: submitted.status, total: submitted.total_materials,
          running: 0, succeeded: 0, failed: 0, failures: [], error_code: null,
          target_connection_id: target === 'current' ? library.embedding_connection_id ?? null : target,
          target_model: models.find((model) => model.id === target)?.model ?? library.embedding_binding?.model ?? '当前模型' })
        onOpenChange(false)
      } else {
        setStatus(await (action === 'retry' ? api.retryReindex(id) : api.cancelReindex(id)))
      }
    } catch (error) { report(error) } finally { setBusy(false) }
  }

  return <>
    {status && (active || status.status === 'failed') && <section className="library-panel library-reindex" aria-label="索引重建进度">
      <div role="status">
        <strong>{status.status === 'failed' ? '索引重建失败' : '正在安全重建索引'}</strong>
        <span> · {status.target_model} · {status.succeeded} / {status.total} 份资料</span>
      </div>
      <progress aria-label="已重建资料" max={Math.max(status.total, 1)} value={status.succeeded} />
      {status.error_code && <p className="error-text">错误代码 {status.error_code}</p>}
      {status.failures.length > 0 && <ul>{status.failures.map((failure) => <li key={failure.material_version_id}>{failure.title} · {failure.error_code}</li>)}</ul>}
      <div className="library-header-actions">
        {status.status === 'failed' && <Button size="sm" disabled={busy} onClick={() => void act('retry')}>重试失败资料</Button>}
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => void act('cancel')}>取消重建</Button>
      </div>
    </section>}
    <Dialog open={open} onOpenChange={(next) => { if (!busy) onOpenChange(next) }}>
      <DialogContent title="重建知识库索引" closeLabel="关闭" description="重建期间可继续使用当前索引。全部资料处理成功后将自动切换。">
        <div className="library-dialog-stack">
          <Select label="目标向量模型" value={target} onValueChange={setTarget} disabled={busy || active} options={[
            { value: 'current', label: '保留当前模型，使用最新切分器重建' },
            ...models.filter((model) => model.id !== library.embedding_connection_id).map((model) => ({ value: model.id, label: `${model.name} · ${model.model}` })),
          ]} />
          {modelError && <p role="alert" className="error-text">{modelError}</p>}
          <Button disabled={busy || active} loading={busy} onClick={() => void act('start')}>
            {target === 'current' ? '重建索引' : '切换模型并安全重建索引'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  </>
}
