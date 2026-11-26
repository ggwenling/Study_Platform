import { useRef, useState } from 'react'
import type { ExtractionMode } from '@/api/types'
import { Button } from '@/components/arc/button/button'
import { Dialog, DialogContent } from '@/components/arc/dialog/dialog'
import { canUseVisionForFile } from '@/visionUi'

type Target = { id: string; name: string }

export function useFileImport(vision: boolean | undefined,
  enqueue: (files: File[], target: Target, mode?: ExtractionMode) => void) {
  const [batch, setBatch] = useState<{ files: File[]; target: Target; trigger: Element | null } | null>(null)

  function addFiles(files: FileList | File[], target: Target) {
    const selected = Array.from(files)
    if (vision && selected.some((file) => canUseVisionForFile(file.name))) {
      setBatch({ files: selected, target, trigger: document.activeElement })
    }
    else enqueue(selected, target)
  }

  return { addFiles, dialog: batch && {
    files: batch.files, targetName: batch.target.name, vision: Boolean(vision),
    trigger: batch.trigger,
    onClose: () => setBatch(null),
    onImport: (mode: ExtractionMode) => {
      if (mode === 'vlm' && !vision) return
      enqueue(batch.files, batch.target, mode)
      setBatch(null)
    },
  } }
}

export function FileImportDialog({ files, targetName, vision, trigger, onClose, onImport }: {
  files: File[]
  targetName: string
  vision: boolean
  trigger: Element | null
  onClose: () => void
  onImport: (mode: ExtractionMode) => void
}) {
  const local = useRef<HTMLButtonElement>(null)
  const visionCount = files.filter((file) => canUseVisionForFile(file.name)).length
  return <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
    <DialogContent title="导入资料" description={`将 ${files.length} 份资料导入「${targetName}」`}
      closeLabel="关闭导入资料" onOpenAutoFocus={(event) => { event.preventDefault(); local.current?.focus() }}
      onCloseAutoFocus={(event) => {
        if (trigger instanceof HTMLElement && trigger.isConnected && trigger !== document.body) {
          event.preventDefault()
          trigger.focus()
        }
      }}>
      <div className="stack-form">
        {files.length === 1 && <p className="import-file-name">{files[0].name}</p>}
        <p>常规识别使用本地文字提取和 OCR。</p>
        {vision ? <p>视觉解析适合图表和扫描件，会将本批次的 {visionCount} 份图片或 PDF 发送给当前视觉模型，可能产生费用。</p>
          : <p>当前视觉模型不可用，可以继续使用常规识别。</p>}
        <div className="form-actions">
          <Button ref={local} type="button" onClick={() => onImport('ocr')}>常规识别并导入</Button>
          {vision && <Button type="button" variant="secondary" onClick={() => onImport('vlm')}>使用视觉解析并导入</Button>}
          <Button type="button" variant="ghost" onClick={onClose}>取消</Button>
        </div>
      </div>
    </DialogContent>
  </Dialog>
}
