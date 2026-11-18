import { expect, it } from 'vitest'
import {
  buildFollowUpPrompt,
  extractSubmittedAnswerText,
  formatLearningTimestamp,
  isReferenceRevealed,
  labelWrongQuestionStatus,
} from './learningUi'

it('hides reference until verdict, text answer, or image answer', () => {
  expect(isReferenceRevealed({ verdict: null, answer: { type: 'pending' }, reference_answer: null })).toBe(false)
  expect(isReferenceRevealed({ verdict: null, answer: { type: 'image' }, reference_answer: 'ref' })).toBe(true)
  expect(isReferenceRevealed({ verdict: null, answer: { type: 'text', text: 'ans' }, reference_answer: 'ref' })).toBe(true)
  expect(isReferenceRevealed({ verdict: 'correct', answer: { type: 'pending' }, reference_answer: 'ref' })).toBe(true)
})

it('extracts submitted answer text and builds follow-up chat prompt', () => {
  expect(extractSubmittedAnswerText({ type: 'text', text: 'B' })).toBe('B')
  expect(extractSubmittedAnswerText({ type: 'image', ocr_text: '手写答案' })).toBe('手写答案')
  const prompt = buildFollowUpPrompt({
    stem: '什么是特征值？',
    userAnswer: '零向量',
    referenceAnswer: '非零向量对应的标量',
    feedback: '特征向量不可为零',
  })
  expect(prompt).toContain('题目：什么是特征值？')
  expect(prompt).toContain('我的作答：零向量')
  expect(prompt).toContain('参考答案：非零向量对应的标量')
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
