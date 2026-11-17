// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from './api/client'
import type { ModelConnection, ModelConnections } from './api/types'
import { ModelConnectionsPanel } from './components/ModelConnections'

const workspace = vi.hoisted(() => ({ notify: vi.fn(), report: vi.fn(), refresh: vi.fn() }))
vi.mock('./WorkspaceData', () => ({ useWorkspace: () => workspace }))
vi.mock('./api/client', () => ({ api: { modelConnections: vi.fn(), createModelConnection: vi.fn(), updateModelConnection: vi.fn(),
  testModelConnection: vi.fn(), defaultModelConnection: vi.fn(), deleteModelConnection: vi.fn() } }))
let root: Root
let node: HTMLDivElement
const row: ModelConnection = { id: 'one', name: '个人连接', kind: 'llm', base_url: 'https://api.example/v1', model: 'personal-model',
  dimensions: null, key_configured: true, key_suffix: 'abcd', revision: 1, status: 'draft', is_default: false, tested_at: null, error_code: null, libraries: [] }
const data = (items = [row]): ModelConnections => ({ items, encryption_available: true, platform_llm: null, platform_embedding: null })
const buttons = () => [...document.querySelectorAll<HTMLButtonElement>('button')]
const button = (text: string) => buttons().find((element) => element.textContent?.trim() === text)!
async function click(text: string) { await act(async () => button(text).click()) }
beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('matchMedia', () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('FormData', window.FormData)
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
  vi.mocked(api.modelConnections).mockResolvedValue(data())
})
afterEach(async () => { await act(async () => root.unmount()); node.remove(); vi.unstubAllGlobals() })

it('requires a successful live test before choosing a default and explains errors', async () => {
  await act(async () => root.render(<ModelConnectionsPanel />))
  expect(button('设为默认').disabled).toBe(true)
  expect(node.textContent).toContain('尾号 abcd')
  const failure = { ...row, status: 'failed' as const, error_code: 'model_auth_failed' }
  vi.mocked(api.testModelConnection).mockResolvedValue(failure)
  vi.mocked(api.modelConnections).mockResolvedValue(data([failure]))
  await click('测试连接')
  expect(node.textContent).toContain('请检查 API Key')
  expect(button('设为默认').disabled).toBe(true)
  const ready = { ...row, status: 'ready' as const }
  vi.mocked(api.testModelConnection).mockResolvedValue(ready)
  vi.mocked(api.modelConnections).mockResolvedValue(data([ready]))
  await click('测试连接')
  expect(button('设为默认').disabled).toBe(false)
  await click('设为默认')
  expect(api.defaultModelConnection).toHaveBeenCalledWith('llm', 'one')
})

it('saves credentials once as a draft and clears the form on close', async () => {
  await act(async () => root.render(<ModelConnectionsPanel />))
  await click('添加连接')
  const form = document.querySelector<HTMLFormElement>('.model-form')!
  const input = (name: string) => form.elements.namedItem(name) as HTMLInputElement
  input('name').value = '新连接'
  input('base_url').value = 'https://api.example/v1'
  input('model').value = 'personal-model'
  input('api_key').value = 'secret-entered-only-once'
  expect(input('api_key').type).toBe('password')
  await act(async () => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(api.createModelConnection).toHaveBeenCalledWith({ kind: 'llm', name: '新连接', base_url: 'https://api.example/v1', model: 'personal-model', api_key: 'secret-entered-only-once' })
  expect(api.defaultModelConnection).not.toHaveBeenCalled()
  await click('添加连接')
  expect(document.querySelector<HTMLInputElement>('input[name="api_key"]')?.value).toBe('')
})

it('disables saving when deployment encryption is missing and supports list retry', async () => {
  vi.mocked(api.modelConnections).mockRejectedValueOnce(new Error('offline'))
  await act(async () => root.render(<ModelConnectionsPanel />))
  vi.mocked(api.modelConnections).mockResolvedValue({ ...data([]), encryption_available: false })
  await click('重试')
  expect(node.textContent).toContain('管理员尚未配置')
  expect(button('添加连接').disabled).toBe(true)
})
