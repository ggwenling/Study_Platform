import { useEffect, useEffectEvent, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { BookMarked, BrainCircuit, Download, FilePlus2, GitBranch, Layers, Pencil, Sparkles, Trash2, Upload } from 'lucide-react'
import { useNavigate } from 'react-router'
import { api } from '@/api/client'
import { ApiError } from '@/api/stream'
import type { Citation, Material, StudioArtifactKind, StudyNote } from '@/api/types'
import { errorMessage } from '@/api/ui'
import { downloadMarkdownFile, sanitizeMarkdownFilename } from '@/conversationActions'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { Input } from '@/components/arc/input/input'
import { Select } from '@/components/arc/select/select'
import { useWorkspace } from '../WorkspaceData'
import { MarkdownContent } from './MarkdownContent'
import { MindmapView } from './MindmapView'
import { NoteEditor } from './NoteEditor'

const artifactBadgeLabel: Record<string, string> = {
  study_guide: '学习指南',
  mindmap: '知识导图',
  formula_sheet: '公式速查',
  chat_note: '对话沉淀',
  note: '研究笔记',
}

const generationLabel: Record<StudioArtifactKind | 'flashcards', string> = {
  study_guide: '学习指南',
  mindmap: '知识导图',
  formula_sheet: '概念公式速查',
  flashcards: '记忆闪卡',
}

type EditorClose = 'create' | 'edit' | 'detail'
const emptyDraft = { title: '', content: '' }

export function LibraryStudio({
  kbId,
  kbName,
  readyCount,
  readyMaterials,
  revision,
  initialNotes,
  onIngested,
  onOpenSource,
  linkedNoteId,
  closeLinkedNote,
}: {
  kbId: string
  kbName: string
  readyCount: number
  readyMaterials: Material[]
  revision: number
  initialNotes?: StudyNote[]
  onIngested: () => void
  onOpenSource?: (citation: Citation) => void
  linkedNoteId?: string | null
  closeLinkedNote?: () => void
}) {
  const { notify, report, refresh } = useWorkspace()
  const navigate = useNavigate()
  const [notes, setNotes] = useState<StudyNote[] | null>(initialNotes ?? null)
  const [notesError, setNotesError] = useState('')
  const [notesRetry, setNotesRetry] = useState(0)
  const notesRequest = useRef<AbortController | null>(null)
  const reportNotesFailure = useEffectEvent(report)
  const [busy, setBusy] = useState<string | null>(null)
  const [generationTarget, setGenerationTarget] = useState<StudioArtifactKind | 'flashcards' | null>(null)
  const [allReadyMaterials, setAllReadyMaterials] = useState(true)
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<string[]>([])
  const [flashcardCount, setFlashcardCount] = useState('8')
  const [activeNote, setActiveNote] = useState<StudyNote | null>(null)
  const [editing, setEditing] = useState(false)
  const [creating, setCreating] = useState(false)
  const [editorDraft, setEditorDraft] = useState(emptyDraft)
  const [discardAction, setDiscardAction] = useState<EditorClose | null>(null)
  const canSaveDraft = Boolean(editorDraft.title.trim() && editorDraft.content.trim())
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [linkedNoteFailure, setLinkedNoteFailure] = useState('')
  const [requestedNote, setRequestedNote] = useState(linkedNoteId)
  if (requestedNote !== linkedNoteId) {
    setRequestedNote(linkedNoteId)
    setActiveNote(null)
    setEditing(false)
    setConfirmDelete(false)
    setDiscardAction(null)
    setLinkedNoteFailure('')
  }

  useEffect(() => {
    if (!linkedNoteId) return
    let live = true
    void api.studio.note(linkedNoteId).then(
      (note) => {
        if (!live) return
        if (note.knowledge_base_id === kbId && note.knowledge_base_name !== null) setActiveNote(note)
        else setLinkedNoteFailure('内容已不可用')
      },
      (error: unknown) => {
        if (!live) return
        setLinkedNoteFailure(error instanceof ApiError && [404, 422].includes(error.status ?? 0) ? '内容已不可用' : errorMessage(error))
      },
    )
    return () => { live = false }
  }, [kbId, linkedNoteId])

  useEffect(() => {
    if (initialNotes && revision === 0 && notesRetry === 0) return
    const controller = new AbortController()
    notesRequest.current = controller
    void api.studio.notes(kbId, controller.signal).then(
      (result) => { if (!controller.signal.aborted) { setNotes(result.items); setNotesError('') } },
      (error: unknown) => {
        if (controller.signal.aborted) return
        setNotesError(errorMessage(error))
        reportNotesFailure(error)
      },
    )
    return () => controller.abort()
  }, [initialNotes, kbId, revision, notesRetry])

  function reloadNotes() {
    notesRequest.current?.abort()
    setNotesRetry((value) => value + 1)
  }

  function openGenerationConfig(target: StudioArtifactKind | 'flashcards') {
    if (busy) return
    if (readyCount === 0) {
      notify('请先导入并完成至少 1 份资料的索引。')
      return
    }
    setGenerationTarget(target)
    setAllReadyMaterials(true)
    setSelectedMaterialIds([])
    setFlashcardCount('8')
  }

  async function submitGeneration(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const target = generationTarget
    if (!target || busy) return
    const materialIds = allReadyMaterials ? null : selectedMaterialIds
    if (!materialIds && !readyCount) return
    if (materialIds && !materialIds.length) return
    const form = new FormData(event.currentTarget)
    const title = String(form.get('title') ?? '').trim() || null
    const kind: StudioArtifactKind | null = target === 'flashcards' ? null : target
    setBusy(target)
    try {
      if (kind) {
        const created = await api.studio.generateNote(kbId, { kind, material_ids: materialIds, title })
        reloadNotes()
        setNotes((prev) => [created, ...(prev ?? []).filter((item) => item.id !== created.id)])
        setActiveNote(created)
        setEditing(false)
        setConfirmDelete(false)
        notify(`已生成「${created.title}」。`)
      } else {
        const deck = await api.studio.generateFlashcards(kbId, {
          count: Number(flashcardCount),
          material_ids: materialIds,
          title,
        })
        notify(`已生成 ${deck.card_count} 张记忆闪卡，可在学习页翻转复习。`)
        void navigate('/learning', { state: { tab: 'flashcards', deckId: deck.id, kbId } })
      }
      setGenerationTarget(null)
    } catch (error) {
      report(error)
    } finally {
      setBusy(null)
    }
  }

  function toggleMaterial(id: string, checked: boolean) {
    setSelectedMaterialIds((current) => {
      if (checked) return current.includes(id) || current.length >= 50 ? current : [...current, id]
      return current.filter((materialId) => materialId !== id)
    })
  }

  function finishClose(action: EditorClose) {
    setDiscardAction(null)
    if (action === 'create') setCreating(false)
    if (action === 'edit' || action === 'detail') setEditing(false)
    if (action === 'detail') {
      if (linkedNoteId) closeLinkedNote?.()
      setActiveNote(null)
      setConfirmDelete(false)
    }
  }

  function requestClose(action: EditorClose) {
    if (busy) return
    const original = action === 'create' ? emptyDraft : activeNote && editing
      ? { title: activeNote.title, content: activeNote.content_md } : null
    if (original && (original.title !== editorDraft.title || original.content !== editorDraft.content)) {
      setDiscardAction(action)
    } else finishClose(action)
  }

  async function submitCreateNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const title = editorDraft.title.trim()
    const content_md = editorDraft.content.trim()
    if (!title || !content_md) return
    setBusy('create')
    try {
      const created = await api.studio.createNote(kbId, { title, content_md })
      reloadNotes()
      setNotes((prev) => [created, ...(prev ?? []).filter((item) => item.id !== created.id)])
      setCreating(false)
      setActiveNote(created)
      setEditing(false)
      notify('研究笔记已保存。')
    } catch (error) {
      report(error)
    } finally {
      setBusy(null)
    }
  }

  async function submitUpdateNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!activeNote || busy) return
    const title = editorDraft.title.trim()
    const content_md = editorDraft.content.trim()
    if (!title || !content_md) return
    setBusy('save')
    try {
      const updated = await api.studio.updateNote(activeNote.id, { title, content_md })
      reloadNotes()
      setActiveNote(updated)
      setNotes((prev) => prev?.map((row) => (row.id === updated.id ? updated : row)) ?? null)
      setEditing(false)
      notify('笔记已更新。')
    } catch (error) {
      report(error)
    } finally {
      setBusy(null)
    }
  }

  async function ingestActiveNote() {
    if (!activeNote || busy) return
    setBusy('ingest')
    try {
      const res = await api.studio.ingestNote(activeNote.id)
      reloadNotes()
      const updated = { ...activeNote, ingested_material_id: res.material_id }
      setActiveNote(updated)
      setNotes((prev) => prev?.map((row) => (row.id === updated.id ? updated : row)) ?? null)
      refresh()
      onIngested()
      notify('笔记已作为 Markdown 资料回写入库，正在建立索引。')
    } catch (error) {
      report(error)
    } finally {
      setBusy(null)
    }
  }

  async function removeActiveNote() {
    if (!activeNote || busy) return
    setBusy('delete')
    try {
      await api.studio.deleteNote(activeNote.id)
      reloadNotes()
      setNotes((prev) => prev?.filter((row) => row.id !== activeNote.id) ?? null)
      setActiveNote(null)
      setConfirmDelete(false)
      if (linkedNoteId) closeLinkedNote?.()
      notify('笔记已删除。')
    } catch (error) {
      report(error)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="library-panel library-studio-panel" aria-label="研究笔记与知识工坊">
      <header className="library-section-heading">
        <Sparkles size={21} />
        <h2>笔记与知识工坊</h2>
        <p>沉淀对话结论，或基于库内资料一键提炼学习指南、结构导图、公式速查与闪卡</p>
      </header>

      <div className="studio-actions">
        <Button
          type="button"
          size="sm"
          variant="secondary"
          loading={busy === 'study_guide'}
          disabled={Boolean(busy)}
          onClick={() => openGenerationConfig('study_guide')}
        >
          <BookMarked size={15} /> 生成学习指南
        </Button>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          loading={busy === 'mindmap'}
          disabled={Boolean(busy)}
          onClick={() => openGenerationConfig('mindmap')}
        >
          <GitBranch size={15} /> 生成知识导图
        </Button>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          loading={busy === 'formula_sheet'}
          disabled={Boolean(busy)}
          onClick={() => openGenerationConfig('formula_sheet')}
        >
          <Sparkles size={15} /> 概念公式速查
        </Button>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          loading={busy === 'flashcards'}
          disabled={Boolean(busy)}
          onClick={() => openGenerationConfig('flashcards')}
        >
          <BrainCircuit size={15} /> 生成记忆闪卡
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={Boolean(busy)}
          onClick={() => { setEditorDraft(emptyDraft); setCreating(true) }}
        >
          <FilePlus2 size={15} /> 新建笔记
        </Button>
      </div>

      <Dialog open={Boolean(generationTarget)} onOpenChange={(open) => { if (!open && !busy) setGenerationTarget(null) }}>
        {generationTarget && <DialogContent
          title={`生成${generationLabel[generationTarget]}`}
          description={`选择「${kbName}」中的资料范围并设置生成标题。`}
          closeLabel="关闭生成配置"
        >
          <form className="studio-generation-form" onSubmit={(event) => void submitGeneration(event)}>
            <Input label="自定义标题（可选）" name="title" maxLength={255} placeholder="留空则自动命名" />
            {generationTarget === 'flashcards' && <Select
              label="闪卡数量"
              value={flashcardCount}
              onValueChange={setFlashcardCount}
              options={[
                { value: '5', label: '5 张' },
                { value: '8', label: '8 张' },
                { value: '12', label: '12 张' },
                { value: '16', label: '16 张' },
              ]}
            />}
            <fieldset className="studio-generation-scope">
              <legend>资料范围</legend>
              <div className="studio-generation-scope-options">
                <label>
                  <input
                    type="radio"
                    name="studio-material-scope"
                    value="all"
                    checked={allReadyMaterials}
                    onChange={() => setAllReadyMaterials(true)}
                  />
                  全部已就绪资料（共 {readyCount} 份）
                </label>
                <label>
                  <input
                    type="radio"
                    name="studio-material-scope"
                    value="selected"
                    checked={!allReadyMaterials}
                    onChange={() => setAllReadyMaterials(false)}
                  />
                  指定资料
                </label>
              </div>
              {!allReadyMaterials && <div className="learning-material-picker studio-generation-materials">
                <p className="muted">最多选择 50 份资料。{!selectedMaterialIds.length && '请至少选择 1 份资料。'}</p>
                <ul>
                  {readyMaterials.map((material) => <li key={material.id}>
                    <label>
                      <input
                        type="checkbox"
                        className="material-scope-checkbox"
                        aria-label={`选择资料：${material.title}`}
                        checked={selectedMaterialIds.includes(material.id)}
                        disabled={!selectedMaterialIds.includes(material.id) && selectedMaterialIds.length >= 50}
                        onChange={(event) => toggleMaterial(material.id, event.target.checked)}
                      />
                      <span>{material.title}</span>
                    </label>
                  </li>)}
                </ul>
              </div>}
              {!allReadyMaterials && !selectedMaterialIds.length && <p className="studio-generation-scope-error" role="status">请选择至少 1 份资料，或切换为全部已就绪资料。</p>}
            </fieldset>
            <div className="form-actions">
              <Button type="submit" loading={busy === generationTarget} disabled={!allReadyMaterials && !selectedMaterialIds.length}>
                确认生成
              </Button>
              <Button type="button" variant="ghost" disabled={Boolean(busy)} onClick={() => setGenerationTarget(null)}>取消</Button>
            </div>
          </form>
        </DialogContent>}
      </Dialog>

      {notesError && <div role="alert">笔记读取失败：{notesError} <Button type="button" size="sm" variant="ghost" onClick={reloadNotes}>重试读取笔记</Button></div>}
      {!notes?.length ? (
        !notesError && <p className="muted library-empty">
          {notes === null
            ? '正在读取笔记…'
            : '暂无研究笔记。你可以在对话回答下方点击「存为笔记」，或点击上方按钮一键生成知识产物。'}
        </p>
      ) : (
        <ul className="studio-note-grid">
          {notes.map((note) => (
            <li key={note.id}>
              <button
                type="button"
                className="studio-note-card"
                onClick={() => {
                  setActiveNote(note)
                  setEditing(false)
                  setConfirmDelete(false)
                }}
              >
                <div className="studio-note-head">
                  <span className="pill">{artifactBadgeLabel[note.artifact_kind] ?? '研究笔记'}</span>
                  {note.ingested_material_id && <span className="pill">已回写索引</span>}
                </div>
                <strong>{note.title}</strong>
                <p>{note.content_md.replace(/[#>*`[\]]/g, '').slice(0, 96)}</p>
              </button>
            </li>
          ))}
        </ul>
      )}

      <Dialog open={creating} onOpenChange={(open) => { if (!open) requestClose('create') }}>
        <DialogContent className="library-wide-note-dialog" title="新建研究笔记" description={`保存在「${kbName}」知识空间中，后续可随时编辑或回写为检索资料。`} closeLabel="关闭新建笔记">
          <form className="stack-form" onSubmit={(e) => void submitCreateNote(e)}>
            <Input label="笔记标题" name="title" maxLength={255} required autoFocus placeholder="例如：核心结论整理"
              value={editorDraft.title} onChange={(event) => setEditorDraft({ ...editorDraft, title: event.target.value })} />
            <NoteEditor content={editorDraft.content} onContentChange={(content) => setEditorDraft((draft) => ({ ...draft, content }))} />
            <div className="form-actions">
              <Button type="submit" loading={busy === 'create'} disabled={!canSaveDraft}>保存笔记</Button>
              <Button type="button" variant="ghost" disabled={Boolean(busy)} onClick={() => requestClose('create')}>取消</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(activeNote || linkedNoteId)}
        onOpenChange={(open) => { if (!open) requestClose('detail') }}
      >
        {linkedNoteId && !activeNote && <DialogContent title="研究笔记" description="查看搜索命中的笔记。" closeLabel="关闭笔记详情">
          {linkedNoteFailure ? <p className="error-text" role="alert">{linkedNoteFailure}</p> : <p role="status">正在读取笔记…</p>}
        </DialogContent>}
        {activeNote && (
          <DialogContent
            className={editing || activeNote.artifact_kind === 'mindmap' ? 'library-wide-note-dialog' : undefined}
            title={confirmDelete ? '删除笔记' : editing ? '编辑笔记' : activeNote.title}
            description={
              confirmDelete
                ? '删除后无法恢复该笔记，已回写入库的资料副本不会自动删除。'
                : `${artifactBadgeLabel[activeNote.artifact_kind] ?? '研究笔记'}${activeNote.edit_revision > 0 ? ` · 已修订 ${activeNote.edit_revision} 次` : ''}`
            }
            closeLabel="关闭笔记详情"
          >
            {confirmDelete ? (
              <div className="stack-form">
                <p>确认删除笔记「{activeNote.title}」吗？</p>
                <div className="form-actions">
                  <Button type="button" variant="danger" loading={busy === 'delete'} onClick={() => void removeActiveNote()}>确认删除</Button>
                  <Button type="button" variant="ghost" disabled={Boolean(busy)} onClick={() => setConfirmDelete(false)}>返回</Button>
                </div>
              </div>
            ) : editing ? (
              <form className="stack-form" onSubmit={(e) => void submitUpdateNote(e)}>
                <Input label="标题" name="title" value={editorDraft.title} maxLength={255} required
                  onChange={(event) => setEditorDraft({ ...editorDraft, title: event.target.value })} />
                <NoteEditor content={editorDraft.content} onContentChange={(content) => setEditorDraft((draft) => ({ ...draft, content }))}
                  citations={activeNote.citations} onSelectCitation={onOpenSource} />
                <div className="form-actions">
                  <Button type="submit" loading={busy === 'save'} disabled={!canSaveDraft}>保存修改</Button>
                  <Button type="button" variant="ghost" disabled={Boolean(busy)} onClick={() => requestClose('edit')}>取消编辑</Button>
                </div>
              </form>
            ) : (
              <div className="studio-note-viewer">
                <div className="studio-note-body answer">
                  {activeNote.artifact_kind === 'mindmap'
                    ? <MindmapView key={activeNote.id} contentMd={activeNote.content_md} citations={activeNote.citations} onSelectCitation={onOpenSource} />
                    : <MarkdownContent citations={activeNote.citations} onSelectCitation={onOpenSource}>{activeNote.content_md}</MarkdownContent>}
                </div>
                {activeNote.citations.length > 0 && (
                  <div className="studio-note-citations">
                    <small className="muted"><Layers size={13} /> 关联来源依据（{activeNote.citations.length}）</small>
                    <div className="citation-list" style={{ marginLeft: 0 }}>
                      {activeNote.citations.map((cit) => (
                        <Button
                          key={cit.id}
                          type="button"
                          size="sm"
                          variant="secondary"
                          className="citation"
                          onClick={() => onOpenSource?.(cit)}
                        >
                          [{cit.source_label}] {cit.title} · {cit.location_label}
                          {cit.availability === 'available' && cit.quote_status === 'unverified' && ' · 原文未验证'}
                          {cit.availability === 'available' && cit.quote_status === 'unavailable' && ' · 历史引文不可用'}
                        </Button>
                      ))}
                    </div>
                  </div>
                )}
                <div className="form-actions">
                  <Button type="button" size="sm" variant="secondary" onClick={() => {
                    setEditorDraft({ title: activeNote.title, content: activeNote.content_md })
                    setEditing(true)
                  }}>
                    <Pencil size={14} /> 编辑
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    loading={busy === 'ingest'}
                    disabled={Boolean(busy)}
                    onClick={() => void ingestActiveNote()}
                  >
                    <Upload size={14} /> {activeNote.ingested_material_id ? '重新回写入库' : '回写为知识库资料'}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      downloadMarkdownFile(
                        sanitizeMarkdownFilename(activeNote.title),
                        `# ${activeNote.title}\n\n${activeNote.content_md}\n`,
                      )
                      notify('已导出笔记 Markdown。')
                    }}
                  >
                    <Download size={14} /> 导出 .md
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={Boolean(busy)}
                    onClick={() => setConfirmDelete(true)}
                  >
                    <Trash2 size={14} /> 删除
                  </Button>
                </div>
              </div>
            )}
          </DialogContent>
        )}
      </Dialog>
      <Dialog open={discardAction !== null} onOpenChange={(open) => { if (!open) setDiscardAction(null) }}>
        {discardAction && <DialogContent title="放弃未保存的更改？" description="关闭编辑后，这次输入的内容将被清除。" closeLabel="关闭确认">
          <div className="form-actions">
            <Button type="button" variant="secondary" autoFocus onClick={() => setDiscardAction(null)}>继续编辑</Button>
            <Button type="button" variant="danger" onClick={() => finishClose(discardAction)}>放弃更改</Button>
          </div>
        </DialogContent>}
      </Dialog>
    </section>
  )
}
