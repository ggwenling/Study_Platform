// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { VisionReparseDialog } from './components/VisionReparseDialog'

let root: Root
let node: HTMLDivElement
const buttons = () => [...document.querySelectorAll<HTMLButtonElement>('button')]
const button = (label: string) => buttons().find((element) => element.textContent?.trim() === label)!

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('matchMedia', () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }))
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
})

afterEach(async () => {
  await act(async () => root.unmount())
  node.remove()
  vi.unstubAllGlobals()
})

it('requires an affirmative action before a PDF is sent for vision parsing', async () => {
  const onConfirm = vi.fn(async () => {})
  const onClose = vi.fn()
  await act(async () => root.render(<VisionReparseDialog title="课件.pdf" mimeType="application/pdf" pageCount={8} onConfirm={onConfirm} onClose={onClose} />))

  expect(document.body.textContent).toContain('预计处理 8 页，约调用视觉模型 8 次。')
  expect(document.body.textContent).toContain('费用取决于模型与内容')
  expect(onConfirm).not.toHaveBeenCalled()

  await act(async () => button('确认并开始视觉解析').click())
  expect(onConfirm).toHaveBeenCalledOnce()
  expect(onClose).toHaveBeenCalledOnce()
})
