import { lazy, Suspense, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { DragEvent, Ref } from 'react'
import { ArrowUp, FileSpreadsheet, FileText, Globe, Image, Paperclip, Plus, Square } from 'lucide-react'
import { useReducedMotion } from 'motion/react'
import { Link } from 'react-router'
const ComposerMetalSend = lazy(() =>
  import('./ComposerMetalSend').then((m) => ({ default: m.ComposerMetalSend })),
)
import { api } from '@/api/client'
import type { Scope } from '@/api/types'
import { chatModelError, errorMessage } from '@/api/ui'
import { clipboardImageFiles } from '../useUploadQueue'
import { clearAcceptedChatDraftText, useChatDrafts } from '../useChatDrafts'
import { useWorkspace } from '../WorkspaceData'
import { Button } from '@/components/arc/button/button'
import { DropdownMenu } from '@/components/arc/dropdown-menu/dropdown-menu'
import { Tooltip } from '@/components/arc/tooltip/tooltip'
import { FadingTextarea } from './FadingTextarea'
import { CurrentMaterials } from './CurrentMaterials'
import { PasteTextDialog } from './PasteTextDialog'
import { WebClipDialog } from './WebClipDialog'
import { FileImportDialog, useFileImport } from './FileImportDialog'
import { ChatModelSelect } from './ChatModelSelect'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { Input } from '@/components/arc/input/input'

export type ComposerHandle = {
  focus: () => void
  chooseImage: () => void
  setSummaryMode: (enabled: boolean) => void
  editAsNew: (content: string, scope: Scope | null) => void
}

export function Composer({ demo = false, conversationId, modelId, onModelChange, savingModel,
  preferredLibrary, initialDraft, initialMaterialIds, onInitialDraftApplied, ref }: {
  demo?: boolean
  conversationId?: string
  modelId?: string | null
  onModelChange?: (id: string | null) => void
  savingModel?: boolean
  preferredLibrary?: string
  initialDraft?: string
  initialMaterialIds?: string[]
  onInitialDraftApplied?: () => void
  ref?: Ref<ComposerHandle>
}) {
  const { user, libraries, overview, loading, capabilities, chat, notify, report, requireLogin, refresh, uploadQueue } = useWorkspace()
  const { lastUploaded } = uploadQueue
  const uploadAtMount = useRef(lastUploaded)
  const { value: draftState, setValue: setDraftState } = useChatDrafts(demo ? undefined : user?.id, conversationId)
  const { text: draft, materialIds: selectedMaterialIds, scoped, supplement, summaryMode } = draftState
  const [scopeStatus, setScopeStatus] = useState<{ libraryId: string; ids: string[]; valid: boolean }>()
  const reportScope = useCallback((libraryId: string, ids: string[], valid: boolean) => {
    setScopeStatus({ libraryId, ids, valid })
  }, [])
  const [panelReveal, setPanelReveal] = useState<{ id: string; at: number } | null>(null)
  const [pasteOpen, setPasteOpen] = useState(false)
  const [webClipOpen, setWebClipOpen] = useState(false)
  const fileImport = useFileImport(capabilities?.vision, uploadQueue.enqueue)
  const [dragging, setDragging] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createBusy, setCreateBusy] = useState(false)
  const [createError, setCreateError] = useState('')
  const form = useRef<HTMLFormElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const imageInput = useRef<HTMLInputElement>(null)
  const modelControl = useRef<HTMLButtonElement>(null)
  const reducedMotion = useReducedMotion() ?? false
  const enableMetal = !reducedMotion
  const requestedLibrary = draftState.libraryId || preferredLibrary
  const recentLibrary = conversationId ? undefined : overview?.recent_chat_library_id
  const loadingLibrary = Boolean(!requestedLibrary && !conversationId && overview == null && loading)
  const defaultLibrary = preferredLibrary ? libraries.find((library) => library.id === preferredLibrary)
    : libraries.find((library) => library.id === recentLibrary) ?? libraries[0]
  const currentLibrary = draftState.scopeRequired || loadingLibrary ? undefined : requestedLibrary
    ? libraries.find((library) => library.id === requestedLibrary)
    : defaultLibrary
  const libraryId = currentLibrary?.id
  const scopeIssue = draftState.scopeRequired ? '历史问题缺少可恢复的资料范围。'
    : !loading && requestedLibrary && !libraryId ? '原知识库不可用。' : undefined
  const models = capabilities?.chat_models
  const accountDefault = models?.find((model) => model.is_default)
  const explicitModel = models?.find((model) => model.id === modelId)
  const effectiveModel = modelId ? explicitModel : accountDefault
  const legacyReady = libraryId ? capabilities?.library_rag?.[libraryId] ?? capabilities?.rag : capabilities?.rag
  const embeddingReady = libraryId ? capabilities?.library_embedding?.[libraryId] : undefined
  const modelReady = models === undefined ? legacyReady : effectiveModel ? effectiveModel.status === 'ready'
    : !modelId && Boolean(capabilities?.platform_chat_model)
  const awaitingModel = Boolean(!demo && conversationId && modelId === undefined && models !== undefined)
  const modelIssue = chat.selectionFailureFor?.(conversationId) ?? (!awaitingModel && modelReady === false && (
    modelId && !explicitModel ? '所选个人模型已不可用。' : chatModelError(effectiveModel?.error_code)
      ?? (effectiveModel ? '请先测试此文本模型。' : '当前没有可用的默认模型。')
  ))
  const active = demo ? null : chat.activeFor(conversationId)
  const pending = !demo && chat.pendingFor(conversationId)
  const sendDisabled = !draft.trim() || (!demo && (savingModel || awaitingModel || loadingLibrary || chat.isBusy(conversationId)))

  useEffect(() => {
    if (!initialDraft || !user) return
    setDraftState(() => ({
      text: initialDraft,
      libraryId: preferredLibrary,
      materialIds: initialMaterialIds ?? [],
      scoped: Boolean(initialMaterialIds?.length),
      supplement: false,
      summaryMode: false,
      scopeRequired: false,
    }))
    setScopeStatus(undefined)
    onInitialDraftApplied?.()
  }, [initialDraft, initialMaterialIds, preferredLibrary, setDraftState, onInitialDraftApplied, user])

  useEffect(() => {
    if (!reducedMotion) void import('./ComposerMetalSend')
  }, [reducedMotion])

  function applySummaryMode(enabled: boolean) {
    setDraftState((current) => ({ ...current, summaryMode: enabled }))
    if (enabled && libraryId && !selectedMaterialIds.length) {
      setPanelReveal({ id: libraryId, at: Date.now() })
    }
  }

  function submit() {
    const sentText = draft
    const content = sentText.trim()
    if (!content || savingModel || awaitingModel || loadingLibrary) return
    if (demo) return notify('这是示例会话。请从首页发起真实问答。')
    if (!user) return requireLogin()
    if (draftState.scopeRequired || (requestedLibrary && !currentLibrary)) {
      return notify('请先确认本轮使用的资料范围。')
    }
    if (!libraryId) return notify('请先创建或选择知识库。')
    if (modelReady === false) return notify('请选择可用的文本模型，或前往设置修复连接。')
    if (embeddingReady === false) return notify('当前知识库的向量模型不可用，请检查知识库绑定。')
    if (scoped && (!scopeStatus?.valid || scopeStatus.libraryId !== libraryId || scopeStatus.ids !== selectedMaterialIds)) return notify('所选资料范围尚未确认，请检查资料状态或选择使用全库。')
    if (summaryMode && selectedMaterialIds.length === 0) {
      setPanelReveal({ id: libraryId, at: Date.now() })
      return notify('请先在下方「当前库资料」中勾选要提炼的已就绪资料。')
    }
    if (pending) return notify('请先重发上次请求，确认发送结果。')
    void chat.send({
      conversation_id: conversationId,
      ...(!conversationId && modelId ? { llm_connection_id: modelId } : {}),
      knowledge_base_id: libraryId,
      answer_mode: supplement ? 'with_general' : 'knowledge_only',
      intent: summaryMode ? 'summary' : 'qa',
      material_ids: scoped ? selectedMaterialIds : null,
      content,
    }, () => clearAcceptedChatDraftText(user.id, conversationId, sentText))
  }

  function chooseLibrary(id: string) {
    setDraftState((current) => ({ ...current, libraryId: id, materialIds: [], scoped: false, scopeRequired: false }))
    setScopeStatus(undefined)
  }

  function editAsNew(content: string, scope: Scope | null) {
    const selectedIds = scope?.selected_material_ids?.filter((id): id is string => typeof id === 'string') ?? []
    setDraftState((current) => ({
      ...current,
      text: content,
      libraryId: scope?.knowledge_base_id || undefined,
      materialIds: selectedIds,
      scoped: scope ? scope.selected_material_ids != null : false,
      supplement: scope?.answer_mode === 'with_general',
      summaryMode: scope?.intent === 'summary',
      scopeRequired: !scope || !scope.knowledge_base_id || scope.selected_material_ids === undefined,
    }))
    setScopeStatus(undefined)
    requestAnimationFrame(() => form.current?.querySelector('textarea')?.focus())
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
    fileImport.addFiles(files, { id: currentLibrary.id, name: currentLibrary.name })
  }

  useImperativeHandle(ref, () => ({
    focus: () => form.current?.querySelector('textarea')?.focus(),
    chooseImage: () => chooseFile(true),
    setSummaryMode: applySummaryMode,
    editAsNew,
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
        submit()
      }}
    >
      {!demo && user && !loading && libraries.length === 0 && <Button type="button" variant="ghost" size="sm" onClick={() => { setCreateError(''); setCreating(true) }}>创建知识库并导入资料</Button>}
      {!demo && scopeIssue && <p className="composer-hint">
        {scopeIssue}
        {defaultLibrary && !loadingLibrary
          ? <Button type="button" variant="ghost" size="sm" onClick={() => chooseLibrary(defaultLibrary.id)}>使用「{defaultLibrary.name}」的资料</Button>
          : '请从知识库项目发起新对话。'}
      </p>}
      {!demo && modelIssue && <p className="composer-hint" role="status">{modelIssue} 可切换模型或 <Link to="/settings#model-settings-title">检查并测试连接</Link>。</p>}
      {!demo && embeddingReady === false && <p className="composer-hint" role="status">当前知识库的向量模型不可用，请检查知识库绑定。</p>}
      {dragging && <p className="composer-hint" role="status">松开鼠标，将文件导入当前知识库</p>}
      <div className="composer-field">
        <FadingTextarea
          label="提问"
          className="composer-input"
          placeholder={summaryMode ? '你希望提炼哪些内容？' : demo || conversationId ? '向你的知识库提问…' : '向你的资料提问…'}
          rows={1}
          maxLength={8000}
          value={draft}
          onChange={(event) => setDraftState((current) => ({ ...current, text: event.target.value }))}
          onPaste={(event) => {
            const images = clipboardImageFiles(event.clipboardData)
            if (!images.length) return
            event.preventDefault()
            addFiles(images)
          }}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return
            event.preventDefault()
            submit()
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
            { label: '导入网页链接', icon: <Globe size={16} />, onSelect: () => { if (ensureImportReady()) setWebClipOpen(true) } },
            { label: '随手贴文本', icon: <FileText size={16} />, onSelect: () => { if (ensureImportReady()) setPasteOpen(true) } },
          ]}
        />
        {[{ ref: fileInput, accept: '.pdf,.docx,.pptx,.txt,.md,.markdown,.png,.jpg,.jpeg' }, { ref: imageInput, accept: 'image/png,image/jpeg' }].map(({ ref, accept }) => (
          <input key={accept} type="file" ref={ref} accept={accept} multiple hidden onChange={(event) => {
            const files = event.target.files ? Array.from(event.target.files) : []
            event.target.value = ''
            if (files.length) addFiles(files)
          }} />
        ))}
        <div className="composer-options">
          {!demo && (
            <Tooltip content={summaryMode ? '已开启摘要提炼模式，点击切回普通问答' : '当前为普通问答，点击切换为摘要提炼模式'}>
              <Button
                type="button"
                variant="secondary"
                className="composer-supplement"
                aria-label="摘要模式"
                aria-pressed={summaryMode}
                onClick={() => applySummaryMode(!summaryMode)}
              >
                <FileSpreadsheet size={17} aria-hidden="true" />
              </Button>
            </Tooltip>
          )}
          <Tooltip content={supplement ? '已开启通用知识补充' : '当前仅使用知识库，点击开启通用知识补充'}>
            <Button type="button" variant="secondary" className="composer-supplement" aria-label="通用知识补充" aria-pressed={supplement} onClick={() => setDraftState((current) => ({ ...current, supplement: !current.supplement }))}>
              <Globe size={18} aria-hidden="true" />
            </Button>
          </Tooltip>
          {!demo && onModelChange && <ChatModelSelect modelId={modelId} conversationId={conversationId}
            saving={savingModel} onChange={onModelChange} triggerRef={modelControl} />}
        </div>
        {enableMetal ? (
          <Suspense fallback={
            <div className="composer-send">
              {active ? <Button type="button" variant="secondary" className="send-button" aria-label="停止生成" onClick={() => void chat.stop(active.conversationId, active.turnId)}><Square size={16} /></Button> : <Button type="submit" variant="secondary" className="send-button" aria-label="发送" disabled={sendDisabled}><ArrowUp size={21} /></Button>}
            </div>
          }>
            <ComposerMetalSend reflectionTarget={modelControl}>
              {active ? <Button type="button" variant="secondary" className="send-button" aria-label="停止生成" onClick={() => void chat.stop(active.conversationId, active.turnId)}><Square size={16} /></Button> : <Button type="submit" variant="secondary" className="send-button" aria-label="发送" disabled={sendDisabled}><ArrowUp size={21} /></Button>}
            </ComposerMetalSend>
          </Suspense>
        ) : (
          <div className="composer-send">
            {active ? <Button type="button" variant="secondary" className="send-button" aria-label="停止生成" onClick={() => void chat.stop(active.conversationId, active.turnId)}><Square size={16} /></Button> : <Button type="submit" variant="secondary" className="send-button" aria-label="发送" disabled={sendDisabled}><ArrowUp size={21} /></Button>}
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
      {pending && <Button type="button" variant="secondary" size="sm" onClick={() => {
        const sentText = draft
        void chat.resend(conversationId, () => {
          if (sentText.trim() === pending.body.content) clearAcceptedChatDraftText(user?.id, conversationId, sentText)
        })
      }}>重发上次请求</Button>}
    </form>
    {fileImport.dialog && <FileImportDialog {...fileImport.dialog} />}
    {!demo && user && libraryId && (
      <CurrentMaterials
        key={user.id}
        libraryId={libraryId}
        name={libraries.find((library) => library.id === libraryId)!.name}
        reveal={Math.max(
          lastUploaded !== uploadAtMount.current && lastUploaded?.libraryId === libraryId ? lastUploaded.at : 0,
          panelReveal?.id === libraryId ? panelReveal.at : 0,
        )}
        summaryMode={summaryMode}
        selectedIds={selectedMaterialIds}
        scoped={scoped}
        onSelectedChange={(ids) => setDraftState((current) => ({ ...current, libraryId, materialIds: ids, scoped: true, scopeRequired: false }))}
        onUseAll={() => setDraftState((current) => ({ ...current, libraryId, materialIds: [], scoped: false, scopeRequired: false }))}
        onScopeStatus={reportScope}
      />
    )}
    <PasteTextDialog
      open={pasteOpen}
      onOpenChange={setPasteOpen}
      libraryName={currentLibrary?.name}
      onSubmitFile={(file) => addFiles([file])}
    />
    <WebClipDialog open={webClipOpen} onOpenChange={setWebClipOpen} libraryId={currentLibrary?.id} libraryName={currentLibrary?.name} />
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
            chooseLibrary(library.id)
            refresh('libraries', 'overview')
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
