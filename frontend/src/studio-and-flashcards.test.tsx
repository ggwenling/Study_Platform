// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api/client'
import type { DueFlashcard, FlashcardDeckDetail, Material, StudyNote } from './api/types'
import { FlashcardPanel } from './components/FlashcardPanel'
import { LibraryStudio } from './components/LibraryStudio'
import { ThemeProvider } from './Theme'

const navigateMock = vi.hoisted(() => vi.fn())
const workspace = vi.hoisted(() => ({ value: {} as Record<string, any> }))

vi.mock('react-router', () => ({
  useNavigate: () => navigateMock,
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))
vi.mock('./WorkspaceData', () => ({ useWorkspace: () => workspace.value }))
vi.mock('./components/MarkdownContent', () => ({
  MarkdownContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))
vi.mock('./components/arc/select/select', () => ({
  Select: ({ label, options, value, onValueChange }: any) => (
    <select aria-label={label} value={value} onChange={(e) => onValueChange(e.target.value)}>
      {options.map((opt: any) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
    </select>
  ),
}))
vi.mock('./api/client', () => ({
  api: {
    studio: {
      notes: vi.fn(),
      createNote: vi.fn(),
      generateNote: vi.fn(),
      updateNote: vi.fn(),
      deleteNote: vi.fn(),
      ingestNote: vi.fn(),
      flashcardDecks: vi.fn(),
      generateFlashcards: vi.fn(),
      flashcardDeck: vi.fn(),
      dueFlashcards: vi.fn(),
      deleteFlashcardDeck: vi.fn(),
      reviewFlashcard: vi.fn(),
    },
    materials: vi.fn(),
  },
}))

let root: Root
let node: HTMLDivElement

const sampleGuide: StudyNote = {
  id: 'note-1',
  knowledge_base_id: 'kb-1',
  knowledge_base_name: '高数资料库',
  title: '高数资料库 · 学习指南与核心提纲',
  note_type: 'exam_guide',
  artifact_kind: 'study_guide',
  content_md: '## 核心主题概览\n矩阵特征值定义 [S1]。',
  edit_revision: 0,
  status: 'ready',
  ingested_material_id: null,
  citations: [],
  created_at: '2026-11-18T02:00:00Z',
  updated_at: '2026-11-18T02:00:00Z',
}

const readyMaterials: Material[] = [
  { id: 'material-1', knowledge_base_id: 'kb-1', title: '线性代数讲义', mime_type: 'text/markdown', byte_size: 100, status: 'ready', version_id: 'v1', job_id: null, created_at: '', error_code: null, error_message: null },
  { id: 'material-2', knowledge_base_id: 'kb-1', title: '矩阵分析笔记', mime_type: 'text/markdown', byte_size: 100, status: 'ready', version_id: 'v2', job_id: null, created_at: '', error_code: null, error_message: null },
]
const secondLibraryMaterials: Material[] = [
  { id: 'material-3', knowledge_base_id: 'kb-2', title: '概率论教材', mime_type: 'application/pdf', byte_size: 200, status: 'ready', version_id: 'v3', job_id: null, created_at: '', error_code: null, error_message: null },
]

const sampleDeck: FlashcardDeckDetail = {
  id: 'deck-1',
  knowledge_base_id: 'kb-1',
  knowledge_base_name: '高数资料库',
  title: '高数资料库 · 记忆闪卡（2 张）',
  card_count: 2,
  due_count: 2,
  created_at: '2026-11-18T02:00:00Z',
  cards: [
    {
      id: 'card-1',
      deck_id: 'deck-1',
      front: '什么是正交矩阵？',
      back: '满足 Q^T Q = I 的实方阵。',
      ease_factor: 2.5,
      interval_days: 0,
      repetition_count: 0,
      due_at: '2026-11-18T02:00:00Z',
      is_due: true,
      sources: [{ title: '线代讲义.md', availability: 'available' }],
    },
    {
      id: 'card-2',
      deck_id: 'deck-1',
      front: '正交矩阵的行列式取值？',
      back: '+1 或 -1。',
      ease_factor: 2.5,
      interval_days: 0,
      repetition_count: 0,
      due_at: '2026-11-18T02:00:00Z',
      is_due: true,
      sources: [],
    },
  ],
}

const dueCard1: DueFlashcard = {
  ...sampleDeck.cards[0],
  deck_title: sampleDeck.title,
  knowledge_base_id: 'kb-1',
  knowledge_base_name: '高数资料库',
}
const dueCard2: DueFlashcard = {
  ...sampleDeck.cards[1],
  deck_title: sampleDeck.title,
  knowledge_base_id: 'kb-1',
  knowledge_base_name: '高数资料库',
}

async function render(view: ReactNode) {
  await act(async () => root.render(<ThemeProvider>{view}</ThemeProvider>))
}

async function changeInput(input: HTMLInputElement | HTMLSelectElement, value: string) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')?.set
    setter?.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await Promise.resolve()
  })
}

function findBtn(text: string) {
  return [...document.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.includes(text))
}

async function pressKey(key: string, code: string) {
  const event = new KeyboardEvent('keydown', { key, code, bubbles: true, cancelable: true })
  await act(async () => window.dispatchEvent(event))
  return event.defaultPrevented
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }))
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
  workspace.value = {
    notify: vi.fn(),
    report: vi.fn(),
    refresh: vi.fn(),
  }
  vi.mocked(api.materials).mockResolvedValue({ items: readyMaterials, next_cursor: null })
})

