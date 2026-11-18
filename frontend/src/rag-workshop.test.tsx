// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api/client'
import type { Fragment, HitTestResponse } from './api/types'
import { LibraryFragments } from './components/LibraryFragments'
import { LibraryHitTest } from './components/LibraryHitTest'
import { ThemeProvider } from './Theme'

const workspace = vi.hoisted(() => ({ value: {} as Record<string, any> }))

vi.mock('./WorkspaceData', () => ({ useWorkspace: () => workspace.value }))
vi.mock('./components/arc/select/select', () => ({
  Select: ({ label, options, value, onValueChange }: any) => (
    <select aria-label={label} value={value} onChange={(e) => onValueChange(e.target.value)}>
      {options.map((opt: any) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
    </select>
  ),
}))
vi.mock('./api/client', () => ({
  api: {
    fragments: vi.fn(),
    setFragmentRetrieval: vi.fn(),
    workshop: {
      hitTest: vi.fn(),
      updateFragment: vi.fn(),
    },
  },
}))

let root: Root
let node: HTMLDivElement

const sampleFragment: Fragment = {
  id: 'chunk-1',
  text: 'OCR误识别旧文本待修正',
  material_id: 'mat-1',
  title: '凸优化讲义.md',
  mime_type: 'text/markdown',
  version_id: 'ver-1',
  locator: { page: 2 },
  estimated_tokens: 12,
  retrieval_enabled: true,
  status: 'succeeded',
  ready_at: '2026-11-18T02:00:00Z',
}

const sampleHitResponse: HitTestResponse = {
  query: '拉格朗日乘子',
  query_terms: ['拉格朗日', '乘子'],
  lexical_weight: 1,
  min_similarity_threshold: 0.35,
  items: [
    {
      chunk_id: 'chunk-1',
      material_id: 'mat-1',
      version_id: 'ver-1',
      title: '凸优化讲义.md',
      locator: { page: 2 },
      location_label: '第 2 页',
      text: '拉格朗日乘子法用于带约束极值求解。',
      retrieval_enabled: true,
      vector_rank: 2,
      vector_similarity: 0.452,
      above_threshold: true,
      lexical_rank: 1,
      lexical_score: 1.82,
      matched_terms: ['拉格朗日', '乘子'],
      rrf_score: 0.0325,
    },
    {
      chunk_id: 'chunk-2',
      material_id: 'mat-1',
      version_id: 'ver-1',
      title: '凸优化讲义.md',
      locator: { page: 5 },
      location_label: '第 5 页',
      text: '噪声干扰段落，相似度较低。',
      retrieval_enabled: true,
      vector_rank: 3,
      vector_similarity: 0.18,
      above_threshold: false,
      lexical_rank: null,
      lexical_score: null,
      matched_terms: [],
      rrf_score: 0,
    },
  ],
}

async function render(view: ReactNode) {
  await act(async () => root.render(<ThemeProvider>{view}</ThemeProvider>))
}

function setInputValue(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')?.set
  setter?.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
}

function findBtn(text: string) {
  return [...document.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.includes(text))
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }))
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
  workspace.value = {
    notify: vi.fn(),
    report: vi.fn(),
    refresh: vi.fn(),
  }
})

afterEach(async () => {
  await act(async () => root.unmount())
  node.remove()
  vi.unstubAllGlobals()
})

describe('RAG Workshop: Hybrid Hit-Testing & Fragment Editor', () => {
  it('runs hybrid hit-testing, displays vector/lexical/RRF diagnostics, and patches a recalled chunk', async () => {
    vi.mocked(api.workshop.hitTest).mockResolvedValue(sampleHitResponse)
    vi.mocked(api.workshop.updateFragment).mockResolvedValue({
      ...sampleFragment,
      text: '拉格朗日乘子法与KKT条件核心推导。',
    })
    const onOpenSource = vi.fn()
    const onUpdated = vi.fn()

    await render(
      <LibraryHitTest
        kbId="kb-1"
        readyCount={2}
        onOpenSource={onOpenSource}
        onFragmentUpdated={onUpdated}
      />,
    )

    const input = node.querySelector<HTMLInputElement>('input[placeholder*="输入测试问题"]')!
    await act(async () => {
      setInputValue(input, '拉格朗日乘子')
    })

    const weightSelect = node.querySelector<HTMLSelectElement>('select[aria-label="混合词法权重"]')!
    await act(async () => {
      weightSelect.value = '1'
      weightSelect.dispatchEvent(new Event('change', { bubbles: true }))
    })

    await act(async () => {
      findBtn('开始命中测试')!.click()
    })

    expect(api.workshop.hitTest).toHaveBeenCalledWith('kb-1', {
      query: '拉格朗日乘子',
      top_k: 6,
      lexical_weight: 1,
    })
    expect(node.textContent).toContain('RRF 0.0325')
    expect(node.textContent).toContain('向量 0.452 (#2)')
    expect(node.textContent).toContain('词法 #1 (IDF 1.82)')
    expect(node.textContent).toContain('低于阈值')

    await act(async () => {
      findBtn('修补片段')!.click()
    })
    const textarea = document.querySelector<HTMLTextAreaElement>('textarea[name="text"]')!
    expect(textarea.value).toBe('拉格朗日乘子法用于带约束极值求解。')
    textarea.value = '拉格朗日乘子法与KKT条件核心推导。'

    await act(async () => {
      textarea.closest('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })

    expect(api.workshop.updateFragment).toHaveBeenCalledWith(
      'kb-1',
      'chunk-1',
      '拉格朗日乘子法与KKT条件核心推导。',
    )
    expect(onUpdated).toHaveBeenCalledOnce()
    expect(node.textContent).toContain('拉格朗日乘子法与KKT条件核心推导。')
  })

  it('edits fragment text directly from LibraryFragments row menu and updates table in-place', async () => {
    vi.mocked(api.fragments).mockResolvedValue({
      total: 1,
      page_size: 20,
      items: [sampleFragment],
    })
    vi.mocked(api.workshop.updateFragment).mockResolvedValue({
      ...sampleFragment,
      text: '贝尔曼最优方程修正文本',
      estimated_tokens: 18,
    })

    await render(
      <LibraryFragments
        id="kb-1"
        tags={[]}
        day={undefined}
        setDay={vi.fn()}
        scope={{}}
        clearScope={vi.fn()}
        revision={0}
        onPreview={vi.fn()}
        onOpenSource={vi.fn()}
      />,
    )

    expect(node.textContent).toContain('OCR误识别旧文本待修正')

    const menuTrigger = document.querySelector<HTMLButtonElement>('button[aria-label="片段操作"]')!
    await act(async () => {
      menuTrigger.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }))
      menuTrigger.click()
    })

    const editMenuItem = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((el) =>
      el.textContent?.includes('编辑片段'),
    )!
    await act(async () => {
      editMenuItem.click()
    })

    const textarea = document.querySelector<HTMLTextAreaElement>('textarea[name="text"]')!
    textarea.value = '贝尔曼最优方程修正文本'
    await act(async () => {
      textarea.closest('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })

    expect(api.workshop.updateFragment).toHaveBeenCalledWith('kb-1', 'chunk-1', '贝尔曼最优方程修正文本')
    expect(workspace.value.notify).toHaveBeenCalledWith('片段已更新并重新生成向量索引。')
    expect(node.textContent).toContain('贝尔曼最优方程修正文本')
  })
})
