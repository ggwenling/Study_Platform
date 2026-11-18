// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api/client'
import type { Citation, Material } from './api/types'
import { Composer } from './components/Composer'
import { MarkdownContent } from './components/MarkdownContent'
import { ThemeProvider } from './Theme'

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

beforeEach(() => {
  vi.resetAllMocks()
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
    chat: { busy: false, send: vi.fn().mockResolvedValue(true) },
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
    })

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
    })

    await act(async () => materialsTrigger.click())
    const clear = [...node.querySelectorAll<HTMLButtonElement>('.current-materials button')].find((button) => button.textContent === '清除限定')!
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
    // Wait for lazy rehype-katex + rehype-highlight plugins to resolve
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60))
    })

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