afterEach(async () => {
  await act(async () => root.unmount())
  node.remove()
  vi.unstubAllGlobals()
})

describe('Studio notes & Flashcard SM-2 review', () => {
  it('opens the source attached to a mindmap node', async () => {
    const citation = {
      id: 'note-cite', source_label: 'S1', material_id: 'material-1', material_version_id: 'v1',
      knowledge_base_id: 'kb-1', title: '线性代数讲义', quote: '矩阵特征值定义', locator: { line: 1 },
      location_label: '第 1 行', version_no: 1, availability: 'available',
    } as const
    const note: StudyNote = {
      ...sampleGuide, artifact_kind: 'mindmap', title: '矩阵导图',
      content_md: '## 知识层级树\n\n- 矩阵 [S1]\n  - 特征值\n\n## 脉络关系说明\n\n特征值解释谱结构。',
      citations: [citation],
    }
    const openSource = vi.fn()
    await render(<LibraryStudio kbId="kb-1" kbName="高数资料库" readyCount={2} readyMaterials={readyMaterials}
      revision={0} initialNotes={[note]} onIngested={vi.fn()} onOpenSource={openSource} />)
    await act(async () => node.querySelector<HTMLButtonElement>('.studio-note-card')!.click())
    expect(document.querySelector('[aria-label="可拖动画布中的知识导图"]')).toBeTruthy()
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="查看引用 S1：线性代数讲义"]')!.click())
    expect(openSource).toHaveBeenCalledWith(citation)
    await act(async () => findBtn('全部折叠')!.click())
    expect(document.querySelector('[data-map-node-id]')?.parentElement?.textContent).not.toContain('特征值')
    await act(async () => findBtn('Markdown 原文')!.click())
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain('特征值解释谱结构')
  })

  it('keeps unsaved note text until the user confirms discarding it', async () => {
    await render(<LibraryStudio kbId="kb-1" kbName="高数资料库" readyCount={2} readyMaterials={readyMaterials}
      revision={0} initialNotes={[]} onIngested={vi.fn()} />)
    await act(async () => findBtn('新建笔记')!.click())
    const field = document.querySelector<HTMLInputElement>('[role="dialog"] input[name="title"]')!
    await changeInput(field, '未保存的标题')
    const close = document.querySelector<HTMLButtonElement>('[aria-label="关闭新建笔记"]')!
    await act(async () => close.click())
    expect(document.body.textContent).toContain('放弃未保存的更改？')
    await act(async () => findBtn('继续编辑')!.click())
    expect(field.value).toBe('未保存的标题')
    await act(async () => close.click())
    await act(async () => findBtn('放弃更改')!.click())
    expect(api.studio.createNote).not.toHaveBeenCalled()
  })

  it('keeps a saved guide when a notes list requested before generation arrives late', async () => {
    let finishStaleRead!: (result: { items: StudyNote[] }) => void
    let finishFreshRead!: (result: { items: StudyNote[] }) => void
    vi.mocked(api.studio.notes)
      .mockImplementationOnce(() => new Promise((resolve) => { finishStaleRead = resolve }))
      .mockImplementationOnce(() => new Promise((resolve) => { finishFreshRead = resolve }))
    vi.mocked(api.studio.generateNote).mockResolvedValue(sampleGuide)
    await render(<LibraryStudio kbId="kb-1" kbName="高数资料库" readyCount={2} readyMaterials={readyMaterials} revision={0} onIngested={vi.fn()} />)

    await act(async () => findBtn('生成学习指南')!.click())
    await act(async () => findBtn('确认生成')!.click())
    expect(vi.mocked(api.studio.notes).mock.calls[0][1]?.aborted).toBe(true)
    expect(node.querySelector('.studio-note-card')?.textContent).toContain(sampleGuide.title)
    await act(async () => finishStaleRead({ items: [] }))
    expect(node.querySelector('.studio-note-card')?.textContent).toContain(sampleGuide.title)
    expect(api.studio.notes).toHaveBeenCalledTimes(2)

    const otherNote = { ...sampleGuide, id: 'note-2', title: '已有笔记' }
    await act(async () => finishFreshRead({ items: [sampleGuide, otherNote] }))
    expect([...node.querySelectorAll('.studio-note-card strong')].map((card) => card.textContent))
      .toEqual([sampleGuide.title, otherNote.title])
  })

  it('generates scoped studio notes with custom titles for each artifact kind', async () => {
    vi.mocked(api.studio.notes).mockResolvedValue({ items: [] })
    vi.mocked(api.studio.generateNote).mockImplementation(async (_kbId, body) => ({
      ...sampleGuide,
      id: body.kind,
      title: body.title ?? sampleGuide.title,
      artifact_kind: body.kind,
    }))
    await render(
      <LibraryStudio kbId="kb-1" kbName="高数资料库" readyCount={2} readyMaterials={readyMaterials} revision={0} onIngested={vi.fn()} />,
    )

    for (const [button, kind, title] of [
      ['生成学习指南', 'study_guide', '我的学习提纲'],
      ['生成知识导图', 'mindmap', '矩阵知识导图'],
      ['概念公式速查', 'formula_sheet', '矩阵公式速查'],
    ] as const) {
      await act(async () => findBtn(button)!.click())
      const scope = document.querySelector<HTMLInputElement>('input[name="studio-material-scope"][value="selected"]')!
      await act(async () => scope.click())
      await act(async () => document.querySelectorAll<HTMLInputElement>('.studio-generation-materials input[type="checkbox"]').forEach((box) => box.click()))
      const titleInput = document.querySelector<HTMLInputElement>('input[name="title"]')!
      await changeInput(titleInput, title)
      await act(async () => findBtn('确认生成')!.click())

      expect(api.studio.generateNote).toHaveBeenLastCalledWith('kb-1', {
        kind,
        material_ids: ['material-1', 'material-2'],
        title,
      })
      expect(document.querySelector('[role="dialog"]')?.textContent).toContain(title)
      const closeButton = document.querySelector<HTMLButtonElement>('[aria-label="关闭笔记详情"]')
      if (closeButton) await act(async () => closeButton.click())
    }
  })

  it('generates studio flashcards with selected materials, title, and chosen count', async () => {
    vi.mocked(api.studio.notes).mockResolvedValue({ items: [] })
    vi.mocked(api.studio.generateFlashcards).mockResolvedValue(sampleDeck)
    await render(
      <LibraryStudio kbId="kb-1" kbName="高数资料库" readyCount={2} readyMaterials={readyMaterials} revision={0} onIngested={vi.fn()} />,
    )
    await act(async () => findBtn('生成记忆闪卡')!.click())
    await act(async () => document.querySelector<HTMLInputElement>('input[name="studio-material-scope"][value="selected"]')!.click())
    await act(async () => document.querySelectorAll<HTMLInputElement>('.studio-generation-materials input[type="checkbox"]').forEach((box) => box.click()))
    await changeInput(document.querySelector<HTMLInputElement>('input[name="title"]')!, '高数闪卡')
    await changeInput(document.querySelector<HTMLSelectElement>('select[aria-label="闪卡数量"]')!, '12')
    await act(async () => findBtn('确认生成')!.click())

    expect(api.studio.generateFlashcards).toHaveBeenCalledWith('kb-1', {
      count: 12,
      material_ids: ['material-1', 'material-2'],
      title: '高数闪卡',
    })
    expect(navigateMock).toHaveBeenCalledWith('/learning', { state: { tab: 'flashcards', deckId: sampleDeck.id, kbId: 'kb-1' } })
  })

  it('edits and ingests a generated studio note', async () => {
    vi.mocked(api.studio.notes).mockResolvedValue({ items: [] })
    vi.mocked(api.studio.generateNote).mockResolvedValue(sampleGuide)
    vi.mocked(api.studio.updateNote).mockResolvedValue({ ...sampleGuide, title: '修订版提纲', edit_revision: 1 })
    vi.mocked(api.studio.ingestNote).mockResolvedValue({
      material_id: 'mat-from-note',
      version_id: 'v1',
      job_id: 'j1',
      status: 'queued',
    })
    const onIngested = vi.fn()

    await render(
      <LibraryStudio kbId="kb-1" kbName="高数资料库" readyCount={2} readyMaterials={readyMaterials} revision={0} onIngested={onIngested} />,
    )
    await act(async () => findBtn('生成学习指南')!.click())
    await act(async () => findBtn('确认生成')!.click())
    expect(api.studio.generateNote).toHaveBeenCalledWith('kb-1', { kind: 'study_guide', material_ids: null, title: null })
    expect(document.body.textContent).toContain('矩阵特征值定义')

    await act(async () => findBtn('回写为知识库资料')!.click())
    expect(api.studio.ingestNote).toHaveBeenCalledWith('note-1')
    expect(onIngested).toHaveBeenCalledOnce()

    await act(async () => findBtn('编辑')!.click())
    const titleInput = document.querySelector<HTMLInputElement>('input[name=title]')!
    await changeInput(titleInput, '修订版提纲')
    await act(async () => {
      titleInput.closest('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    })
    expect(api.studio.updateNote).toHaveBeenCalledWith('note-1', expect.objectContaining({ title: '修订版提纲' }))
  })

  it('generates flashcards, flips to reveal answer, and submits SM-2 rating to advance', async () => {
    vi.mocked(api.studio.flashcardDecks).mockResolvedValue({ items: [] })
    vi.mocked(api.studio.generateFlashcards).mockResolvedValue(sampleDeck)
    vi.mocked(api.studio.reviewFlashcard).mockResolvedValue({
      ...sampleDeck.cards[0],
      interval_days: 2,
      repetition_count: 1,
      ease_factor: 2.65,
      is_due: false,
    })

    await render(
      <FlashcardPanel
        knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]}
        defaultKbId="kb-1"
        notify={vi.fn()}
      />,
    )
    await act(async () => findBtn('从当前库生成闪卡')!.click())
    expect(api.studio.generateFlashcards).toHaveBeenCalledWith('kb-1', { count: 8, material_ids: null, title: null })
    expect(node.textContent).toContain('什么是正交矩阵？')

    await act(async () => findBtn('翻转查看答案')!.click())
    expect(node.textContent).toContain('满足 Q^T Q = I 的实方阵')

    await act(async () => findBtn('熟练掌握')!.click())
    expect(api.studio.reviewFlashcard).toHaveBeenCalledWith('card-1', 3, expect.any(String))
    expect(node.textContent).toContain('正交矩阵的行列式取值？')
  })
  it('retries an uncertain review with the same idempotency key and rating', async () => {
    vi.mocked(api.studio.flashcardDecks).mockResolvedValue({ items: [] })
    vi.mocked(api.studio.generateFlashcards).mockResolvedValue(sampleDeck)
    vi.mocked(api.studio.reviewFlashcard).mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce(sampleDeck.cards[0]).mockResolvedValueOnce(sampleDeck.cards[1])
    await render(<FlashcardPanel knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]} defaultKbId="kb-1" notify={vi.fn()} />)
    await act(async () => findBtn('从当前库生成闪卡')!.click())
    await act(async () => findBtn('翻转查看答案')!.click())
    await act(async () => findBtn('熟练掌握')!.click())
    expect(node.textContent).toContain('评分结果尚未确认')
    expect(findBtn('困难')!.disabled).toBe(true)
    await act(async () => findBtn('重试上次评分')!.click())
    expect(api.studio.reviewFlashcard).toHaveBeenCalledTimes(2)
    expect(vi.mocked(api.studio.reviewFlashcard).mock.calls[1]).toEqual(vi.mocked(api.studio.reviewFlashcard).mock.calls[0])
    expect(node.textContent).toContain('正交矩阵的行列式取值？')
    await act(async () => findBtn('翻转查看答案')!.click())
    await act(async () => findBtn('熟练掌握')!.click())
    const [, , firstKey] = vi.mocked(api.studio.reviewFlashcard).mock.calls[0]
    expect(vi.mocked(api.studio.reviewFlashcard).mock.calls[2]).toEqual(['card-2', 3, expect.any(String)])
    expect(vi.mocked(api.studio.reviewFlashcard).mock.calls[2][2]).not.toBe(firstKey)
  })

  it('generates flashcards from selected ready materials and resets selection when the library changes', async () => {
    vi.mocked(api.studio.flashcardDecks).mockResolvedValue({ items: [] })
    vi.mocked(api.studio.generateFlashcards).mockResolvedValue(sampleDeck)
    vi.mocked(api.materials).mockImplementation(async (kbId) => ({
      items: kbId === 'kb-2' ? secondLibraryMaterials : readyMaterials,
      next_cursor: null,
    }))
    await render(
      <FlashcardPanel
        knowledgeBases={[
          { id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' },
          { id: 'kb-2', name: '概率资料库', description: null, created_at: '', updated_at: '' },
        ]}
        defaultKbId="kb-1"
        notify={vi.fn()}
      />,
    )
    await act(async () => findBtn('出卡资料范围 · 默认全库（共 2 份就绪资料）')!.click())
    await act(async () => document.querySelector<HTMLInputElement>('[aria-label="限定资料：线性代数讲义"]')!.click())
    await act(async () => document.querySelector<HTMLInputElement>('[aria-label="限定资料：矩阵分析笔记"]')!.click())
    expect(node.textContent).toContain('出卡资料范围 · 已勾选 2 份')

    await changeInput(document.querySelector<HTMLSelectElement>('select[aria-label="选择知识库"]')!, 'kb-2')
    expect(node.textContent).toContain('出卡资料范围 · 默认全库（共 1 份就绪资料）')
    expect(document.querySelector<HTMLInputElement>('[aria-label="限定资料：概率论教材"]')?.checked).toBe(false)

    await changeInput(document.querySelector<HTMLSelectElement>('select[aria-label="选择知识库"]')!, 'kb-1')
    await act(async () => document.querySelector<HTMLInputElement>('[aria-label="限定资料：线性代数讲义"]')!.click())
    await act(async () => document.querySelector<HTMLInputElement>('[aria-label="限定资料：矩阵分析笔记"]')!.click())
    await changeInput(document.querySelector<HTMLInputElement>('input[maxlength="255"]')!, '我的线代卡组')
    await act(async () => findBtn('从当前库生成闪卡')!.click())

    expect(api.studio.generateFlashcards).toHaveBeenCalledWith('kb-1', {
      count: 8,
      material_ids: ['material-1', 'material-2'],
      title: '我的线代卡组',
    })
  })

  it('starts the cross-deck due queue, guards keyboard shortcuts, and reviews weak cards again', async () => {
    const onDueQueueStarted = vi.fn()
    const onOverviewRefresh = vi.fn()
    vi.mocked(api.studio.flashcardDecks).mockResolvedValue({ items: [] })
    vi.mocked(api.studio.dueFlashcards).mockResolvedValue({ total_due: 1, items: [dueCard1] })
    vi.mocked(api.studio.reviewFlashcard).mockResolvedValue({ ...dueCard1, is_due: false })
    const panel = (active: boolean) => (
      <FlashcardPanel
        active={active}
        knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]}
        defaultKbId="kb-1"
        notify={vi.fn()}
        startDueQueue
        onDueQueueStarted={onDueQueueStarted}
        onOverviewRefresh={onOverviewRefresh}
      />
    )
    await render(panel(true))

    expect(api.studio.dueFlashcards).toHaveBeenCalledWith(undefined, 100)
    expect(onDueQueueStarted).toHaveBeenCalledOnce()
    expect(node.textContent).toContain('高数资料库 · 高数资料库 · 记忆闪卡（2 张）')

    const titleInput = document.querySelector<HTMLInputElement>('input[maxlength="255"]')!
    titleInput.focus()
    await pressKey(' ', 'Space')
    expect(node.textContent).toContain('什么是正交矩阵？')
    expect(node.textContent).not.toContain('满足 Q^T Q = I 的实方阵')

    titleInput.blur()
    await pressKey(' ', 'Space')
    expect(node.textContent).toContain('满足 Q^T Q = I 的实方阵')
    await render(panel(false))
    expect(await pressKey('1', 'Digit1')).toBe(false)
    expect(await pressKey(' ', 'Space')).toBe(false)
    expect(api.studio.reviewFlashcard).not.toHaveBeenCalled()
    await render(panel(true))
    expect(node.textContent).toContain('满足 Q^T Q = I 的实方阵')
    await act(async () => findBtn('去对话追问')!.click())
    expect(navigateMock).toHaveBeenCalledWith('/', {
      state: expect.objectContaining({
        preferredLibrary: 'kb-1',
        initialDraft: expect.stringContaining('闪卡问题：什么是正交矩阵？'),
      }),
    })
    expect(navigateMock.mock.calls.at(-1)?.[1].state.initialDraft).toContain('卡片要点：满足 Q^T Q = I 的实方阵。')

    titleInput.focus()
    await pressKey('1', 'Digit1')
    expect(api.studio.reviewFlashcard).not.toHaveBeenCalled()
    const dialog = document.createElement('div')
    dialog.setAttribute('role', 'dialog')
    document.body.append(dialog)
    titleInput.blur()
    await pressKey('1', 'Digit1')
    expect(api.studio.reviewFlashcard).not.toHaveBeenCalled()
    dialog.remove()

    titleInput.blur()
    await pressKey('1', 'Digit1')
    expect(api.studio.reviewFlashcard).toHaveBeenCalledWith('card-1', 0, expect.any(String))
    expect(node.textContent).toContain('本轮复习总结')
    expect(node.textContent).toContain('重来 1')
    expect(onOverviewRefresh).toHaveBeenCalledOnce()
    expect(await pressKey(' ', 'Space')).toBe(false)
    expect(await pressKey('4', 'Digit4')).toBe(false)
    expect(api.studio.reviewFlashcard).toHaveBeenCalledOnce()
    await act(async () => findBtn('仅重刷本轮生疏卡（1 张）')!.click())
    expect(node.textContent).toContain('重刷本轮生疏卡')
    await act(async () => findBtn('翻转查看答案')!.click())
    await act(async () => findBtn('熟练掌握')!.click())
    expect(node.textContent).toContain('熟练掌握 1')
    expect(node.textContent).toContain('重来 0')
    expect(findBtn('仅重刷本轮生疏卡')).toBeUndefined()
    expect(onOverviewRefresh).toHaveBeenCalledTimes(2)
  })

  it('continues a bounded due batch without repeating already reviewed cards', async () => {
    vi.mocked(api.studio.flashcardDecks).mockResolvedValue({ items: [] })
    vi.mocked(api.studio.dueFlashcards)
      .mockResolvedValueOnce({ total_due: 2, items: [dueCard1] })
      .mockResolvedValueOnce({ total_due: 2, items: [dueCard1, dueCard2] })
    vi.mocked(api.studio.reviewFlashcard).mockImplementation(async (id, rating) => ({
      ...(id === dueCard1.id ? dueCard1 : dueCard2),
      is_due: rating === 0,
    }))
    await render(
      <FlashcardPanel
        knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]}
        defaultKbId="kb-1"
        notify={vi.fn()}
        startDueQueue
      />,
    )

    await act(async () => findBtn('翻转查看答案')!.click())
    await act(async () => findBtn('重来（10分钟后）')!.click())
    expect(findBtn('继续复习更多到期闪卡')).toBeDefined()
    await act(async () => findBtn('继续复习更多到期闪卡')!.click())
    expect(api.studio.dueFlashcards).toHaveBeenCalledTimes(2)
    expect(node.textContent).toContain('正交矩阵的行列式取值？')

    await act(async () => findBtn('翻转查看答案')!.click())
    await act(async () => findBtn('良好')!.click())
    expect(node.textContent).toContain('重来 1')
    expect(node.textContent).toContain('良好 1')
    expect(findBtn('继续复习更多到期闪卡')).toBeUndefined()
  })

  it('defaults a deck to due cards and offers free review when none are due', async () => {
    vi.mocked(api.studio.flashcardDecks).mockResolvedValue({
      items: [{ id: 'deck-1', knowledge_base_id: 'kb-1', knowledge_base_name: '高数资料库', title: sampleDeck.title, card_count: 2, due_count: 1, created_at: '' }],
    })
    vi.mocked(api.studio.flashcardDeck).mockResolvedValue({
      ...sampleDeck,
      due_count: 1,
      cards: [sampleDeck.cards[0], { ...sampleDeck.cards[1], is_due: false }],
    })
    await render(
      <FlashcardPanel
        knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]}
        defaultKbId="kb-1"
        notify={vi.fn()}
      />,
    )
    await act(async () => findBtn('开始翻转复习')!.click())
    expect(node.textContent).toContain('第 1 / 1 张')
    expect(node.textContent).not.toContain('正交矩阵的行列式取值？')
    await act(async () => findBtn('重温全部（2）')!.click())
    expect(node.textContent).toContain('第 1 / 2 张')
    await act(async () => findBtn('下一张')!.click())
    expect(node.textContent).toContain('正交矩阵的行列式取值？')

    vi.mocked(api.studio.flashcardDeck).mockResolvedValue({
      ...sampleDeck,
      due_count: 0,
      cards: sampleDeck.cards.map((card) => ({ ...card, is_due: false })),
    })
    await render(
      <FlashcardPanel
        knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]}
        defaultKbId="kb-1"
        notify={vi.fn()}
        initialDeckId="deck-1"
      />,
    )
    await act(async () => Promise.resolve())
    expect(node.textContent).toContain('本组今日到期卡片已全部复习完毕')
    await act(async () => findBtn('自由重温全部卡片')!.click())
    expect(node.textContent).toContain('什么是正交矩阵？')
  })

})
