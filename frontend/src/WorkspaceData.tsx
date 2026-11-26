import { createContext, useCallback, useContext, useEffect, useEffectEvent, useRef, useState } from 'react'
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

const initialRevisions = {
  libraries: 0, conversations: 0, overview: 0, capabilities: 0, materials: 0,
  messages: 0, notes: 0, learning: 0, usage: 0,
}
type Resource = keyof typeof initialRevisions

type Data = {
  user: User | null
  libraries: KnowledgeBase[]
  recent: Conversation[]
  overview: Dashboard | null
  capabilities: Capabilities | null
  revisions: typeof initialRevisions
  loading: boolean
  refresh: (...resources: [Resource, ...Resource[]]) => void
  notify: (message: string, options?: NoticeOptions) => void
  report: (error: unknown) => void
  requireLogin: () => void
  chat: ChatController
  uploadQueue: ReturnType<typeof useUploadQueue>
  libraryUploadQueue: ReturnType<typeof useUploadQueue>
  aiJobs: ReturnType<typeof useAiJobs>
}

const Workspace = createContext<Data | null>(null)

const emptyLibraries: KnowledgeBase[] = []
const emptyRecent: Conversation[] = []
const loadLibraries = () => allPages(api.knowledgeBases)
const loadRecent = () => api.conversations().then((page) => page.items)

function useResource<T>(user: User | null, revision: number, load: () => Promise<T>, report: (error: unknown) => void) {
  const [value, setValue] = useState<T | null>(null)
  const [loading, setLoading] = useState(Boolean(user))
  const reportFailure = useEffectEvent(report)
  useEffect(() => {
    if (!user) return
    let active = true
    void load().then(
      (data) => { if (active) { setValue(data); setLoading(false) } },
      (error) => { if (active) { setLoading(false); reportFailure(error) } },
    )
    return () => { active = false }
  }, [user, revision, load])
  return { value, loading }
}

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
  const [revisions, setRevisions] = useState(initialRevisions)
  const expired = useRef(false)
  const refresh: Data['refresh'] = useCallback((...resources) => {
    api.invalidateWorkspaceReads(resources)
    setRevisions((current) => {
      const next = { ...current }
      for (const resource of resources) next[resource] += 1
      return next
    })
  }, [])

  function report(error: unknown) {
    if (error instanceof ApiError && error.status === 401) {
      if (expired.current) return
      expired.current = true
      onExpired()
      requireLogin()
    }
    notify(errorMessage(error))
  }

  const { value: libraries, loading: loadingLibraries } = useResource(user, revisions.libraries, loadLibraries, report)
  const { value: recent, loading: loadingRecent } = useResource(user, revisions.conversations, loadRecent, report)
  const { value: overview, loading: loadingOverview } = useResource(user, revisions.overview, api.overview, report)
  const { value: capabilities, loading: loadingCapabilities } = useResource(user, revisions.capabilities, api.capabilities, report)
  const loading = loadingLibraries || loadingRecent || loadingOverview || loadingCapabilities
  const chat = useChat(() => refresh('messages', 'conversations', 'overview', 'usage'), report)
  const aiJobs = useAiJobs({ userId: user?.id, notify, report, refresh: () => refresh('notes', 'learning', 'overview', 'usage') })
  const libraryUploadQueue = useUploadQueue({
    userId: user?.id, maxUploadBytes: capabilities?.max_upload_bytes,
    maxPdfUploadBytes: capabilities?.max_pdf_upload_bytes, refresh: () => refresh('materials', 'overview'), notify, report,
  })

  return (
    <Workspace.Provider value={{ user, libraries: libraries ?? emptyLibraries, recent: recent ?? emptyRecent, overview, capabilities, revisions, loading, refresh, notify, report, requireLogin, chat, uploadQueue: libraryUploadQueue, libraryUploadQueue, aiJobs }}>
      {children}
    </Workspace.Provider>
  )
}
