import { useCallback, useRef, useState, useSyncExternalStore } from 'react'

const storagePrefix = 'knowloop:drafts:'
const maxDrafts = 30

export type ChatDraftValue = {
  text: string
  libraryId?: string
  materialIds: string[]
  scoped: boolean
  supplement: boolean
  summaryMode: boolean
  scopeRequired: boolean
}

type StoredDraft = ChatDraftValue & { updatedAt: number }
type DraftMap = Record<string, StoredDraft>

const emptyDraft: ChatDraftValue = {
  text: '', materialIds: [], scoped: false, supplement: false, summaryMode: false, scopeRequired: false,
}
const draftsByUser = new Map<string, DraftMap>()
const listeners = new Set<() => void>()

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

function publish() {
  for (const listener of listeners) listener()
}

function draftsFor(userId: string): DraftMap {
  const existing = draftsByUser.get(userId)
  if (existing) return existing
  const drafts: DraftMap = Object.create(null)
  try {
    const raw = window.sessionStorage.getItem(storagePrefix + userId)
    const parsed = raw ? JSON.parse(raw) : null
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      for (const [id, row] of Object.entries(parsed) as [string, Partial<StoredDraft> | null][]) {
        if (!row || typeof row !== 'object' || !Number.isFinite(row.updatedAt)) continue
        drafts[id] = {
          text: typeof row.text === 'string' ? row.text : '',
          libraryId: typeof row.libraryId === 'string' && (id !== 'home' || row.text || row.scoped === true)
            ? row.libraryId : undefined,
          materialIds: Array.isArray(row.materialIds) ? row.materialIds.filter((item) => typeof item === 'string') : [],
          scoped: row.scoped === true,
          supplement: row.supplement === true,
          summaryMode: row.summaryMode === true,
          scopeRequired: row.scopeRequired === true,
          updatedAt: row.updatedAt!,
        }
      }
    }
  } catch { /* In-memory drafts remain available if session storage is blocked. */ }
  draftsByUser.set(userId, drafts)
  return drafts
}

function writeDraft(userId: string, draftId: string, value: ChatDraftValue) {
  const drafts = draftsFor(userId)
  const latest = Math.max(0, ...Object.values(drafts).map((row) => row.updatedAt))
  const updated = { ...drafts, [draftId]: { ...value, updatedAt: Math.max(Date.now(), latest + 1) } }
  const recent = Object.fromEntries(Object.entries(updated).sort(([, a], [, b]) => b.updatedAt - a.updatedAt).slice(0, maxDrafts))
  draftsByUser.set(userId, recent)
  try { window.sessionStorage.setItem(storagePrefix + userId, JSON.stringify(recent)) } catch { /* Keep the memory copy. */ }
  publish()
}

export function clearAcceptedChatDraftText(userId: string | undefined, conversationId: string | undefined, sentText: string) {
  if (!userId) return
  const draftId = conversationId ?? 'home'
  const draft = draftsFor(userId)[draftId]
  if (draft && draft.text === sentText) {
    const scope = conversationId ? {} : { libraryId: undefined, materialIds: [], scoped: false, scopeRequired: false }
    writeDraft(userId, draftId, { ...draft, ...scope, text: '' })
  }
}

export function clearAllChatDrafts(keepUserId?: string) {
  for (const id of draftsByUser.keys()) {
    if (id !== keepUserId) draftsByUser.delete(id)
  }
  try {
    const store = window.sessionStorage
    for (let index = store.length - 1; index >= 0; index -= 1) {
      const key = store.key(index)
      if (key?.startsWith(storagePrefix) && key !== storagePrefix + keepUserId) store.removeItem(key)
    }
  } catch { /* The in-memory copy was already cleared. */ }
  publish()
}

export function useChatDrafts(userId: string | undefined, conversationId?: string) {
  const draftId = conversationId ?? 'home'
  const [local, setLocal] = useState(emptyDraft)
  const identity = `${userId ?? ''}\u0000${draftId}`
  const latestIdentity = useRef(identity)
  latestIdentity.current = identity
  const getSnapshot = useCallback(() => userId ? draftsFor(userId)[draftId] ?? emptyDraft : emptyDraft, [userId, draftId])
  const stored = useSyncExternalStore(subscribe, getSnapshot, () => emptyDraft)

  const setValue = useCallback((update: (current: ChatDraftValue) => ChatDraftValue) => {
    if (latestIdentity.current !== identity) return
    if (!userId) { setLocal(update); return }
    const current = getSnapshot()
    const next = update(current)
    if (next !== current) writeDraft(userId, draftId, next)
  }, [draftId, getSnapshot, identity, userId])

  return { value: userId ? stored : local, setValue }
}
