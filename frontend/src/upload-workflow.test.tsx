// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, uploadMaterial } from './api/client'
import type { Material, UploadResult } from './api/types'
import { CurrentMaterials } from './components/CurrentMaterials'
import { Composer } from './components/Composer'

const workspace = vi.hoisted(() => ({ value: {} as Record<string, any> }))
vi.mock('./WorkspaceData', () => ({ useWorkspace: () => workspace.value }))
vi.mock('./api/client', () => ({ api: { materials: vi.fn(), retryMaterial: vi.fn(), createKnowledgeBase: vi.fn() }, uploadMaterial: vi.fn() }))
vi.mock('metal-fx', () => ({ BEND: {}, useMetalBend() {}, MetalFx: ({ children }: { children: ReactNode }) => <div>{children}</div> }))
vi.mock('./components/FadingTextarea', () => ({ FadingTextarea: ({ label, ...props }: any) => <textarea aria-label={label} {...props} /> }))
vi.mock('./components/arc/select/select', () => ({ Select: ({ label, options, value, onValueChange }: any) => <select aria-label={label} value={value} onChange={(event) => onValueChange(event.target.value)}>{options.map((option: any) => <option key={option.value} value={option.value}>{option.label}</option>)}</select> }))
vi.mock('./components/arc/accordion/accordion', () => ({ Accordion: ({ items }: any) => <>{items.map((item: any, index: number) => <section key={index}>{item.title}{item.content}</section>)}</> }))

let root: Root
let node: HTMLDivElement
const material = (status: string, id = 'm'): Material => ({ id, knowledge_base_id: 'A', title: '资料甲', mime_type: 'text/plain', byte_size: 3, version_id: 'v', job_id: 'j', status, created_at: '', error_code: null, error_message: status === 'failed' ? '无法提取文字' : null })
const library = (id: string) => ({ id, name: `库${id}`, description: null, created_at: '', updated_at: '' })
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
async function render(view: ReactNode) { await act(async () => root.render(view)) }
async function click(text: string) {
  const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent === text)!
  expect(button).toBeTruthy()
  await act(async () => button.click())
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.resetAllMocks()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }))
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
  workspace.value = { user: { id: 'one' }, libraries: [library('A'), library('B')], revision: 0, capabilities: { rag: true, max_upload_bytes: 1000 }, refresh: vi.fn(), report: vi.fn(), notify: vi.fn(), requireLogin: vi.fn(), chat: { busy: false, send: vi.fn() } }
  vi.mocked(api.materials).mockResolvedValue({ items: [], next_cursor: null })
})
afterEach(async () => { await act(async () => root.unmount()); node.remove(); vi.useRealTimers(); vi.unstubAllGlobals() })

it('polls processing stages, announces readiness and stops at terminal state', async () => {
  vi.mocked(api.materials).mockResolvedValueOnce({ items: [material('ocr')], next_cursor: null }).mockResolvedValue({ items: [material('ready')], next_cursor: null })
  await render(<CurrentMaterials libraryId="A" name="库A" />)
  expect(node.textContent).toContain('识别文字中')
  expect(node.textContent).toContain('暂无可问答资料，请等待处理完成')
  await act(async () => { await vi.advanceTimersByTimeAsync(2200) })
  expect(node.textContent).toContain('资料已就绪，可以继续提问')
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(api.materials).toHaveBeenCalledTimes(2)
})

it('preserves stale rows on read error and lets the user resume polling', async () => {
  vi.mocked(api.materials).mockResolvedValueOnce({ items: [material('parsing')], next_cursor: null }).mockRejectedValueOnce(new TypeError('offline')).mockResolvedValue({ items: [material('ready')], next_cursor: null })
  await render(<CurrentMaterials libraryId="A" name="库A" />)
  await act(async () => { await vi.advanceTimersByTimeAsync(2200) })
  expect(node.textContent).toContain('状态读取失败')
  expect(node.textContent).toContain('上次读取结果')
  expect(node.textContent).toContain('资料甲')
  await click('重新读取状态')
  expect(node.textContent).toContain('1 份可问答')
})

