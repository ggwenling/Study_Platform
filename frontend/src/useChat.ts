import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { api, retryTurn, streamChat } from '@/api/client'
import type { ChatPhase, ChatRequest, Citation, Message, Scope, Turn } from '@/api/types'

type Active = { conversationId: string; turnId: string; assistantId: string; phase: ChatPhase | null }
type Pending = { body: ChatRequest; key: string }
type Recovery = { abort: AbortController; promise: Promise<void> }
const terminal = (status: string) => ['completed', 'failed', 'stopped', 'cancelled'].includes(status)

// A turn can expire before its message snapshot has caught up.
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

export function useChat(refresh: () => void, report: (error: unknown) => void) {
  const navigate = useNavigate()
  const controller = useRef<AbortController | null>(null)
  const [active, setActive] = useState<Active | null>(null)
  const [cached, setCached] = useState<Record<string, Message[]>>({})
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<Pending | null>(null)
  const [recoveryErrors, setRecoveryErrors] = useState<Record<string, boolean>>({})
  const recoveries = useRef(new Map<string, Recovery>())
  const callbacks = useRef({ refresh, report })
  callbacks.current = { refresh, report }

  useEffect(() => () => {
    controller.current?.abort()
    for (const recovery of recoveries.current.values()) recovery.abort.abort()
    recoveries.current.clear()
  }, [])

  function merge(id: string, messages: Message[], fromRecovery = false) {
    setCached((current) => {
      const rows = new Map((current[id] ?? []).map((item) => [item.id, item]))
      for (const message of messages) {
        const existing = rows.get(message.id)
        if (fromRecovery && existing && terminal(existing.status) && !terminal(message.status)) continue
        rows.set(message.id, message)
      }
      return { ...current, [id]: [...rows.values()] }
    })
  }

  // Read-only recovery: never resubmit a request or start another paid generation.
  const startRecovery = useCallback((conversationId: string, turnId: string): Recovery => {
    const existing = recoveries.current.get(turnId)
    if (existing) return existing
    const abort = new AbortController()
    const recovery = { abort, promise: Promise.resolve() }
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
            merge(conversationId, turnMessages(saved), true)
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

  async function run(request: Pending | null, retry?: { conversationId: string; turnId: string }) {
    if (controller.current) return false
    // A retry reuses the same turn/message ids; discard the older recovery first.
    if (retry) {
      recoveries.current.get(retry.turnId)?.abort.abort()
      recoveries.current.delete(retry.turnId)
      setRecoveryErrors((current) => ({ ...current, [retry.turnId]: false }))
    }
    const abort = new AbortController()
    controller.current = abort
    setBusy(true)
    setPending(null)
    let current: Active | null = null
    let replay = false
    let receivedPhase = false
    let scope: Scope | null = null
    let answer = ''
    let citations: Citation[] = []
    const createdAt = new Date().toISOString()

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
      }])
    }

    function release() {
      if (abort.signal.aborted || controller.current !== abort) return
      controller.current = null
      setBusy(false)
      setActive(null)
      refresh()
    }

    try {
      const events = retry ? retryTurn(retry.conversationId, retry.turnId, abort.signal)
        : streamChat(request!.body, request!.key, abort.signal)
      for await (const event of events) {
        if (abort.signal.aborted) return false
        if (event.type === 'meta') {
          replay = Boolean(event.replay)
          current = { conversationId: event.conversation_id, turnId: event.turn_id, assistantId: event.assistant_message_id, phase: replay ? null : 'working' }
          scope = event.scope
          setActive(current)
          if (request) {
            merge(event.conversation_id, [{
              id: event.user_message_id, conversation_id: event.conversation_id, turn_id: event.turn_id,
              created_at: createdAt, sequence_no: Number.MAX_SAFE_INTEGER - 1, role: 'user',
              content: request.body.content, scope, citations: [], status: 'completed',
            }])
          }
          assistant('streaming')
          if (request && !request.body.conversation_id) void navigate(`/chat/${event.conversation_id}`)
          refresh()
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
        } else if (event.type === 'sources') {
          citations = event.citations
          assistant('streaming')
        } else if ((event.type === 'done' || event.type === 'stopped') && event.turn_id === current?.turnId) {
          if (event.message) merge(event.message.conversation_id, [event.message])
          else assistant('stopped')
          // The stream has confirmed the outcome. Canonical ordering can sync independently.
          release()
          startRecovery(current.conversationId, current.turnId)
          return true
        }
      }
      if (current) {
        phase(null)
        // Replace transient data with the stored messages and their canonical order.
        const recovery = startRecovery(current.conversationId, current.turnId)
        const cancel = () => recovery.abort.abort()
        abort.signal.addEventListener('abort', cancel, { once: true })
        try { await recovery.promise } finally { abort.signal.removeEventListener('abort', cancel) }
      }
      return true
    } catch (error) {
      if (abort.signal.aborted) return false
      if (controller.current !== abort) return true
      phase(null)
      report(error)
      if (current) {
        const recovery = startRecovery(current.conversationId, current.turnId)
        const cancel = () => recovery.abort.abort()
        abort.signal.addEventListener('abort', cancel, { once: true })
        try { await recovery.promise } finally { abort.signal.removeEventListener('abort', cancel) }
      } else if (request && (error instanceof TypeError || (error instanceof Error && 'retryable' in error && error.retryable))) {
        setPending(request)
      }
      return Boolean(current)
    } finally {
      release()
    }
  }

  async function stop(conversationId: string, turnId: string) {
    const ownStream = active?.turnId === turnId
    if (controller.current && !ownStream) return
    recoveries.current.get(turnId)?.abort.abort()
    recoveries.current.delete(turnId)
    // Hold the operation lock until cancellation settles, so its response cannot
    // overwrite a retry that reuses the same assistant message id.
    const stopping = new AbortController()
    controller.current?.abort()
    controller.current = stopping
    setBusy(true)
    setActive(null)
    try {
      const saved = await api.cancelTurn(conversationId, turnId)
      if (stopping.signal.aborted) return
      merge(conversationId, turnMessages(saved))
      refresh()
    } catch (error) {
      if (!stopping.signal.aborted) report(error)
    } finally {
      if (!stopping.signal.aborted) {
        controller.current = null
        setBusy(false)
      }
    }
  }

  return {
    active, busy, cached, stop, pending, recover, recoveryErrors,
    send: (body: ChatRequest) => run({ body, key: crypto.randomUUID() }),
    resend: () => pending && run(pending),
    retry: (conversationId: string, turnId: string) => run(null, { conversationId, turnId }),
  }
}

export type ChatController = ReturnType<typeof useChat>
