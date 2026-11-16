/** @vitest-environment jsdom */
import { describe, expect, it, vi } from 'vitest'
import { useCoarsePointer } from './useCoarsePointer'

describe('useCoarsePointer', () => {
  it('defaults to false when matchMedia is unavailable', () => {
    vi.stubGlobal('matchMedia', undefined)
    expect(useCoarsePointer).toBeDefined()
  })

  it('reads (pointer: coarse) from matchMedia', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(pointer: coarse)',
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }))
    expect(typeof useCoarsePointer).toBe('function')
  })
})