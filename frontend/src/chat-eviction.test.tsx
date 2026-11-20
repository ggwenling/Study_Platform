// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, streamChat } from './api/client'
import type { ChatController } from './useChat'
import { useChat } from './useChat'

vi.mock('react-router', () => ({ useNavigate: () => vi.fn(), useLocation: () => ({ pathname: '/' }) }))
vi.mock('./api/client', () => ({
  api: {
    turn: vi.fn(),
    cancelTurn: vi.fn(),
  },
  streamChat: vi.fn(),
  retryTurn: vi.fn(),
  editTurn: vi.fn(),
}))

let root: Root
let node: HTMLDivElement
let latestChat!: ChatController
const scope = { knowledge_base_id: 'kb-1', knowledge_base_name: '测试库', answer_mode: 'knowledge_only' as const, index_version_ids: [] }

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function Harness({ refresh, report }: { refresh: () => void; report: (err: unknown) => void }) {
  latestChat = useChat(refresh, report)
  return <div data-busy={String(latestChat.busy)} />
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
})

afterEach(async () => {
  await act(async () => root.unmount())
  node.remove()
  vi.unstubAllGlobals()
})

describe('useChat.evictConversation', () => {
  it('aborts an active SSE stream and background recovery polling without firing 404 errors or holding busy lock', async () => {
    const refresh = vi.fn()
    const report = vi.fn()
    let streamSignal!: AbortSignal
    let turnSignal!: AbortSignal

    vi.mocked(streamChat).mockImplementation((_body, _key, signal) => {
      streamSignal = signal!
      return (async function* () {
        yield {
          type: 'meta' as const,
          conversation_id: 'conv-1',
          turn_id: 'turn-1',
          user_message_id: 'u-1',
          assistant_message_id: 'a-1',
          scope: {
            knowledge_base_id: 'kb-1',
            knowledge_base_name: '测试库',
            answer_mode: 'knowledge_only' as const,
            index_version_ids: [],
          },
        }
        await new Promise<void>((_resolve, reject) => {
          signal?.addEventListener('abort', () => reject(Object.assign(new Error('Not Found'), { status: 404 })), {
            once: true,
          })
        })
      })()
    })

    vi.mocked(api.turn).mockImplementation((_cid, _tid, signal) => {
      turnSignal = signal!
      return new Promise((_resolve, reject) => {
        signal?.addEventListener('abort', () => reject(Object.assign(new Error('Not Found'), { status: 404 })), {
          once: true,
        })
      })
    })

    await act(async () => root.render(<Harness refresh={refresh} report={report} />))

    // Start streaming conv-1 AND background recovery for an older turn in conv-1
    let sendPromise!: Promise<boolean>
    await act(async () => {
      latestChat.recover('conv-1', 'turn-old')
      sendPromise = latestChat.send({
        conversation_id: 'conv-1',
        knowledge_base_id: 'kb-1',
        content: '流式生成中问题',
        answer_mode: 'knowledge_only',
        intent: 'qa',
      })
      await Promise.resolve()
    })

    expect(latestChat.busy).toBe(true)
    expect(latestChat.active?.conversationId).toBe('conv-1')
    expect(latestChat.cached['conv-1']?.length).toBeGreaterThan(0)

    // Evict conv-1 while both stream and recovery are in flight
    await act(async () => {
      latestChat.evictConversation('conv-1')
      await sendPromise
    })

    expect(streamSignal.aborted).toBe(true)
    expect(turnSignal.aborted).toBe(true)
    expect(latestChat.busy).toBe(false)
    expect(latestChat.active).toBeNull()
    expect(latestChat.cached['conv-1']).toBeUndefined()
    expect(report).not.toHaveBeenCalled()
  })

  it('runs conversations A and B in parallel and evicts only the selected conversation', async () => {
    const gateA = deferred<void>()
    const gateB = deferred<void>()
    const signals: Record<string, AbortSignal> = {}
    vi.mocked(streamChat).mockImplementation((body, _key, signal) => {
      const conversationId = body.conversation_id!
      const turnId = `turn-${conversationId}`
      signals[conversationId] = signal!
      return (async function* () {
        yield {
          type: 'meta' as const,
          conversation_id: conversationId,
          turn_id: turnId,
          user_message_id: `user-${conversationId}`,
          assistant_message_id: `assistant-${conversationId}`,
          scope,
        }
        await (conversationId === 'a' ? gateA.promise : gateB.promise)
        yield {
          type: 'done' as const,
          turn_id: turnId,
          scope,
          message: {
            id: `assistant-${conversationId}`,
            conversation_id: conversationId,
            turn_id: turnId,
            role: 'assistant' as const,
            sequence_no: 2,
            status: 'completed' as const,
            content: `回答 ${conversationId}`,
            citations: [],
            scope,
            created_at: '2026-01-01T00:00:00Z',
          },
        }
      })()
    })
    vi.mocked(api.turn).mockImplementation(async (conversationId, turnId) => ({
      conversation_id: conversationId,
      turn_id: turnId,
      scope,
      status: 'completed',
      messages: [{
        id: `assistant-${conversationId}`,
        conversation_id: conversationId,
        turn_id: turnId,
        role: 'assistant',
        sequence_no: 2,
        status: 'completed',
        content: `回答 ${conversationId}`,
        citations: [],
        scope,
        created_at: '2026-01-01T00:00:00Z',
      }],
      error_code: null,
    }))
    await act(async () => root.render(<Harness refresh={vi.fn()} report={vi.fn()} />))

    let sendA!: Promise<boolean>
    let sendB!: Promise<boolean>
    await act(async () => { sendA = latestChat.send({ conversation_id: 'a', knowledge_base_id: 'kb-1', content: 'A' }) })
    await act(async () => { sendB = latestChat.send({ conversation_id: 'b', knowledge_base_id: 'kb-1', content: 'B' }) })
    expect(latestChat.isBusy('a')).toBe(true)
    expect(latestChat.isBusy('b')).toBe(true)
    expect(latestChat.activeFor('a')?.turnId).toBe('turn-a')
    expect(latestChat.activeFor('b')?.turnId).toBe('turn-b')
    const blocked = await latestChat.send({ conversation_id: 'a', knowledge_base_id: 'kb-1', content: 'A2' })
    expect(blocked).toBe(false)
    expect(streamChat).toHaveBeenCalledTimes(2)

    await act(async () => {
      latestChat.evictConversation('a')
      await Promise.resolve()
    })
    expect(signals.a.aborted).toBe(true)
    expect(signals.b.aborted).toBe(false)
    expect(latestChat.isBusy('a')).toBe(false)
    expect(latestChat.isBusy('b')).toBe(true)
    expect(latestChat.cached.a).toBeUndefined()
    expect(latestChat.cached.b?.length).toBeGreaterThan(0)

    await act(async () => { gateA.resolve(); await sendA })
    await act(async () => { gateB.resolve(); await sendB })
    expect(latestChat.isBusy('b')).toBe(false)
    expect(latestChat.cached.b?.find((row) => row.role === 'assistant')).toMatchObject({ content: '回答 b', status: 'completed' })
  })
})
