import { expect, it } from 'vitest'
import { formatLearningTimestamp, isReferenceRevealed, labelWrongQuestionStatus } from './learningUi'

it('hides reference until verdict or image answer', () => {
  expect(isReferenceRevealed({ verdict: null, answer: { type: 'pending' }, reference_answer: null })).toBe(false)
  expect(isReferenceRevealed({ verdict: null, answer: { type: 'image' }, reference_answer: 'ref' })).toBe(true)
  expect(isReferenceRevealed({ verdict: 'correct', answer: { type: 'pending' }, reference_answer: 'ref' })).toBe(true)
})

it('maps wrong-question status to Chinese labels', () => {
  expect(labelWrongQuestionStatus('open')).toBe('待复习')
  expect(labelWrongQuestionStatus('reviewing')).toBe('复习中')
  expect(labelWrongQuestionStatus('mastered')).toBe('已掌握')
  expect(labelWrongQuestionStatus('unknown')).toBe('unknown')
})

it('formats learning timestamps for display', () => {
  expect(formatLearningTimestamp('2026-11-16T08:30:00.000Z')).toMatch(/\d{2}:\d{2}/)
})