// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router'
import { api } from './api/client'
import type { AiJob, SubmittedAiJob } from './api/types'
import { AiJobProgress } from './components/AiJobProgress'
import { useAiJobs } from './useAiJobs'

vi.mock('./api/client', () => ({
  api: {
    aiJobs: { list: vi.fn(), get: vi.fn(), cancel: vi.fn() },
    learning: { startAttempt: vi.fn() },
  },
}))

const notify = vi.fn()
const report = vi.fn()
const refresh = vi.fn()
let root: Root
let node: HTMLDivElement

function makeJob(overrides: Partial<AiJob> = {}): AiJob {
  return {
    id: 'job-1', job_type: 'notes', knowledge_base_id: 'kb-1', status: 'queued',
    phase: 'queued', progress: 0, title: '学习指南', result_resource_type: null,
    result_resource_id: null, error_code: null, error_message: null,
    created_at: '2026-11-20T00:00:00Z', updated_at: '2026-11-20T00:00:00Z',
    ...overrides,
  }
}

function LocationText() {
  const location = useLocation()
  return <output data-location>{`${location.pathname}${location.search} ${JSON.stringify(location.state)}`}</output>
}

function Harness({ userId = 'user-1' }: { userId?: string }) {
  const jobs = useAiJobs({ userId, notify, report, refresh })
  return <>
    <AiJobProgress jobs={jobs.jobs} cancel={jobs.cancel} />
    <button type="button" onClick={() => jobs.track({
      job_id: 'job-2', job_type: 'notes', status: 'queued', knowledge_base_id: 'kb-1', title: '新指南',
    } satisfies SubmittedAiJob)}>提交</button>
    <LocationText />
  </>
}

async function render(view: ReactNode = <Harness />) {
  await act(async () => root.render(<MemoryRouter>{view}</MemoryRouter>))
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.useFakeTimers()
  window.sessionStorage.clear()
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
  vi.mocked(api.aiJobs.list).mockResolvedValue([])
})

