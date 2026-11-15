// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { UserMenu } from './user-menu'

it('keeps the full knowledge base names and selects them from a short current name', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  const node = document.body.appendChild(document.createElement('div'))
  const root = createRoot(node)
  const names = ['name', '【演示】雪松项目资料库']

  function Picker() {
    const [current, setCurrent] = useState(names[0])
    return <UserMenu
      choice={{ label: '当前知识库', value: current, icon: null }}
      items={names.map((label) => ({ label, checked: label === current, onSelect: () => setCurrent(label) }))}
    />
  }

  try {
    await act(async () => root.render(<Picker />))
    const trigger = node.querySelector('button')!
    expect(trigger.getAttribute('aria-label')).toBe('当前知识库，name')
    await act(async () => trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })))
    const choices = [...document.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]')]
    expect(choices.map((choice) => choice.textContent)).toEqual(names)
    expect(choices.map((choice) => choice.getAttribute('aria-checked'))).toEqual(['true', 'false'])
    expect(document.querySelector('[role="menuitem"]')).toBeNull()
    await act(async () => choices[1].click())
    expect(trigger.getAttribute('aria-label')).toBe(`当前知识库，${names[1]}`)
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(trigger)
  } finally {
    await act(async () => root.unmount())
    node.remove()
    vi.unstubAllGlobals()
  }
})
