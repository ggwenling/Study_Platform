// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from './api/client'
import type { Page, SearchItem } from './api/types'
import { SearchPanel } from './components/SearchPanel'

vi.mock('./WorkspaceData', () => ({ useWorkspace: () => ({ libraries: [], recent: [], report: vi.fn() }) }))
vi.mock('./api/client', () => ({ api: { search: vi.fn() } }))

let node: HTMLElement
let root: Root
let input: HTMLInputElement
const makePage = (id: string, type: SearchItem['type'], title: string, next_cursor: string | null): Page<SearchItem> => ({
  items: [{ id, type, title, knowledge_base_id: 'kb' }], next_cursor,
})

async function changeInput(value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

beforeEach(async () => {
  vi.resetAllMocks()
  vi.useFakeTimers()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }))
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
  await act(async () => root.render(<MemoryRouter><SearchPanel onSelect={vi.fn()} /></MemoryRouter>))
  input = node.querySelector('input')!
})

afterEach(async () => {
  await act(async () => root.unmount())
  node.remove()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('keeps only the current query and loads additional results with the same cursor and search mode', async () => {
  let resolveOlder!: (page: Page<SearchItem>) => void
  vi.mocked(api.search)
    .mockImplementationOnce(() => new Promise((resolve) => { resolveOlder = resolve }))
    .mockResolvedValueOnce(makePage('chunk', 'fragment', '新资料', 'cursor'))
    .mockResolvedValueOnce(makePage('note', 'note', '研究记录', null))

  expect(node.querySelector('[role=option]')?.textContent).toBe('新对话')
  expect(api.search).not.toHaveBeenCalled()
  await changeInput('旧关键词')
  await act(async () => vi.advanceTimersByTimeAsync(180))
  const firstSignal = vi.mocked(api.search).mock.calls[0][3]!
  await changeInput('新关键词')
  expect(firstSignal.aborted).toBe(true)
  await act(async () => vi.advanceTimersByTimeAsync(180))
  await act(async () => resolveOlder(makePage('old', 'material', '应被丢弃', null)))
  expect(node.textContent).not.toContain('应被丢弃')
  expect(node.querySelector('[role=option]')?.getAttribute('href')).toBe('/knowledge?id=kb&fragment=chunk')

  await changeInput('新关键词 ')
  expect(node.textContent).toContain('新资料')
  const more = [...node.querySelectorAll('button')].find((button) => button.textContent === '加载更多')!
  await act(async () => more.click())
  expect(api.search).toHaveBeenLastCalledWith('新关键词', 'cursor', true, expect.any(AbortSignal))
  expect([...node.querySelectorAll('[role=option]')].map((option) => option.getAttribute('href'))).toEqual([
    '/knowledge?id=kb&fragment=chunk', '/knowledge?id=kb&note=note',
  ])
})
