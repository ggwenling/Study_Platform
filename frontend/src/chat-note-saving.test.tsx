// @vitest-environment jsdom
import { act } from 'react'
import type { Ref } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from './api/client'
import type { Citation, Message, StudyNote } from './api/types'
import LiveChat from './components/LiveChat'
import type { ComposerHandle } from './components/Composer'

const { notify, editAsNew } = vi.hoisted(() => ({ notify: vi.fn(), editAsNew: vi.fn() }))
const chat = {
  cached: {}, accepted: {} as Record<string, Message[]>, recoveryErrors: {}, activeFor: () => null,
  isBusy: () => false, acknowledgeSaved: vi.fn(), edit: vi.fn(), retry: vi.fn(),
}
const workspace = { user: { id: 'user' }, revision: 0, notify, chat }
vi.mock('./WorkspaceData', () => ({ useWorkspace: () => workspace }))
vi.mock('./components/Composer', async () => {
  const { useImperativeHandle } = await import('react')
  return { Composer: ({ ref }: { ref: Ref<ComposerHandle> }) => {
    useImperativeHandle(ref, () => ({ editAsNew, focus() {}, chooseImage() {}, setSummaryMode() {} }), [])
    return null
  } }
})
vi.mock('./components/MarkdownContent', () => ({
  MarkdownContent: ({ children, citations, onSelectCitation }: { children: string; citations?: Citation[]; onSelectCitation?: (citation: Citation) => void }) => (
    <p>{children}{citations?.map((citation) => <button key={citation.id} type="button" onClick={() => onSelectCitation?.(citation)}>{citation.source_label}</button>)}</p>
  ),
}))
vi.mock('./components/arc/select/select', () => ({
  Select: ({ label, value, options, onValueChange }: { label: string; value: string; options: { label: string; value: string }[]; onValueChange: (value: string) => void }) => (
    <select aria-label={label} value={value} onChange={(event) => onValueChange(event.target.value)}>
      <option value="">选择笔记</option>
      {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
  ),
}))
vi.mock('./api/client', () => ({
  api: { conversation: vi.fn(), messages: vi.fn(), source: vi.fn(), studio: { notes: vi.fn(), createNote: vi.fn(), appendNote: vi.fn() } },
}))

const base = {
  conversation_id: 'conversation', turn_id: 'turn', status: 'completed', created_at: '2026-11-20T00:00:00Z',
  citations: [], scope: { knowledge_base_id: 'library', knowledge_base_name: '数学', answer_mode: 'knowledge_only', intent: 'qa', index_version_ids: [] },
} as const
const messages: Message[] = [
  { ...base, scope: { ...base.scope, index_version_ids: [] }, citations: [], id: 'user-message', sequence_no: 0, role: 'user', content: '如何判断矩阵是否可逆？' },
  { ...base, scope: { ...base.scope, index_version_ids: [] }, citations: [], id: 'answer', sequence_no: 1, role: 'assistant', content: '计算行列式。[S1]' },
]
const existingNote = { id: 'existing', title: '矩阵练习', knowledge_base_id: 'library' } as StudyNote
let node: HTMLElement
let root: Root

function LocationView() {
  return <p data-note-location>{useLocation().search}</p>
}

function click(text: string) {
  const button = [...document.querySelectorAll('button')].find((element) => element.textContent?.trim() === text)
  expect(button, text).toBeDefined()
  button!.click()
}

async function renderChat(key = 'initial') {
  await act(async () => root.render(<MemoryRouter key={key} initialEntries={['/chat']}>
    <Routes>
      <Route path="/chat" element={<LiveChat id="conversation" />} />
      <Route path="/knowledge" element={<LocationView />} />
    </Routes>
  </MemoryRouter>))
}

beforeEach(async () => {
  vi.resetAllMocks()
  chat.accepted = {}
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }))
  vi.mocked(api.messages).mockResolvedValue({ items: messages, next_cursor: null })
  vi.mocked(api.conversation).mockResolvedValue({ id: 'conversation', title: '线性代数', updated_at: base.created_at, created_at: base.created_at, project_knowledge_base_id: 'library' })
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
  await renderChat()
})

afterEach(async () => {
  await act(async () => root.unmount())
  node.remove()
  vi.unstubAllGlobals()
})

it('saves a new note with an edited title and links to the saved note', async () => {
  vi.mocked(api.studio.createNote).mockResolvedValue({ ...existingNote, id: 'new', title: '可逆矩阵' })
  await act(async () => click('存为笔记'))
  const dialog = document.querySelector('[role="dialog"]')!
  const title = dialog.querySelector('input')!
  expect(title.value).toBe(messages[0].content)
  expect(dialog.textContent).toContain(messages[1].content)
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(title, '可逆矩阵')
    title.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => click('保存笔记'))
  expect(api.studio.createNote).toHaveBeenCalledWith('library', { title: '可逆矩阵', content_md: messages[1].content, message_id: 'answer', note_type: 'summary' })
  const action = notify.mock.lastCall?.[1].action
  expect(action.label).toBe('查看笔记')
  await act(async () => action.onClick())
  expect(node.querySelector('[data-note-location]')?.textContent).toBe('?id=library&note=new')
})

