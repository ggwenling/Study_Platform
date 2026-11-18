import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '@/api/client'
import type { Conversation } from '@/api/types'
import { errorMessage } from '@/api/ui'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { Input } from '@/components/arc/input/input'

export type ConversationDialogTarget = {
  mode: 'rename' | 'delete'
  conversation: Pick<Conversation, 'id' | 'title'>
}

export function ConversationDialogs({
  target,
  onClose,
  onRenamed,
  onDeleted,
  report,
  notify,
}: {
  target: ConversationDialogTarget | null
  onClose: () => void
  onRenamed?: (updated: Conversation) => void
  onDeleted?: (deletedId: string) => void
  report: (error: unknown) => void
  notify: (message: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function handleOpenChange(open: boolean) {
    if (!open && !busy) {
      setError('')
      onClose()
    }
  }

  async function submitRename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!target || busy) return
    const title = String(new FormData(event.currentTarget).get('title') ?? '').trim()
    if (!title) return
    setBusy(true)
    setError('')
    try {
      const updated = await api.updateConversation(target.conversation.id, { title })
      onRenamed?.(updated)
      notify('会话已重命名。')
      onClose()
    } catch (failure) {
      setError(errorMessage(failure))
      report(failure)
    } finally {
      setBusy(false)
    }
  }

  async function confirmDelete() {
    if (!target || busy) return
    setBusy(true)
    setError('')
    try {
      await api.deleteConversation(target.conversation.id)
      onDeleted?.(target.conversation.id)
      notify('会话已删除。')
      onClose()
    } catch (failure) {
      setError(errorMessage(failure))
      report(failure)
    } finally {
      setBusy(false)
    }
  }

  const label = target?.conversation.title?.trim() || '未命名对话'

  return (
    <Dialog open={Boolean(target)} onOpenChange={handleOpenChange}>
      {target?.mode === 'rename' && (
        <DialogContent key={`rename:${target.conversation.id}`} title="重命名会话" closeLabel="关闭重命名会话">
          <form className="stack-form" onSubmit={(event) => void submitRename(event)}>
            <Input
              label="会话标题"
              name="title"
              defaultValue={target.conversation.title ?? ''}
              maxLength={200}
              required
              autoFocus
            />
            {error && <p className="error-text" role="alert">{error}</p>}
            <div className="form-actions">
              <Button type="submit" loading={busy}>保存</Button>
              <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>取消</Button>
            </div>
          </form>
        </DialogContent>
      )}
      {target?.mode === 'delete' && (
        <DialogContent
          key={`delete:${target.conversation.id}`}
          title="删除会话"
          description="删除后无法恢复该会话的消息记录，知识库中的资料不会受影响。"
          closeLabel="关闭删除会话"
        >
          <div className="stack-form">
            <p>确认删除会话「{label}」吗？</p>
            {error && <p className="error-text" role="alert">{error}</p>}
            <div className="form-actions">
              <Button type="button" variant="danger" loading={busy} onClick={() => void confirmDelete()}>确认删除</Button>
              <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>取消</Button>
            </div>
          </div>
        </DialogContent>
      )}
    </Dialog>
  )
}
