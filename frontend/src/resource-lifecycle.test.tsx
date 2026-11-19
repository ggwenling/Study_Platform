// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api/client'
import type { Conversation, LibraryOverview, Material, Message } from './api/types'
import { formatConversationMarkdown, sanitizeMarkdownFilename } from './conversationActions'
import { ConversationDialogs } from './components/ConversationDialogs'
import { CurrentMaterials } from './components/CurrentMaterials'
import Library from './components/Library'
import { ThemeProvider } from './Theme'

const navigateMock = vi.hoisted(() => vi.fn())
const workspace = vi.hoisted(() => ({ value: {} as Record<string, any> }))

vi.mock('react-router', () => ({
  useNavigate: () => navigateMock,
  useSearchParams: () => [new URLSearchParams(), vi.fn()],
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))
vi.mock('./WorkspaceData', () => ({ useWorkspace: () => workspace.value }))
vi.mock('./api/client', () => ({
  api: {
    updateConversation: vi.fn(),
    deleteConversation: vi.fn(),
    materials: vi.fn(),
    cancelJob: vi.fn(),
    deleteMaterial: vi.fn(),
    retryMaterial: vi.fn(),
    libraryOverview: vi.fn(),
    syncLibrary: vi.fn(),
    documentLocations: vi.fn(),
    fragments: vi.fn(),
    studio: { notes: vi.fn() },
    deleteKnowledgeBase: vi.fn(),
  },
  uploadMaterial: vi.fn(),
}))
vi.mock('./components/arc/accordion/accordion', () => ({
  Accordion: ({ items }: any) => <>{items.map((item: any, index: number) => <section key={index}>{item.title}{item.content}</section>)}</>,
}))

let root: Root
let node: HTMLDivElement

const materialRow = (status: string, id = 'm1'): Material => ({
  id,
  knowledge_base_id: 'kb-1',
  title: '线性代数讲义.pdf',
  mime_type: 'application/pdf',
  byte_size: 2048,
  status,
  version_id: 'v1',
  job_id: 'job-1',
  created_at: '2026-11-18T00:00:00Z',
  error_code: null,
  error_message: null,
})

const overviewData: LibraryOverview = {
  file_count: 2,
  ready_count: 1,
  parsed_count: 1,
  fragment_count: 8,
  cut_count: 8,
  embedded_count: 8,
  ocr_count: 0,
  ocr_queue: 0,
  ocr_material_ids: [],
  processing_count: 0,
  failed_count: 0,
  last_synced_at: null,
  topics: [],
  tags: [],
  activity: [],
}

async function render(view: ReactNode) {
  await act(async () => root.render(<ThemeProvider>{view}</ThemeProvider>))
}

function findButton(label: string) {
  return [...document.querySelectorAll<HTMLButtonElement>('button')].find((btn) => btn.textContent?.trim() === label)
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.studio.notes).mockResolvedValue({ items: [] })
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} takeRecords() { return [] } })
  vi.stubGlobal('matchMedia', () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }))
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
  workspace.value = {
    user: { id: 'u1', email: 'u@example.test', display_name: '测试', avatar_url: null },
    libraries: [
      { id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' },
      { id: 'kb-2', name: '英语资料库', description: null, created_at: '', updated_at: '' },
    ],
    loading: false,
    revision: 0,
    capabilities: { rag: true, max_upload_bytes: 10_000_000 },
    refresh: vi.fn(),
    notify: vi.fn(),
    report: vi.fn(),
    requireLogin: vi.fn(),
  }
})

afterEach(async () => {
  await act(async () => root.unmount())
  node.remove()
  vi.unstubAllGlobals()
})

