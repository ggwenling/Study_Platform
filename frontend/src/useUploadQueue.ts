import { useCallback, useEffect, useRef, useState } from 'react'
import { uploadMaterial } from '@/api/client'

export type UploadQueueItem = {
  id: string
  file: File
  name: string
  libraryId: string
  libraryName: string
  status: 'queued' | 'uploading' | 'done' | 'failed' | 'cancelled'
}

const ALLOWED_EXTENSIONS = new Set(['.pdf', '.docx', '.txt', '.md', '.markdown', '.png', '.jpg', '.jpeg'])

export function isSupportedUploadFile(filename: string): boolean {
  const dot = filename.lastIndexOf('.')
  if (dot < 0) return false
  return ALLOWED_EXTENSIONS.has(filename.slice(dot).toLowerCase())
}

export function clipboardImageFiles(clipboard: DataTransfer | null): File[] {
  if (!clipboard) return []
  const direct = Array.from(clipboard.files ?? []).filter((file) =>
    file.type === 'image/png' || file.type === 'image/jpeg',
  )
  const items = direct.length
    ? direct
    : Array.from(clipboard.items ?? [])
        .filter((item) => item.kind === 'file' && (item.type === 'image/png' || item.type === 'image/jpeg'))
        .map((item) => item.getAsFile())
        .filter((file): file is File => file !== null)
  if (!items.length) return []
  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 15)
  return items.map((file, index) => {
    const ext = file.type === 'image/jpeg' ? '.jpg' : '.png'
    const generic = !file.name || /^image\.(png|jpe?g)$/i.test(file.name)
    if (!generic) return file
    const suffix = items.length > 1 ? `-${index + 1}` : ''
    return new File([file], `截图-${stamp}${suffix}${ext}`, { type: file.type })
  })
}

let nextUploadSeq = 1

export function useUploadQueue({
  userId,
  maxUploadBytes,
  refresh,
  notify,
  report,
  onUploadedToLibrary,
}: {
  userId?: string | null
  maxUploadBytes?: number
  refresh: () => void
  notify: (message: string) => void
  report: (error: unknown) => void
  onUploadedToLibrary?: (libraryId: string) => void
}) {
  const [items, setItems] = useState<UploadQueueItem[]>([])
  const itemsRef = useRef<UploadQueueItem[]>([])
  itemsRef.current = items
  const activeController = useRef<{ id: string; controller: AbortController } | null>(null)
  const callbacks = useRef({ refresh, notify, report, onUploadedToLibrary })
  callbacks.current = { refresh, notify, report, onUploadedToLibrary }

  useEffect(() => () => {
    activeController.current?.controller.abort()
    activeController.current = null
  }, [userId])

  useEffect(() => {
    if (activeController.current) return
    const next = items.find((item) => item.status === 'queued')
    if (!next) return
    const controller = new AbortController()
    activeController.current = { id: next.id, controller }
    setItems((current) => current.map((row) => row.id === next.id ? { ...row, status: 'uploading' } : row))

    void uploadMaterial(next.libraryId, next.file, undefined, controller.signal).then(
      () => {
        if (controller.signal.aborted) return
        activeController.current = null
        setItems((current) => current.map((row) => row.id === next.id ? { ...row, status: 'done' } : row))
        callbacks.current.refresh()
        callbacks.current.onUploadedToLibrary?.(next.libraryId)
      },
      (error) => {
        if (controller.signal.aborted) return
        activeController.current = null
        setItems((current) => current.map((row) => row.id === next.id ? { ...row, status: 'failed' } : row))
        callbacks.current.report(error)
      },
    )
  }, [items])

  const enqueue = useCallback((files: FileList | File[], target: { id: string; name: string }) => {
    const incoming = Array.from(files)
    if (!incoming.length) return 0
    const accepted: UploadQueueItem[] = []
    for (const file of incoming) {
      if (!isSupportedUploadFile(file.name)) {
        callbacks.current.notify(`不支持的文件类型：「${file.name}」（支持 PDF、DOCX、TXT、MD、PNG、JPG）。`)
        continue
      }
      if (maxUploadBytes && file.size > maxUploadBytes) {
        callbacks.current.notify(`「${file.name}」超过服务器允许的大小。`)
        continue
      }
      if (file.size === 0) {
        callbacks.current.notify(`「${file.name}」内容为空。`)
        continue
      }
      accepted.push({
        id: `up-${nextUploadSeq++}`,
        file,
        name: file.name,
        libraryId: target.id,
        libraryName: target.name,
        status: 'queued',
      })
    }
    if (accepted.length) {
      setItems((current) => [...current, ...accepted])
      callbacks.current.onUploadedToLibrary?.(target.id)
    }
    return accepted.length
  }, [maxUploadBytes])

  const retryItem = useCallback((id: string) => {
    setItems((current) => current.map((row) =>
      row.id === id && (row.status === 'failed' || row.status === 'cancelled')
        ? { ...row, status: 'queued' }
        : row,
    ))
  }, [])

  const cancelItem = useCallback((id: string) => {
    if (activeController.current?.id === id) {
      activeController.current.controller.abort()
      activeController.current = null
    }
    setItems((current) => current.map((row) =>
      row.id === id && (row.status === 'queued' || row.status === 'uploading')
        ? { ...row, status: 'cancelled' }
        : row,
    ))
  }, [])

  const dismissFinished = useCallback(() => {
    setItems((current) => current.filter((row) => row.status === 'queued' || row.status === 'uploading'))
  }, [])

  const uploading = items.some((row) => row.status === 'uploading' || row.status === 'queued')
  const latestNote = (() => {
    const currentUploading = items.find((row) => row.status === 'uploading')
    const queuedCount = items.filter((row) => row.status === 'queued').length
    if (currentUploading) {
      const tail = queuedCount > 0 ? `（队列剩余 ${queuedCount} 份）` : ''
      return `正在上传「${currentUploading.name}」至「${currentUploading.libraryName}」…${tail}`
    }
    const last = items.at(-1)
    if (!last) return ''
    if (last.status === 'done') return `「${last.name}」已提交至「${last.libraryName}」。`
    if (last.status === 'failed') return `「${last.name}」上传失败，请重试。`
    if (last.status === 'cancelled') return `「${last.name}」已取消上传。`
    return ''
  })()

  return { items, uploading, latestNote, enqueue, retryItem, cancelItem, dismissFinished }
}
