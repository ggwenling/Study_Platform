// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { ContextMenu } from './context-menu'

it('closes when focus leaves and lets Tab continue from the trigger', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }))
  const node = document.body.appendChild(document.createElement('div'))
  const root = createRoot(node)
  const press = (target: Element, key: string, shiftKey = false) => {
    const event = new KeyboardEvent('keydown', { key, shiftKey, bubbles: true, cancelable: true })
    target.dispatchEvent(event)
    return event
  }

  try {
    await act(async () => root.render(<>
      <ContextMenu label="项目会话" items={[{ id: 'one', label: '打开' }, { id: 'two', label: '复制' }]}>会话</ContextMenu>
      <button type="button">相邻控件</button>
    </>))
    const trigger = node.querySelector<HTMLElement>('[aria-haspopup=menu]')!
    const outside = node.querySelector('button')!
    for (const shift of [false, true]) {
      await act(async () => { trigger.focus(); press(trigger, 'Enter') })
      expect(document.activeElement?.textContent).toBe('打开')
      await act(async () => { press(document.activeElement!, 'ArrowDown') })
      expect(document.activeElement?.textContent).toBe('复制')
      await act(async () => { expect(press(document.activeElement!, 'Tab', shift).defaultPrevented).toBe(false) })
      expect(trigger.getAttribute('aria-expanded')).toBe('false')
      expect(document.activeElement).toBe(trigger)
    }
    await act(async () => { press(trigger, 'Enter') })
    await act(async () => outside.focus())
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(outside)

    await act(async () => { trigger.focus(); press(trigger, 'Enter') })
    await act(async () => { expect(press(document.activeElement!, 'Escape').defaultPrevented).toBe(true) })
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(trigger)
  } finally {
    await act(async () => root.unmount())
    node.remove()
    vi.unstubAllGlobals()
  }
})
