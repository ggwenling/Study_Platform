// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatEvent, Message, Scope, Turn } from './api/types'
import { api, retryTurn, streamChat } from './api/client'
import { useChat, type ChatController } from './useChat'
import LiveChat from './components/LiveChat'

const workspace = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
vi.mock('./api/client', () => ({ api: { turn: vi.fn(), cancelTurn: vi.fn(), conversation: vi.fn(), messages: vi.fn() }, streamChat: vi.fn(), retryTurn: vi.fn() }))
vi.mock('react-router', () => ({ useNavigate: () => vi.fn(), Link: ({ children }: { children: React.ReactNode }) => <span>{children}</span> }))
vi.mock('./WorkspaceData', () => ({ useWorkspace: () => workspace.value }))
vi.mock('./components/Composer', () => ({ Composer: () => null }))
vi.mock('./components/MarkdownContent', () => ({ MarkdownContent: ({ children }: { children: React.ReactNode }) => <span>{children}</span> }))
vi.mock('./components/ProtectedMaterial', () => ({ DownloadMaterial: () => null, MaterialPage: () => null }))
// Exercise the chat lifecycle independently of Canvas rendering (checked in a browser).
vi.mock('thinking-orbs', () => ({ ThinkingOrb: ({ state, size, theme }: { state: string; size: number; theme: string }) => <canvas data-phase={state} data-size={size} data-theme={theme} /> }))

const scope: Scope = { knowledge_base_id: 'k', knowledge_base_name: '知识库', answer_mode: 'knowledge_only', index_version_ids: [] }
const message = (status: Message['status'], content = '保存的回答'): Message => ({ id: 'a', conversation_id: 'c', turn_id: 't', role: 'assistant', sequence_no: 2, status, content, citations: [], scope, created_at: '2026-01-01T00:00:00Z' })
const turn = (status: string, row = message(status as Message['status'])): Turn => ({ conversation_id: 'c', turn_id: 't', scope, status, messages: [row], error_code: null })
const meta: ChatEvent = { type: 'meta', conversation_id: 'c', turn_id: 't', user_message_id: 'u', assistant_message_id: 'a', scope }
const user = { id: 'one' }
const report = vi.fn()
let chat: ChatController
let root: Root
let node: HTMLDivElement
let revision = 0
let view = false

function Harness() {
  chat = useChat(() => { revision += 1 }, report)
  workspace.value = { user, chat, revision, report, notify: vi.fn(), requireLogin: vi.fn() }
  return view ? <LiveChat id="c" /> : null
}
async function render() { await act(async () => root.render(<Harness />)) }
async function advance(ms = 2000) { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.resetAllMocks()
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  node = document.createElement('div')
  document.body.append(node)
  root = createRoot(node)
  revision = 0
  view = false
  vi.mocked(api.conversation).mockResolvedValue({ id: 'c', title: '对话', project_knowledge_base_id: null, created_at: '', updated_at: '' })
  vi.mocked(api.messages).mockResolvedValue({ items: [], next_cursor: null })
})
afterEach(async () => { await act(async () => root.unmount()); node.remove(); vi.useRealTimers() })

