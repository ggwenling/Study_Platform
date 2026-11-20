import { useCallback, useEffect, useRef, useState } from 'react'
import { BrainCircuit, ChevronRight, MessageCircle, RotateCw, Trash2 } from 'lucide-react'
import { useNavigate } from 'react-router'
import { api } from '@/api/client'
import type { DueFlashcard, FlashcardDeckDetail, FlashcardDeckSummary, FlashcardItem, KnowledgeBase, Material } from '@/api/types'
import { ApiError } from '@/api/stream'
import { allPages, errorMessage } from '@/api/ui'
import { Button } from '@/components/arc/button/button'
import { EmptyState } from '@/components/arc/empty-state/empty-state'
import { Accordion } from '@/components/arc/accordion/accordion'
import { Input } from '@/components/arc/input/input'
import { Select } from '@/components/arc/select/select'
import { FlashcardReviewSummary, type FlashcardRating, type FlashcardRatingCounts } from './FlashcardReviewSummary'
import { MarkdownContent } from './MarkdownContent'

type ReviewCard = FlashcardItem & Pick<DueFlashcard, 'deck_title' | 'knowledge_base_id' | 'knowledge_base_name'>
type QueueMode = 'due' | 'deck-due' | 'deck-all' | 'weak' | null

const deckCountOptions = [
  { value: '5', label: '5 张闪卡' },
  { value: '8', label: '8 张闪卡' },
  { value: '12', label: '12 张闪卡' },
  { value: '16', label: '16 张闪卡' },
]

