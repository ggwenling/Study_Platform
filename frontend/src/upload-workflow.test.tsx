// @vitest-environment jsdom
import { act, cloneElement, isValidElement, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, uploadMaterial } from './api/client'
import type { Material, UploadResult } from './api/types'
import { CurrentMaterials } from './components/CurrentMaterials'
import { Composer } from './components/Composer'
import { isSupportedUploadFile, useUploadQueue } from './useUploadQueue'
import { clearAllChatDrafts } from './useChatDrafts'
import { locatorLabel, safeExternalUrl } from './api/ui'
import { PasteTextDialog } from './components/PasteTextDialog'
import { WebClipDialog } from './components/WebClipDialog'
import { ThemeProvider } from './Theme'

const workspace = vi.hoisted(() => ({ value: {} as Record<string, any> }))
vi.mock('./WorkspaceData', () => ({ useWorkspace: () => workspace.value }))
vi.mock('./api/client', () => ({ api: { materials: vi.fn(), retryMaterial: vi.fn(), createKnowledgeBase: vi.fn(), webClip: vi.fn() }, uploadMaterial: vi.fn() }))
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
function SharedQueue({ children }: { children: ReactNode }) {
  const queue = useUploadQueue({
    userId: workspace.value.user?.id,
    maxUploadBytes: workspace.value.capabilities?.max_upload_bytes,
    maxPdfUploadBytes: workspace.value.capabilities?.max_pdf_upload_bytes,
    refresh: workspace.value.refresh,
    notify: workspace.value.notify,
    report: workspace.value.report,
  })
  workspace.value.uploadQueue = queue
  workspace.value.libraryUploadQueue = queue
  return isValidElement(children) ? cloneElement(children) : children
}
async function render(view: ReactNode) { await act(async () => root.render(<ThemeProvider><SharedQueue>{view}</SharedQueue></ThemeProvider>)) }
async function click(text: string) {
  const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent === text)!
  expect(button).toBeTruthy()
  await act(async () => button.click())
}

beforeEach(() => {
  clearAllChatDrafts()
  vi.useFakeTimers()
  vi.resetAllMocks()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
  workspace.value = { user: { id: 'one' }, libraries: [library('A'), library('B')], revision: 0, capabilities: { rag: true, max_upload_bytes: 1000 }, refresh: vi.fn(), report: vi.fn(), notify: vi.fn(), requireLogin: vi.fn(), chat: { isBusy: () => false, activeFor: () => null, pendingFor: () => null, send: vi.fn() } }
  vi.mocked(api.materials).mockResolvedValue({ items: [], next_cursor: null })
})
afterEach(async () => { await act(async () => root.unmount()); node.remove(); vi.useRealTimers(); vi.unstubAllGlobals() })

