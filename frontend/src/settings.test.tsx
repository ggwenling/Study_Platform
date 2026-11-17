// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from './api/client'
import type { ModelUsageSummary, User } from './api/types'
import { Settings } from './components/Settings'

vi.mock('./WorkspaceData', () => ({ useWorkspace: () => ({ revision: 0 }) }))
vi.mock('./components/ModelConnections', () => ({ ModelConnectionsPanel: () => <section aria-label="模型连接" /> }))
vi.mock('./api/client', () => ({ api: { modelUsage: vi.fn() } }))
const user: User = { id: 'one', display_name: '测试用户', email: 'test@example.test', avatar_url: null }
const bucket = (key: string, calls: number) => ({ key, calls, successes: calls, failures: 0, fallback_calls: 0, total_tokens: calls * 100 })
const usage: ModelUsageSummary = { days: 30, since: '2026-11-01',
  totals: { calls: 10, successes: 8, failures: 2, fallback_calls: 1, prompt_tokens: 600, completion_tokens: 400, total_tokens: 1000 },
  by_day: [], by_purpose: [bucket('chat', 6), bucket('embedding', 4)], by_model: [bucket('example-model', 10)],
  by_connection: [{ ...bucket('platform', 10), label: '平台连接' }] }
let node: HTMLDivElement
let root: Root
beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('matchMedia', () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }))
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
  vi.mocked(api.modelUsage).mockResolvedValue(usage)
})
afterEach(async () => { await act(async () => root.unmount()); node.remove(); vi.unstubAllGlobals() })
async function render(account: User | null = user) { await act(async () => root.render(<Settings user={account} requireLogin={vi.fn()} />)) }

it('derives the overview from real counts and keeps usage details collapsed by default', async () => {
  await render()
  expect(api.modelUsage).toHaveBeenCalledWith(30)
  const values = [...node.querySelectorAll('.settings-kv dd')].map((item) => item.textContent)
  expect(values).toEqual(['10', '80%', '2', '1,000'])
  const purposes = [...node.querySelectorAll('.usage-purpose-list li')].map((item) => item.textContent)
  expect(purposes[0]).toContain('60%')
  expect(purposes[1]).toContain('40%')
  const details = [...node.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.includes('详细用量'))!
  expect(details.getAttribute('aria-expanded')).toBe('false')
  await act(async () => details.click())
  expect(details.getAttribute('aria-expanded')).toBe('true')
  expect(node.textContent).toContain('平台连接')
  expect(node.textContent).toContain('example-model')
})

it('shows an empty success rate instead of a misleading zero percent without calls', async () => {
  vi.mocked(api.modelUsage).mockResolvedValue({ ...usage, totals: { ...usage.totals, calls: 0, successes: 0, failures: 0, total_tokens: 0 }, by_purpose: [] })
  await render()
  expect([...node.querySelectorAll('.settings-kv dd')].map((item) => item.textContent)).toEqual(['0', '—', '0', '0'])
  expect(node.textContent).toContain('暂无模型调用记录')
  expect(node.textContent).not.toContain('NaN')
})

it('retries failed usage independently of the model connections', async () => {
  vi.mocked(api.modelUsage).mockRejectedValueOnce(new Error('offline'))
  await render()
  expect(node.querySelector('[aria-label="模型连接"]')).toBeTruthy()
  const retry = [...node.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent === '重试用量')!
  expect(retry).toBeTruthy()
  await act(async () => retry.click())
  expect(api.modelUsage).toHaveBeenCalledTimes(2)
  expect(node.textContent).toContain('80%')
})

it('requires login before reading personal usage', async () => {
  await render(null)
  expect(api.modelUsage).not.toHaveBeenCalled()
  expect(node.textContent).toContain('登录后查看设置')
})
