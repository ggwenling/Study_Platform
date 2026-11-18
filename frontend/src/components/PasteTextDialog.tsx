import { useState } from 'react'
import type { FormEvent } from 'react'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { Input } from '@/components/arc/input/input'
import { Textarea } from '@/components/arc/textarea/textarea'

function deriveNoteFilename(rawTitle: string, content: string): string {
  const candidate = rawTitle.trim()
    || content
      .split(/\r?\n/)
      .map((line) => line.replace(/^#{1,6}\s+/, '').trim())
      .find(Boolean)
      ?.slice(0, 48)
    || `笔记-${new Date().toISOString().slice(0, 10)}`
  const cleaned = candidate.replace(/[\\/:*?"<>|]/g, '-').trim() || '随手贴笔记'
  return /\.(md|markdown|txt)$/i.test(cleaned) ? cleaned : `${cleaned}.md`
}

export function PasteTextDialog({
  open,
  onOpenChange,
  libraryName,
  onSubmitFile,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  libraryName?: string
  onSubmitFile: (file: File) => void
}) {
  const [error, setError] = useState('')

  function handleOpenChange(next: boolean) {
    if (!next) setError('')
    onOpenChange(next)
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const title = String(form.get('title') ?? '')
    const content = String(form.get('content') ?? '').trim()
    if (!content) {
      setError('请输入要保存的文本内容。')
      return
    }
    const filename = deriveNoteFilename(title, content)
    const file = new File([content], filename, { type: 'text/markdown' })
    setError('')
    onSubmitFile(file)
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        title="随手贴文本"
        description={libraryName ? `将笔记或摘录作为 Markdown 资料存入「${libraryName}」并自动索引。` : '将笔记或摘录作为 Markdown 资料入库。'}
        closeLabel="关闭随手贴文本"
      >
        <form className="stack-form" onSubmit={submit}>
          <Input
            label="标题（可选）"
            name="title"
            maxLength={120}
            placeholder="留空则按首行自动命名"
            autoFocus
          />
          <Textarea
            label="文本或 Markdown 内容"
            name="content"
            rows={7}
            maxLength={200000}
            required
            placeholder="支持带 # 标题的 Markdown 或普通文本…"
          />
          {error && <p className="error-text" role="alert">{error}</p>}
          <div className="form-actions">
            <Button type="submit">添加并建立索引</Button>
            <Button type="button" variant="ghost" onClick={() => handleOpenChange(false)}>取消</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