it('aborts on switching scope and ignores late responses from the old library', async () => {
  const old = deferred<{ items: Material[]; next_cursor: null }>()
  vi.mocked(api.materials).mockReturnValueOnce(old.promise)
  await render(<CurrentMaterials key="A" libraryId="A" name="库A" />)
  const signal = vi.mocked(api.materials).mock.calls[0][2]!
  await render(<CurrentMaterials key="B" libraryId="B" name="库B" />)
  expect(signal.aborted).toBe(true)
  await act(async () => old.resolve({ items: [material('ocr')], next_cursor: null }))
  expect(node.textContent).toContain('库B')
  expect(node.textContent).not.toContain('资料甲')
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(api.materials).toHaveBeenCalledTimes(2)
})

it('shows failures and prevents duplicate retry while pending', async () => {
  vi.mocked(api.materials).mockResolvedValue({ items: [material('failed')], next_cursor: null })
  const retry = deferred<UploadResult>()
  vi.mocked(api.retryMaterial).mockReturnValue(retry.promise)
  await render(<CurrentMaterials libraryId="A" name="库A" />)
  expect(node.textContent).toContain('无法提取文字')
  await click('重试处理')
  expect(node.querySelector('button')?.disabled).toBe(true)
  await click('重试处理')
  expect(api.retryMaterial).toHaveBeenCalledTimes(1)
  await act(async () => retry.resolve({ material_id: 'm', version_id: 'v', job_id: 'new', status: 'queued' }))
  expect(workspace.value.refresh).toHaveBeenCalledOnce()
})

it('does not describe a partially failed library as wholly ready', async () => {
  vi.mocked(api.materials).mockResolvedValue({ items: [material('ready'), material('failed', 'broken')], next_cursor: null })
  await render(<CurrentMaterials libraryId="A" name="库A" />)
  expect(node.textContent).toContain('1 份可问答 · 1 份需处理')
  expect(node.textContent).toContain('本次提问仅使用已就绪资料')
  expect(node.textContent).not.toContain('资料已就绪，可以继续提问')
})

it('binds in-flight upload to A after switching to B and preserves the draft', async () => {
  const upload = deferred<UploadResult>()
  vi.mocked(uploadMaterial).mockReturnValue(upload.promise)
  await render(<Composer />)
  const textarea = node.querySelector('textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, '待发送问题')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
  const file = new File(['hello'], '资料.txt', { type: 'text/plain' })
  const input = node.querySelector<HTMLInputElement>('input[type=file]')!
  Object.defineProperty(input, 'files', { value: [file] })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))
  await act(async () => {
    const select = node.querySelector('select')!
    select.value = 'B'
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await act(async () => upload.resolve({ material_id: 'm', version_id: 'v', job_id: 'j', status: 'queued' }))
  expect(uploadMaterial).toHaveBeenCalledWith('A', file, undefined, expect.any(AbortSignal))
  expect(node.textContent).toContain('已提交至「库A」')
  expect(node.querySelector('[aria-label="当前知识库资料：库B"]')).toBeTruthy()
  expect(textarea.value).toBe('待发送问题')
  expect(workspace.value.chat.send).not.toHaveBeenCalled()
})

it('creates and selects the first library without leaving chat', async () => {
  workspace.value.libraries = []
  vi.mocked(api.createKnowledgeBase).mockResolvedValue(library('C'))
  await render(<Composer />)
  await click('创建知识库并导入资料')
  const input = document.querySelector<HTMLInputElement>('input[name=name]')!
  input.value = '库C'
  await act(async () => input.closest('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(api.createKnowledgeBase).toHaveBeenCalledWith({ name: '库C' })
  workspace.value.libraries = [library('A'), library('C')]
  await render(<Composer />)
  expect(node.querySelector('select')?.value).toBe('C')
})
