import type { LearningItem } from '@/api/types'

export function isReferenceRevealed(item: Pick<LearningItem, 'verdict' | 'answer' | 'reference_answer'>) {
  if (item.verdict) return true
  const answer = item.answer
  return Boolean(answer && typeof answer === 'object' && answer.type === 'image')
}

const wrongQuestionStatusLabel: Record<string, string> = {
  open: '待复习',
  reviewing: '复习中',
  mastered: '已掌握',
}

export function labelWrongQuestionStatus(status: string) {
  return wrongQuestionStatusLabel[status] ?? status
}

export function formatLearningTimestamp(iso: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(iso))
}