// @vitest-environment jsdom
import { act, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { api } from './api/client'
import type { ModelConnection, ReindexStatus } from './api/types'
import { LibraryReindex } from './components/LibraryReindex'

vi.mock('./WorkspaceData', () => ({ useWorkspace: () => ({ notify: vi.fn(), report: vi.fn() }) }))
vi.mock('./api/client', () => ({ api: {
  reindexStatus: vi.fn(), startReindex: vi.fn(), cancelReindex: vi.fn(), retryReindex: vi.fn(), modelConnections: vi.fn(),
} }))
vi.mock('./components/arc/dialog/dialog', () => ({
  Dialog: ({ open, children }: { open: boolean; children: ReactNode }) => open ? children : null,
  DialogContent: ({ children }: { children: ReactNode }) => children,
}))
vi.mock('./components/arc/button/button', () => ({
  Button: ({ children, disabled, onClick }: ButtonHTMLAttributes<HTMLButtonElement>) =>
    <button type="button" disabled={disabled} onClick={onClick}>{children}</button>,
}))
vi.mock('./components/arc/select/select', () => ({
  Select: ({ label, value, onValueChange, options }: { label: string; value: string; onValueChange: (v: string) => void; options: { value: string; label: string }[] }) =>
    <select aria-label={label} value={value} onChange={(event) => onValueChange(event.target.value)}>
      {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>,
}))

it('switches to a selected model after the replacement index finishes', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  const node = document.createElement('div')
  document.body.append(node)
  const root = createRoot(node)
  const onPublished = vi.fn()
  const onOpenChange = vi.fn()
  const library = { id: 'kb', name: '我的资料', description: null, created_at: '', updated_at: '' }
  const active: ReindexStatus = { job_id: 'reindex', status: 'running', total: 2, succeeded: 1, failed: 0, running: 1,
    target_model: 'new-model', target_connection_id: 'next', failures: [], error_code: null }
  vi.mocked(api.modelConnections).mockResolvedValue({ items: [{ id: 'next', kind: 'embedding', name: '新向量', model: 'new-model', status: 'ready' } as ModelConnection],
    encryption_available: true, platform_llm: null, platform_embedding: null })
  vi.mocked(api.reindexStatus).mockResolvedValueOnce(null).mockResolvedValueOnce(active)
    .mockResolvedValue({ ...active, status: 'succeeded', running: 0, succeeded: 2 })
  vi.mocked(api.startReindex).mockResolvedValue({ job_id: 'reindex', status: 'queued', total_materials: 2 })
  try {
    await act(async () => root.render(<LibraryReindex library={library} open onOpenChange={onOpenChange} onPublished={onPublished} />))
    const select = node.querySelector('select')!
    await act(async () => {
      select.value = 'next'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    const submit = [...node.querySelectorAll('button')].find((button) => button.textContent === '切换模型并安全重建索引')!
    await act(async () => submit.click())
    expect(api.startReindex).toHaveBeenCalledWith('kb', 'next')
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(node.querySelector('progress')?.value).toBe(1)
    expect(node.textContent).toContain('取消重建')
    expect(onPublished).not.toHaveBeenCalled()
    await act(async () => vi.advanceTimersByTimeAsync(1800))
    expect(onPublished).toHaveBeenCalledTimes(1)
    expect(node.querySelector('progress')).toBeNull()
  } finally {
    await act(async () => root.unmount())
    node.remove()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  }
})
