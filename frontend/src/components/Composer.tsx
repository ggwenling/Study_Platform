import { lazy, Suspense, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { DragEvent, Ref } from 'react'
import { ArrowUp, FileText, Globe, Image, Paperclip, Plus, Square } from 'lucide-react'
import { useReducedMotion } from 'motion/react'
const ComposerMetalSend = lazy(() =>
  import('./ComposerMetalSend').then((m) => ({ default: m.ComposerMetalSend })),
)
import { api } from '@/api/client'
import { allPages, errorMessage } from '@/api/ui'
import type { Material } from '@/api/types'
import { clipboardImageFiles, useUploadQueue } from '../useUploadQueue'
import { useWorkspace } from '../WorkspaceData'
import { Button } from '@/components/arc/button/button'
import { DropdownMenu } from '@/components/arc/dropdown-menu/dropdown-menu'
import { Select } from '@/components/arc/select/select'
import { Tooltip } from '@/components/arc/tooltip/tooltip'
import { FadingTextarea } from './FadingTextarea'
import { CurrentMaterials } from './CurrentMaterials'
import { PasteTextDialog } from './PasteTextDialog'
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
  const [uploadTarget, setUploadTarget] = useState<{ id: string; at: number } | null>(null)
  const [pasteOpen, setPasteOpen] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createBusy, setCreateBusy] = useState(false)
  const [createError, setCreateError] = useState('')
  const form = useRef<HTMLFormElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const imageInput = useRef<HTMLInputElement>(null)
  const libraryControl = useRef<HTMLButtonElement>(null)
  const reducedMotion = useReducedMotion() ?? false
  const enableMetal = !reducedMotion
  const requestedLibrary = selected || preferredLibrary
  const currentLibrary = requestedLibrary ? libraries.find((library) => library.id === requestedLibrary) : libraries[0]
  const libraryId = currentLibrary?.id
  const modelReady = libraryId ? capabilities?.library_rag?.[libraryId] ?? capabilities?.rag : capabilities?.rag
  const active = chat.active?.conversationId === conversationId ? chat.active : null
  const pending = !demo && chat.pending && (chat.pending.body.conversation_id ?? undefined) === conversationId

  const uploadQueue = useUploadQueue({
    userId: user?.id,
    maxUploadBytes: capabilities?.max_upload_bytes,
    refresh,
    notify,
    report,
    onUploadedToLibrary: (targetId) => setUploadTarget({ id: targetId, at: Date.now() }),
  })

  useEffect(() => {
    if (!reducedMotion) void import('./ComposerMetalSend')
  }, [reducedMotion])

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
    if (modelReady === false) return notify('当前模型不可用，请在设置的「模型连接」中配置或测试连接。')
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

  function ensureImportReady(): boolean {
    if (demo) { notify('请从真实会话或知识库添加资料。'); return false }
    if (!user) { requireLogin(); return false }
    if (!currentLibrary) { notify('请先创建或选择要导入的知识库。'); return false }
    return true
  }

  function chooseFile(image: boolean) {
    if (!ensureImportReady()) return
    const input = image ? imageInput : fileInput
    input.current?.click()
  }

  function addFiles(files: FileList | File[]) {
    if (!ensureImportReady() || !currentLibrary) return
    uploadQueue.enqueue(files, { id: currentLibrary.id, name: currentLibrary.name })
  }

  useImperativeHandle(ref, () => ({
    focus: () => form.current?.querySelector('textarea')?.focus(),
    chooseImage: () => chooseFile(true),
  }))

  function onDragOver(event: DragEvent<HTMLFormElement>) {
    if (!Array.from(event.dataTransfer?.types ?? []).includes('Files')) return
    event.preventDefault()
    if (!dragging) setDragging(true)
  }

  function onDragLeave(event: DragEvent<HTMLFormElement>) {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return
    setDragging(false)
  }

  function onDrop(event: DragEvent<HTMLFormElement>) {
    if (!event.dataTransfer?.files?.length) return
    event.preventDefault()
    setDragging(false)
    addFiles(event.dataTransfer.files)
  }

  const failedUploads = uploadQueue.items.filter((row) => row.status === 'failed')

  return (
    <div className="composer-wrapper">
    {!user && <button type="button" className="composer-login" aria-label="登录后使用提问、选库和上传" onClick={requireLogin} />}
    <form
      ref={form}
      inert={!user}
      className="composer"
      data-dragging={dragging || undefined}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      {!demo && user && libraries.length === 0 && <Button type="button" variant="ghost" size="sm" onClick={() => { setCreateError(''); setCreating(true) }}>创建知识库并导入资料</Button>}
      {!demo && requestedLibrary && !libraryId && libraries.length > 0 && <p className="composer-hint">原知识库不可用，请重新选择。</p>}
      {!demo && modelReady === false && <p className="composer-hint">当前模型不可用，请在设置的「模型连接」中配置或测试连接。</p>}
      {dragging && <p className="composer-hint" role="status">松开鼠标，将文件导入当前知识库</p>}
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
          onPaste={(event) => {
            const images = clipboardImageFiles(event.clipboardData)
            if (!images.length) return
            event.preventDefault()
            addFiles(images)
          }}
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
          items={[
            { label: '添加附件', icon: <Paperclip size={16} />, onSelect: () => chooseFile(false) },
            { label: '添加图片', icon: <Image size={16} />, onSelect: () => chooseFile(true) },
            { label: '随手贴文本', icon: <FileText size={16} />, onSelect: () => { if (ensureImportReady()) setPasteOpen(true) } },
          ]}
        />
        {[{ ref: fileInput, accept: '.pdf,.docx,.txt,.md,.markdown,.png,.jpg,.jpeg' }, { ref: imageInput, accept: 'image/png,image/jpeg' }].map(({ ref, accept }) => (
          <input key={accept} type="file" ref={ref} accept={accept} multiple hidden onChange={(event) => {
            const files = event.target.files ? Array.from(event.target.files) : []
            event.target.value = ''
            if (files.length) addFiles(files)
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
      {uploadQueue.latestNote && <span className="composer-hint" role="status">{uploadQueue.latestNote}</span>}
      {failedUploads.length > 0 && (
        <div className="upload-queue-failures">
          {failedUploads.map((item) => (
            <span key={item.id} className="upload-failure-item">
              <span>「{item.name}」上传失败</span>
              <Button type="button" size="sm" variant="secondary" onClick={() => uploadQueue.retryItem(item.id)}>重试上传</Button>
            </span>
          ))}
        </div>
      )}
      {pending && <Button type="button" variant="secondary" size="sm" onClick={async () => { if (await chat.resend()) setDraft('') }}>重发上次请求</Button>}
    </form>
    {!demo && user && libraryId && <CurrentMaterials key={`${user.id}:${libraryId}`} libraryId={libraryId} name={libraries.find((library) => library.id === libraryId)!.name} reveal={uploadTarget?.id === libraryId ? uploadTarget.at : 0} />}
    <PasteTextDialog
      open={pasteOpen}
      onOpenChange={setPasteOpen}
      libraryName={currentLibrary?.name}
      onSubmitFile={(file) => addFiles([file])}
    />
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
