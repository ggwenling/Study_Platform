import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { api, editTurn, retryTurn, streamChat } from '@/api/client'
import type { ChatPhase, ChatRequest, Citation, Message, Scope, Turn } from '@/api/types'

type Active = { conversationId: string; turnId: string; assistantId: string; phase: ChatPhase | null }
type Pending = { body: ChatRequest; key: string }
type Recovery = { conversationId: string; abort: AbortController; promise: Promise<void> }
type RunAction =
  | { kind: 'send'; request: Pending }
  | { kind: 'retry'; conversationId: string; turnId: string }
  | { kind: 'edit'; conversationId: string; turnId: string; content: string }

const homeKey = '__new__'
const terminal = (status: string) => ['completed', 'failed', 'stopped', 'cancelled'].includes(status)

function turnMessages(turn: Turn): Message[] {
  const status = turn.status === 'cancelled' ? 'stopped' : turn.status
  return turn.messages.map((message) => message.role === 'assistant' && terminal(status)
    ? { ...message, status: status as Message['status'] } : message)
}

function pause(signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve() }
    const timer = setTimeout(finish, 2000)
    signal.addEventListener('abort', finish, { once: true })
    if (signal.aborted) finish()
  })
}

function sameSavedMessage(accepted: Message, saved: Message) {
  return accepted.content === saved.content && accepted.status === saved.status &&
    accepted.citations.length === saved.citations.length &&
    accepted.citations.every((citation, index) => citation.id === saved.citations[index]?.id)
}

