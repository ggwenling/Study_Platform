import type { LearningItem } from '@/api/types'

export function isReferenceRevealed(item: Pick<LearningItem, 'verdict' | 'answer' | 'reference_answer'>) {
  if (item.verdict) return true
  const answer = item.answer
  return Boolean(
    answer && typeof answer === 'object' && (answer.type === 'image' || answer.type === 'text'),
  )
}

export function extractSubmittedAnswerText(answer: Record<string, unknown> | null | undefined): string {
  if (!answer || typeof answer !== 'object') return ''
  if (typeof answer.text === 'string') return answer.text
  if (typeof answer.ocr_text === 'string') return answer.ocr_text
  return ''
}

export function buildFollowUpPrompt(params: {
  stem: string
  userAnswer?: string | null
  referenceAnswer?: string | null
  feedback?: string | null
}): string {
  const lines = ['请结合知识库资料为我详细讲解这道练习题，指出考察的核心依据与解题思路：', `题目：${params.stem}`]
  if (params.userAnswer?.trim()) lines.push(`我的作答：${params.userAnswer.trim()}`)
  if (params.referenceAnswer?.trim()) lines.push(`参考答案：${params.referenceAnswer.trim()}`)
  if (params.feedback?.trim()) lines.push(`判题反馈：${params.feedback.trim()}`)
  return lines.join('\n')
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
