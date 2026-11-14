import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'
import { api, uploadMaterial } from '@/api/client'
import type { Material, MaterialPreview } from '@/api/types'
import { allPages, errorMessage } from '@/api/ui'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { EmptyState } from '@/components/arc/empty-state/empty-state'
import { Select } from '@/components/arc/select/select'
import { useWorkspace } from '../WorkspaceData'
import { DownloadMaterial } from './ProtectedMaterial'

const phases: Record<string, string> = {
  queued: '等待处理', parsing: '解析中', ocr: '识别文字中', indexing: '建立索引中',
  ready: '可问答', failed: '处理失败', cancelled: '已取消',
}

export default function Library({ preferredId }: { preferredId?: string }) {
  const { user, libraries, revision, capabilities, refresh, notify, report, requireLogin } = useWorkspace()
  const [selected, setSelected] = useState(preferredId ?? '')
  const [materials, setMaterials] = useState<Material[]>([])
  const [loadedId, setLoadedId] = useState<string | null>(null)
  const [loadFailure, setLoadFailure] = useState<{ id: string; message: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<MaterialPreview | null>(null)
  const [previewName, setPreviewName] = useState('')
  const libraryId = libraries.some((library) => library.id === selected) ? selected : libraries[0]?.id
  const listed = libraryId === loadedId ? materials : []

  useEffect(() => {
    if (!libraryId) return
    let active = true
    let timer: number
    async function load() {
      try {
        const rows = await allPages((cursor) => api.materials(libraryId!, cursor))
        if (!active) return
        setMaterials(rows)
        setLoadedId(libraryId!)
        setLoadFailure(null)
        if (rows.some((row) => !['ready', 'failed', 'cancelled'].includes(row.status))) {
          timer = window.setTimeout(load, 2200)
        }
      } catch (error) {
        if (active) {
          setLoadFailure({ id: libraryId!, message: errorMessage(error) })
          report(error)
        }
      }
    }
    void load()
    return () => { active = false; window.clearTimeout(timer) }
  }, [libraryId, revision])

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const form = event.currentTarget
    const name = String(new FormData(form).get('name')).trim()
    if (!name) return
    setBusy(true)
    try {
      const created = await api.createKnowledgeBase({ name })
      setSelected(created.id)
      form.reset()
      refresh()
      notify('知识库已创建。')
    } catch (error) { report(error) } finally { setBusy(false) }
  }

  async function upload(file: File) {
    if (!libraryId || busy) return
    if (capabilities && file.size > capabilities.max_upload_bytes) return notify('文件超过服务器允许的大小。')
    setBusy(true)
    try {
      await uploadMaterial(libraryId, file)
      refresh()
      notify('资料已提交处理。')
    } catch (error) { report(error) } finally { setBusy(false) }
  }

  if (!user) return <div className="placeholder-page"><EmptyState title="登录后管理知识库" description="在个人空间创建知识库并导入资料。" action={<Button onClick={requireLogin}>登录</Button>} /></div>

  return (
    <section className="resource-page">
      <h1>知识库</h1>
      <form className="stack-form create-library" onSubmit={create}>
        <label>新知识库名称<input name="name" required maxLength={200} placeholder="例如：产品资料" /></label>
        <Button type="submit" loading={busy}>创建知识库</Button>
      </form>
      {libraryId ? <>
        <div className="resource-toolbar">
          <Select label="当前知识库" value={libraryId} onValueChange={setSelected} options={libraries.map(({ id, name }) => ({ value: id, label: name }))} />
          <label className="upload-label">导入资料<input type="file" accept=".pdf,.docx,.txt,.png,.jpg,.jpeg" disabled={busy} onChange={(event) => {
            const file = event.target.files?.[0]
            event.target.value = ''
            if (file) void upload(file)
          }} /></label>
        </div>
        {loadFailure?.id === libraryId ? <EmptyState title="暂时无法读取资料" description={loadFailure.message} action={<Button onClick={refresh}>重试</Button>} />
          : loadedId !== libraryId ? <p role="status" className="muted">正在读取资料…</p> : listed.length ? (
          <ul className="resource-list">
            {listed.map((material) => <li key={material.id}>
              <div><strong>{material.title}</strong><p className="muted">{phases[material.status] ?? material.status}</p>{material.error_message && <p className="error-text">{material.error_message}</p>}</div>
              {material.status === 'ready' && material.version_id && <Button variant="secondary" size="sm" onClick={async () => {
                try {
                  const result = await api.preview(material.version_id!)
                  setPreviewName(material.title)
                  setPreview(result)
                } catch (error) { report(error) }
              }}>查看文字</Button>}
              {['failed', 'cancelled'].includes(material.status) && <Button variant="secondary" size="sm" disabled={busy} onClick={async () => {
                setBusy(true)
                try { await api.retryMaterial(material.id); refresh() } catch (error) { report(error) } finally { setBusy(false) }
              }}>重试处理</Button>}
            </li>)}
          </ul>
        ) : <EmptyState title="这个知识库还没有资料" description="导入一份资料，处理完成后即可在对话中提问。" />}
      </> : <EmptyState title="暂无知识库" description="创建第一个知识库，开始整理个人资料。" />}
      <Link className="return-link" to="/">返回对话首页</Link>
      <Dialog open={Boolean(preview)} onOpenChange={(open) => { if (!open) setPreview(null) }}>
        <DialogContent title={previewName || '资料预览'} description="查看从原件提取的文字。">
          {preview && <div className="source-detail">
            <DownloadMaterial version={preview.version_id} title={previewName} />
            {preview.sections.map((section, index) => <p key={index}>{section.text}</p>)}
          </div>}
        </DialogContent>
      </Dialog>
    </section>
  )
}
