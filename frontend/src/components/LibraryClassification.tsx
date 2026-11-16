import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import type { ClassificationState, Material } from '@/api/types'
import { errorMessage } from '@/api/ui'
import { Button } from '@/components/arc/button/button'
import { Input } from '@/components/arc/input/input'
import { Select } from '@/components/arc/select/select'
import { useWorkspace } from '../WorkspaceData'

const suggestionStatus: Record<string, string> = {
  missing: '暂无 AI 建议', queued: '等待生成建议', running: '正在生成建议',
  failed: '生成失败，可重试', succeeded: '待确认',
  confirmed: '已确认', ignored: '已忽略',
}

export function LibraryClassification({ id, materials, labels, onChange }: {
  id: string; materials: Material[]; labels: string[]; onChange: () => void;
}) {
  const [selected, setSelected] = useState('')
  const material = materials.find((row) => row.id === selected) ?? materials[0]
  if (!material) return <p className="muted">导入资料后可生成分类建议。</p>
  return <div className="library-dialog-stack">
    {labels.length > 0 && <div className="library-labels">{labels.map((label) => <span className="library-status" key={label}>{label}</span>)}</div>}
    <Select label="选择资料" value={material.id} onValueChange={setSelected} options={materials.map((row) => ({ value: row.id, label: row.title }))} />
    <ClassificationEditor key={material.id} id={id} material={material.id} onChange={onChange} />
  </div>
}

function ClassificationEditor({ id, material, onChange }: { id: string; material: string; onChange: () => void }) {
  const { report, notify } = useWorkspace()
  const [data, setData] = useState<ClassificationState>()
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    let timer: number
    async function load() {
      try {
        const result = await api.classification(id, material)
        if (!active) return
        setData(result)
        setError('')
        if (['queued', 'running'].includes(result.status)) timer = window.setTimeout(load, 2000)
      } catch (failure) {
        if (active) setError(errorMessage(failure))
      }
    }
    void load()
    return () => { active = false; window.clearTimeout(timer) }
  }, [id, material, revision])

  async function generate() {
    setBusy(true)
    try {
      setData(await api.generateClassification(id, material))
      setRevision((current) => current + 1)
    } catch (failure) { report(failure) } finally { setBusy(false) }
  }

  async function decide(form?: HTMLFormElement) {
    if (!data?.suggestion_id) return
    const values = form ? new FormData(form) : undefined
    const topic = String(values?.get('topic') ?? '').trim() || null
    const tags = String(values?.get('tags') ?? '').split(/[,，]/).map((tag) => tag.trim()).filter(Boolean)
    setBusy(true)
    try {
      setData(await api.confirmClassification(id, material, { suggestion_id: data.suggestion_id, topic, tags, ignore: !form }))
      notify(form ? '已确认资料分类。' : '已忽略本次建议。')
      onChange()
    } catch (failure) { report(failure); setRevision((current) => current + 1) } finally { setBusy(false) }
  }

  if (error) return <p role="alert">{error} <Button size="sm" onClick={() => setRevision((current) => current + 1)}>重试</Button></p>
  if (!data) return <p role="status">正在读取分类…</p>
  const generating = ['queued', 'running'].includes(data.status)
  return <>
    <p>当前主题：{data.topic ?? '未分类'}<br />当前标签：{data.tags.join('、') || '无'}</p>
    <p className="muted" role="status">{data.ready ? suggestionStatus[data.status] ?? data.status : '索引就绪后可以生成建议。'}</p>
    {data.status === 'succeeded' && data.suggestion ? <form key={data.suggestion_id} className="library-dialog-stack" onSubmit={(event) => {
      event.preventDefault()
      void decide(event.currentTarget)
    }}>
      <Input label="主题" name="topic" maxLength={80} defaultValue={data.suggestion.topic ?? ''} />
      <Input label="标签" name="tags" description="用逗号分隔，最多 12 个标签。" defaultValue={data.suggestion.tags.join('，')} />
      <div className="library-dialog-actions">
        <Button type="submit" disabled={busy}>确认分类</Button>
        <Button type="button" variant="secondary" disabled={busy} onClick={() => void decide()}>忽略建议</Button>
      </div>
    </form> : <Button variant="secondary" disabled={!data.ready || generating || busy} onClick={() => void generate()}>
      {data.status === 'failed' ? '重试生成' : data.status === 'confirmed' || data.status === 'ignored' ? '重新生成建议' : '生成 AI 建议'}
    </Button>}
  </>
}
