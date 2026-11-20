import { useState, type FormEvent } from 'react'
import { api } from '@/api/client'
import { errorMessage } from '@/api/ui'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { Input } from '@/components/arc/input/input'
import { useWorkspace } from '../WorkspaceData'

export function WebClipDialog({ open, onOpenChange, libraryId, libraryName }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  libraryId?: string
  libraryName?: string
}) {
  const { uploadQueue, refresh, notify } = useWorkspace()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  function changeOpen(next: boolean) {
    if (submitting) return
    if (!next) setError('')
    onOpenChange(next)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting) return
    if (!libraryId || !libraryName) {
      setError('请先选择要导入的知识库。')
      return
    }
    const form = new FormData(event.currentTarget)
    const url = String(form.get('url') ?? '').trim()
    const title = String(form.get('title') ?? '').trim()
    setSubmitting(true)
    setError('')
    try {
      await api.webClip(libraryId, { url, ...(title ? { title } : {}) })
      uploadQueue.markSubmitted({ id: libraryId, name: libraryName })
      refresh()
      notify(`网页已提交至「${libraryName}」，正在处理。`)
      onOpenChange(false)
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setSubmitting(false)
    }
  }

  return <Dialog open={open} onOpenChange={changeOpen}>
    <DialogContent title="导入网页" description="仅导入公开可读取网页；需要登录或动态加载的页面，请改用「随手贴文本」。" closeLabel="关闭网页导入">
      <form className="stack-form" onSubmit={(event) => void submit(event)}>
        <Input label="网页链接" name="url" type="url" maxLength={2048} placeholder="https://example.com/article" autoComplete="url" required />
        <Input label="自定义标题（可选）" name="title" maxLength={255} placeholder="留空时使用网页标题" />
        {error && <p className="error-text" role="alert">{error}</p>}
        <div className="form-actions">
          <Button type="button" variant="ghost" disabled={submitting} onClick={() => changeOpen(false)}>取消</Button>
          <Button type="submit" loading={submitting} disabled={submitting || !libraryId}>导入网页</Button>
        </div>
      </form>
    </DialogContent>
  </Dialog>
}
