import { Button } from '@/components/arc/button/button'

export type FlashcardRating = 0 | 1 | 2 | 3

export type FlashcardRatingCounts = Record<FlashcardRating, number>

export function FlashcardReviewSummary({
  counts,
  onReviewWeak,
  onContinueDue,
  continueLoading = false,
  onBack,
}: {
  counts: FlashcardRatingCounts
  onReviewWeak: () => void
  onContinueDue?: () => void
  continueLoading?: boolean
  onBack: () => void
}) {
  const weakCount = counts[0] + counts[1]

  return (
    <article className="learning-card" aria-label="本轮复习总结">
      <p className="learning-meta">本轮复习总结 · 本轮掌握分布</p>
      <div className="flashcard-ratings" aria-label="本轮评分数量">
        <span className="pill">重来 {counts[0]}</span>
        <span className="pill">困难 {counts[1]}</span>
        <span className="pill">良好 {counts[2]}</span>
        <span className="pill">熟练掌握 {counts[3]}</span>
      </div>
      <div className="learning-actions">
        {weakCount > 0 && (
          <Button type="button" variant="secondary" onClick={onReviewWeak}>
            仅重刷本轮生疏卡（{weakCount} 张）
          </Button>
        )}
        {onContinueDue && (
          <Button type="button" variant="secondary" loading={continueLoading} disabled={continueLoading} onClick={onContinueDue}>
            继续复习更多到期闪卡
          </Button>
        )}
        <Button type="button" variant="ghost" onClick={onBack}>
          返回卡组列表
        </Button>
      </div>
    </article>
  )
}
