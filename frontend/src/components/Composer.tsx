import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Image, Info, Layers, Paperclip, Square } from 'lucide-react'
import { Link } from 'react-router'
import { api, uploadMaterial } from '@/api/client'
import { allPages } from '@/api/ui'
import type { Material } from '@/api/types'
import { useWorkspace } from '../WorkspaceData'
import { Button } from '@/components/arc/button/button'
import { Select } from '@/components/arc/select/select'
import { Switch } from '@/components/arc/switch/switch'
import { Textarea } from '@/components/arc/textarea/textarea'
import { Tooltip } from '@/components/arc/tooltip/tooltip'

const demoLibrary = [{ value: 'product', label: '产品资料库' }]
const emptyLibrary = [{ value: 'empty', label: '暂无可用知识库', disabled: true }]

export function Composer({ demo = false, conversationId, preferredLibrary, summary = false }: {
  demo?: boolean
  conversationId?: string
  preferredLibrary?: string
  summary?: boolean
}) {
  const { user, libraries, capabilities, chat, notify, report, requireLogin, refresh, revision } = useWorkspace()
  const [draft, setDraft] = useState('')
  const [supplement, setSupplement] = useState(false)
  const [selected, setSelected] = useState('')
  const [ready, setReady] = useState<Material[]>([])
  const [materialId, setMaterialId] = useState('')
  const [uploading, setUploading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const imageInput = useRef<HTMLInputElement>(null)
  const requestedLibrary = selected || preferredLibrary
  const libraryId = requestedLibrary ? libraries.find((library) => library.id === requestedLibrary)?.id : libraries[0]?.id
  const active = chat.active?.conversationId === conversationId ? chat.active : null
  const pending = !demo && chat.pending && (chat.pending.body.conversation_id ?? undefined) === conversationId

  useEffect(() => {
    if (demo || !summary || !libraryId) return
    let active = true
    setReady([])
    setMaterialId('')
    void allPages((cursor) => api.materials(libraryId, cursor)).then(
      (materials) => { if (active) setReady(materials.filter((item) => item.status === 'ready')) },
      (error) => { if (active) report(error) },
    )
    return () => { active = false }
  }, [libraryId, summary, demo, revision])

  async function submit() {
    const content = draft.trim()
    if (!content) return
    if (demo) return notify('这是示例会话。请从首页发起真实问答。')
    if (!user) return requireLogin()
    if (!libraryId) return notify('请先创建或选择知识库。')
    if (capabilities?.rag === false) return notify('服务器尚未配置问答模型。')
    if (summary && !ready.some((material) => material.id === materialId && material.knowledge_base_id === libraryId)) return notify('请先选择一份已就绪的资料。')
    if (pending) return notify('请先重发上次请求，确认发送结果。')
    if (await chat.send({
      conversation_id: conversationId,
      knowledge_base_id: libraryId,
      answer_mode: supplement ? 'with_general' : 'knowledge_only',
      intent: summary ? 'summary' : 'qa',
      material_ids: summary ? [materialId] : null,
      content,
    })) setDraft((current) => current.trim() === content ? '' : current)
  }

  function chooseFile(image: boolean) {
    if (demo) return notify('请从真实会话或知识库添加资料。')
    if (!user) return requireLogin()
    if (!libraryId) return notify('请先创建或选择要导入的知识库。')
    const input = image ? imageInput : fileInput
    input.current?.click()
  }

  async function upload(file: File) {
    if (!libraryId || uploading) return
    if (capabilities && file.size > capabilities.max_upload_bytes) return notify('文件超过服务器允许的大小。')
    setUploading(true)
    try {
      await uploadMaterial(libraryId, file)
      refresh()
      notify('资料已提交处理。请在知识库查看状态。')
    } catch (error) {
      report(error)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="composer-wrapper">
    {!user && <button type="button" className="composer-login" aria-label="登录后使用提问、选库和上传" onClick={requireLogin} />}
    <form
      inert={!user}
      className={`composer ${demo || conversationId ? 'composer-chat' : ''}`}
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      <div className="composer-options">
        <div className="library-control">
          <Layers size={18} aria-hidden="true" />
          <Select
            label="知识库"
            className="library-select"
            options={demo ? demoLibrary : libraries.length ? libraries.map(({ id, name }) => ({ value: id, label: name })) : emptyLibrary}
            value={demo ? 'product' : libraryId ?? ''}
            onValueChange={setSelected}
            placeholder="选择知识库"
          />
        </div>
        <div className="supplement-control">
          <Switch className="supplement-switch" label="通用知识补充" checked={supplement} onCheckedChange={setSupplement} />
          <Tooltip content="开启后，回答可以补充模型通用知识。">
            <Button type="button" className="icon-button" variant="ghost" size="sm" aria-label="通用知识补充说明">
              <Info size={17} />
            </Button>
          </Tooltip>
        </div>
      </div>
      {!demo && user && libraries.length === 0 && <Link className="composer-hint" to="/knowledge">创建知识库并导入资料</Link>}
      {!demo && requestedLibrary && !libraryId && libraries.length > 0 && <p className="composer-hint">原知识库不可用，请重新选择。</p>}
      {!demo && capabilities?.rag === false && <p className="composer-hint">服务器尚未配置问答模型。</p>}
      {summary && <Select label="摘要资料" value={materialId} onValueChange={setMaterialId} options={ready.map(({ id, title }) => ({ value: id, label: title }))} placeholder="选择已就绪资料" />}
      <div className="composer-field">
        <Textarea
          label="提问"
          className="composer-input"
          placeholder={summary ? '你希望提炼哪些内容？' : demo || conversationId ? '向你的知识库提问…' : '向你的资料提问…'}
          rows={2}
          maxLength={8000}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return
            event.preventDefault()
            void submit()
          }}
        />
      </div>
      <div className="composer-actions">
        <Tooltip content="添加附件">
          <Button type="button" variant="ghost" className="icon-button" aria-label="添加附件" disabled={uploading} onClick={() => chooseFile(false)}>
            <Paperclip size={21} />
          </Button>
        </Tooltip>
        <Tooltip content="添加图片">
          <Button type="button" variant="ghost" className="icon-button" aria-label="添加图片" disabled={uploading} onClick={() => chooseFile(true)}>
            <Image size={21} />
          </Button>
        </Tooltip>
        {[{ ref: fileInput, accept: '.pdf,.docx,.txt,.png,.jpg,.jpeg' }, { ref: imageInput, accept: 'image/png,image/jpeg' }].map(({ ref, accept }) => (
          <input key={accept} type="file" ref={ref} accept={accept} hidden onChange={(event) => {
            const file = event.target.files?.[0]
            event.target.value = ''
            if (file) void upload(file)
          }} />
        ))}
        {uploading && <span className="composer-hint" role="status">正在上传…</span>}
        {pending && <Button type="button" variant="secondary" size="sm" onClick={async () => { if (await chat.resend()) setDraft('') }}>重发上次请求</Button>}
        {active ? <Button type="button" className="send-button" aria-label="停止生成" onClick={() => void chat.stop(active.conversationId, active.turnId)}><Square size={17} /></Button> : <Button type="submit" className="send-button" aria-label="发送" disabled={!draft.trim() || (!demo && chat.busy)}>
          <ArrowUp size={22} />
        </Button>}
      </div>
    </form>
    </div>
  )
}
