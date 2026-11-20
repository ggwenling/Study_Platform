import type { LearningAttempt, LearningItem, WrongQuestionItem } from '@/api/types'

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
  if (typeof answer.transcribed_text === 'string') return answer.transcribed_text
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

export function wrongReviewLabel(question: Pick<WrongQuestionItem, 'is_due' | 'next_review_at' | 'last_wrong_at' | 'status'>) {
  if (question.is_due || question.status === 'open') return '今日待重练'
  const next = question.next_review_at
    ? new Date(question.next_review_at)
    : question.status === 'reviewing'
      ? new Date(new Date(question.last_wrong_at).getTime() + 3 * 24 * 60 * 60 * 1000)
      : null
  if (!next || Number.isNaN(next.getTime())) return '未安排复习'
  return `下次复习：${String(next.getMonth() + 1).padStart(2, '0')}-${String(next.getDate()).padStart(2, '0')}`
}

export function wrongIdsForAttempt(attempt: Pick<LearningAttempt, 'knowledge_base_id' | 'items'>, questions: WrongQuestionItem[]) {
  const used = new Set<string>()
  const ids: string[] = []
  for (const item of attempt.items) {
    if (item.verdict !== 'incorrect') continue
    const itemKbId = item.knowledge_base_id ?? attempt.knowledge_base_id
    const originId = item.origin_wrong_id ?? questions.find((question) =>
      question.quiz_item_id === item.quiz_item_id &&
      (!itemKbId || !question.knowledge_base_id || question.knowledge_base_id === itemKbId),
    )?.id
    if (originId && !used.has(originId)) {
      used.add(originId)
      ids.push(originId)
    }
  }
  return ids
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
