import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { api, retryTurn, streamChat } from '@/api/client'
import type { ChatRequest, Citation, Message, Scope } from '@/api/types'

type Active = { conversationId: string; turnId: string; assistantId: string }
type Pending = { body: ChatRequest; key: string }

export function useChat(refresh: () => void, report: (error: unknown) => void) {
  const navigate = useNavigate()
  const controller = useRef<AbortController | null>(null)
  const [active, setActive] = useState<Active | null>(null)
  const [cached, setCached] = useState<Record<string, Message[]>>({})
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<Pending | null>(null)

  useEffect(() => () => { controller.current?.abort() }, [])

  function merge(id: string, messages: Message[]) {
    setCached((current) => {
      const rows = new Map((current[id] ?? []).map((item) => [item.id, item]))
      for (const message of messages) rows.set(message.id, message)
      return { ...current, [id]: [...rows.values()] }
    })
  }

  async function run(request: Pending | null, retry?: { conversationId: string; turnId: string }) {
    if (controller.current) return false
    const abort = new AbortController()
    controller.current = abort
    setBusy(true)
    setPending(null)
    let current: Active | null = null
    let scope: Scope | null = null
    let answer = ''
    let citations: Citation[] = []
    const createdAt = new Date().toISOString()

    function assistant(status: Message['status']) {
      if (!current) return
      merge(current.conversationId, [{
        id: current.assistantId, conversation_id: current.conversationId, turn_id: current.turnId,
        created_at: createdAt, sequence_no: Number.MAX_SAFE_INTEGER, role: 'assistant',
        content: answer, scope, citations, status,
      }])
    }

    try {
      const events = retry ? retryTurn(retry.conversationId, retry.turnId, abort.signal)
        : streamChat(request!.body, request!.key, abort.signal)
      for await (const event of events) {
        if (event.type === 'meta') {
          current = { conversationId: event.conversation_id, turnId: event.turn_id, assistantId: event.assistant_message_id }
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
        } else if (event.type === 'delta') {
          answer += event.delta
          assistant('streaming')
        } else if (event.type === 'sources') {
          citations = event.citations
          assistant('streaming')
        } else if ((event.type === 'done' || event.type === 'stopped') && event.message) {
          merge(event.message.conversation_id, [event.message])
        }
      }
      if (current) {
        // Replace transient data with the stored messages and their canonical order.
        const saved = await api.turn(current.conversationId, current.turnId)
        merge(current.conversationId, saved.messages)
      }
      return true
    } catch (error) {
      if (abort.signal.aborted) return false
      report(error)
      if (current) {
        try {
          const saved = await api.turn(current.conversationId, current.turnId)
          merge(current.conversationId, saved.messages)
        } catch {
          assistant('failed')
        }
      } else if (request && (error instanceof TypeError || (error instanceof Error && 'retryable' in error && error.retryable))) {
        setPending(request)
      }
      return Boolean(current)
    } finally {
      if (!abort.signal.aborted) {
        controller.current = null
        setBusy(false)
        setActive(null)
        refresh()
      }
    }
  }

  async function stop(conversationId: string, turnId: string) {
    try {
      const saved = await api.cancelTurn(conversationId, turnId)
      merge(conversationId, saved.messages)
      if (active?.turnId === turnId) {
        controller.current?.abort()
        controller.current = null
        setBusy(false)
        setActive(null)
      }
      refresh()
    } catch (error) {
      report(error)
    }
  }

  return {
    active, busy, cached, stop, pending,
    send: (body: ChatRequest) => run({ body, key: crypto.randomUUID() }),
    resend: () => pending && run(pending),
    retry: (conversationId: string, turnId: string) => run(null, { conversationId, turnId }),
  }
}

export type ChatController = ReturnType<typeof useChat>
