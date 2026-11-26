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
const colorSchemeSelector = 'meta[name="color-scheme"]'
const colorSchemeMeta = new DOMParser().parseFromString(page, 'text/html').querySelector<HTMLMetaElement>(colorSchemeSelector)
const colorScheme = () => document.querySelector<HTMLMetaElement>(colorSchemeSelector)?.content
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
  if (colorSchemeMeta) document.head.append(colorSchemeMeta.cloneNode())
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
  document.querySelector(colorSchemeSelector)?.remove()
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
  expect(colorScheme()).toBe('dark')
  expect(localStorage.getItem('knowloop-theme')).toBe('dark')
  expect(selected('Dark')?.getAttribute('aria-checked')).toBe('true')
  await systemTheme(true)
  await choose('Light')
  expectTheme('light')
  expect(colorScheme()).toBe('light')
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
  await systemTheme(false)
  await choose('System')
  expectTheme('light')
  expect(colorScheme()).toBe('light dark')
  expect(localStorage.getItem('knowloop-theme')).toBeNull()
  await systemTheme(true)
  expectTheme('dark')
  expect(colorScheme()).toBe('light dark')
  await act(async () => root.unmount())
  expect(remove).toHaveBeenCalledWith('change', expect.any(Function))
  root = createRoot(node)
  await systemTheme(false)
  await render()
  expectTheme('light')
  await openMenu()
  expect(selected('System')?.getAttribute('aria-checked')).toBe('true')
})

it.each([null, 'invalid', 'system', 'light', 'dark'])('resolves %s before React mounts and agrees with the application', async (saved) => {
  if (saved) localStorage.setItem('knowloop-theme', saved)
  media.matches = true
  expect(colorScheme()).toBe('light dark')
  window.eval(bootstrap)
  const scheme = saved === 'light' || saved === 'dark' ? saved : 'light dark'
  expect(colorScheme()).toBe(scheme)
  expect(document.documentElement.dataset.theme).toBe(saved === 'light' ? 'light' : 'dark')
  await render()
  expectTheme(saved === 'light' ? 'light' : 'dark')
  expect(colorScheme()).toBe(scheme)
})

it('updates the theme when another tab changes or clears the saved preference', async () => {
  await render()
  await act(async () => {
    localStorage.setItem('knowloop-theme', 'dark')
    window.dispatchEvent(new StorageEvent('storage', { storageArea: localStorage, key: 'knowloop-theme', newValue: 'dark' }))
  })
  expectTheme('dark')
  expect(colorScheme()).toBe('dark')
  await act(async () => {
    localStorage.clear()
    window.dispatchEvent(new StorageEvent('storage', { storageArea: localStorage }))
  })
  expectTheme('light')
  expect(colorScheme()).toBe('light dark')
  expect(localStorage.getItem('knowloop-theme')).toBeNull()
})

it('keeps switching and following the system when browser storage is unavailable', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
  window.eval(bootstrap)
  expect(document.documentElement.dataset.theme).toBe('light')
  expect(colorScheme()).toBe('light dark')
  await render()
  await openMenu()
  await choose('Dark')
  expectTheme('dark')
  expect(colorScheme()).toBe('dark')
  await choose('System')
  await systemTheme(true)
  expectTheme('dark')
})
