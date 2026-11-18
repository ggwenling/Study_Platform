import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { BookMarked, BrainCircuit, Download, FilePlus2, GitBranch, Layers, Pencil, Sparkles, Trash2, Upload } from 'lucide-react'
import { useNavigate } from 'react-router'
import { api } from '@/api/client'
import { ApiError } from '@/api/stream'
import type { Citation, StudioArtifactKind, StudyNote } from '@/api/types'
import { errorMessage } from '@/api/ui'
import { downloadMarkdownFile, sanitizeMarkdownFilename } from '@/conversationActions'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { Input } from '@/components/arc/input/input'
import { Textarea } from '@/components/arc/textarea/textarea'
import { useWorkspace } from '../WorkspaceData'
import { MarkdownContent } from './MarkdownContent'

const artifactBadgeLabel: Record<string, string> = {
  study_guide: '学习指南',
  mindmap: '知识导图',
  formula_sheet: '公式速查',
  chat_note: '对话沉淀',
  note: '研究笔记',
}

export function LibraryStudio({
  kbId,
  kbName,
  readyCount,
  revision,
  onIngested,
  onOpenSource,
  linkedNoteId,
  closeLinkedNote,
}: {
  kbId: string
  kbName: string
  readyCount: number
  revision: number
  onIngested: () => void
  onOpenSource?: (citation: Citation) => void
  linkedNoteId?: string | null
  closeLinkedNote?: () => void
}) {
  const { notify, report, refresh } = useWorkspace()
  const navigate = useNavigate()
  const [notes, setNotes] = useState<StudyNote[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [activeNote, setActiveNote] = useState<StudyNote | null>(null)
  const [editing, setEditing] = useState(false)
  const [creating, setCreating] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [linkedNoteFailure, setLinkedNoteFailure] = useState('')

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

  const loadNotes = useCallback(async () => {
    if (!api.studio?.notes) return
    setLoading(true)
    try {
      const res = await api.studio.notes(kbId)
      setNotes(res.items)
    } catch (error) {
      report(error)
    } finally {
      setLoading(false)
    }
  }, [kbId, report])

  useEffect(() => {
    void loadNotes()
  }, [loadNotes, revision])

  async function generateArtifact(kind: StudioArtifactKind) {
    if (busy) return
    if (readyCount === 0) {
      notify('请先导入并完成至少 1 份资料的索引。')
      return
    }
    setBusy(kind)
    try {
      const created = await api.studio.generateNote(kbId, { kind })
      setNotes((prev) => [created, ...prev.filter((item) => item.id !== created.id)])
      setActiveNote(created)
      setEditing(false)
      setConfirmDelete(false)
      notify(`已生成「${created.title}」。`)
    } catch (error) {
      report(error)
    } finally {
      setBusy(null)
    }
  }

  async function generateFlashcards() {
    if (busy) return
    if (readyCount === 0) {
      notify('请先导入并完成至少 1 份资料的索引。')
      return
    }
    setBusy('flashcards')
    try {
      const deck = await api.studio.generateFlashcards(kbId, { count: 8 })
      notify(`已生成 ${deck.card_count} 张记忆闪卡，可在学习页翻转复习。`)
      void navigate('/learning', { state: { tab: 'flashcards', deckId: deck.id, kbId } })
    } catch (error) {
      report(error)
    } finally {
      setBusy(null)
    }
  }

  async function submitCreateNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const form = new FormData(event.currentTarget)
    const title = String(form.get('title') ?? '').trim()
    const content_md = String(form.get('content_md') ?? '').trim()
    if (!title || !content_md) return
    setBusy('create')
    try {
      const created = await api.studio.createNote(kbId, { title, content_md })
      setNotes((prev) => [created, ...prev])
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
    const form = new FormData(event.currentTarget)
    const title = String(form.get('title') ?? '').trim()
    const content_md = String(form.get('content_md') ?? '').trim()
    if (!title || !content_md) return
    setBusy('save')
    try {
      const updated = await api.studio.updateNote(activeNote.id, { title, content_md })
      setActiveNote(updated)
      setNotes((prev) => prev.map((row) => (row.id === updated.id ? updated : row)))
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
      const updated = { ...activeNote, ingested_material_id: res.material_id }
      setActiveNote(updated)
      setNotes((prev) => prev.map((row) => (row.id === updated.id ? updated : row)))
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
      setNotes((prev) => prev.filter((row) => row.id !== activeNote.id))
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
          onClick={() => void generateArtifact('study_guide')}
        >
          <BookMarked size={15} /> 生成学习指南
        </Button>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          loading={busy === 'mindmap'}
          disabled={Boolean(busy)}
          onClick={() => void generateArtifact('mindmap')}
        >
          <GitBranch size={15} /> 生成知识导图
        </Button>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          loading={busy === 'formula_sheet'}
          disabled={Boolean(busy)}
          onClick={() => void generateArtifact('formula_sheet')}
        >
          <Sparkles size={15} /> 概念公式速查
        </Button>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          loading={busy === 'flashcards'}
          disabled={Boolean(busy)}
          onClick={() => void generateFlashcards()}
        >
          <BrainCircuit size={15} /> 生成记忆闪卡
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={Boolean(busy)}
          onClick={() => setCreating(true)}
        >
          <FilePlus2 size={15} /> 新建笔记
        </Button>
      </div>

      {notes.length === 0 ? (
        <p className="muted library-empty">
          {loading
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

      <Dialog open={creating} onOpenChange={(open) => { if (!busy) setCreating(open) }}>
        <DialogContent title="新建研究笔记" description={`保存在「${kbName}」知识空间中，后续可随时编辑或回写为检索资料。`} closeLabel="关闭新建笔记">
          <form className="stack-form" onSubmit={(e) => void submitCreateNote(e)}>
            <Input label="笔记标题" name="title" maxLength={255} required autoFocus placeholder="例如：核心结论整理" />
            <Textarea label="Markdown 内容" name="content_md" rows={8} maxLength={200000} required placeholder="支持 Markdown 与 $...$ 数学公式…" />
            <div className="form-actions">
              <Button type="submit" loading={busy === 'create'}>保存笔记</Button>
              <Button type="button" variant="ghost" disabled={Boolean(busy)} onClick={() => setCreating(false)}>取消</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(activeNote || linkedNoteId)}
        onOpenChange={(open) => {
          if (!open && !busy) {
            if (linkedNoteId) closeLinkedNote?.()
            setActiveNote(null)
            setEditing(false)
            setConfirmDelete(false)
          }
        }}
      >
        {linkedNoteId && !activeNote && <DialogContent title="研究笔记" description="查看搜索命中的笔记。" closeLabel="关闭笔记详情">
          {linkedNoteFailure ? <p className="error-text" role="alert">{linkedNoteFailure}</p> : <p role="status">正在读取笔记…</p>}
        </DialogContent>}
        {activeNote && (
          <DialogContent
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
                <Input label="标题" name="title" defaultValue={activeNote.title} maxLength={255} required />
                <Textarea label="Markdown 内容" name="content_md" defaultValue={activeNote.content_md} rows={10} maxLength={200000} required />
                <div className="form-actions">
                  <Button type="submit" loading={busy === 'save'}>保存修改</Button>
                  <Button type="button" variant="ghost" disabled={Boolean(busy)} onClick={() => setEditing(false)}>取消编辑</Button>
                </div>
              </form>
            ) : (
              <div className="studio-note-viewer">
                <div className="studio-note-body answer">
                  <MarkdownContent
                    citations={activeNote.citations}
                    onSelectCitation={(cit) => onOpenSource?.(cit)}
                  >
                    {activeNote.content_md}
                  </MarkdownContent>
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
                        </Button>
                      ))}
                    </div>
                  </div>
                )}
                <div className="form-actions">
                  <Button type="button" size="sm" variant="secondary" onClick={() => setEditing(true)}>
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
    </section>
  )
}
