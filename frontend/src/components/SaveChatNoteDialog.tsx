import { useEffect, useState, type FormEvent } from 'react'
import { api } from '@/api/client'
import { errorMessage } from '@/api/ui'
import type { StudyNote } from '@/api/types'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { Input } from '@/components/arc/input/input'
import { Select } from '@/components/arc/select/select'

export type SaveChatNoteRequest = { title: string } | { noteId: string; sectionHeading: string }

export function SaveChatNoteDialog({ libraryId, libraryName, titleSuggestion, content, onSave, onClose, description, contentLabel }: {
  libraryId: string
  libraryName: string
  titleSuggestion: string
  content: string
  onSave: (request: SaveChatNoteRequest) => Promise<void>
  onClose: () => void
  description?: string
  contentLabel?: string
}) {
  const [append, setAppend] = useState(false)
  const [title, setTitle] = useState(titleSuggestion)
  const [heading, setHeading] = useState(titleSuggestion)
  const [noteId, setNoteId] = useState('')
  const [notes, setNotes] = useState<Pick<StudyNote, 'id' | 'title'>[] | null>(null)
  const [notesError, setNotesError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')

  useEffect(() => {
    if (!append || notes) return
    const abort = new AbortController()
    api.studio.notes(libraryId, abort.signal).then(({ items }) => {
      if (!abort.signal.aborted) setNotes(items.map(({ id, title }) => ({ id, title })))
    }, (error) => {
      if (!abort.signal.aborted) setNotesError(errorMessage(error))
    })
    return () => abort.abort()
  }, [append, attempt, libraryId, notes])

  function chooseMode(next: boolean) {
    setAppend(next)
    setSaveError('')
    setNotesError('')
  }

  const canSave = append ? Boolean(notes?.some((note) => note.id === noteId)) : Boolean(title.trim())

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSave || saving) return
    setSaving(true)
    setSaveError('')
    try {
      await onSave(append ? { noteId, sectionHeading: heading.trim() } : { title: title.trim() })
    } catch (error) {
      setSaveError(errorMessage(error))
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !saving) onClose() }}>
      <DialogContent title="存为笔记" description={description ?? `将本轮回答保存到「${libraryName}」。`} closeLabel="关闭存为笔记">
        <form className="stack-form" onSubmit={submit}>
          <div className="form-actions" role="group" aria-label="保存方式">
            <Button type="button" variant={append ? 'secondary' : 'primary'} aria-pressed={!append} disabled={saving} onClick={() => chooseMode(false)}>新建研究笔记</Button>
            <Button type="button" variant={append ? 'primary' : 'secondary'} aria-pressed={append} disabled={saving} onClick={() => chooseMode(true)}>追加到已有笔记</Button>
          </div>
          {append ? <>
            {notesError ? (
              <p className="error-text" role="alert">{notesError} <Button type="button" size="sm" variant="secondary" onClick={() => { setNotesError(''); setAttempt((current) => current + 1) }}>重新读取笔记</Button></p>
            ) : notes === null ? <p className="muted" role="status">正在读取本库笔记…</p>
              : notes.length === 0 ? <p className="muted">本库暂无笔记，可以切换到「新建研究笔记」。</p>
                : <>
                  <Select label="目标笔记" placeholder="选择本库笔记" value={noteId} onValueChange={setNoteId}
                    options={notes.map(({ id, title }) => ({ value: id, label: title }))} disabled={saving} />
                  <Input label="小节标题（可选）" value={heading} onChange={(event) => setHeading(event.target.value)} maxLength={255} disabled={saving} />
                </>}
          </> : <>
            <Input label="笔记标题" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} required disabled={saving} />
            <div className="save-note-preview">
              <strong>{contentLabel ?? '回答摘要'}</strong>
              <p>{content.length > 320 ? `${content.slice(0, 320)}…` : content}</p>
            </div>
          </>}
          {saveError && <p className="error-text" role="alert">{saveError}</p>}
          <div className="form-actions">
            <Button type="submit" disabled={!canSave} loading={saving}>{append ? '追加到笔记' : '保存笔记'}</Button>
            <Button type="button" variant="ghost" disabled={saving} onClick={onClose}>取消</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
