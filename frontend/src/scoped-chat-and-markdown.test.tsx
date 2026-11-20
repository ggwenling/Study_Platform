// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api/client'
import type { ChatRequest, Citation, Material, Scope } from './api/types'
import { Composer } from './components/Composer'
import type { ComposerHandle } from './components/Composer'
import { MarkdownContent } from './components/MarkdownContent'
import { ThemeProvider } from './Theme'
import { clearAllChatDrafts } from './useChatDrafts'

const workspace = vi.hoisted(() => ({ value: {} as Record<string, any> }))

vi.mock('./WorkspaceData', () => ({ useWorkspace: () => workspace.value }))
vi.mock('./api/client', () => ({
  api: {
    materials: vi.fn(),
    retryMaterial: vi.fn(),
    cancelJob: vi.fn(),
    deleteMaterial: vi.fn(),
    createKnowledgeBase: vi.fn(),
  },
  uploadMaterial: vi.fn(),
}))
vi.mock('metal-fx', () => ({ BEND: {}, useMetalBend() {}, MetalFx: ({ children }: { children: ReactNode }) => <div>{children}</div> }))
vi.mock('./components/FadingTextarea', () => ({ FadingTextarea: ({ label, ...props }: any) => <textarea aria-label={label} {...props} /> }))
vi.mock('./components/arc/select/select', () => ({
  Select: ({ label, options, value, onValueChange }: any) => (
    <select aria-label={label} value={value} onChange={(event) => onValueChange(event.target.value)}>
      {options.map((option: any) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
  ),
}))

let root: Root
let node: HTMLDivElement

const readyMaterial = (id: string, title: string, kb = 'A'): Material => ({
  id,
  knowledge_base_id: kb,
  title,
  mime_type: 'text/markdown',
  byte_size: 128,
  status: 'ready',
  version_id: `v-${id}`,
  job_id: `j-${id}`,
  created_at: '',
  error_code: null,
  error_message: null,
})

async function render(view: ReactNode) {
  await act(async () => root.render(<ThemeProvider>{view}</ThemeProvider>))
}

async function enterText(text: string) {
  const textarea = node.querySelector('textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, text)
    textarea.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

beforeEach(() => {
  clearAllChatDrafts()
  vi.resetAllMocks()
  sessionStorage.clear()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }))
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
  workspace.value = {
    user: { id: 'u1' },
    libraries: [
      { id: 'A', name: '库A', description: null, created_at: '', updated_at: '' },
      { id: 'B', name: '库B', description: null, created_at: '', updated_at: '' },
    ],
    revision: 0,
    capabilities: { rag: true, max_upload_bytes: 10_000_000 },
    refresh: vi.fn(),
    report: vi.fn(),
    notify: vi.fn(),
    requireLogin: vi.fn(),
    chat: {
      busy: false,
      send: vi.fn(async (_request, onAccepted: () => void) => { onAccepted(); return true }),
      isBusy: vi.fn().mockReturnValue(false),
      activeFor: vi.fn().mockReturnValue(null),
      pendingFor: vi.fn().mockReturnValue(null),
      resend: vi.fn().mockResolvedValue(false),
      stop: vi.fn(),
    },
    uploadQueue: { items: [], uploading: false, latestNote: '', lastUploaded: null },
  }
})

afterEach(async () => {
  await act(async () => root.unmount())
  node.remove()
  vi.unstubAllGlobals()
})

describe('scoped material selection & in-conversation summary toggle', () => {
  it('allows multi-selecting materials for QA, toggling summary mode, and clears scope on library switch', async () => {
    vi.mocked(api.materials).mockImplementation(async (kbId: string) => ({
      items: kbId === 'A'
        ? [readyMaterial('m1', '第一章.md', 'A'), readyMaterial('m2', '第二章.md', 'A')]
        : [readyMaterial('m3', '外语讲义.md', 'B')],
      next_cursor: null,
    }))

    await render(<Composer conversationId="conv-1" />)
    await act(async () => { await Promise.resolve() })

    const materialsTrigger = node.querySelector<HTMLButtonElement>('.current-materials button[aria-expanded]')!
    await act(async () => materialsTrigger.click())
    const checkboxes = node.querySelectorAll<HTMLInputElement>('input[type=checkbox]')
    expect(checkboxes).toHaveLength(2)
    await act(async () => {
      checkboxes[0].click()
      checkboxes[1].click()
    })
    expect(node.textContent).toContain('本轮提问已限定 2 份资料')
    expect(node.querySelector('.current-materials [role=status]')?.textContent).toContain('本轮提问已限定 2 份资料')
    expect(node.querySelector('form.composer')?.textContent).not.toContain('已限定')

    const textarea = node.querySelector('textarea')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, '对比两章重点')
      textarea.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await act(async () => {
      node.querySelector('form.composer')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })
    expect(workspace.value.chat.send).toHaveBeenLastCalledWith({
      conversation_id: 'conv-1',
      knowledge_base_id: 'A',
      answer_mode: 'knowledge_only',
      intent: 'qa',
      material_ids: ['m1', 'm2'],
      content: '对比两章重点',
    }, expect.any(Function))

    const summaryToggle = node.querySelector<HTMLButtonElement>('button[aria-label="摘要模式"]')!
    await act(async () => summaryToggle.click())
    expect(summaryToggle.getAttribute('aria-pressed')).toBe('true')
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, '提炼核心要点')
      textarea.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await act(async () => {
      node.querySelector('form.composer')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })
    expect(workspace.value.chat.send).toHaveBeenLastCalledWith({
      conversation_id: 'conv-1',
      knowledge_base_id: 'A',
      answer_mode: 'knowledge_only',
      intent: 'summary',
      material_ids: ['m1', 'm2'],
      content: '提炼核心要点',
    }, expect.any(Function))

    await act(async () => materialsTrigger.click())
    const clear = node.querySelector<HTMLButtonElement>('.current-materials button[aria-label="清除选择"]')!
    await act(async () => clear.click())
    expect(materialsTrigger.getAttribute('aria-expanded')).toBe('false')
    expect(node.querySelector('.current-materials [role=status]')?.textContent).toContain('请至少勾选 1 份资料')

    const select = node.querySelector('select')!
    await act(async () => {
      select.value = 'B'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(node.textContent).not.toContain('已限定 2 份资料')
  })
})

it('keeps an invalid selection blocked until the user explicitly uses the whole library', async () => {
  vi.mocked(api.materials).mockResolvedValue({ items: [readyMaterial('m1', '第一章.md')], next_cursor: null })
  await render(<Composer />)
  await act(async () => node.querySelector<HTMLButtonElement>('.current-materials button[aria-expanded]')!.click())
  await act(async () => node.querySelector<HTMLInputElement>('input[type=checkbox]')!.click())
  vi.mocked(api.materials).mockResolvedValue({ items: [], next_cursor: null })
  workspace.value.revision++
  await render(<Composer />)
  expect(node.textContent).toContain('已选资料不可用')
  const textarea = node.querySelector('textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, '问题')
    textarea.dispatchEvent(new Event('change', { bubbles: true }))
  })
  const submit = async () => act(async () => node.querySelector('form.composer')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  await submit()
  expect(workspace.value.chat.send).not.toHaveBeenCalled()
  await act(async () => [...node.querySelectorAll('button')].find((button) => button.textContent === '移除')!.click())
  await submit()
  expect(workspace.value.chat.send).not.toHaveBeenCalled()
  await act(async () => [...node.querySelectorAll('button')].find((button) => button.textContent === '使用全库')!.click())
  await submit()
  expect(workspace.value.chat.send).toHaveBeenCalledWith(expect.objectContaining({ material_ids: null }), expect.any(Function))
})

it('restores drafts per user and conversation with their library and answer options', async () => {
  vi.mocked(api.materials).mockResolvedValue({ items: [], next_cursor: null })
  await render(<Composer conversationId="conv-1" />)
  await enterText('保留的草稿')
  await act(async () => {
    const select = node.querySelector<HTMLSelectElement>('select[aria-label="知识库"]')!
    select.value = 'B'
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await act(async () => node.querySelector<HTMLButtonElement>('button[aria-label="通用知识补充"]')!.click())
  await act(async () => node.querySelector<HTMLButtonElement>('button[aria-label="摘要模式"]')!.click())

  await act(async () => root.render(null))
  await render(<Composer conversationId="conv-2" />)
  expect(node.querySelector('textarea')!.value).toBe('')
  await act(async () => root.render(null))
  await render(<Composer conversationId="conv-1" />)

  expect(node.querySelector('textarea')!.value).toBe('保留的草稿')
  expect(node.querySelector<HTMLSelectElement>('select[aria-label="知识库"]')!.value).toBe('B')
  expect(node.querySelector<HTMLButtonElement>('button[aria-label="通用知识补充"]')!.getAttribute('aria-pressed')).toBe('true')
  expect(node.querySelector<HTMLButtonElement>('button[aria-label="摘要模式"]')!.getAttribute('aria-pressed')).toBe('true')
})

it('only opens materials for uploads observed during the current Composer visit', async () => {
  vi.mocked(api.materials).mockResolvedValue({ items: [], next_cursor: null })
  const trigger = () => node.querySelector<HTMLButtonElement>('.current-materials button[aria-expanded]')!
  await render(<Composer />)
  expect(trigger().getAttribute('aria-expanded')).toBe('false')

  workspace.value.uploadQueue.lastUploaded = { libraryId: 'A', at: 100 }
  await render(<Composer />)
  expect(trigger().getAttribute('aria-expanded')).toBe('true')
  await act(async () => trigger().click())
  expect(trigger().getAttribute('aria-expanded')).toBe('false')

  await render(null)
  await render(<Composer conversationId="conv-1" />)
  expect(trigger().getAttribute('aria-expanded')).toBe('false')
  workspace.value.uploadQueue.lastUploaded = { libraryId: 'B', at: 200 }
  await render(<Composer conversationId="conv-1" />)
  expect(trigger().getAttribute('aria-expanded')).toBe('false')
  workspace.value.uploadQueue.lastUploaded = { libraryId: 'A', at: 300 }
  await render(<Composer conversationId="conv-1" />)
  expect(trigger().getAttribute('aria-expanded')).toBe('true')
})

it('keeps a newer edit when the sent text is accepted and clears accepted text after unmount', async () => {
  vi.mocked(api.materials).mockResolvedValue({ items: [], next_cursor: null })
  let accepted!: () => void
  let finish!: (result: boolean) => void
  workspace.value.chat.send.mockImplementation((_request: ChatRequest, onAccepted: () => void) => {
    accepted = onAccepted
    return new Promise<boolean>((resolve) => { finish = resolve })
  })
  await render(<Composer conversationId="conv-1" />)
  await enterText('发送中的问题')
  await act(async () => node.querySelector<HTMLFormElement>('form.composer')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  await enterText('发送期间的新修改')
  await act(async () => accepted())

  expect(node.querySelector('textarea')!.value).toBe('发送期间的新修改')
  const stored = JSON.parse(sessionStorage.getItem('knowloop:drafts:u1')!)
  expect(stored['conv-1'].text).toBe('发送期间的新修改')

  await act(async () => finish(true))
  await render(null)
  await render(<Composer />)
  await enterText('首页发送的问题')
  await act(async () => node.querySelector<HTMLFormElement>('form.composer')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(node.querySelector('textarea')!.value).toBe('首页发送的问题')
  await render(null)
  await act(async () => accepted())
  await render(<Composer />)
  expect(node.querySelector('textarea')!.value).toBe('')
  await enterText('下一条新问题')
  await act(async () => finish(true))
  expect(node.querySelector('textarea')!.value).toBe('下一条新问题')
})

it('clears the matching resent draft on acceptance while the answer is still streaming', async () => {
  vi.mocked(api.materials).mockResolvedValue({ items: [], next_cursor: null })
  workspace.value.chat.pendingFor.mockReturnValue({ body: { content: '待重发的问题' }, key: 'pending' })
  let accepted!: () => void
  let finish!: (result: boolean) => void
  workspace.value.chat.resend.mockImplementation((_id: string | undefined, onAccepted: () => void) => {
    accepted = onAccepted
    return new Promise<boolean>((resolve) => { finish = resolve })
  })
  await render(<Composer />)
  await enterText(' 待重发的问题 ')
  await act(async () => [...node.querySelectorAll('button')].find((button) => button.textContent?.includes('重发上次请求'))!.click())
  expect(node.querySelector('textarea')!.value).toBe(' 待重发的问题 ')
  await act(async () => accepted())
  expect(node.querySelector('textarea')!.value).toBe('')
  await act(async () => finish(true))
})

it('caps the per-user draft LRU at 30 conversations and clears it on auth reset', async () => {
  vi.mocked(api.materials).mockResolvedValue({ items: [], next_cursor: null })
  for (let index = 0; index < 31; index += 1) {
    await render(<Composer conversationId={`conv-${index}`} />)
    await enterText(`草稿${index}`)
    await act(async () => root.render(null))
  }

  const drafts = JSON.parse(sessionStorage.getItem('knowloop:drafts:u1')!)
  expect(Object.keys(drafts)).toHaveLength(30)
  expect(drafts['conv-0']).toBeUndefined()
  expect(drafts['conv-30'].text).toBe('草稿30')
  sessionStorage.setItem('unrelated', 'keep')
  clearAllChatDrafts()
  expect(sessionStorage.getItem('knowloop:drafts:u1')).toBeNull()
  expect(sessionStorage.getItem('unrelated')).toBe('keep')
})

it('does not share the home draft with demo chat', async () => {
  vi.mocked(api.materials).mockResolvedValue({ items: [], next_cursor: null })
  await render(<Composer />)
  await enterText('私人的首页草稿')
  await act(async () => root.render(null))
  await render(<Composer demo />)
  expect(node.querySelector('textarea')!.value).toBe('')
  await enterText('示例消息')
  const stored = JSON.parse(sessionStorage.getItem('knowloop:drafts:u1')!)
  expect(stored.home.text).toBe('私人的首页草稿')
})

it('retains the draft in memory if sessionStorage cannot be written', async () => {
  vi.mocked(api.materials).mockResolvedValue({ items: [], next_cursor: null })
  const blocked = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage disabled') })
  try {
    await render(<Composer conversationId="blocked-storage" />)
    await enterText('本次会话草稿')
    await act(async () => root.render(null))
    await render(<Composer conversationId="blocked-storage" />)
    expect(node.querySelector('textarea')!.value).toBe('本次会话草稿')
  } finally {
    blocked.mockRestore()
  }
})

it('requires explicit scope recovery for historical questions and blocks invalid materials', async () => {
  vi.mocked(api.materials).mockResolvedValue({ items: [readyMaterial('m1', '第一章.md')], next_cursor: null })
  const composer = { current: null as ComposerHandle | null }
  await render(<Composer ref={(value) => { composer.current = value }} />)
  await act(async () => composer.current!.editAsNew('无范围历史问题', null))
  await act(async () => node.querySelector<HTMLFormElement>('form.composer')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(workspace.value.chat.send).not.toHaveBeenCalled()
  expect(workspace.value.notify).toHaveBeenCalledWith('请先显式选择知识库，再决定是否限定资料范围。')

  const invalidMaterialScope: Scope = {
    knowledge_base_id: 'A',
    knowledge_base_name: '库A',
    answer_mode: 'with_general',
    intent: 'qa',
    selected_material_ids: ['removed-material'],
    index_version_ids: [],
  }
  await act(async () => composer.current!.editAsNew('恢复原始范围的问题', invalidMaterialScope))
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
  await act(async () => node.querySelector<HTMLFormElement>('form.composer')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(workspace.value.chat.send).not.toHaveBeenCalled()
  expect(workspace.value.notify).toHaveBeenCalledWith('所选资料范围尚未确认，请检查资料状态或选择使用全库。')
})

it('does not widen a historical question when its original library is unavailable', async () => {
  const composer = { current: null as ComposerHandle | null }
  await render(<Composer ref={(value) => { composer.current = value }} />)
  await act(async () => composer.current!.editAsNew('原库已删除的问题', {
    knowledge_base_id: 'deleted-library',
    knowledge_base_name: '已删除库',
    answer_mode: 'knowledge_only',
    intent: 'qa',
    selected_material_ids: null,
    index_version_ids: [],
  }))
  await act(async () => node.querySelector<HTMLFormElement>('form.composer')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(workspace.value.chat.send).not.toHaveBeenCalled()
  expect(workspace.value.notify).toHaveBeenCalledWith('原知识库不可用，请重新选择知识库。')
})

it('limits a material selection to 50', { timeout: 15_000 }, async () => {
  vi.mocked(api.materials).mockResolvedValue({ items: Array.from({ length: 51 }, (_, i) => readyMaterial(`m${i}`, `资料${i}`)), next_cursor: null })
  await render(<Composer />)
  await act(async () => node.querySelector<HTMLButtonElement>('.current-materials button[aria-expanded]')!.click())
  const boxes = [...node.querySelectorAll<HTMLInputElement>('input[type=checkbox]')]
  for (const box of boxes.slice(0, 50)) act(() => box.click())
  expect(boxes[50].disabled).toBe(true)
  expect(node.textContent).toContain('最多选择 50 份资料')
})

describe('inline [S1] citations & KaTeX math rendering in MarkdownContent', () => {
  const sampleCitation: Citation = {
    id: 'cit-1',
    source_label: 'S1',
    material_id: 'm1',
    material_version_id: 'v1',
    knowledge_base_id: 'A',
    title: '高等数学讲义.md',
    quote: '欧拉公式将复指数函数与三角函数联系起来。',
    locator: { heading: '欧拉公式', start_line: 12 },
    location_label: '欧拉公式 · 第 12 行',
    version_no: 1,
    availability: 'available',
  }

  it('renders [S1] as an interactive button in prose while keeping code and math untouched', async () => {
    const onSelect = vi.fn()
    const markdown = [
      '由欧拉公式 $e^{i\\pi} + 1 = 0$ 可知该结论成立 [S1]，而未匹配编号保留静态角标 [S9]。',
      '',
      '行内代码 `arr[S1]` 与代码块不应被替换：',
      '```python',
      'value = items[S1]',
      '```',
      '',
      '$$\\sum_{k=1}^{n} a_{[S1]} = 1$$',
    ].join('\n')

    await render(
      <MarkdownContent citations={[sampleCitation]} onSelectCitation={onSelect}>
        {markdown}
      </MarkdownContent>,
    )
    await act(async () => { await vi.dynamicImportSettled() })

    const buttons = node.querySelectorAll<HTMLButtonElement>('button.inline-citation')
    expect(buttons).toHaveLength(1)
    expect(buttons[0].textContent).toBe('S1')
    await act(async () => buttons[0].click())
    expect(onSelect).toHaveBeenCalledWith(sampleCitation)

    expect(node.querySelector('sup.inline-citation-static')?.textContent).toBe('S9')
    const inlineCode = [...node.querySelectorAll('code')].find((el) => el.textContent === 'arr[S1]')
    expect(inlineCode).toBeTruthy()
    expect(node.querySelector('pre')?.textContent).toContain('items[S1]')
    expect(node.querySelector('.katex')).toBeTruthy()
  })
})
