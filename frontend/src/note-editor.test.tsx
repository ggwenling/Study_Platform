// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { NoteEditor } from './components/NoteEditor'

let container: HTMLElement
let root: Root

function Editor() {
  const [content, changeContent] = useState('')
  return <NoteEditor content={content} onContentChange={changeContent} />
}

function viewButton(label: string) {
  return [...container.querySelectorAll<HTMLButtonElement>('.note-editor-toolbar button')]
    .find((button) => button.textContent?.trim() === label)
}

async function typeText(content: string) {
  const field = container.querySelector('textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, content)
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
  return field
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  container = document.body.appendChild(document.createElement('div'))
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  vi.unstubAllGlobals()
})

it('updates math and GFM tables in the default split preview', async () => {
  await act(async () => root.render(<Editor />))
  expect(viewButton('分栏预览')?.getAttribute('aria-pressed')).toBe('true')
  await typeText('$E=mc^2$\n\n| 变量 | 含义 |\n| --- | --- |\n| E | 能量 |')
  await act(async () => { await vi.dynamicImportSettled() })
  expect(container.querySelector('.note-editor-preview .katex')).toBeTruthy()
  expect(container.querySelector('.note-editor-preview td')?.textContent).toBe('E')
  await act(async () => viewButton('预览')!.click())
  expect(container.querySelector('textarea')).toBeNull()
  await act(async () => viewButton('编辑')!.click())
  expect(container.querySelector('textarea')?.value).toContain('$E=mc^2$')
})

it('uses single-pane controls on mobile and inserts indentation with Tab', async () => {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(max-width: 760px)', addEventListener() {}, removeEventListener() {} }))
  await act(async () => root.render(<Editor />))
  expect(viewButton('分栏预览')).toBeUndefined()
  expect(viewButton('编辑')?.getAttribute('aria-pressed')).toBe('true')
  const field = await typeText('笔记')
  field.setSelectionRange(0, 0)
  await act(async () => field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })))
  expect(field.value).toBe('  笔记')
  await act(async () => viewButton('预览')!.click())
  expect(container.querySelector('.note-editor-preview')?.textContent).toContain('笔记')
  expect(container.querySelector('textarea')).toBeNull()
})