it('loads the answer library’s notes and appends to the selected note', async () => {
  vi.mocked(api.studio.notes).mockResolvedValue({ items: [existingNote] })
  vi.mocked(api.studio.appendNote).mockResolvedValue(existingNote)
  await act(async () => click('存为笔记'))
  expect(api.studio.notes).not.toHaveBeenCalled()
  await act(async () => click('追加到已有笔记'))
  expect(api.studio.notes).toHaveBeenCalledWith('library', expect.any(AbortSignal))
  const select = document.querySelector<HTMLSelectElement>('select[aria-label="目标笔记"]')!
  await act(async () => {
    select.value = 'existing'
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await act(async () => document.querySelector<HTMLButtonElement>('[role="dialog"] button[type="submit"]')!.click())
  expect(api.studio.appendNote).toHaveBeenCalledWith('existing', {
    content_md: messages[1].content, message_id: 'answer', section_heading: messages[0].content,
  })
  expect(api.studio.createNote).not.toHaveBeenCalled()
  await act(async () => notify.mock.lastCall?.[1].action.onClick())
  expect(node.querySelector('[data-note-location]')?.textContent).toBe('?id=library&note=existing')
})

it('regenerates or edits the last turn and stages an older question as a new draft', async () => {
  const scope = { ...messages[0].scope!, selected_material_ids: ['material-1'], intent: 'summary' as const }
  const older = [
    { ...messages[0], id: 'older-user', turn_id: 'older-turn', sequence_no: 0, content: '历史提问', scope },
    { ...messages[1], id: 'older-answer', turn_id: 'older-turn', sequence_no: 1, scope },
  ]
  chat.edit.mockResolvedValue(true)
  vi.mocked(api.messages).mockResolvedValue({ items: [...older, ...messages.map((message) => ({ ...message, sequence_no: message.sequence_no + 2 }))], next_cursor: null })
  await renderChat('turn-actions')
  expect([...node.querySelectorAll('button')].filter((button) => button.textContent?.trim() === '重新生成')).toHaveLength(1)
  await act(async () => click('重新生成'))
  expect(chat.retry).toHaveBeenCalledWith('conversation', 'turn')
  await act(async () => click('编辑为新提问'))
  expect(editAsNew).toHaveBeenCalledWith('历史提问', scope)
  await act(async () => click('编辑并重发'))
  const field = node.querySelector<HTMLTextAreaElement>('.chat-question-editor textarea')!
  expect(field.value).toBe(messages[0].content)
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, ' 原位编辑后的问题 ')
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => click('保存并重新生成'))
  expect(chat.edit).toHaveBeenCalledWith('conversation', 'turn', '原位编辑后的问题')
  expect(node.querySelector('.chat-question-editor')).toBeNull()
})

it('shows accepted replacement messages while the saved snapshot is stale', async () => {
  chat.accepted = { conversation: [
    { ...messages[0], content: '新问题' },
    { ...messages[1], content: '新回答' },
  ] }
  await renderChat('accepted')
  expect(node.textContent).toContain('新问题')
  expect(node.textContent).toContain('新回答')
  expect(node.textContent).not.toContain(messages[1].content)
  expect(chat.acknowledgeSaved).toHaveBeenCalledWith('conversation', messages)
})

it('opens an inline citation in a mobile drawer and returns to the source list', async () => {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(max-width: 760px)', addEventListener() {}, removeEventListener() {} }))
  const citation: Citation = {
    id: 'citation', source_label: 'S1', material_id: 'material', material_version_id: 'version',
    knowledge_base_id: 'library', title: '矩阵讲义', quote: '非零行列式说明矩阵可逆。', locator: { line: 1 },
    location_label: '第 1 行', version_no: 1, availability: 'available',
  }
  vi.mocked(api.source).mockResolvedValue(citation)
  vi.mocked(api.messages).mockResolvedValue({ items: [messages[0], { ...messages[1], citations: [citation] }], next_cursor: null })
  await renderChat('mobile')
  expect(node.querySelector('.source-panel')).toBeNull()
  await act(async () => click('S1'))
  const drawer = document.querySelector('#live-sources-drawer')!
  expect(drawer.getAttribute('role')).toBe('dialog')
  expect(drawer.textContent).toContain(citation.quote)
  await act(async () => click('返回引用列表'))
  expect(drawer.textContent).toContain('本轮回答范围')
  await act(async () => drawer.querySelector<HTMLButtonElement>('.source-card')!.click())
  expect(drawer.textContent).toContain(citation.quote)
})
