import { useState } from 'react'
import { errorMessage } from '@/api/ui'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'

export function VisionReparseDialog({ title, mimeType, pageCount, onConfirm, onClose }: {
  title: string
  mimeType: string | null
  pageCount?: number | null
  onConfirm: () => Promise<void>
  onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const isPdf = mimeType === 'application/pdf'
  const estimate = isPdf
    ? pageCount == null ? '页数暂不可用，无法估算模型调用次数。' : `预计处理 ${pageCount} 页，约调用视觉模型 ${pageCount} 次。`
    : '将发送 1 张图，约调用视觉模型 1 次。'

  async function confirm() {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await onConfirm()
      onClose()
    } catch (failure) {
      setError(errorMessage(failure))
      setBusy(false)
    }
  }

  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
    <DialogContent title="使用视觉理解重新解析？" description="只有确认后，资料才会发送给当前默认视觉模型。" closeLabel="关闭视觉解析确认">
      <div className="stack-form">
        <p>资料：{title}</p>
        <p>{estimate}</p>
        <p>视觉模型按实际用量收费，费用取决于模型与内容；超出服务端页数上限时会拒绝解析。</p>
        {error && <p className="error-text" role="alert">{error}</p>}
        <div className="form-actions">
          <Button type="button" loading={busy} onClick={() => void confirm()}>确认并开始视觉解析</Button>
          <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>取消</Button>
        </div>
      </div>
    </DialogContent>
  </Dialog>
}