export function FlashcardPanel({
  knowledgeBases,
  defaultKbId,
  initialDeckId,
  notify,
  onDeckCountChange,
  startDueQueue = false,
  onDueQueueStarted,
  onOverviewRefresh,
}: {
  knowledgeBases: KnowledgeBase[]
  defaultKbId: string
  initialDeckId?: string
  notify: (message: string) => void
  onDeckCountChange?: (count: number) => void
  startDueQueue?: boolean
  onDueQueueStarted?: () => void
  onOverviewRefresh?: () => void
}) {
  const navigate = useNavigate()
  const [kbId, setKbId] = useState(defaultKbId || knowledgeBases[0]?.id || '')
  const [cardCount, setCardCount] = useState('8')
  const [deckTitle, setDeckTitle] = useState('')
  const [materials, setMaterials] = useState<Material[]>([])
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<string[]>([])
  const [scoped, setScoped] = useState(false)
  const [materialsStatus, setMaterialsStatus] = useState<{ kbId: string; ready: boolean }>({ kbId: '', ready: false })
  const [materialsError, setMaterialsError] = useState('')
  const [materialsRetry, setMaterialsRetry] = useState(0)
  const [decks, setDecks] = useState<FlashcardDeckSummary[]>([])
  const [activeDeck, setActiveDeck] = useState<FlashcardDeckDetail | null>(null)
  const [reviewCards, setReviewCards] = useState<ReviewCard[]>([])
  const [queueMode, setQueueMode] = useState<QueueMode>(null)
  const [cardIndex, setCardIndex] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [showReviewSummary, setShowReviewSummary] = useState(false)
  const [ratingsByCardId, setRatingsByCardId] = useState<Record<string, FlashcardRating>>({})
  const [reviewedCardsById, setReviewedCardsById] = useState<Record<string, ReviewCard>>({})
  const [dueQueueHasMore, setDueQueueHasMore] = useState(false)
  const [dueQueueSeenIds, setDueQueueSeenIds] = useState<Set<string>>(() => new Set())
  const [busy, setBusy] = useState(false)
  const [pendingReview, setPendingReview] = useState<{ cardId: string; rating: 0 | 1 | 2 | 3; key: string } | null>(null)
  const ratingInFlight = useRef(false)

  useEffect(() => {
    if (!knowledgeBases.length) {
      if (kbId) setKbId('')
      return
    }
    if (!kbId || !knowledgeBases.some((kb) => kb.id === kbId)) {
      const validDefault =
        defaultKbId && knowledgeBases.some((kb) => kb.id === defaultKbId)
          ? defaultKbId
          : knowledgeBases[0].id
      setKbId(validDefault)
    }
  }, [knowledgeBases, defaultKbId, kbId])

  const loadDecks = useCallback(async () => {
    if (!api.studio?.flashcardDecks) return
    try {
      const res = await api.studio.flashcardDecks()
      setDecks(res.items)
      onDeckCountChange?.(res.items.length)
    } catch (error) {
      if (error instanceof ApiError) notify(errorMessage(error))
    }
  }, [notify, onDeckCountChange])

  const setDeckQueue = useCallback((detail: FlashcardDeckDetail, mode: 'deck-due' | 'deck-all') => {
    setActiveDeck(detail)
    setReviewCards(detail.cards.map((card) => ({
      ...card,
      deck_title: detail.title,
      knowledge_base_id: detail.knowledge_base_id,
      knowledge_base_name: detail.knowledge_base_name,
    })).filter((card) => mode === 'deck-all' || card.is_due))
    setQueueMode(mode)
    setCardIndex(0)
    setFlipped(false)
    setShowReviewSummary(false)
    setPendingReview(null)
    setRatingsByCardId({})
    setReviewedCardsById({})
    setDueQueueHasMore(false)
    setDueQueueSeenIds(new Set())
  }, [])

  const openDeck = useCallback(async (deckId: string) => {
    if (!api.studio?.flashcardDeck) return
    setBusy(true)
    try {
      const detail = await api.studio.flashcardDeck(deckId)
      setDeckQueue(detail, 'deck-due')
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '无法加载闪卡组')
    } finally {
      setBusy(false)
    }
  }, [notify, setDeckQueue])

  useEffect(() => {
    void loadDecks()
  }, [loadDecks])

  useEffect(() => {
    if (initialDeckId) void openDeck(initialDeckId)
  }, [initialDeckId, openDeck])

  useEffect(() => {
    setSelectedMaterialIds([])
    setScoped(false)
  }, [kbId])

  useEffect(() => {
    setMaterialsStatus({ kbId, ready: false })
    setMaterialsError('')
    setMaterials([])
    if (!kbId) {
      return
    }
    const controller = new AbortController()
    void allPages((cursor) => api.materials(kbId, cursor, controller.signal)).then(
      (rows) => {
        if (!controller.signal.aborted) {
          setMaterials(rows.filter((item) => item.status === 'ready'))
          setMaterialsStatus({ kbId, ready: true })
        }
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setMaterialsError(errorMessage(error))
      },
    )
    return () => controller.abort()
  }, [kbId, materialsRetry])

  const readyMaterials = materialsStatus.kbId === kbId && materialsStatus.ready ? materials : []
  const invalidMaterialScope = scoped && (
    materialsStatus.kbId !== kbId ||
    !materialsStatus.ready ||
    !selectedMaterialIds.length ||
    selectedMaterialIds.length > 50 ||
    selectedMaterialIds.some((id) => !readyMaterials.some((row) => row.id === id))
  )

  const loadDueQueue = useCallback(async (continuation = false) => {
    if (busy || pendingReview) {
      notify('请先完成或重试当前闪卡评分。')
      return
    }
    if (!api.studio?.dueFlashcards) {
      notify('暂时无法加载今日到期闪卡。')
      return
    }
    setBusy(true)
    try {
      const page = await api.studio.dueFlashcards(undefined, 100)
      const seen = continuation ? dueQueueSeenIds : new Set<string>()
      const nextCards = page.items.filter((item) => !seen.has(item.id))
      if (!continuation) {
        setActiveDeck(null)
        setQueueMode('due')
        setPendingReview(null)
        setRatingsByCardId({})
        setReviewedCardsById({})
        setDueQueueSeenIds(new Set())
      }
      setDueQueueHasMore(nextCards.length > 0 && page.total_due > page.items.length)
      setReviewCards(nextCards)
      setCardIndex(0)
      setFlipped(false)
      setShowReviewSummary(continuation && nextCards.length === 0)
      if (continuation && nextCards.length === 0) notify('当前没有更多未复习的到期闪卡。')
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '无法加载今日到期闪卡')
    } finally {
      setBusy(false)
    }
  }, [busy, dueQueueSeenIds, notify, pendingReview])

  const dueQueueRequestHandled = useRef(false)
  useEffect(() => {
    if (!startDueQueue) {
      dueQueueRequestHandled.current = false
      return
    }
    if (dueQueueRequestHandled.current) return
    dueQueueRequestHandled.current = true
    void loadDueQueue().finally(() => onDueQueueStarted?.())
  }, [startDueQueue, loadDueQueue, onDueQueueStarted])

  async function generateDeck() {
    if (!kbId || busy) return
    if (invalidMaterialScope) {
      notify('所选资料范围尚未确认，请检查资料状态或选择使用全库。')
      return
    }
    setBusy(true)
    try {
      const created = await api.studio.generateFlashcards(kbId, {
        count: Number(cardCount) || 8,
        material_ids: scoped ? selectedMaterialIds : null,
        title: deckTitle.trim() || null,
      })
      setDeckQueue(created, 'deck-due')
      await loadDecks()
      onOverviewRefresh?.()
      notify(`已生成 ${created.card_count} 张记忆闪卡。`)
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '生成闪卡失败')
    } finally {
      setBusy(false)
    }
  }

  const rateCurrentCard = useCallback(async (rating: FlashcardRating, retry = false) => {
    if (busy || ratingInFlight.current) return
    const current = reviewCards[cardIndex]
    if (!current || (pendingReview && !retry)) return
    const review = pendingReview ?? { cardId: current.id, rating, key: crypto.randomUUID() }
    ratingInFlight.current = true
    setPendingReview(review)
    setBusy(true)
    try {
      const updated = await api.studio.reviewFlashcard(review.cardId, review.rating, review.key)
      setPendingReview(null)
      const reviewedCard = { ...current, ...updated, sources: current.sources }
      setReviewCards((prev) => prev.map((card) => (card.id === updated.id ? reviewedCard : card)))
      setActiveDeck((prev) => {
        if (!prev) return prev
        const cards = prev.cards.map((card) => (card.id === updated.id ? { ...card, ...updated, sources: card.sources } : card))
        return {
          ...prev,
          due_count: cards.filter((c) => c.is_due).length,
          cards,
        }
      })
      setRatingsByCardId((prev) => ({ ...prev, [updated.id]: review.rating }))
      setReviewedCardsById((prev) => ({ ...prev, [updated.id]: reviewedCard }))
      if (queueMode === 'due') setDueQueueSeenIds((prev) => new Set(prev).add(updated.id))
      onOverviewRefresh?.()
      setFlipped(false)
      if (cardIndex < reviewCards.length - 1) {
        setCardIndex((idx) => idx + 1)
      } else {
        setShowReviewSummary(true)
      }
      await loadDecks()
    } catch (error) {
      if (error instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(error.status ?? 0)) setPendingReview(null)
      notify(error instanceof ApiError ? errorMessage(error) : '提交复习结果失败')
    } finally {
      ratingInFlight.current = false
      setBusy(false)
    }
  }, [busy, cardIndex, loadDecks, notify, onOverviewRefresh, pendingReview, queueMode, reviewCards])

  function startWeakReview() {
    const weakCards = Object.values(reviewedCardsById).filter((card) => {
      const rating = ratingsByCardId[card.id]
      return rating === 0 || rating === 1
    })
    if (!weakCards.length) return
    setReviewCards(weakCards)
    setQueueMode('weak')
    setCardIndex(0)
    setFlipped(false)
    setShowReviewSummary(false)
  }

  function switchDeckQueue() {
    if (!activeDeck) return
    setDeckQueue(activeDeck, queueMode === 'deck-due' ? 'deck-all' : 'deck-due')
  }

  function backToDeckList() {
    setActiveDeck(null)
    setReviewCards([])
    setQueueMode(null)
    setCardIndex(0)
    setFlipped(false)
    setShowReviewSummary(false)
    setPendingReview(null)
    setRatingsByCardId({})
    setReviewedCardsById({})
    setDueQueueHasMore(false)
    setDueQueueSeenIds(new Set())
  }

  function askAboutCard(current: ReviewCard) {
    const prompt = [
      '请基于知识库资料讲解这张闪卡背后的原理，并在结尾给我一个追问，帮助我检查是否真正理解。',
      `闪卡问题：${current.front}`,
      `卡片要点：${current.back}`,
    ].join('\n\n')
    void navigate('/', { state: { preferredLibrary: current.knowledge_base_id, initialDraft: prompt } })
  }

  async function removeDeck(deckId: string) {
    if (busy) return
    setBusy(true)
    try {
      await api.studio.deleteFlashcardDeck(deckId)
      if (activeDeck?.id === deckId) backToDeckList()
      await loadDecks()
      onOverviewRefresh?.()
      notify('闪卡组已删除。')
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '删除失败')
    } finally {
      setBusy(false)
    }
  }

  const card = reviewCards[cardIndex]
  const ratingCounts: FlashcardRatingCounts = { 0: 0, 1: 0, 2: 0, 3: 0 }
  Object.values(ratingsByCardId).forEach((rating) => { ratingCounts[rating] += 1 })

  useEffect(() => {
    if (!card) return
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target
      const focused = document.activeElement
      const ignoresTyping = (element: Element | null) =>
        Boolean(element?.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])'))
      if (
        event.defaultPrevented || event.repeat || event.altKey || event.ctrlKey || event.metaKey ||
        ignoresTyping(target instanceof Element ? target : null) ||
        ignoresTyping(focused instanceof Element ? focused : null) ||
        document.querySelector('[role="dialog"]')
      ) return
      if (event.code === 'Space') {
        if (busy || pendingReview) return
        event.preventDefault()
        setFlipped((current) => !current)
        return
      }
      if (!flipped || busy || pendingReview || !['1', '2', '3', '4'].includes(event.key)) return
      event.preventDefault()
      void rateCurrentCard((Number(event.key) - 1) as FlashcardRating)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [card, flipped, busy, pendingReview, rateCurrentCard])

  return (
    <div className="flashcard-workspace">
      <div className="learning-config">
        <div className="learning-config-grid">
          <Select
            label="选择知识库"
            value={kbId}
            onValueChange={setKbId}
            options={knowledgeBases.map((kb) => ({ value: kb.id, label: kb.name }))}
          />
          <Select
            label="闪卡数量"
            value={cardCount}
            onValueChange={setCardCount}
            options={deckCountOptions}
          />
          <Input
            label="卡组标题"
            maxLength={255}
            placeholder="留空则自动命名"
            value={deckTitle}
            onChange={(event) => setDeckTitle(event.target.value)}
          />
        </div>
        {materialsError && <div role="alert">资料状态读取失败：{materialsError} <Button type="button" size="sm" variant="ghost" onClick={() => setMaterialsRetry((value) => value + 1)}>重新读取资料</Button></div>}
        <Accordion
          defaultOpen={-1}
          items={[
            {
              title: scoped
                ? `出卡资料范围 · 已勾选 ${selectedMaterialIds.length} 份`
                : `出卡资料范围 · 默认全库（共 ${readyMaterials.length} 份就绪资料）`,
              content: (
                <div className="learning-material-picker">
                  <p className="muted">最多选择 50 份资料。{readyMaterials.length > 50 ? '资料超过上限，请手动选择。' : ''}{scoped && !selectedMaterialIds.length ? '请重新选择资料或使用全库。' : ''}</p>
                  {scoped && (
                    <Button type="button" size="sm" variant="ghost" onClick={() => { setSelectedMaterialIds([]); setScoped(false) }}>
                      使用全库
                    </Button>
                  )}
                  <div className="learning-config-actions">
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      disabled={!readyMaterials.length || readyMaterials.length > 50 || selectedMaterialIds.length >= readyMaterials.length}
                      onClick={() => { setScoped(true); setSelectedMaterialIds(readyMaterials.map((row) => row.id)) }}
                    >
                      全选资料
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={!selectedMaterialIds.length}
                      onClick={() => { setScoped(true); setSelectedMaterialIds([]) }}
                    >
                      清空选择
                    </Button>
                  </div>
                  {(materialsStatus.kbId !== kbId || !materialsStatus.ready) && !materialsError && <p role="status">正在读取资料…</p>}
                  {materialsStatus.kbId === kbId && materialsStatus.ready && !readyMaterials.length && <p>当前知识库没有已就绪资料。</p>}
                  <ul>
                    {readyMaterials.map((mat) => (
                      <li key={mat.id}>
                        <label>
                          <input
                            type="checkbox"
                            className="material-scope-checkbox"
                            aria-label={`限定资料：${mat.title}`}
                            checked={selectedMaterialIds.includes(mat.id)}
                            disabled={!selectedMaterialIds.includes(mat.id) && selectedMaterialIds.length >= 50}
                            onChange={(event) => {
                              if (event.target.checked) {
                                setScoped(true)
                                setSelectedMaterialIds((prev) => prev.length < 50 ? [...prev, mat.id] : prev)
                              } else {
                                setSelectedMaterialIds((prev) => prev.filter((id) => id !== mat.id))
                              }
                            }}
                          />
                          <span>{mat.title}</span>
                        </label>
                      </li>
                    ))}
                  </ul>
                </div>
              ),
            },
          ]}
        />
        <div className="learning-config-actions">
          <Button
            type="button"
            loading={busy}
            disabled={busy || Boolean(pendingReview) || !kbId || invalidMaterialScope}
            onClick={() => void generateDeck()}
          >
            <BrainCircuit size={16} aria-hidden /> 从当前库生成闪卡
          </Button>
        </div>
      </div>

      {activeDeck && activeDeck.due_count > 0 && (queueMode === 'deck-due' || queueMode === 'deck-all') && (
        <div className="learning-actions">
          <Button type="button" size="sm" variant="ghost" disabled={busy || Boolean(pendingReview)} onClick={switchDeckQueue}>
            {queueMode === 'deck-due' ? `重温全部（${activeDeck.card_count}）` : `仅看到期（${activeDeck.due_count}）`}
          </Button>
        </div>
      )}

      {showReviewSummary ? (
        <FlashcardReviewSummary
          counts={ratingCounts}
          onReviewWeak={startWeakReview}
          onContinueDue={queueMode === 'due' && dueQueueHasMore ? () => void loadDueQueue(true) : undefined}
          continueLoading={busy}
          onBack={backToDeckList}
        />
      ) : card ? (
        <article className="learning-card flashcard-stage" aria-label="闪卡复习卡片">
          <header className="learning-card-top">
            <div>
              <p className="learning-meta">
                {activeDeck?.title ?? `${card.knowledge_base_name ?? '知识库'} · ${card.deck_title}`}
                {queueMode === 'weak' ? ' · 重刷本轮生疏卡' : ''} · 第 {cardIndex + 1} / {reviewCards.length} 张
              </p>
              <span className="pill">
                间隔 {card.interval_days} 天 · 已复习 {card.repetition_count} 次
              </span>
            </div>
            <Button type="button" size="sm" variant="ghost" disabled={busy || Boolean(pendingReview)} onClick={backToDeckList}>
              返回卡组列表
            </Button>
          </header>

          <div
            className={`flashcard-box${flipped ? ' is-flipped' : ''}`}
            onClick={() => setFlipped((on) => !on)}
            role="button"
            tabIndex={0}
            aria-keyshortcuts="Space Enter"
            aria-label={flipped ? '点击切回正面问题' : '点击翻转查看答案'}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                setFlipped((on) => !on)
              }
            }}
          >
            <span className="flashcard-side-label">{flipped ? '背面 · 核心解答' : '正面 · 问题 / 概念'}</span>
            <div className="flashcard-content">
              <MarkdownContent>{flipped ? card.back : card.front}</MarkdownContent>
            </div>
          </div>

          {flipped && (
            <div className="learning-actions">
              {card.sources.length > 0 && <small className="muted">来源：{card.sources.map((source) => source.title).join('、')}</small>}
              <Button type="button" size="sm" variant="ghost" onClick={() => askAboutCard(card)}>
                <MessageCircle size={14} aria-hidden /> 去对话追问
              </Button>
            </div>
          )}

          {pendingReview && <div role="alert">评分结果尚未确认，请重试上次评分。
            <Button type="button" size="sm" disabled={busy} onClick={() => void rateCurrentCard(pendingReview.rating, true)}>重试上次评分</Button>
          </div>}
          {!flipped ? (
            <div className="learning-actions">
              <Button type="button" variant="secondary" disabled={busy || Boolean(pendingReview)} onClick={() => setFlipped(true)}>
                <RotateCw size={15} aria-hidden /> 翻转查看答案 <kbd>[Space]</kbd>
              </Button>
            </div>
          ) : (
            <div className="flashcard-ratings" role="group" aria-label="记忆程度评分">
              <Button type="button" size="sm" variant="danger" aria-keyshortcuts="1" disabled={busy || Boolean(pendingReview)} onClick={() => void rateCurrentCard(0)}>
                重来（10分钟后） <kbd>[1]</kbd>
              </Button>
              <Button type="button" size="sm" variant="secondary" aria-keyshortcuts="2" disabled={busy || Boolean(pendingReview)} onClick={() => void rateCurrentCard(1)}>
                困难 <kbd>[2]</kbd>
              </Button>
              <Button type="button" size="sm" variant="secondary" aria-keyshortcuts="3" disabled={busy || Boolean(pendingReview)} onClick={() => void rateCurrentCard(2)}>
                良好 <kbd>[3]</kbd>
              </Button>
              <Button type="button" size="sm" variant="primary" aria-keyshortcuts="4" disabled={busy || Boolean(pendingReview)} onClick={() => void rateCurrentCard(3)}>
                熟练掌握 <kbd>[4]</kbd>
              </Button>
            </div>
          )}

          <div className="learning-nav">
            <Button
              type="button"
              variant="ghost"
              disabled={cardIndex === 0 || busy || Boolean(pendingReview)}
              onClick={() => { setFlipped(false); setCardIndex((i) => i - 1) }}
            >
              上一张
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={cardIndex >= reviewCards.length - 1 || busy || Boolean(pendingReview)}
              onClick={() => { setFlipped(false); setCardIndex((i) => i + 1) }}
            >
              下一张 <ChevronRight size={16} aria-hidden />
            </Button>
          </div>
        </article>
      ) : activeDeck ? (
        <EmptyState
          title={activeDeck.due_count === 0 && queueMode === 'deck-due' ? '本组今日到期卡片已全部复习完毕' : '本组没有可复习闪卡'}
          description="你可以自由重温本组全部卡片。"
          icon={<BrainCircuit aria-hidden />}
          action={activeDeck.card_count > 0 && queueMode === 'deck-due' ? (
            <Button type="button" disabled={busy} onClick={() => setDeckQueue(activeDeck, 'deck-all')}>
              自由重温全部卡片
            </Button>
          ) : undefined}
        />
      ) : queueMode === 'due' ? (
        <EmptyState
          title="今日到期闪卡已全部复习完毕"
          description="目前没有更多待复习闪卡。"
          icon={<BrainCircuit aria-hidden />}
          action={<Button type="button" onClick={backToDeckList}>返回卡组列表</Button>}
        />
      ) : decks.length === 0 ? (
        <EmptyState
          title="暂无记忆闪卡组"
          description="选择知识库后点击「从当前库生成闪卡」，系统会基于资料提炼问答卡片并按间隔重复安排复习。"
          icon={<BrainCircuit aria-hidden />}
        />
      ) : (
        <ul className="learning-history-list">
          {decks.map((deck) => (
            <li key={deck.id}>
              <div>
                <strong>{deck.title}</strong>
                <span className="learning-wrong-meta">
                  {deck.knowledge_base_name ? `${deck.knowledge_base_name} · ` : ''}
                  共 {deck.card_count} 张 · 待复习 {deck.due_count} 张
                </span>
              </div>
              <div className="learning-actions">
                <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void openDeck(deck.id)}>
                  开始翻转复习
                </Button>
                <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => void removeDeck(deck.id)}>
                  <Trash2 size={14} aria-hidden /> 删除
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
