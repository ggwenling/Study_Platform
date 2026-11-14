import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import { Button } from '@/components/arc/button/button'
import { useWorkspace } from '../WorkspaceData'

export function DownloadMaterial({ version, title }: { version: string; title: string }) {
  const { report } = useWorkspace()
  const [busy, setBusy] = useState(false)
  return <Button type="button" variant="secondary" loading={busy} onClick={async () => {
    setBusy(true)
    try {
      const file = await api.content(version)
      const url = URL.createObjectURL(await file.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = title
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) { report(error) } finally { setBusy(false) }
  }}>下载原件</Button>
}

export function MaterialPage({ version, page, title }: { version: string; page: number; title: string }) {
  const { report } = useWorkspace()
  const [url, setUrl] = useState('')
  useEffect(() => {
    let current = true
    let objectUrl = ''
    void api.pageImage(version, page).then((response) => response.blob()).then((blob) => {
      if (!current) return
      objectUrl = URL.createObjectURL(blob)
      setUrl(objectUrl)
    }, (error) => { if (current) report(error) })
    return () => { current = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [version, page])
  return url ? <img className="source-image" src={url} alt={title} /> : null
}
