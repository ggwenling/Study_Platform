// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api/client'
import type { Dashboard, LearningAttempt, WrongQuestionItem } from './api/types'
import { Learning } from './components/Learning'
import { ThemeProvider } from './Theme'
import { wrongIdsForAttempt } from './learningUi'

const navigateMock = vi.hoisted(() => vi.fn())
const locationMock = vi.hoisted(() => ({ value: { key: 'initial', state: null as unknown } }))
const refreshOverview = vi.fn()

vi.mock('react-router', () => ({
  useNavigate: () => navigateMock,
  useLocation: () => locationMock.value,
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))
vi.mock('./components/MarkdownContent', () => ({
  MarkdownContent: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))
vi.mock('./components/arc/select/select', () => ({
  Select: ({ label, options, value, onValueChange }: any) => (
    <select aria-label={label} value={value} onChange={(e) => onValueChange(e.target.value)}>
      {options.map((opt: any) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
    </select>
  ),
}))
vi.mock('./components/arc/accordion/accordion', () => ({
  Accordion: ({ items }: any) => <>{items.map((item: any, idx: number) => <section key={idx}>{item.title}{item.content}</section>)}</>,
}))
vi.mock('./components/FlashcardPanel', () => ({
  FlashcardPanel: ({ startDueQueue, onDueQueueStarted }: { startDueQueue?: boolean; onDueQueueStarted?: () => void }) => (
    <div data-testid="flashcard-panel" data-start-due-queue={startDueQueue ? 'true' : 'false'}>
      {startDueQueue && <button type="button" onClick={onDueQueueStarted}>完成到期队列加载</button>}
    </div>
  ),
}))
vi.mock('./api/client', () => ({
  api: {
    materials: vi.fn(),
    learning: {
      generateQuiz: vi.fn(),
      attempts: vi.fn(),
      startAttempt: vi.fn(),
      attempt: vi.fn(),
      submitAnswerText: vi.fn(),
      submitAnswerImage: vi.fn(),
      patchVerdict: vi.fn(),
      wrongQuestions: vi.fn(),
      retryWrong: vi.fn(),
      masterWrong: vi.fn(),
    },
  },
}))

let root: Root
let node: HTMLDivElement

const testOverview: Dashboard = {
  knowledge_base_count: 1,
  material_count: 2,
  conversation_count: 0,
  completed_qa_count: 0,
  due_flashcard_count: 0,
  due_wrong_question_count: 1,
  activity: [],
}

const sampleAttempt: LearningAttempt = {
  id: 'att-1',
  quiz_id: 'q-1',
  title: '高数 · 混合练习',
  knowledge_base_id: 'kb-1',
  status: 'in_progress',
  items: [
    {
      response_id: 'resp-1',
      quiz_item_id: 'item-1',
      ordinal: 0,
      stem: '下列哪一项关于特征向量正确？',
      item_type: 'mcq',
      options: ['A. 可以是零向量', 'B. 必须是非零向量', 'C. 必须是单位向量', 'D. 只能是实向量'],
      verdict: null,
      verdict_source: null,
      confidence: null,
      feedback: null,
      answer: { type: 'pending' },
      sources: [{ chunk_id: 'c1', material_id: 'm1', material_version_id: 'v1', title: '线代讲义.md', location_label: '第 2 页', availability: 'available' }],
    },
    {
      response_id: 'resp-2',
      quiz_item_id: 'item-2',
      ordinal: 1,
      stem: '简述行列式为零与矩阵可逆性的关系。',
      item_type: 'short_answer',
      options: [],
      verdict: null,
      verdict_source: null,
      confidence: null,
      feedback: null,
      answer: { type: 'pending' },
      sources: [],
    },
  ],
}

const sampleWrong: WrongQuestionItem = {
  id: 'wrong-1',
  quiz_item_id: 'item-1',
  knowledge_base_id: 'kb-1',
  knowledge_base_name: '高数资料库',
  item_type: 'mcq',
  stem: '下列哪一项关于特征向量正确？',
  options: sampleAttempt.items[0].options,
  reference_answer: 'B',
  explanation: '按定义特征向量非零。',
  last_feedback: '回答不正确，参考答案为 B。',
  last_answer: 'A. 可以是零向量',
  sources: [{ chunk_id: 'c1', material_id: 'm1', material_version_id: 'v1', title: '线代讲义.md', location_label: '第 2 页', availability: 'available', quote: '特征向量定义为非零列向量。' }],
  status: 'open',
  wrong_count: 1,
  last_wrong_at: '2026-11-18T02:00:00Z',
  next_review_at: '2026-11-18T02:00:00Z',
  is_due: true,
}

async function render(view: ReactNode) {
  await act(async () => root.render(<ThemeProvider>{view}</ThemeProvider>))
}

function findButtonContaining(text: string) {
  return [...node.querySelectorAll<HTMLButtonElement>('button')].find((b) => !b.closest('[hidden]') && b.textContent?.includes(text))
}

async function changeSelect(label: string, value: string) {
  await act(async () => {
    const select = node.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!
    select.value = value
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

beforeEach(() => {
  vi.resetAllMocks()
  locationMock.value = { key: 'initial', state: null }
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }))
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
  vi.mocked(api.materials).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(api.learning.attempts).mockResolvedValue({
    items: [
      {
        id: 'att-1',
        quiz_id: 'q-1',
        title: '高数 · 混合练习',
        knowledge_base_id: 'kb-1',
        knowledge_base_name: '高数资料库',
        status: 'in_progress',
        item_count: 2,
        answered_count: 0,
        correct_count: 0,
        started_at: '2026-11-18T01:00:00Z',
        updated_at: '2026-11-18T01:05:00Z',
      },
    ],
    next_cursor: null,
  })
  vi.mocked(api.learning.wrongQuestions).mockResolvedValue({ items: [sampleWrong] })
  vi.mocked(api.learning.attempt).mockResolvedValue(sampleAttempt)
})

afterEach(async () => {
  await act(async () => root.unmount())
  node.remove()
  vi.unstubAllGlobals()
})

describe('Learning 2.0 workflow', () => {
  it('starts the daily flashcard queue from route state and the Learning overview', async () => {
    locationMock.value = { key: 'due-cards', state: { tab: 'flashcards', startDueQueue: true } }
    const overview = { ...testOverview, due_flashcard_count: 3 }
    await render(<Learning knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]} notify={vi.fn()} overview={overview} refreshOverview={refreshOverview} />)

    expect(node.querySelector('#learning-tab-flashcards')?.getAttribute('aria-selected')).toBe('true')
    expect(node.querySelector('[data-testid="flashcard-panel"]')?.getAttribute('data-start-due-queue')).toBe('true')
    await act(async () => findButtonContaining('完成到期队列加载')!.click())
    expect(node.querySelector('[data-testid="flashcard-panel"]')?.getAttribute('data-start-due-queue')).toBe('false')
    await act(async () => findButtonContaining('开始今日到期闪卡')!.click())
    expect(node.querySelector('[data-testid="flashcard-panel"]')?.getAttribute('data-start-due-queue')).toBe('true')
  })

  it('shows the due review entry and retries only today’s due wrong questions', async () => {
    vi.mocked(api.learning.retryWrong).mockResolvedValue(sampleAttempt)
    locationMock.value = { key: 'due-wrong', state: { tab: 'wrong', wrongFilter: 'due' } }
    await render(<Learning knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]} notify={vi.fn()} overview={testOverview} refreshOverview={refreshOverview} />)
    await act(async () => { await Promise.resolve() })

    expect(node.querySelector('#learning-tab-wrong')?.getAttribute('aria-selected')).toBe('true')
    expect(node.querySelector<HTMLSelectElement>('select[aria-label="复习范围"]')?.value).toBe('due')
    expect(node.textContent).toContain('今日待重练')
    expect(node.textContent).toContain('闪卡 0')
    expect(node.textContent).toContain('错题 1')
    await act(async () => findButtonContaining('错题重练（1）')!.click())
    expect(api.learning.retryWrong).toHaveBeenCalledWith(expect.objectContaining({ due_only: true }))
    expect(vi.mocked(api.learning.retryWrong).mock.calls[0][0]?.wrong_ids).toBeUndefined()
  })

  it('labels a reviewing wrong question with its next scheduled review date', async () => {
    vi.mocked(api.learning.wrongQuestions).mockResolvedValue({ items: [{
      ...sampleWrong,
      status: 'reviewing',
      next_review_at: '2026-11-23T12:00:00Z',
      is_due: false,
    }] })
    locationMock.value = { key: 'wrong-list', state: { tab: 'wrong' } }
    await render(<Learning knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]} notify={vi.fn()} overview={testOverview} refreshOverview={refreshOverview} />)
    await act(async () => { await Promise.resolve() })
    expect(node.textContent).toContain('复习中')
    expect(node.textContent).toContain('下次复习：11-23')
  })

  it('shows the full-paper three-state recap and retries only matching wrong IDs', async () => {
    const completedAttempt: LearningAttempt = {
      ...sampleAttempt,
      items: [
        { ...sampleAttempt.items[0], verdict: 'incorrect', knowledge_base_id: 'kb-1' },
        { ...sampleAttempt.items[1], verdict: 'correct' },
        { ...sampleAttempt.items[1], response_id: 'resp-3', quiz_item_id: 'item-3', ordinal: 2, stem: '需要确认的题目', verdict: 'pending' },
      ],
    }
    vi.mocked(api.learning.attempt).mockResolvedValue(completedAttempt)
    vi.mocked(api.learning.retryWrong).mockResolvedValue({ ...sampleAttempt, id: 'att-retry' })
    await render(<Learning knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]} notify={vi.fn()} overview={testOverview} refreshOverview={refreshOverview} />)
    await act(async () => findButtonContaining('继续上次练习')!.click())

    expect(node.textContent).toContain('正确 1 题 · 错误 1 题 · 待确认 1 题')
    expect(node.textContent).not.toContain('分数')
    expect(node.textContent).not.toContain('排名')
    await act(async () => findButtonContaining('查看待确认题目')!.click())
    expect(node.querySelector('[aria-label="第 3 题"]')?.getAttribute('aria-current')).toBe('step')
    await act(async () => findButtonContaining('立即重练本卷错题（1）')!.click())
    expect(api.learning.retryWrong).toHaveBeenCalledWith({ wrong_ids: ['wrong-1'], knowledge_base_id: 'kb-1' })

    const retryClone = {
      ...completedAttempt,
      items: [{ ...completedAttempt.items[0], quiz_item_id: 'clone-item-1', origin_wrong_id: 'wrong-1' }],
    }
    expect(wrongIdsForAttempt(retryClone, [sampleWrong])).toEqual(['wrong-1'])
    const unmatchedClone = {
      ...completedAttempt,
      items: [{ ...completedAttempt.items[0], quiz_item_id: 'clone-item-without-origin', origin_wrong_id: null }],
    }
    expect(wrongIdsForAttempt(unmatchedClone, [sampleWrong])).toEqual([])
  })

  it('appends cursor history pages without replacing the first page', async () => {
    const firstPage = {
      id: 'att-1',
      quiz_id: 'q-1',
      title: '高数 · 混合练习',
      knowledge_base_id: 'kb-1',
      knowledge_base_name: '高数资料库',
      status: 'in_progress',
      item_count: 2,
      answered_count: 0,
      correct_count: 0,
      started_at: '2026-11-18T01:00:00Z',
      updated_at: '2026-11-18T01:05:00Z',
    }
    const secondPage = {
      id: 'att-2',
      quiz_id: 'q-2',
      title: '线代巩固练习',
      knowledge_base_id: 'kb-1',
      knowledge_base_name: '高数资料库',
      status: 'completed',
      item_count: 2,
      answered_count: 2,
      correct_count: 1,
      started_at: '2026-11-17T01:00:00Z',
      updated_at: '2026-11-17T01:05:00Z',
    }
    vi.mocked(api.learning.attempts)
      .mockResolvedValueOnce({ items: [firstPage], next_cursor: 'cursor-1' })
      .mockResolvedValueOnce({ items: [secondPage], next_cursor: null })
    await render(<Learning knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]} notify={vi.fn()} overview={testOverview} refreshOverview={refreshOverview} />)
    await act(async () => { await Promise.resolve() })
    await act(async () => findButtonContaining('历史练习')!.click())

    expect(node.textContent).toContain('高数 · 混合练习')
    await act(async () => findButtonContaining('加载更多历史记录')!.click())
    expect(api.learning.attempts).toHaveBeenLastCalledWith(undefined, 20, 'cursor-1')
    expect(node.textContent).toContain('线代巩固练习')
    expect(node.textContent).toContain('高数 · 混合练习')
  })

  it('resumes unfinished attempt, grades MCQ & text answers, and supports wrong-book retry & chat follow-up', async () => {
    vi.mocked(api.learning.submitAnswerText)
      .mockResolvedValueOnce({
        response_id: 'resp-1',
        verdict: 'correct',
        verdict_source: 'auto',
        confidence: 1,
        feedback: '回答正确。 按定义特征向量非零。',
        answer: { type: 'text', text: 'B. 必须是非零向量' },
        reference_answer: 'B',
        explanation: '按定义特征向量非零。',
        sources: sampleWrong.sources,
      })
      .mockResolvedValueOnce({
        response_id: 'resp-2',
        verdict: 'correct',
        verdict_source: 'auto',
        confidence: 0.95,
        feedback: '要点完整。',
        answer: { type: 'text', text: '行列式为零等价于矩阵不可逆' },
        reference_answer: '方阵可逆当且仅当行列式不为零',
        sources: [],
      })
    vi.mocked(api.learning.retryWrong).mockResolvedValue({ ...sampleAttempt, id: 'att-retry', title: '高数资料库 · 错题重练' })

    await render(
      <Learning
        knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]}
        notify={vi.fn()}
        overview={testOverview}
        refreshOverview={refreshOverview}
      />,
    )
    await act(async () => { await Promise.resolve() })

    // Resume unfinished attempt
    const resumeBtn = findButtonContaining('继续上次练习')!
    expect(resumeBtn).toBeTruthy()
    await act(async () => resumeBtn.click())
    expect(api.learning.attempt).toHaveBeenCalledWith('att-1')

    // Click MCQ option B
    const optB = findButtonContaining('B. 必须是非零向量')!
    await act(async () => optB.click())
    expect(api.learning.submitAnswerText).toHaveBeenCalledWith('resp-1', 'B. 必须是非零向量')
    expect(node.textContent).toContain('按定义特征向量非零')
    expect(node.textContent).toContain('特征向量定义为非零列向量')

    // Jump to question 2 and submit text answer
    await act(async () => findButtonContaining('下一题')!.click())
    const textarea = node.querySelector('textarea')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, '行列式为零等价于矩阵不可逆')
      textarea.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await act(async () => findButtonContaining('提交文字作答')!.click())
    expect(api.learning.submitAnswerText).toHaveBeenCalledWith('resp-2', '行列式为零等价于矩阵不可逆')

    // Switch to wrong-book tab, expand explanation, trigger chat follow-up, and trigger retry
    await act(async () => findButtonContaining('错题本')!.click())
    await act(async () => findButtonContaining('查看解析与依据')!.click())
    expect(node.textContent).toContain('上次作答：A. 可以是零向量')
    expect(node.textContent).toContain('特征向量定义为非零列向量')

    await act(async () => findButtonContaining('去对话追问')!.click())
    expect(navigateMock).toHaveBeenCalledWith('/', expect.objectContaining({
      state: expect.objectContaining({ preferredLibrary: 'kb-1', initialDraft: expect.stringContaining('下列哪一项关于特征向量正确') }),
    }))

    await act(async () => findButtonContaining('重练此题')!.click())
    expect(api.learning.retryWrong).toHaveBeenCalledWith({ wrong_ids: ['wrong-1'], knowledge_base_id: undefined })
    expect(node.textContent).toContain('错题重练')
  })

  it('keeps an empty selected scope blocked until using the whole library and caps selection at 50', async () => {
    const rows = Array.from({ length: 51 }, (_, i) => ({ id: `m${i}`, knowledge_base_id: 'kb-1', title: `资料${i}`, mime_type: 'text/plain', byte_size: 1, status: 'ready', version_id: 'v', job_id: null, created_at: '', error_code: null, error_message: null }))
    vi.mocked(api.materials).mockResolvedValue({ items: rows, next_cursor: null })
    vi.mocked(api.learning.generateQuiz).mockRejectedValue(new Error('stop after observing request'))
    const notify = vi.fn()
    await render(<Learning knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]} notify={notify} overview={testOverview} refreshOverview={refreshOverview} />)
    const boxes = [...node.querySelectorAll<HTMLInputElement>('.learning-material-picker input[type=checkbox]')]
    for (const box of boxes.slice(0, 50)) await act(async () => box.click())
    expect(boxes[50].disabled).toBe(true)
    for (const box of boxes.slice(0, 50)) await act(async () => box.click())
    await act(async () => findButtonContaining('从当前库出题')!.click())
    expect(api.learning.generateQuiz).not.toHaveBeenCalled()
    expect(notify).toHaveBeenCalledWith(expect.stringContaining('所选资料范围尚未确认'))
    await act(async () => findButtonContaining('使用全库')!.click())
    await act(async () => findButtonContaining('从当前库出题')!.click())
    expect(api.learning.generateQuiz).toHaveBeenCalledWith('kb-1', 5, expect.objectContaining({ material_ids: null }))
  })

  it('uses each question library for follow-up and reports an unavailable library', async () => {
    const notify = vi.fn()
    vi.mocked(api.learning.attempt).mockResolvedValue({ ...sampleAttempt, knowledge_base_id: 'kb-1', items: [{ ...sampleAttempt.items[0], knowledge_base_id: null }] })
    await render(<Learning knowledgeBases={[{ id: 'kb-1', name: '高数资料库', description: null, created_at: '', updated_at: '' }]} notify={notify} overview={testOverview} refreshOverview={refreshOverview} />)
    await act(async () => findButtonContaining('继续上次练习')!.click())
    await act(async () => findButtonContaining('去对话追问')!.click())
    expect(notify).toHaveBeenCalledWith('原知识库不可用，无法追问此题。')
    expect(navigateMock).not.toHaveBeenCalled()
  })

  it('falls back to remaining valid kbId when selected KB is deleted and catches masterWrong errors cleanly', async () => {
    const notify = vi.fn()
    vi.mocked(api.learning.masterWrong).mockRejectedValueOnce(new Error('network failure'))

    await render(
      <Learning
        knowledgeBases={[
          { id: 'kb-deleted', name: '将被删库', description: null, created_at: '', updated_at: '' },
          { id: 'kb-alive', name: '保留库', description: null, created_at: '', updated_at: '' },
        ]}
        notify={notify}
        overview={testOverview}
        refreshOverview={refreshOverview}
      />,
    )
    await act(async () => { await Promise.resolve() })
    const kbSelect = node.querySelector<HTMLSelectElement>('select[aria-label="当前知识库"]')!
    expect(kbSelect.value).toBe('kb-deleted')

    await render(
      <Learning
        knowledgeBases={[
          { id: 'kb-alive', name: '保留库', description: null, created_at: '', updated_at: '' },
        ]}
        notify={notify}
        overview={testOverview}
        refreshOverview={refreshOverview}
      />,
    )
    await act(async () => { await Promise.resolve() })
    expect(node.querySelector<HTMLSelectElement>('select[aria-label="当前知识库"]')!.value).toBe('kb-alive')

    await act(async () => findButtonContaining('错题本')!.click())
    await act(async () => findButtonContaining('已掌握')!.click())
    expect(api.learning.masterWrong).toHaveBeenCalledWith('wrong-1')
    expect(notify).toHaveBeenCalledWith('操作失败')
  })
})
