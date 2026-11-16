import type { LearningItem } from '@/api/types'

export function isReferenceRevealed(item: Pick<LearningItem, 'verdict' | 'answer' | 'reference_answer'>) {
  if (item.verdict) return true
  const answer = item.answer
  return Boolean(answer && typeof answer === 'object' && answer.type === 'image')
}