import { expect, it } from 'vitest'
import { isReferenceRevealed } from './learningUi'

it('hides reference until verdict or image answer', () => {
  expect(isReferenceRevealed({ verdict: null, answer: { type: 'pending' }, reference_answer: null })).toBe(false)
  expect(isReferenceRevealed({ verdict: null, answer: { type: 'image' }, reference_answer: 'ref' })).toBe(true)
  expect(isReferenceRevealed({ verdict: 'correct', answer: { type: 'pending' }, reference_answer: 'ref' })).toBe(true)
})