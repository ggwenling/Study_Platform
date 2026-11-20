import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '@/api/client'
import { ApiError } from '@/api/stream'
import type { Capabilities, Conversation, Dashboard, KnowledgeBase, User } from '@/api/types'
import { allPages, errorMessage } from '@/api/ui'
import { useChat } from './useChat'
import type { ChatController } from './useChat'
import { useAiJobs } from './useAiJobs'
import { useUploadQueue } from './useUploadQueue'

export type NoticeOptions = {
  action?: { label: string; onClick: () => void }
  durationMs?: number
}

type Data = {
  user: User | null
  libraries: KnowledgeBase[]
  recent: Conversation[]
  overview: Dashboard | null
  capabilities: Capabilities | null
  revision: number
  loading: boolean
  refresh: () => void
  notify: (message: string, options?: NoticeOptions) => void
  report: (error: unknown) => void
  requireLogin: () => void
  chat: ChatController
  uploadQueue: ReturnType<typeof useUploadQueue>
  libraryUploadQueue: ReturnType<typeof useUploadQueue>
  aiJobs: ReturnType<typeof useAiJobs>
}

const Workspace = createContext<Data | null>(null)

export function useWorkspace() {
  const data = useContext(Workspace)
  if (!data) throw new Error('WorkspaceData is required')
  return data
}

export function WorkspaceData({ user, notify, requireLogin, onExpired, children }: {
  user: User | null
  notify: Data['notify']
  requireLogin: Data['requireLogin']
  onExpired: () => void
  children: ReactNode
}) {
  const [revision, setRevision] = useState(0)
  const [libraries, setLibraries] = useState<KnowledgeBase[]>([])
  const [recent, setRecent] = useState<Conversation[]>([])
  const [overview, setOverview] = useState<Dashboard | null>(null)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [loading, setLoading] = useState(Boolean(user))
  const refresh = () => setRevision((value) => value + 1)

  function report(error: unknown) {
    if (error instanceof ApiError && error.status === 401) {
      onExpired()
      requireLogin()
    }
    notify(errorMessage(error))
  }

  const chat = useChat(refresh, report)
  const aiJobs = useAiJobs({ userId: user?.id, notify, report, refresh })
  const libraryUploadQueue = useUploadQueue({ userId: user?.id, maxUploadBytes: capabilities?.max_upload_bytes, refresh, notify, report })
  useEffect(() => {
    if (!user) return
    let active = true
    void Promise.allSettled([
      allPages(api.knowledgeBases).then((data) => { if (active) setLibraries(data) }),
      api.conversations().then((data) => { if (active) setRecent(data.items) }),
      api.overview().then((data) => { if (active) setOverview(data) }),
      api.capabilities().then((data) => { if (active) setCapabilities(data) }),
    ]).then((results) => {
      if (!active) return
      setLoading(false)
      const failure = results.find((result) => result.status === 'rejected' && result.reason instanceof ApiError && result.reason.status === 401)
        ?? results.find((result) => result.status === 'rejected')
      if (failure?.status === 'rejected') report(failure.reason)
    })
    return () => { active = false }
  }, [user, revision])

  return (
    <Workspace.Provider value={{ user, libraries, recent, overview, capabilities, revision, loading, refresh, notify, report, requireLogin, chat, uploadQueue: libraryUploadQueue, libraryUploadQueue, aiJobs }}>
      {children}
    </Workspace.Provider>
  )
}
