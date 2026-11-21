// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { downloadExportResponse, exportResponseFilename } from './studyExports'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.body.replaceChildren()
})

describe('study export downloads', () => {
  it('uses a UTF-8 Content-Disposition filename and revokes its object URL', async () => {
    const response = new Response('front\tback\ttags', {
      headers: { 'Content-Disposition': "attachment; filename*=UTF-8''%E5%AD%A6%E4%B9%A0%E9%97%AE%E7%AD%94.tsv" },
    })
    const createObjectURL = vi.fn(() => 'blob:study-export')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    let downloadedFilename = ''
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      downloadedFilename = this.download
    })

    await downloadExportResponse(response, 'flashcards.tsv')

    expect(downloadedFilename).toBe('学习问答.tsv')
    expect(createObjectURL).toHaveBeenCalledOnce()
    expect(createObjectURL.mock.calls[0]?.[0]).toMatchObject({
      size: 15,
      type: 'text/plain;charset=utf-8',
    })
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:study-export')
    expect(document.querySelector('a[download]')).toBeNull()
  })

  it('uses a safe fallback when the server omits a filename', () => {
    const response = new Response('archive')

    expect(exportResponseFilename(response, 'notes.zip')).toBe('notes.zip')
  })

  it('removes path separators and control characters from server filenames', () => {
    const response = new Response('export', {
      headers: { 'Content-Disposition': 'attachment; filename="../../cards\\n.tsv"' },
    })

    const filename = exportResponseFilename(response, 'cards.tsv')
    expect(filename).toBe('.._.._cards_n.tsv')
    expect(filename).not.toMatch(/[\\/]/)
  })
})