export function useChat(refresh: () => void, report: (error: unknown) => void) {
  const navigate = useNavigate()
  const location = useLocation()
  const locationRef = useRef(location)
  locationRef.current = location
  const controllers = useRef(new Map<string, AbortController>())
  const recoveries = useRef(new Map<string, Recovery>())
  const cachedRef = useRef<Record<string, Message[]>>({})
  const acceptedRef = useRef(new Map<string, Map<string, Message>>())
  const [activeByConversation, setActiveByConversation] = useState<Record<string, Active>>({})
  const [busyByConversation, setBusyByConversation] = useState<Record<string, boolean>>({})
  const [cached, setCached] = useState<Record<string, Message[]>>({})
  const [acceptedMessages, setAcceptedMessages] = useState<Record<string, Message[]>>({})
  const [pendingByConversation, setPendingByConversation] = useState<Record<string, Pending>>({})
  const [recoveryErrors, setRecoveryErrors] = useState<Record<string, boolean>>({})
  const callbacks = useRef({ refresh, report })
  callbacks.current = { refresh, report }

  useEffect(() => () => {
    for (const controller of controllers.current.values()) controller.abort()
    controllers.current.clear()
    for (const recovery of recoveries.current.values()) recovery.abort.abort()
    recoveries.current.clear()
    cachedRef.current = {}
    acceptedRef.current.clear()
  }, [])

  function accept(id: string, messages: Message[]) {
    const rows = new Map(acceptedRef.current.get(id) ?? [])
    for (const message of messages) rows.set(message.id, message)
    acceptedRef.current.set(id, rows)
    setAcceptedMessages((current) => ({ ...current, [id]: [...rows.values()] }))
  }

  function merge(id: string, messages: Message[], fromRecovery = false, isAccepted = false) {
    const rows = new Map((cachedRef.current[id] ?? []).map((item) => [item.id, item]))
    const acceptedRows: Message[] = []
    for (const message of messages) {
      const existing = rows.get(message.id)
      if (fromRecovery && existing && terminal(existing.status) && !terminal(message.status)) continue
      rows.set(message.id, message)
      if (isAccepted) acceptedRows.push(message)
    }
    cachedRef.current = { ...cachedRef.current, [id]: [...rows.values()] }
    setCached(cachedRef.current)
    if (acceptedRows.length) accept(id, acceptedRows)
  }

  const acknowledgeSaved = useCallback((conversationId: string, messages: Message[]) => {
    const accepted = acceptedRef.current.get(conversationId)
    if (!accepted?.size) return
    const saved = new Map(messages.map((message) => [message.id, message]))
    let changed = false
    for (const [id, message] of accepted) {
      const row = saved.get(id)
      if (row && sameSavedMessage(message, row)) {
        accepted.delete(id)
        changed = true
      }
    }
    if (!changed) return
    if (accepted.size) {
      setAcceptedMessages((current) => ({ ...current, [conversationId]: [...accepted.values()] }))
    } else {
      acceptedRef.current.delete(conversationId)
      setAcceptedMessages((current) => {
        const next = { ...current }
        delete next[conversationId]
        return next
      })
    }
  }, [])

  // Read-only recovery: never resubmit a request or start another paid generation.
  const startRecovery = useCallback((conversationId: string, turnId: string, preserveAccepted = false): Recovery => {
    const existing = recoveries.current.get(turnId)
    if (existing) return existing
    const abort = new AbortController()
    const recovery: Recovery = { conversationId, abort, promise: Promise.resolve() }
    recoveries.current.set(turnId, recovery)
    setRecoveryErrors((current) => ({ ...current, [turnId]: false }))
    recovery.promise = (async () => {
      let timedOut = false
      const deadline = setTimeout(() => { timedOut = true; abort.abort() }, 120_000)
      try {
        while (!abort.signal.aborted) {
          try {
            const saved = await api.turn(conversationId, turnId, abort.signal)
            if (abort.signal.aborted) return
            merge(conversationId, turnMessages(saved), true, preserveAccepted)
            if (terminal(saved.status)) {
              callbacks.current.refresh()
              return
            }
          } catch (error) {
            if (abort.signal.aborted) return
            if (error instanceof Error && 'status' in error && [401, 403, 404].includes(Number(error.status))) throw error
          }
          await pause(abort.signal)
        }
      } catch (error) {
        if (!abort.signal.aborted) {
          setRecoveryErrors((current) => ({ ...current, [turnId]: true }))
          callbacks.current.report(error)
        }
      } finally {
        clearTimeout(deadline)
        if (recoveries.current.get(turnId) === recovery) {
          recoveries.current.delete(turnId)
          if (timedOut) setRecoveryErrors((current) => ({ ...current, [turnId]: true }))
        }
      }
    })()
    return recovery
  }, [])

  const recover = useCallback((conversationId: string, turnId: string) => {
    const recovery = startRecovery(conversationId, turnId)
    return () => {
      if (recoveries.current.get(turnId) === recovery) {
        recoveries.current.delete(turnId)
        recovery.abort.abort()
      }
    }
  }, [startRecovery])

  async function run(action: RunAction, onAccepted?: () => void): Promise<boolean> {
    const request = action.kind === 'send' ? action.request : null
    const conversationId = action.kind === 'send' ? action.request.body.conversation_id ?? null : action.conversationId
    const startKey = conversationId ?? homeKey
    if (controllers.current.has(startKey)) return false
    const waiting = pendingByConversation[startKey]
    if (waiting && (!request || waiting.key !== request.key)) return false

    const abort = new AbortController()
    let operationKey = startKey
    controllers.current.set(operationKey, abort)
    setBusyByConversation((current) => ({ ...current, [operationKey]: true }))
    if (request) {
      setPendingByConversation((current) => {
        if (!current[operationKey]) return current
        const next = { ...current }
        delete next[operationKey]
        return next
      })
    }

    if (action.kind !== 'send') {
      recoveries.current.get(action.turnId)?.abort.abort()
      recoveries.current.delete(action.turnId)
      setRecoveryErrors((current) => ({ ...current, [action.turnId]: false }))
      const previous = cachedRef.current[startKey]?.find((message) => message.role === 'assistant' && message.turn_id === action.turnId)
      setActiveByConversation((current) => ({
        ...current,
        [startKey]: { conversationId: startKey, turnId: action.turnId, assistantId: previous?.id ?? '', phase: 'working' },
      }))
    }

    const shouldRedirectHome = action.kind === 'send' && !action.request.body.conversation_id && locationRef.current.pathname === '/'
    const homeLocationKey = locationRef.current.key
    const acceptedAction = action.kind !== 'send'
    let current: Active | null = null
    let replay = false
    let receivedPhase = false
    let scope: Scope | null = null
    let answer = ''
    let citations: Citation[] = []
    const createdAt = new Date().toISOString()
    const operationTurnId = action.kind === 'send' ? null : action.turnId

    function migrateConversation(nextId: string) {
      if (operationKey === nextId) return true
      const previousKey = operationKey
      if (controllers.current.get(previousKey) !== abort || controllers.current.has(nextId)) return false
      controllers.current.delete(previousKey)
      controllers.current.set(nextId, abort)
      operationKey = nextId
      setBusyByConversation((current) => {
        const next = { ...current, [nextId]: true }
        delete next[previousKey]
        return next
      })
      setPendingByConversation((current) => {
        if (!current[previousKey]) return current
        const next = { ...current }
        delete next[previousKey]
        return next
      })
      setActiveByConversation((current) => {
        const next = { ...current }
        delete next[previousKey]
        return next
      })
      return true
    }

    function setActive(active: Active) {
      setActiveByConversation((current) => ({ ...current, [operationKey]: active }))
    }

    function phase(value: ChatPhase | null) {
      if (!current || current.phase === value) return
      current = { ...current, phase: value }
      setActive(current)
    }

    function assistant(status: Message['status']) {
      if (!current) return
      merge(current.conversationId, [{
        id: current.assistantId, conversation_id: current.conversationId, turn_id: current.turnId,
        created_at: createdAt, sequence_no: Number.MAX_SAFE_INTEGER, role: 'assistant',
        content: answer, scope, citations, status,
      }], false, acceptedAction)
    }

    function release() {
      if (controllers.current.get(operationKey) !== abort) return
      controllers.current.delete(operationKey)
      setBusyByConversation((current) => {
        const next = { ...current }
        delete next[operationKey]
        return next
      })
      setActiveByConversation((current) => {
        const active = current[operationKey]
        if (!active || (active.turnId !== currentTurnId())) return current
        const next = { ...current }
        delete next[operationKey]
        return next
      })
      callbacks.current.refresh()
    }

    function currentTurnId() {
      return current?.turnId ?? operationTurnId
    }

    try {
      const events = action.kind === 'retry'
        ? retryTurn(action.conversationId, action.turnId, abort.signal)
        : action.kind === 'edit'
          ? editTurn(action.conversationId, action.turnId, action.content, abort.signal)
          : streamChat(action.request.body, action.request.key, abort.signal)
      for await (const event of events) {
        if (abort.signal.aborted || controllers.current.get(operationKey) !== abort) return Boolean(current)
        if (event.type === 'meta') {
          if (!current) onAccepted?.()
          replay = Boolean(event.replay)
          if (!migrateConversation(event.conversation_id)) return true
          current = { conversationId: event.conversation_id, turnId: event.turn_id, assistantId: event.assistant_message_id, phase: replay ? null : 'working' }
          scope = event.scope
          setActive(current)
          if (action.kind === 'send') {
            merge(event.conversation_id, [{
              id: event.user_message_id, conversation_id: event.conversation_id, turn_id: event.turn_id,
              created_at: createdAt, sequence_no: Number.MAX_SAFE_INTEGER - 1, role: 'user',
              content: action.request.body.content, scope, citations: [], status: 'completed',
            }])
          } else if (action.kind === 'edit') {
            const previous = cachedRef.current[event.conversation_id]?.find((message) => message.id === event.user_message_id)
              ?? cachedRef.current[event.conversation_id]?.find((message) => message.role === 'user' && message.turn_id === event.turn_id)
            merge(event.conversation_id, [{
              id: event.user_message_id, conversation_id: event.conversation_id, turn_id: event.turn_id,
              created_at: previous?.created_at ?? createdAt,
              sequence_no: previous?.sequence_no ?? Number.MAX_SAFE_INTEGER - 1,
              role: 'user', content: action.content, scope, citations: [], status: 'completed',
            }], false, true)
          }
          assistant('streaming')
          if (shouldRedirectHome && locationRef.current.pathname === '/' && locationRef.current.key === homeLocationKey) {
            void navigate(`/chat/${event.conversation_id}`)
          }
          callbacks.current.refresh()
        } else if (event.type === 'phase') {
          if (!replay && event.turn_id === current?.turnId) {
            receivedPhase = true
            phase(event.phase)
          }
        } else if (event.type === 'delta') {
          if (event.turn_id !== current?.turnId) continue
          if (!replay && !receivedPhase) phase('composing')
          answer += event.delta
          assistant('streaming')
        } else if (event.type === 'sources' && event.turn_id === current?.turnId) {
          citations = event.citations
          assistant('streaming')
        } else if ((event.type === 'done' || event.type === 'stopped') && event.turn_id === current?.turnId) {
          if (event.message) merge(event.message.conversation_id, [event.message], false, acceptedAction)
          else assistant('stopped')
          release()
          startRecovery(current.conversationId, current.turnId, acceptedAction)
          return true
        }
      }
      if (current) {
        phase(null)
        const recovery = startRecovery(current.conversationId, current.turnId, acceptedAction)
        const cancel = () => recovery.abort.abort()
        abort.signal.addEventListener('abort', cancel, { once: true })
        try { await recovery.promise } finally { abort.signal.removeEventListener('abort', cancel) }
      }
      return Boolean(current)
    } catch (error) {
      if (abort.signal.aborted) return Boolean(current)
      if (controllers.current.get(operationKey) !== abort) return true
      phase(null)
      callbacks.current.report(error)
      if (current) {
        const recovery = startRecovery(current.conversationId, current.turnId, acceptedAction)
        const cancel = () => recovery.abort.abort()
        abort.signal.addEventListener('abort', cancel, { once: true })
        try { await recovery.promise } finally { abort.signal.removeEventListener('abort', cancel) }
      } else if (action.kind === 'send' && (error instanceof TypeError || (error instanceof Error && 'retryable' in error && error.retryable))) {
        setPendingByConversation((current) => ({ ...current, [startKey]: action.request }))
      } else if (action.kind !== 'send' && (error instanceof TypeError || (error instanceof Error && 'retryable' in error && error.retryable))) {
        const recovery = startRecovery(action.conversationId, action.turnId)
        const cancel = () => recovery.abort.abort()
        abort.signal.addEventListener('abort', cancel, { once: true })
        try { await recovery.promise } finally { abort.signal.removeEventListener('abort', cancel) }
      }
      return Boolean(current)
    } finally {
      release()
    }
  }

  async function stop(conversationId: string, turnId: string) {
    const operationKey = conversationId
    const active = activeByConversation[conversationId]
    const ownStream = active?.turnId === turnId
    const running = controllers.current.get(operationKey)
    if (running && !ownStream) return
    recoveries.current.get(turnId)?.abort.abort()
    recoveries.current.delete(turnId)
    const stopping = new AbortController()
    running?.abort()
    controllers.current.set(operationKey, stopping)
    setBusyByConversation((current) => ({ ...current, [operationKey]: true }))
    setActiveByConversation((current) => {
      const next = { ...current }
      delete next[operationKey]
      return next
    })
    try {
      const saved = await api.cancelTurn(conversationId, turnId, stopping.signal)
      if (stopping.signal.aborted) return
      merge(conversationId, turnMessages(saved), false, true)
      callbacks.current.refresh()
    } catch (error) {
      if (!stopping.signal.aborted) callbacks.current.report(error)
    } finally {
      if (!stopping.signal.aborted && controllers.current.get(operationKey) === stopping) {
        controllers.current.delete(operationKey)
        setBusyByConversation((current) => {
          const next = { ...current }
          delete next[operationKey]
          return next
        })
      }
    }
  }

  const busy = Object.values(busyByConversation).some(Boolean)
  const active = activeByConversation[homeKey] ?? Object.values(activeByConversation)[0] ?? null
  const pending = pendingByConversation[homeKey] ?? Object.values(pendingByConversation)[0] ?? null

  return {
    active,
    busy,
    cached,
    accepted: acceptedMessages,
    pending,
    recoveryErrors,
    stop,
    recover,
    acknowledgeSaved,
    activeFor: (conversationId?: string) => activeByConversation[conversationId ?? homeKey] ?? null,
    isBusy: (conversationId?: string) => Boolean(busyByConversation[conversationId ?? homeKey]),
    pendingFor: (conversationId?: string) => pendingByConversation[conversationId ?? homeKey] ?? null,
    evictConversation: (conversationId: string) => {
      const evictedTurns: string[] = []
      for (const [turnId, recovery] of recoveries.current.entries()) {
        if (recovery.conversationId === conversationId) {
          recovery.abort.abort()
          recoveries.current.delete(turnId)
          evictedTurns.push(turnId)
        }
      }
      const active = activeByConversation[conversationId]
      if (active) evictedTurns.push(active.turnId)
      for (const row of cachedRef.current[conversationId] ?? []) {
        if (row.turn_id) evictedTurns.push(row.turn_id)
      }
      controllers.current.get(conversationId)?.abort()
      controllers.current.delete(conversationId)
      setBusyByConversation((current) => {
        if (!(conversationId in current)) return current
        const next = { ...current }
        delete next[conversationId]
        return next
      })
      setActiveByConversation((current) => {
        if (!(conversationId in current)) return current
        const next = { ...current }
        delete next[conversationId]
        return next
      })
      setPendingByConversation((current) => {
        if (!(conversationId in current)) return current
        const next = { ...current }
        delete next[conversationId]
        return next
      })
      const nextCache = { ...cachedRef.current }
      delete nextCache[conversationId]
      cachedRef.current = nextCache
      setCached(nextCache)
      acceptedRef.current.delete(conversationId)
      setAcceptedMessages((current) => {
        if (!(conversationId in current)) return current
        const next = { ...current }
        delete next[conversationId]
        return next
      })
      if (evictedTurns.length > 0) {
        setRecoveryErrors((current) => {
          const next = { ...current }
          for (const turnId of evictedTurns) delete next[turnId]
          return next
        })
      }
    },
    send: (body: ChatRequest, onAccepted?: () => void) =>
      run({ kind: 'send', request: { body, key: crypto.randomUUID() } }, onAccepted),
    resend: (conversationId?: string, onAccepted?: () => void) => {
      const request = pendingByConversation[conversationId ?? homeKey]
      return request ? run({ kind: 'send', request }, onAccepted) : Promise.resolve(false)
    },
    retry: (conversationId: string, turnId: string) => run({ kind: 'retry', conversationId, turnId }),
    edit: (conversationId: string, turnId: string, content: string, onAccepted?: () => void) =>
      run({ kind: 'edit', conversationId, turnId, content }, onAccepted),
  }
}

export type ChatController = ReturnType<typeof useChat>
