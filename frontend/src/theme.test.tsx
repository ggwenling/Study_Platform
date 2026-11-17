// @vitest-environment jsdom
import page from '../index.html?raw'
import { act, StrictMode, useRef } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { App } from './App'
import { ThemeProvider } from './Theme'
import { ComposerMetalSend } from './components/ComposerMetalSend'

const user = vi.hoisted(() => ({ id: 'one', display_name: '测试用户', email: 'theme@example.test' }))
vi.mock('./api/client', () => ({ api: { refresh: vi.fn(async () => user) } }))
vi.mock('./WorkspaceData', () => ({
  WorkspaceData: ({ children }: React.PropsWithChildren) => children,
  useWorkspace: () => ({ user, libraries: [], recent: [], loading: false }),
}))
vi.mock('./components/Home', () => ({ Home: () => {
  const ref = useRef<HTMLButtonElement>(null)
  return <ComposerMetalSend reflectionTarget={ref}><button ref={ref}>发送</button></ComposerMetalSend>
} }))
vi.mock('metal-fx', () => ({ BEND: {}, useMetalBend() {},
  MetalFx: ({ children, theme }: React.PropsWithChildren<{ theme: string }>) => <div data-metal-theme={theme}>{children}</div>,
}))

let root: Root
let node: HTMLDivElement
let media: EventTarget & { matches: boolean }
const bootstrap = page.match(/<script>([\s\S]*?)<\/script>/)![1]
const selected = (name: string) => document.querySelector(`[role="menuitemradio"][aria-label="${name}"]`)
async function openMenu() {
  const trigger = node.querySelector<HTMLButtonElement>('button[aria-haspopup="menu"]')!
  expect(trigger).toBeTruthy()
  await act(async () => trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })))
}
async function choose(name: string) {
  expect(selected(name)).toBeTruthy()
  await act(async () => (selected(name) as HTMLButtonElement).click())
}
async function systemTheme(dark: boolean) {
  await act(async () => { media.matches = dark; media.dispatchEvent(new Event('change')) })
}
async function render() {
  await act(async () => root.render(<StrictMode><ThemeProvider><MemoryRouter><App /></MemoryRouter></ThemeProvider></StrictMode>))
}
function expectTheme(theme: string) {
  expect(document.documentElement.dataset.theme).toBe(theme)
  expect(node.querySelector('[data-metal-theme]')?.getAttribute('data-metal-theme')).toBe(theme)
}

beforeEach(() => {
  localStorage.clear()
  media = Object.assign(new EventTarget(), { matches: false })
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('scrollTo', vi.fn())
  vi.stubGlobal('matchMedia', (query: string) => query === '(prefers-color-scheme: dark)' ? media : {
    matches: false, addEventListener() {}, removeEventListener() {},
  })
  node = document.body.appendChild(document.createElement('div'))
  root = createRoot(node)
})
afterEach(async () => {
  await act(async () => root.unmount())
  node.remove()
  localStorage.clear()
  delete document.documentElement.dataset.theme
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('shows the account theme choices, applies light/dark globally and restores the saved selection', async () => {
  await render()
  await openMenu()
  expect(selected('System')?.getAttribute('aria-checked')).toBe('true')
  await choose('Dark')
  expectTheme('dark')
  expect(localStorage.getItem('knowloop-theme')).toBe('dark')
  expect(selected('Dark')?.getAttribute('aria-checked')).toBe('true')
  await systemTheme(true)
  await choose('Light')
  expectTheme('light')
  await systemTheme(false)
  await systemTheme(true)
  expectTheme('light')
  expect(localStorage.getItem('knowloop-theme')).toBe('light')
  await act(async () => root.unmount())
  root = createRoot(node)
  await render()
  expectTheme('light')
  await openMenu()
  expect(selected('Light')?.getAttribute('aria-checked')).toBe('true')
})

it('follows live system changes, resumes following after an explicit choice, and removes its listener', async () => {
  const remove = vi.spyOn(media, 'removeEventListener')
  await render()
  expectTheme('light')
  await systemTheme(true)
  expectTheme('dark')
  await openMenu()
  await choose('Light')
  expectTheme('light')
  await choose('System')
  expectTheme('dark')
  expect(localStorage.getItem('knowloop-theme')).toBe('system')
  await systemTheme(false)
  expectTheme('light')
  await act(async () => root.unmount())
  expect(remove).toHaveBeenCalledWith('change', expect.any(Function))
  root = createRoot(node)
  await systemTheme(true)
  await render()
  expectTheme('dark')
  await openMenu()
  expect(selected('System')?.getAttribute('aria-checked')).toBe('true')
})

it.each([null, 'invalid', 'system', 'light', 'dark'])('resolves %s before React mounts and agrees with the application', async (saved) => {
  if (saved) localStorage.setItem('knowloop-theme', saved)
  media.matches = true
  window.eval(bootstrap)
  expect(document.documentElement.dataset.theme).toBe(saved === 'light' ? 'light' : 'dark')
  await render()
  expectTheme(saved === 'light' ? 'light' : 'dark')
})

it('keeps switching and following the system when browser storage is unavailable', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
  window.eval(bootstrap)
  expect(document.documentElement.dataset.theme).toBe('light')
  await render()
  await openMenu()
  await choose('Dark')
  expectTheme('dark')
  await choose('System')
  await systemTheme(true)
  expectTheme('dark')
})
