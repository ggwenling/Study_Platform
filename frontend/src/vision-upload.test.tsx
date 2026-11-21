// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { uploadMaterial } from '@/api/client'
import { useUploadQueue } from './useUploadQueue'

vi.mock('@/api/client', () => ({ uploadMaterial: vi.fn() }))

it('uses VLM only for an opted-in eligible upload in a mixed queue', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  const node = document.body.appendChild(document.createElement('div'))
  const root = createRoot(node)
  let enqueue: ReturnType<typeof useUploadQueue>['enqueue']
  function Fixture() {
    enqueue = useUploadQueue({ userId: 'owner', refresh: vi.fn(), notify: vi.fn(), report: vi.fn() }).enqueue
    return null
  }

  const pdf = new File(['PDF'], 'slides.pdf', { type: 'application/pdf' })
  const notes = new File(['Notes'], 'notes.txt', { type: 'text/plain' })
  const image = new File(['PNG'], 'image.png', { type: 'image/png' })
  vi.mocked(uploadMaterial).mockResolvedValue({ material_id: 'm', version_id: 'v', job_id: 'j', status: 'queued' })
  try {
    await act(async () => root.render(<Fixture />))
    const target = { id: 'library', name: 'My library' }
    await act(async () => {
      enqueue([pdf, notes], target, 'vlm')
      enqueue([image], target)
    })
    expect(vi.mocked(uploadMaterial).mock.calls.map(([id, file, , , mode]) => [id, file.name, mode])).toEqual([
      ['library', 'slides.pdf', 'vlm'],
      ['library', 'notes.txt', undefined],
      ['library', 'image.png', undefined],
    ])
  } finally {
    await act(async () => root.unmount())
    node.remove()
    vi.unstubAllGlobals()
  }
})