describe('conversation export & lifecycle dialogs', () => {
  it('formats conversation markdown with scope badges and citation footnotes', () => {
    const messages: Message[] = [
      {
        id: 'm-user',
        conversation_id: 'c1',
        sequence_no: 0,
        role: 'user',
        content: '特征值的定义是什么？',
        status: 'completed',
        created_at: '2026-11-18T01:00:00Z',
        scope: {
          knowledge_base_id: 'kb-1',
          knowledge_base_name: '高数资料库',
          answer_mode: 'knowledge_only',
          selected_material_ids: ['m1'],
          index_version_ids: ['idx-1'],
        },
        citations: [],
        turn_id: 't1',
      },
      {
        id: 'm-ai',
        conversation_id: 'c1',
        sequence_no: 1,
        role: 'assistant',
        content: '设 A 是 n 阶矩阵，若 Ax = λx [S1]。',
        status: 'completed',
        created_at: '2026-11-18T01:00:02Z',
        scope: {
          knowledge_base_id: 'kb-1',
          knowledge_base_name: '高数资料库',
          answer_mode: 'with_general',
          index_version_ids: ['idx-1'],
        },
        citations: [
          {
            id: 'cit-1',
            source_label: 'S1',
            material_id: 'm1',
            material_version_id: 'v1',
            knowledge_base_id: 'kb-1',
            title: '线性代数讲义.pdf',
            quote: 'Ax = λx',
            locator: { page: 12 },
            location_label: '第 12 页',
            version_no: 1,
            availability: 'available',
          },
        ],
        turn_id: 't1',
      },
    ]

    const md = formatConversationMarkdown('特征值复习', messages)
    expect(md).toContain('# 特征值复习')
    expect(md).toContain('> 范围：高数资料库 · 限定 1 份资料 · 仅知识库')
    expect(md).toContain('> 范围：高数资料库 · 通用知识补充')
    expect(md).toContain('- [S1] 线性代数讲义.pdf（第 12 页）')
    expect(sanitizeMarkdownFilename('特征值/复习:笔记?')).toBe('特征值-复习-笔记-.md')
  })

  it('renames and deletes a conversation through ConversationDialogs', async () => {
    const renamed: Conversation = {
      id: 'c1',
      title: '新标题',
      project_knowledge_base_id: null,
      created_at: '',
      updated_at: '',
    }
    vi.mocked(api.updateConversation).mockResolvedValue(renamed)
    vi.mocked(api.deleteConversation).mockResolvedValue(undefined)
    const onRenamed = vi.fn()
    const onDeleted = vi.fn()
    const onClose = vi.fn()

    await render(
      <ConversationDialogs
        target={{ mode: 'rename', conversation: { id: 'c1', title: '旧标题' } }}
        onClose={onClose}
        onRenamed={onRenamed}
        report={workspace.value.report}
        notify={workspace.value.notify}
      />,
    )
    const input = document.querySelector<HTMLInputElement>('input[name=title]')!
    input.value = '  新标题  '
    await act(async () => {
      input.closest('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })
    expect(api.updateConversation).toHaveBeenCalledWith('c1', { title: '新标题' })
    expect(onRenamed).toHaveBeenCalledWith(renamed)

    await render(
      <ConversationDialogs
        target={{ mode: 'delete', conversation: { id: 'c1', title: '新标题' } }}
        onClose={onClose}
        onDeleted={onDeleted}
        report={workspace.value.report}
        notify={workspace.value.notify}
      />,
    )
    await act(async () => findButton('确认删除')!.click())
    expect(api.deleteConversation).toHaveBeenCalledWith('c1')
    expect(onDeleted).toHaveBeenCalledWith('c1')
  })
})

describe('material cancel/delete & knowledge base deletion', () => {
  it('reveals the complete library after its notes and fragments finish, and keeps the empty note state during sync', async () => {
    vi.mocked(api.libraryOverview).mockResolvedValue(overviewData)
    vi.mocked(api.syncLibrary).mockResolvedValue(overviewData)
    vi.mocked(api.materials).mockResolvedValue({ items: [], next_cursor: null })
    let resolveFragments!: () => void
    vi.mocked(api.fragments).mockReturnValueOnce(new Promise((resolve) => {
      resolveFragments = () => resolve({ total: 0, page_size: 20, items: [] })
    })).mockResolvedValue({ total: 0, page_size: 20, items: [] })
    let resolveNotes!: () => void
    vi.mocked(api.studio.notes).mockReturnValueOnce(new Promise((resolve) => {
      resolveNotes = () => resolve({ items: [] })
    }))

    await render(<Library preferredId="kb-1" />)
    expect(node.querySelector('.library-loading')).toBeTruthy()
    expect(node.querySelector('.library-detail-ready')).toBeNull()
    await act(async () => resolveNotes())
    expect(node.querySelector('.library-detail-ready')).toBeNull()
    await act(async () => resolveFragments())
    expect(node.querySelector('.library-loading')).toBeNull()
    const emptyNotes = node.querySelector('.library-studio-panel .library-empty')
    expect(emptyNotes?.textContent).toContain('暂无研究笔记')
    expect(api.studio.notes).toHaveBeenCalledTimes(1)
    expect(api.fragments).toHaveBeenCalledTimes(1)

    vi.mocked(api.studio.notes).mockReturnValueOnce(new Promise((resolve) => {
      resolveNotes = () => resolve({ items: [] })
    }))
    await act(async () => findButton('同步')!.click())
    expect(api.studio.notes).toHaveBeenCalledTimes(2)
    expect(node.querySelector('.library-studio-panel .library-empty')).toBe(emptyNotes)
    expect(emptyNotes?.textContent).toContain('暂无研究笔记')
    await act(async () => resolveNotes())
    expect(node.querySelector('.library-studio-panel .library-empty')).toBe(emptyNotes)
  })

  it('cancels an active ingest job and deletes a material with confirmation in CurrentMaterials', async () => {
    vi.mocked(api.materials).mockResolvedValue({ items: [materialRow('ocr')], next_cursor: null })
    vi.mocked(api.cancelJob).mockResolvedValue({ id: 'job-1', status: 'cancelled' })
    vi.mocked(api.deleteMaterial).mockResolvedValue(undefined)

    await render(<CurrentMaterials libraryId="kb-1" name="高数资料库" />)
    await act(async () => findButton('取消处理')!.click())
    expect(api.cancelJob).toHaveBeenCalledWith('job-1')
    expect(workspace.value.refresh).toHaveBeenCalledTimes(1)

    await act(async () => findButton('删除')!.click())
    expect(document.body.textContent).toContain('确认删除资料「线性代数讲义.pdf」吗？')
    await act(async () => findButton('确认删除')!.click())
    expect(api.deleteMaterial).toHaveBeenCalledWith('m1')
    expect(workspace.value.refresh).toHaveBeenCalledTimes(2)
  })

  it('deletes a knowledge base after showing impact summary and navigates to remaining library', async () => {
    vi.mocked(api.libraryOverview).mockResolvedValue(overviewData)
    vi.mocked(api.documentLocations).mockResolvedValue([])
    vi.mocked(api.materials).mockResolvedValue({ items: [materialRow('ready')], next_cursor: null })
    vi.mocked(api.fragments).mockResolvedValue({ total: 0, page_size: 20, items: [] })
    vi.mocked(api.deleteKnowledgeBase).mockResolvedValue(undefined)

    await render(<Library preferredId="kb-1" />)
    const menuTrigger = document.querySelector<HTMLButtonElement>('button[aria-label="知识库操作"]')!
    await act(async () => {
      menuTrigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
      menuTrigger.click()
    })
    const deleteItem = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((el) => el.textContent?.includes('删除知识库'))!
    expect(deleteItem).toBeTruthy()
    await act(async () => deleteItem.click())

    expect(document.body.textContent).toContain('即将删除知识库「高数资料库」（共 2 份资料）')
    await act(async () => findButton('确认删除知识库')!.click())
    expect(api.deleteKnowledgeBase).toHaveBeenCalledWith('kb-1')
    expect(navigateMock).toHaveBeenCalledWith('/knowledge?id=kb-2', { replace: true })
  })
})
