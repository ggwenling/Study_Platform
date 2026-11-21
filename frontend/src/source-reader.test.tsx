// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from './api/client'
import type { MaterialReader } from './api/types'
import { SourceReaderDrawer } from './components/SourceReaderDrawer'

const workspace = vi.hoisted(() => ({ libraries: [{ id: 'kb', name: '资料库' }], notify: vi.fn(), report: vi.fn() }))
vi.mock('./WorkspaceData', () => ({ useWorkspace: () => workspace }))
vi.mock('./api/client', () => ({
  api: {
    materialReader: vi.fn(), pageImage: vi.fn(), content: vi.fn(),
    studio: { notes: vi.fn(), createNote: vi.fn(), appendNote: vi.fn() },
  },
}))

const reader: MaterialReader = {
  material_id: 'material', version_id: 'version', knowledge_base_id: 'kb', title: '矩阵讲义',
  mime_type: 'application/pdf', source_url: null, page_count: 4, preview_page_count: 4,
  can_render_pages: true, content_url: '/content', target_chunk_id: 'target', target_page: 2,
  target_quote: '特征值',
  chunks: [
    { id: 'before', ordinal: 1, text: '第一段。', locator: { page: 1 }, location_label: '第 1 页', is_target: false },
    { id: 'target', ordinal: 2, text: '矩阵的特征值。', locator: { page: 2 }, location_label: '第 2 页', is_target: true },
    { id: 'after', ordinal: 3, text: '第三段。', locator: { page: 3 }, location_label: '第 3 页', is_target: false },
  ],
  total_chunks: 3, has_prev: true, has_next: true,
}

let node: HTMLDivElement
let root: Root

async function render(view: ReactNode) {
  await act(async () => root.render(<MemoryRouter>{view}</MemoryRouter>))
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
}

async function click(label: string) {
  const button = [...document.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.includes(label))
  expect(button, label).toBeTruthy()
  await act(async () => button!.click())
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.mocked(api.materialReader).mockResolvedValue(reader)
  vi.mocked(api.pageImage).mockImplementation(() => new Promise<Response>(() => {}))
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
})

afterEach(async () => {
  await act(async () => root.unmount())
  node.remove()
  vi.unstubAllGlobals()
})

it('loads and highlights the citation chunk, then expands adjacent context', async () => {
  const previous = { ...reader, chunks: [
    { id: 'earlier', ordinal: 0, text: '前文。', locator: {}, location_label: '前文', is_target: false },
    ...reader.chunks.slice(0, 2),
  ], has_prev: false }
  const next = { ...reader, chunks: [
    ...reader.chunks.slice(1),
    { id: 'later', ordinal: 4, text: '后文。', locator: {}, location_label: '后文', is_target: false },
  ], has_next: false }
  vi.mocked(api.materialReader).mockResolvedValueOnce(reader).mockResolvedValueOnce(previous).mockResolvedValueOnce(next)
  const followUp = vi.fn()
  await render(<SourceReaderDrawer open onOpenChange={vi.fn()} target={{
    materialVersionId: 'version', materialId: 'material', knowledgeBaseId: 'kb', chunkId: 'target',
    title: '矩阵讲义', quote: '特征值', locator: { page: 2 }, locationLabel: '第 2 页',
  }} onAskFollowUp={followUp} />)

  expect(api.materialReader).toHaveBeenNthCalledWith(1, 'version', 'target', 3, expect.any(AbortSignal))
  expect(document.querySelector('[data-target="true"]')?.textContent).toContain('矩阵的特征值')
  expect(document.querySelector('mark')?.textContent).toBe('特征值')
  await click('加载前文')
  expect(api.materialReader).toHaveBeenNthCalledWith(2, 'version', 'before', 3, expect.any(AbortSignal))
  expect(document.querySelector('.source-reader-copy')?.textContent).toContain('前文。')
  await click('加载后文')
  expect(api.materialReader).toHaveBeenNthCalledWith(3, 'version', 'after', 3, expect.any(AbortSignal))
  expect(document.querySelector('.source-reader-copy')?.textContent).toContain('后文。')
  await click('针对此段追问')
  expect(followUp).toHaveBeenCalledWith(expect.objectContaining({ knowledgeBaseId: 'kb', materialId: 'material', excerpt: '特征值' }))
  await click('摘录到笔记')
  const dialogs = [...document.querySelectorAll('[role=dialog]')]
  const noteDialog = dialogs.find((dialog) => dialog.textContent?.includes('原文摘录'))
  expect(noteDialog?.textContent).toContain('来源：矩阵讲义 · 第 2 页')
  expect(noteDialog?.textContent).toContain('原文摘录')
})

it('renders paged PDF images and requests adjacent pages', async () => {
  await render(<SourceReaderDrawer open onOpenChange={vi.fn()} target={{
    materialVersionId: 'version', materialId: 'material', knowledgeBaseId: 'kb', chunkId: 'target',
    title: '矩阵讲义', locator: { page: 2 }, locationLabel: '第 2 页',
  }} />)
  await click('原页图像')
  expect(api.pageImage).toHaveBeenNthCalledWith(1, 'version', 2)
  await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="下一页"]')!.click())
  expect(api.pageImage).toHaveBeenNthCalledWith(2, 'version', 3)
})