describe('disconnected chat recovery', () => {
  it('shows real phases, ignores another turn and removes the orb before final recovery', async () => {
    view = true
    const search = deferred<void>()
    const compose = deferred<void>()
    const finish = deferred<void>()
    const saved = deferred<Turn>()
    vi.mocked(api.turn).mockReturnValue(saved.promise)
    vi.mocked(streamChat).mockImplementation(async function* () {
      yield meta
      await search.promise
      yield { type: 'phase', turn_id: 't', phase: 'searching' }
      yield { type: 'phase', turn_id: 'other', phase: 'solving' }
      await compose.promise
      yield { type: 'phase', turn_id: 't', phase: 'composing' }
      yield { type: 'delta', turn_id: 't', delta: '正文' }
      await finish.promise
      yield { type: 'done', turn_id: 't', message: message('completed', '正文'), scope }
    })
    await render()
    let sent!: Promise<boolean>
    await act(async () => { sent = chat.send({ knowledge_base_id: 'k', content: '问题' }) })
    expect(node.querySelector('canvas')?.dataset).toMatchObject({ phase: 'working', size: '64', theme: 'auto' })
    await act(async () => search.resolve())
    expect(node.querySelector('canvas')?.dataset.phase).toBe('searching')
    expect(node.textContent).toContain('正在检索资料')
    await act(async () => compose.resolve())
    expect(node.querySelector('canvas')?.dataset.phase).toBe('composing')
    expect(node.textContent).toContain('正在生成回答')
    await act(async () => finish.resolve())
    expect(node.querySelector('canvas')).toBeNull()
    expect(chat.busy).toBe(true)
    await act(async () => { saved.resolve(turn('completed')); await sent })
    expect(node.querySelector('canvas')).toBeNull()
  })

  it('removes the orb immediately on stream failure and never animates recovery snapshots', async () => {
    view = true
    const fail = deferred<void>()
    const saved = deferred<Turn>()
    vi.mocked(api.turn).mockReturnValue(saved.promise)
    vi.mocked(streamChat).mockImplementation(async function* () {
      yield meta
      yield { type: 'phase', turn_id: 't', phase: 'searching' }
      await fail.promise
      throw new TypeError('disconnected')
    })
    await render()
    let sent!: Promise<boolean>
    await act(async () => { sent = chat.send({ knowledge_base_id: 'k', content: '问题' }) })
    expect(node.querySelector('canvas')).not.toBeNull()
    await act(async () => fail.resolve())
    expect(node.querySelector('canvas')).toBeNull()
    expect(node.textContent).toContain('正在确认生成状态')
    await act(async () => { saved.resolve(turn('failed')); await sent })
    expect(node.querySelector('canvas')).toBeNull()
  })

  it('does not animate replayed answers', async () => {
    view = true
    const finish = deferred<void>()
    vi.mocked(api.turn).mockResolvedValue(turn('completed'))
    vi.mocked(streamChat).mockImplementation(async function* () {
      yield { ...meta, replay: true }
      yield { type: 'delta', turn_id: 't', delta: '历史答案' }
      await finish.promise
      yield { type: 'done', turn_id: 't', message: message('completed'), scope }
    })
    await render()
    let sent!: Promise<boolean>
    await act(async () => { sent = chat.send({ knowledge_base_id: 'k', content: '问题' }) })
    expect(node.querySelector('canvas')).toBeNull()
    await act(async () => { finish.resolve(); await sent })
  })

  it('uses text streaming as the composing fallback for older servers', async () => {
    view = true
    const finish = deferred<void>()
    vi.mocked(api.turn).mockResolvedValue(turn('completed'))
    vi.mocked(streamChat).mockImplementation(async function* () {
      yield meta
      yield { type: 'delta', turn_id: 't', delta: '正文' }
      await finish.promise
      yield { type: 'done', turn_id: 't', message: message('completed'), scope }
    })
    await render()
    let sent!: Promise<boolean>
    await act(async () => { sent = chat.send({ knowledge_base_id: 'k', content: '问题' }) })
    expect(node.querySelector('canvas')?.dataset.phase).toBe('composing')
    await act(async () => { finish.resolve(); await sent })
  })

  it('unmounts the orb and aborts the stream on account replacement', async () => {
    view = true
    const late = deferred<void>()
    vi.mocked(streamChat).mockImplementation(async function* () {
      yield meta
      await late.promise
      yield { type: 'phase', turn_id: 't', phase: 'composing' }
    })
    await render()
    let sent!: Promise<boolean>
    await act(async () => { sent = chat.send({ knowledge_base_id: 'k', content: '问题' }) })
    const signal = vi.mocked(streamChat).mock.calls[0][2]!
    expect(node.querySelector('canvas')).not.toBeNull()
    await act(async () => root.render(<Harness key="new-account" />))
    expect(signal.aborted).toBe(true)
    expect(node.querySelector('canvas')).toBeNull()
    await act(async () => { late.resolve(); await sent })
    expect(chat.active).toBeNull()
    expect(chat.cached).toEqual({})
  })

  it('stops the orb before cancellation responds and ignores aborted late phases', async () => {
    view = true
    const late = deferred<void>()
    const cancelled = deferred<Turn>()
    vi.mocked(api.cancelTurn).mockReturnValue(cancelled.promise)
    vi.mocked(streamChat).mockImplementation(async function* () {
      yield meta
      await late.promise
      yield { type: 'phase', turn_id: 't', phase: 'solving' }
    })
    await render()
    let sent!: Promise<boolean>
    await act(async () => { sent = chat.send({ knowledge_base_id: 'k', content: '问题' }) })
    expect(node.querySelector('canvas')).not.toBeNull()
    let stopped!: Promise<void>
    await act(async () => { stopped = chat.stop('c', 't') })
    expect(node.querySelector('canvas')).toBeNull()
    await act(async () => { late.resolve(); await sent })
    expect(node.querySelector('canvas')).toBeNull()
    await act(async () => { cancelled.resolve(turn('stopped')); await stopped })
  })

  it('keeps polling a running turn until complete without resubmitting generation', async () => {
    vi.mocked(streamChat).mockImplementation(async function* () { yield meta; yield { type: 'delta', turn_id: 't', delta: '部分' }; throw new TypeError('disconnected') })
    vi.mocked(api.turn).mockResolvedValueOnce(turn('running', message('streaming', '部分'))).mockResolvedValueOnce(turn('completed'))
    await render()
    let sent!: Promise<boolean>
    await act(async () => { sent = chat.send({ knowledge_base_id: 'k', content: '问题' }) })
    expect(chat.busy).toBe(true)
    expect(chat.cached.c.find((row) => row.id === 'a')?.status).toBe('streaming')
    await advance()
    expect(await sent).toBe(true)
    expect(chat.busy).toBe(false)
    expect(chat.cached.c.find((row) => row.id === 'a')).toMatchObject({ status: 'completed', content: '保存的回答', sequence_no: 2 })
    expect(streamChat).toHaveBeenCalledTimes(1)
    expect(retryTurn).not.toHaveBeenCalled()
    await advance(10000)
    expect(api.turn).toHaveBeenCalledTimes(2)
  })

  it('recovers a reopened streaming message and uses turn failure to show retry', async () => {
    view = true
    vi.mocked(api.messages).mockResolvedValue({ items: [message('streaming')], next_cursor: null })
    vi.mocked(api.turn).mockResolvedValueOnce(turn('running', message('streaming'))).mockResolvedValueOnce(turn('failed', message('streaming')))
    await render()
    expect(node.textContent).toContain('正在确认生成状态')
    await advance()
    expect(node.textContent).toContain('回答失败')
    expect(node.textContent).toContain('重试本轮')
    expect(node.textContent).not.toContain('正在生成')
    expect(retryTurn).not.toHaveBeenCalled()
  })

  it('lets canonical saved completion replace stale cached streaming content', async () => {
    view = true
    vi.mocked(api.messages).mockResolvedValue({ items: [message('streaming', '旧快照')], next_cursor: null })
    vi.mocked(api.turn).mockResolvedValue(turn('running', message('streaming', '缓存旧快照')))
    await render()
    expect(node.textContent).toContain('缓存旧快照')
    vi.mocked(api.messages).mockResolvedValue({ items: [message('completed', '最终正文')], next_cursor: null })
    revision += 1
    await render()
    expect(node.textContent).toContain('最终正文')
    expect(node.textContent).not.toContain('缓存旧快照')
    expect(node.textContent).not.toContain('正在生成')
    const count = vi.mocked(api.turn).mock.calls.length
    await advance(10000)
    expect(api.turn).toHaveBeenCalledTimes(count)
  })

  it('bounds recovery and offers an explicit read-only status check after timeout', async () => {
    view = true
    vi.mocked(api.messages).mockResolvedValue({ items: [message('streaming')], next_cursor: null })
    vi.mocked(api.turn).mockResolvedValue(turn('running', message('streaming')))
    await render()
    await advance(120000)
    expect(node.textContent).toContain('暂时无法确认生成状态')
    const count = vi.mocked(api.turn).mock.calls.length
    await advance(20000)
    expect(api.turn).toHaveBeenCalledTimes(count)
    vi.mocked(api.turn).mockResolvedValue(turn('completed'))
    const button = [...node.querySelectorAll('button')].find((item) => item.textContent === '重新查询状态')!
    await act(async () => button.click())
    expect(node.textContent).not.toContain('暂时无法确认')
    expect(streamChat).not.toHaveBeenCalled()
    expect(retryTurn).not.toHaveBeenCalled()
  })

  it('aborts recovery on unmount/account replacement and ignores a late result', async () => {
    const saved = deferred<Turn>()
    vi.mocked(api.turn).mockReturnValue(saved.promise)
    await render()
    await act(async () => { chat.recover('c', 't') })
    const signal = vi.mocked(api.turn).mock.calls[0][2]!
    await act(async () => root.render(<Harness key="new-account" />))
    expect(signal.aborted).toBe(true)
    await act(async () => saved.resolve(turn('completed', message('completed', '旧账号回答'))))
    expect(chat.cached).toEqual({})
    await advance(10000)
    expect(api.turn).toHaveBeenCalledTimes(1)
  })

  it('cancels an older recovery before retry and ignores its late terminal snapshot', async () => {
    const old = deferred<Turn>()
    const finish = deferred<void>()
    vi.mocked(api.turn).mockReturnValueOnce(old.promise).mockResolvedValue(turn('completed', message('completed', '新回答')))
    vi.mocked(retryTurn).mockImplementation(async function* () { yield meta; yield { type: 'delta', turn_id: 't', delta: '新回答' }; await finish.promise; yield { type: 'done', turn_id: 't', message: message('completed', '新回答'), scope } })
    await render()
    await act(async () => { chat.recover('c', 't') })
    let retried!: Promise<boolean>
    await act(async () => { retried = chat.retry('c', 't') })
    expect(vi.mocked(api.turn).mock.calls[0][2]?.aborted).toBe(true)
    await act(async () => old.resolve(turn('failed', message('failed', '旧回答'))))
    expect(chat.cached.c[0]).toMatchObject({ content: '新回答', status: 'streaming' })
    await act(async () => { finish.resolve(); await retried })
    expect(chat.cached.c[0]).toMatchObject({ content: '新回答', status: 'completed' })
  })

  it('shows the retry stream over an earlier saved failure and retains its final answer', async () => {
    view = true
    const finish = deferred<void>()
    vi.mocked(api.messages).mockResolvedValue({ items: [message('failed', '上次失败的内容')], next_cursor: null })
    vi.mocked(api.turn).mockResolvedValue(turn('completed', message('completed', '本次成功的内容')))
    vi.mocked(retryTurn).mockImplementation(async function* () {
      yield meta
      yield { type: 'delta', turn_id: 't', delta: '本次成功的内容' }
      await finish.promise
      yield { type: 'done', turn_id: 't', message: message('completed', '本次成功的内容'), scope }
    })
    await render()
    expect(node.textContent).toContain('上次失败的内容')
    // Leave the old stored snapshot in place while the next GET is pending.
    const saved = deferred<{ items: Message[]; next_cursor: null }>()
    vi.mocked(api.messages).mockReturnValue(saved.promise)
    let retried!: Promise<boolean>
    await act(async () => { retried = chat.retry('c', 't') })
    expect(node.textContent).toContain('本次成功的内容')
    expect(node.textContent).not.toContain('上次失败的内容')
    await act(async () => { finish.resolve(); await retried })
    expect(node.textContent).toContain('本次成功的内容')
    expect(node.textContent).not.toContain('回答失败')
    await act(async () => saved.reject(new Error('refresh failed')))
    expect(report).toHaveBeenCalledWith(expect.objectContaining({ message: 'refresh failed' }))
    expect(node.textContent).toContain('本次成功的内容')
    expect(node.textContent).not.toContain('回答失败')
  })

  it('stops polling when stopped and preserves stopped canonical messages', async () => {
    const old = deferred<Turn>()
    vi.mocked(api.turn).mockReturnValue(old.promise)
    vi.mocked(api.cancelTurn).mockResolvedValue(turn('stopped', message('streaming', '已保留内容')))
    await render()
    await act(async () => { chat.recover('c', 't') })
    await act(async () => chat.stop('c', 't'))
    expect(vi.mocked(api.turn).mock.calls[0][2]?.aborted).toBe(true)
    await act(async () => old.resolve(turn('running', message('streaming', '旧内容'))))
    expect(chat.cached.c[0]).toMatchObject({ status: 'stopped', content: '已保留内容' })
    await advance(10000)
    expect(api.turn).toHaveBeenCalledTimes(1)
  })
})
