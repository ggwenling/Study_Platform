import { lazy, Suspense, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { Ref } from 'react'
import { ArrowUp, Globe, Image, Paperclip, Plus, Square } from 'lucide-react'
import { useReducedMotion } from 'motion/react'
const ComposerMetalSend = lazy(() =>
  import('./ComposerMetalSend').then((m) => ({ default: m.ComposerMetalSend })),
)
import { api, uploadMaterial } from '@/api/client'
import { allPages, errorMessage } from '@/api/ui'
import type { Material } from '@/api/types'
import { useWorkspace } from '../WorkspaceData'
import { Button } from '@/components/arc/button/button'
import { DropdownMenu } from '@/components/arc/dropdown-menu/dropdown-menu'
import { Select } from '@/components/arc/select/select'
import { Tooltip } from '@/components/arc/tooltip/tooltip'
import { FadingTextarea } from './FadingTextarea'
import { CurrentMaterials } from './CurrentMaterials'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { Input } from '@/components/arc/input/input'

const demoLibrary = [{ value: 'product', label: '产品资料库' }]
const emptyLibrary = [{ value: 'empty', label: '暂无可用知识库', disabled: true }]

function shortLibraryName(name: string) {
  return Array.from(name.replace(/^【演示】\s*/, '') || name).slice(0, 4).join('')
}

export type ComposerHandle = { focus: () => void; chooseImage: () => void }

export function Composer({ demo = false, conversationId, preferredLibrary, summary = false, ref }: {
  demo?: boolean
  conversationId?: string
  preferredLibrary?: string
  summary?: boolean
  ref?: Ref<ComposerHandle>
}) {
  const { user, libraries, capabilities, chat, notify, report, requireLogin, refresh, revision } = useWorkspace()
  const [draft, setDraft] = useState('')
  const [supplement, setSupplement] = useState(false)
  const [selected, setSelected] = useState('')
  const [ready, setReady] = useState<Material[]>([])
  const [materialId, setMaterialId] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadNote, setUploadNote] = useState('')
  const [uploadTarget, setUploadTarget] = useState<{ id: string; at: number } | null>(null)
  const [creating, setCreating] = useState(false)
  const [createBusy, setCreateBusy] = useState(false)
  const [createError, setCreateError] = useState('')
  const uploadController = useRef<AbortController | null>(null)
  const form = useRef<HTMLFormElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const imageInput = useRef<HTMLInputElement>(null)
  const libraryControl = useRef<HTMLButtonElement>(null)
  const reducedMotion = useReducedMotion() ?? false
  const enableMetal = !reducedMotion
  const requestedLibrary = selected || preferredLibrary
  const libraryId = requestedLibrary ? libraries.find((library) => library.id === requestedLibrary)?.id : libraries[0]?.id
  const active = chat.active?.conversationId === conversationId ? chat.active : null
  const pending = !demo && chat.pending && (chat.pending.body.conversation_id ?? undefined) === conversationId

  useEffect(() => {
    if (!reducedMotion) void import('./ComposerMetalSend')
  }, [reducedMotion])

  useEffect(() => () => uploadController.current?.abort(), [user?.id])

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

  useImperativeHandle(ref, () => ({
    focus: () => form.current?.querySelector('textarea')?.focus(),
    chooseImage: () => chooseFile(true),
  }))

  async function upload(file: File) {
    if (!libraryId || uploading) return
    if (capabilities && file.size > capabilities.max_upload_bytes) return notify('文件超过服务器允许的大小。')
    setUploading(true)
    const target = libraries.find((library) => library.id === libraryId)!
    const controller = new AbortController()
    uploadController.current = controller
    setUploadTarget({ id: target.id, at: Date.now() })
    setUploadNote(`正在上传「${file.name}」至「${target.name}」…`)
    try {
      await uploadMaterial(target.id, file, undefined, controller.signal)
      if (controller.signal.aborted) return
      refresh()
      setUploadNote(`「${file.name}」已提交至「${target.name}」。`)
    } catch (error) {
      if (!controller.signal.aborted) { setUploadNote(`「${file.name}」上传失败，请重新添加。`); report(error) }
    } finally {
      if (!controller.signal.aborted) setUploading(false)
    }
  }

  return (
    <div className="composer-wrapper">
    {!user && <button type="button" className="composer-login" aria-label="登录后使用提问、选库和上传" onClick={requireLogin} />}
    <form
      ref={form}
      inert={!user}
      className="composer"
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      {!demo && user && libraries.length === 0 && <Button type="button" variant="ghost" size="sm" onClick={() => { setCreateError(''); setCreating(true) }}>创建知识库并导入资料</Button>}
      {!demo && requestedLibrary && !libraryId && libraries.length > 0 && <p className="composer-hint">原知识库不可用，请重新选择。</p>}
      {!demo && capabilities?.rag === false && <p className="composer-hint">服务器尚未配置问答模型。</p>}
      {summary && <Select label="摘要资料" value={materialId} onValueChange={setMaterialId} options={ready.map(({ id, title }) => ({ value: id, label: title }))} placeholder="选择已就绪资料" />}
      <div className="composer-field">
        <FadingTextarea
          label="提问"
          className="composer-input"
          placeholder={summary ? '你希望提炼哪些内容？' : demo || conversationId ? '向你的知识库提问…' : '向你的资料提问…'}
          rows={1}
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
        <DropdownMenu
          label="添加资料"
          icon={<Plus size={20} />}
          iconOnly
          className="composer-add"
          disabled={uploading}
          items={[
            { label: '添加附件', icon: <Paperclip size={16} />, onSelect: () => chooseFile(false) },
            { label: '添加图片', icon: <Image size={16} />, onSelect: () => chooseFile(true) },
          ]}
        />
        {[{ ref: fileInput, accept: '.pdf,.docx,.txt,.png,.jpg,.jpeg' }, { ref: imageInput, accept: 'image/png,image/jpeg' }].map(({ ref, accept }) => (
          <input key={accept} type="file" ref={ref} accept={accept} hidden onChange={(event) => {
            const file = event.target.files?.[0]
            event.target.value = ''
            if (file) void upload(file)
          }} />
        ))}
        <div className="composer-options">
          <Tooltip content={supplement ? '已开启通用知识补充' : '当前仅使用知识库，点击开启通用知识补充'}>
            <Button type="button" variant="secondary" className="composer-supplement" aria-label="通用知识补充" aria-pressed={supplement} onClick={() => setSupplement((on) => !on)}>
              <Globe size={18} aria-hidden="true" />
            </Button>
          </Tooltip>
          <Select
            ref={libraryControl}
            label="知识库"
            className="composer-select"
            formatValue={shortLibraryName}
            options={demo ? demoLibrary : libraries.length ? libraries.map(({ id, name }) => ({ value: id, label: name })) : emptyLibrary}
            value={demo ? 'product' : libraryId ?? ''}
            onValueChange={setSelected}
            placeholder="选知识库"
          />
        </div>
        {enableMetal ? (
          <Suspense fallback={
            <div className="composer-send">
              {active ? <Button type="button" variant="secondary" className="send-button" aria-label="停止生成" onClick={() => void chat.stop(active.conversationId, active.turnId)}><Square size={16} /></Button> : <Button type="submit" variant="secondary" className="send-button" aria-label="发送" disabled={!draft.trim() || (!demo && chat.busy)}><ArrowUp size={21} /></Button>}
            </div>
          }>
            <ComposerMetalSend reflectionTarget={libraryControl}>
              {active ? <Button type="button" variant="secondary" className="send-button" aria-label="停止生成" onClick={() => void chat.stop(active.conversationId, active.turnId)}><Square size={16} /></Button> : <Button type="submit" variant="secondary" className="send-button" aria-label="发送" disabled={!draft.trim() || (!demo && chat.busy)}><ArrowUp size={21} /></Button>}
            </ComposerMetalSend>
          </Suspense>
        ) : (
          <div className="composer-send">
            {active ? <Button type="button" variant="secondary" className="send-button" aria-label="停止生成" onClick={() => void chat.stop(active.conversationId, active.turnId)}><Square size={16} /></Button> : <Button type="submit" variant="secondary" className="send-button" aria-label="发送" disabled={!draft.trim() || (!demo && chat.busy)}><ArrowUp size={21} /></Button>}
          </div>
        )}
      </div>
      {uploadNote && <span className="composer-hint" role="status">{uploadNote}</span>}
      {pending && <Button type="button" variant="secondary" size="sm" onClick={async () => { if (await chat.resend()) setDraft('') }}>重发上次请求</Button>}
    </form>
    {!demo && user && libraryId && <CurrentMaterials key={`${user.id}:${libraryId}`} libraryId={libraryId} name={libraries.find((library) => library.id === libraryId)!.name} reveal={uploadTarget?.id === libraryId ? uploadTarget.at : 0} />}
    <Dialog open={creating} onOpenChange={setCreating}>
      <DialogContent title="创建知识库" description="创建后可直接添加资料并提问。" closeLabel="关闭创建知识库">
        <form className="stack-form" onSubmit={async (event) => {
          event.preventDefault()
          if (createBusy) return
          const name = String(new FormData(event.currentTarget).get('name')).trim()
          if (!name) return
          setCreateBusy(true)
          setCreateError('')
          try {
            const library = await api.createKnowledgeBase({ name })
            setSelected(library.id)
            refresh()
            setCreating(false)
          } catch (error) { setCreateError(errorMessage(error)); report(error) } finally { setCreateBusy(false) }
        }}>
          <Input label="知识库名称" name="name" required maxLength={200} autoFocus placeholder="例如：工作资料" />
          {createError && <p className="error-text" role="alert">{createError}</p>}
          <Button type="submit" loading={createBusy}>创建知识库</Button>
        </form>
      </DialogContent>
    </Dialog>
    </div>
  )
}