afterEach(async () => {
  await act(async () => root.unmount())
  node.remove()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('restores active jobs, polls their progress, and cancels them', async () => {
  vi.mocked(api.aiJobs.list)
    .mockResolvedValueOnce([makeJob()])
    .mockResolvedValueOnce([makeJob({ status: 'running', phase: 'running', progress: 55 })])
  vi.mocked(api.aiJobs.cancel).mockResolvedValue(makeJob({ status: 'cancelled', phase: 'cancelled' }))
  await render()

  expect(node.textContent).toContain('学习指南')
  expect(node.textContent).toContain('排队中')
  expect(window.sessionStorage.getItem('knowloop.ai-jobs.user-1')).toContain('job-1')

  await act(async () => { await vi.advanceTimersByTimeAsync(1800) })
  expect(node.textContent).toContain('55%')
  await act(async () => node.querySelector<HTMLButtonElement>('.ai-job-progress-item button')!.click())
  expect(api.aiJobs.cancel).toHaveBeenCalledWith('job-1')
  expect(node.querySelector('.ai-job-progress-item')).toBeNull()
  expect(window.sessionStorage.getItem('knowloop.ai-jobs.user-1')).toBe('[]')
})

it('starts polling for a new account while an old account request is pending', async () => {
  let resolveOldRequest!: (jobs: AiJob[]) => void
  vi.mocked(api.aiJobs.list)
    .mockImplementationOnce(() => new Promise((resolve) => { resolveOldRequest = resolve }))
    .mockResolvedValueOnce([])
  await render(<Harness userId="user-1" />)

  await render(<Harness userId="user-2" />)
  expect(api.aiJobs.list).toHaveBeenCalledTimes(2)
  resolveOldRequest([makeJob()])
  await act(async () => { await Promise.resolve(); await Promise.resolve() })

  expect(node.textContent).not.toContain('学习指南')
  expect(window.sessionStorage.getItem('knowloop.ai-jobs.user-1')).toBeNull()
  expect(window.sessionStorage.getItem('knowloop.ai-jobs.user-2')).toBe('[]')
})

it('keeps a completed cancellation race tracked until polling can notify', async () => {
  window.sessionStorage.setItem('knowloop.ai-jobs.user-1', JSON.stringify(['job-1']))
  vi.mocked(api.aiJobs.list)
    .mockResolvedValueOnce([makeJob()])
    .mockResolvedValueOnce([makeJob({ status: 'succeeded', phase: 'ready', progress: 100 })])
  vi.mocked(api.aiJobs.cancel).mockResolvedValue(makeJob({ status: 'succeeded', phase: 'ready', progress: 100 }))
  await render()

  await act(async () => node.querySelector<HTMLButtonElement>('.ai-job-progress-item button')!.click())
  expect(window.sessionStorage.getItem('knowloop.ai-jobs.user-1')).toContain('job-1')
  await act(async () => { await vi.advanceTimersByTimeAsync(1800) })

  expect(notify).toHaveBeenCalledWith('已完成知识工坊产物「学习指南」。', undefined)
  expect(window.sessionStorage.getItem('knowloop.ai-jobs.user-1')).toBe('[]')
})

it('pauses polling while hidden and polls immediately when visible again', async () => {
  let resolveHiddenRequest!: (jobs: AiJob[]) => void
  vi.mocked(api.aiJobs.list)
    .mockImplementationOnce(() => new Promise((resolve) => { resolveHiddenRequest = resolve }))
    .mockResolvedValueOnce([makeJob()])
  const descriptor = Object.getOwnPropertyDescriptor(document, 'visibilityState')
  try {
    await render()
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    await act(async () => document.dispatchEvent(new Event('visibilitychange')))
    await act(async () => { await vi.advanceTimersByTimeAsync(3600) })
    expect(api.aiJobs.list).toHaveBeenCalledOnce()

    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    await act(async () => document.dispatchEvent(new Event('visibilitychange')))
    expect(api.aiJobs.list).toHaveBeenCalledTimes(2)
    resolveHiddenRequest([makeJob({ title: 'stale hidden response' })])
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(node.textContent).not.toContain('stale hidden response')
  } finally {
    if (descriptor) Object.defineProperty(document, 'visibilityState', descriptor)
    else Reflect.deleteProperty(document, 'visibilityState')
  }
})

it('recovers a completed note after refresh and offers a direct link', async () => {
  window.sessionStorage.setItem('knowloop.ai-jobs.user-1', JSON.stringify(['job-1']))
  vi.mocked(api.aiJobs.list).mockResolvedValue([makeJob({
    status: 'succeeded', phase: 'ready', progress: 100,
    result_resource_type: 'note', result_resource_id: 'note-1',
  })])
  await render()

  expect(notify).toHaveBeenCalledWith('已完成知识工坊产物「学习指南」。', expect.objectContaining({ action: expect.objectContaining({ label: '立即查看' }) }))
  expect(refresh).toHaveBeenCalledOnce()
  await act(async () => notify.mock.calls[0][1].action.onClick())
  expect(node.querySelector('[data-location]')?.textContent).toContain('/knowledge?id=kb-1&note=note-1')
})

it('opens a completed flashcard deck from its toast action', async () => {
  window.sessionStorage.setItem('knowloop.ai-jobs.user-1', JSON.stringify(['job-1']))
  vi.mocked(api.aiJobs.list).mockResolvedValue([makeJob({
    job_type: 'flashcards', title: '记忆卡', status: 'succeeded', phase: 'ready', progress: 100,
    result_resource_type: 'flashcard_deck', result_resource_id: 'deck-1',
  })])
  await render()

  await act(async () => notify.mock.calls[0][1].action.onClick())
  expect(node.querySelector('[data-location]')?.textContent).toContain('/learning')
  expect(node.querySelector('[data-location]')?.textContent).toContain('"deckId":"deck-1"')
})

it('starts a completed quiz only after the user chooses its toast action', async () => {
  window.sessionStorage.setItem('knowloop.ai-jobs.user-1', JSON.stringify(['job-1']))
  vi.mocked(api.aiJobs.list).mockResolvedValue([makeJob({
    job_type: 'quiz', title: '练习题', status: 'succeeded', phase: 'ready', progress: 100,
    result_resource_type: 'quiz', result_resource_id: 'quiz-1',
  })])
  vi.mocked(api.learning.startAttempt).mockResolvedValue({ id: 'attempt-1' } as never)
  await render()

  expect(api.learning.startAttempt).not.toHaveBeenCalled()
  await act(async () => {
    notify.mock.calls[0][1].action.onClick()
    await Promise.resolve()
    await Promise.resolve()
  })
  expect(api.learning.startAttempt).toHaveBeenCalledWith('quiz-1', true)
  expect(node.querySelector('[data-location]')?.textContent).toContain('"attemptId":"attempt-1"')
})

it('shows retry guidance for a tracked failure without replaying old results', async () => {
  window.sessionStorage.setItem('knowloop.ai-jobs.user-1', JSON.stringify(['job-1']))
  vi.mocked(api.aiJobs.list).mockResolvedValue([makeJob({
    status: 'failed', phase: 'failed', error_code: 'generation_failed',
    error_message: '模型暂不可用。',
  })])
  await render()
  expect(notify).toHaveBeenCalledWith(expect.stringContaining('generation_failed'), expect.objectContaining({ durationMs: 8000 }))
  expect(notify).toHaveBeenCalledWith(expect.stringContaining('检查资料和模型设置后重新提交'), expect.anything())

  notify.mockClear()
  vi.mocked(api.aiJobs.list).mockResolvedValue([makeJob({ status: 'succeeded', result_resource_id: 'old-note' })])
  await act(async () => { await vi.advanceTimersByTimeAsync(1800) })
  expect(notify).not.toHaveBeenCalled()
})