it('polls processing stages, announces readiness and stops at terminal state', async () => {
  vi.mocked(api.materials).mockResolvedValueOnce({
    items: [{ ...material('ocr'), processed_pages: 75, total_pages: 150 }], next_cursor: null,
  }).mockResolvedValue({ items: [material('ready')], next_cursor: null })
  await render(<CurrentMaterials libraryId="A" name="库A" />)
  expect(node.textContent).toContain('识别文字中')
  expect(node.textContent).toContain('已处理 75 / 150 页')
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
  await render(<CurrentMaterials libraryId="A" name="库A" />)
  const signal = vi.mocked(api.materials).mock.calls[0][2]!
  const status = node.querySelector('.current-materials-title [role="status"]')
  await render(<CurrentMaterials libraryId="B" name="库B" />)
  expect(node.querySelector('.current-materials-title [role="status"]')).toBe(status)
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

it('applies the source material scope when opening a follow-up draft', async () => {
  vi.mocked(api.materials).mockResolvedValue({ items: [material('ready')], next_cursor: null })
  await render(<Composer preferredLibrary="A" initialDraft="继续解释这个来源" initialMaterialIds={['m']} />)

  const form = node.querySelector<HTMLFormElement>('form.composer')!
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  })

  expect(workspace.value.chat.send).toHaveBeenCalledWith(expect.objectContaining({
    knowledge_base_id: 'A',
    material_ids: ['m'],
    content: '继续解释这个来源',
  }), expect.any(Function))
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

it('accepts PPTX files and processes the shared queue sequentially with single-item retry', async () => {
  const first = new File(['broken'], '一.txt', { type: 'text/plain' })
  const second = new File(['presentation'], '二.pptx', { type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation' })
  vi.mocked(uploadMaterial)
    .mockRejectedValueOnce(new Error('network error'))
    .mockResolvedValue({ material_id: 'm2', version_id: 'v2', job_id: 'j2', status: 'queued' })

  await render(<Composer />)
  const input = node.querySelector<HTMLInputElement>('input[type=file]')!
  expect(input.accept).toContain('.pptx')
  Object.defineProperty(input, 'files', { value: [first, second] })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))
  await act(async () => { await Promise.resolve(); await Promise.resolve() })

  expect(uploadMaterial).toHaveBeenCalledTimes(2)
  expect(uploadMaterial).toHaveBeenNthCalledWith(1, 'A', first, undefined, expect.any(AbortSignal))
  expect(uploadMaterial).toHaveBeenNthCalledWith(2, 'A', second, undefined, expect.any(AbortSignal))
  expect(node.textContent).toContain('「二.pptx」已提交至「库A」')
  expect(node.textContent).toContain('「一.txt」上传失败')

  await click('重试上传')
  await act(async () => { await Promise.resolve() })
  expect(uploadMaterial).toHaveBeenCalledTimes(3)
})

it('uses the Composer vision choice once and resets it after the selected upload', async () => {
  workspace.value.capabilities = { ...workspace.value.capabilities, vision: true }
  vi.mocked(uploadMaterial).mockResolvedValue({ material_id: 'm', version_id: 'v', job_id: 'j', status: 'queued' })
  await render(<Composer />)

  const visionSwitch = node.querySelector<HTMLButtonElement>('button[role="switch"]')!
  expect(visionSwitch.getAttribute('aria-checked')).toBe('false')
  await act(async () => visionSwitch.click())
  expect(visionSwitch.getAttribute('aria-checked')).toBe('true')

  const input = node.querySelector<HTMLInputElement>('input[type=file][accept^=".pdf"]')!
  const pdf = new File(['PDF'], '讲义.pdf', { type: 'application/pdf' })
  Object.defineProperty(input, 'files', { value: [pdf], configurable: true })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))
  await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() })

  expect(visionSwitch.getAttribute('aria-checked')).toBe('false')
  expect(uploadMaterial).toHaveBeenNthCalledWith(1, 'A', pdf, undefined, expect.any(AbortSignal), 'vlm')

  const nextPdf = new File(['PDF 2'], '下一份.pdf', { type: 'application/pdf' })
  Object.defineProperty(input, 'files', { value: [nextPdf], configurable: true })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))
  await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() })
  expect(uploadMaterial).toHaveBeenNthCalledWith(2, 'A', nextPdf, undefined, expect.any(AbortSignal))
})

it('accepts drag-and-drop files and Ctrl+V pasted screenshots in Composer', async () => {
  vi.mocked(uploadMaterial).mockResolvedValue({ material_id: 'm', version_id: 'v', job_id: 'j', status: 'queued' })
  await render(<Composer />)

  const form = node.querySelector('form.composer')!
  const dropped = new File(['# 第一章'], '讲义.md', { type: 'text/markdown' })
  const dropEvent = new Event('drop', { bubbles: true, cancelable: true })
  Object.defineProperty(dropEvent, 'dataTransfer', { value: { files: [dropped], types: ['Files'] } })
  await act(async () => form.dispatchEvent(dropEvent))
  expect(uploadMaterial).toHaveBeenCalledWith('A', dropped, undefined, expect.any(AbortSignal))

  const textarea = node.querySelector('textarea')!
  const rawImage = new File([new Uint8Array([137, 80, 78, 71])], 'image.png', { type: 'image/png' })
  const pasteEvent = new Event('paste', { bubbles: true, cancelable: true })
  Object.defineProperty(pasteEvent, 'clipboardData', { value: { files: [rawImage], items: [] } })
  await act(async () => textarea.dispatchEvent(pasteEvent))
  expect(pasteEvent.defaultPrevented).toBe(true)
  const pastedCall = vi.mocked(uploadMaterial).mock.calls.at(-1)!
  expect(pastedCall[0]).toBe('A')
  expect((pastedCall[1] as File).name).toMatch(/^截图-.*\.png$/)
})

it('packages pasted Markdown note with auto-derived filename and formats Markdown heading locators', async () => {
  const onSubmitFile = vi.fn()
  await render(<PasteTextDialog open onOpenChange={vi.fn()} libraryName="库A" onSubmitFile={onSubmitFile} />)
  const contentInput = document.querySelector<HTMLTextAreaElement>('textarea[name=content]')!
  contentInput.value = '# 矩阵特征值总结\n特征向量非零。'
  await act(async () => {
    contentInput.closest('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  })
  expect(onSubmitFile).toHaveBeenCalledOnce()
  const generated = onSubmitFile.mock.calls[0][0] as File
  expect(generated.name).toBe('矩阵特征值总结.md')
  expect(generated.type).toBe('text/markdown')
  expect(locatorLabel({ heading: '矩阵特征值总结', start_line: 1 })).toBe('矩阵特征值总结 · 第 1 行')
  expect(locatorLabel({ slide: 3 })).toBe('第 3 页幻灯片')
  expect(locatorLabel({ slide: 3, notes: true })).toBe('第 3 页幻灯片 · 备注')
})

it('only exposes absolute HTTP(S) source links and accepts PPTX queue extensions', () => {
  expect(safeExternalUrl('https://example.com/path')).toBe('https://example.com/path')
  expect(safeExternalUrl('javascript:alert(1)')).toBeNull()
  expect(safeExternalUrl('http:example.com')).toBeNull()
  expect(safeExternalUrl('/relative/path')).toBeNull()
  expect(safeExternalUrl('https://user:pass@example.com')).toBeNull()
  expect(isSupportedUploadFile('slides.PPTX')).toBe(true)
  expect(isSupportedUploadFile('slides.ppt')).toBe(false)
})

it('submits a web clip through the shared queue and refreshes library status', async () => {
  const onOpenChange = vi.fn()
  vi.mocked(api.webClip).mockResolvedValue({ material_id: 'web', version_id: 'web-v', job_id: 'web-j', status: 'queued' })
  await render(<WebClipDialog open onOpenChange={onOpenChange} libraryId="A" libraryName="库A" />)
  const url = document.querySelector<HTMLInputElement>('input[name=url]')!
  const title = document.querySelector<HTMLInputElement>('input[name=title]')!
  url.value = 'https://example.com/article'
  title.value = '文章标题'
  const form = document.querySelector<HTMLFormElement>('[role=dialog] form')!
  await act(async () => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))

  expect(api.webClip).toHaveBeenCalledWith('A', { url: 'https://example.com/article', title: '文章标题' })
  expect(onOpenChange).toHaveBeenCalledWith(false)
  expect(workspace.value.refresh).toHaveBeenCalled()
  expect(workspace.value.notify).toHaveBeenCalledWith('网页已提交至「库A」，正在处理。')
  expect(workspace.value.uploadQueue.lastUploaded?.libraryId).toBe('A')
})

it('keeps a failed web clip in the dialog with an accessible error', async () => {
  vi.mocked(api.webClip).mockRejectedValue(new Error('未能读取网页'))
  await render(<WebClipDialog open onOpenChange={vi.fn()} libraryId="A" libraryName="库A" />)
  const url = document.querySelector<HTMLInputElement>('input[name=url]')!
  url.value = 'https://example.com/article'
  const form = document.querySelector<HTMLFormElement>('[role=dialog] form')!
  await act(async () => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))

  expect(document.querySelector('[role=alert]')?.textContent).toBe('请求失败，请检查连接后重试。')
  expect(workspace.value.refresh).not.toHaveBeenCalled()
  expect(workspace.value.notify).not.toHaveBeenCalled()
})


it('clears queued and failed upload items on logout or account switch to prevent cross-account uploads', async () => {
  const inFlight = deferred<UploadResult>()
  vi.mocked(uploadMaterial)
    .mockReturnValueOnce(inFlight.promise)
    .mockResolvedValue({ material_id: 'm-b', version_id: 'v-b', job_id: 'j-b', status: 'queued' })

  await render(<Composer />)
  const userAFile1 = new File(['secret1'], '用户甲正在传.txt', { type: 'text/plain' })
  const userAFile2 = new File(['secret2'], '用户甲待传.txt', { type: 'text/plain' })
  const input = node.querySelector<HTMLInputElement>('input[type=file]')!
  Object.defineProperty(input, 'files', { value: [userAFile1, userAFile2], configurable: true })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))

  expect(uploadMaterial).toHaveBeenCalledTimes(1)
  const firstSignal = vi.mocked(uploadMaterial).mock.calls[0][3]!
  expect(node.textContent).toContain('用户甲正在传.txt')

  // Logout (user -> null), then login as user 'two'
  workspace.value = { ...workspace.value, user: null }
  await render(<Composer />)
  expect(firstSignal.aborted).toBe(true)
  expect(node.textContent).not.toContain('用户甲正在传.txt')
  expect(node.textContent).not.toContain('用户甲待传.txt')

  workspace.value = { ...workspace.value, user: { id: 'two' }, libraries: [library('B')] }
  await render(<Composer />)
  const userBFile = new File(['hello-b'], '用户乙新文件.txt', { type: 'text/plain' })
  Object.defineProperty(input, 'files', { value: [userBFile], configurable: true })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))

  expect(uploadMaterial).toHaveBeenCalledTimes(2)
  expect(uploadMaterial).toHaveBeenNthCalledWith(2, 'B', userBFile, undefined, expect.any(AbortSignal))
})
