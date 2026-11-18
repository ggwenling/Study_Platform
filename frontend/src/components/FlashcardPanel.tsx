import { useCallback, useEffect, useState } from 'react'
import { BrainCircuit, ChevronRight, RotateCw, Trash2 } from 'lucide-react'
import { api } from '@/api/client'
import type { FlashcardDeckDetail, FlashcardDeckSummary, KnowledgeBase } from '@/api/types'
import { ApiError } from '@/api/stream'
import { errorMessage } from '@/api/ui'
import { Button } from '@/components/arc/button/button'
import { EmptyState } from '@/components/arc/empty-state/empty-state'
import { Select } from '@/components/arc/select/select'
import { MarkdownContent } from './MarkdownContent'

const deckCountOptions = [
  { value: '5', label: '5 张闪卡' },
  { value: '8', label: '8 张闪卡' },
  { value: '12', label: '12 张闪卡' },
]

export function FlashcardPanel({
  knowledgeBases,
  defaultKbId,
  initialDeckId,
  notify,
  onDeckCountChange,
}: {
  knowledgeBases: KnowledgeBase[]
  defaultKbId: string
  initialDeckId?: string
  notify: (message: string) => void
  onDeckCountChange?: (count: number) => void
}) {
  const [kbId, setKbId] = useState(defaultKbId || knowledgeBases[0]?.id || '')
  const [cardCount, setCardCount] = useState('8')
  const [decks, setDecks] = useState<FlashcardDeckSummary[]>([])
  const [activeDeck, setActiveDeck] = useState<FlashcardDeckDetail | null>(null)
  const [cardIndex, setCardIndex] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (defaultKbId && !kbId) setKbId(defaultKbId)
  }, [defaultKbId, kbId])

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

  const openDeck = useCallback(async (deckId: string) => {
    if (!api.studio?.flashcardDeck) return
    setBusy(true)
    try {
      const detail = await api.studio.flashcardDeck(deckId)
      setActiveDeck(detail)
      setCardIndex(0)
      setFlipped(false)
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '无法加载闪卡组')
    } finally {
      setBusy(false)
    }
  }, [notify])

  useEffect(() => {
    void loadDecks()
  }, [loadDecks])

  useEffect(() => {
    if (initialDeckId) void openDeck(initialDeckId)
  }, [initialDeckId, openDeck])

  async function generateDeck() {
    if (!kbId || busy) return
    setBusy(true)
    try {
      const created = await api.studio.generateFlashcards(kbId, { count: Number(cardCount) || 8 })
      setActiveDeck(created)
      setCardIndex(0)
      setFlipped(false)
      await loadDecks()
      notify(`已生成 ${created.card_count} 张记忆闪卡。`)
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '生成闪卡失败')
    } finally {
      setBusy(false)
    }
  }

  async function rateCurrentCard(rating: 0 | 1 | 2 | 3) {
    if (!activeDeck || busy) return
    const current = activeDeck.cards[cardIndex]
    if (!current) return
    setBusy(true)
    try {
      const updated = await api.studio.reviewFlashcard(current.id, rating)
      setActiveDeck((prev) => {
        if (!prev) return prev
        const cards = prev.cards.map((c) => (c.id === updated.id ? { ...c, ...updated, sources: c.sources } : c))
        return {
          ...prev,
          due_count: cards.filter((c) => c.is_due).length,
          cards,
        }
      })
      setFlipped(false)
      if (cardIndex < activeDeck.cards.length - 1) {
        setCardIndex((idx) => idx + 1)
      } else {
        notify('已完成本组一轮闪卡复习！')
      }
      await loadDecks()
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '提交复习结果失败')
    } finally {
      setBusy(false)
    }
  }

  async function removeDeck(deckId: string) {
    if (busy) return
    setBusy(true)
    try {
      await api.studio.deleteFlashcardDeck(deckId)
      if (activeDeck?.id === deckId) setActiveDeck(null)
      await loadDecks()
      notify('闪卡组已删除。')
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '删除失败')
    } finally {
      setBusy(false)
    }
  }

  const card = activeDeck?.cards[cardIndex]

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
        </div>
        <div className="learning-config-actions">
          <Button type="button" loading={busy} disabled={busy || !kbId} onClick={() => void generateDeck()}>
            <BrainCircuit size={16} aria-hidden /> 从当前库生成闪卡
          </Button>
        </div>
      </div>

      {activeDeck && card ? (
        <article className="learning-card flashcard-stage" aria-label="闪卡复习卡片">
          <header className="learning-card-top">
            <div>
              <p className="learning-meta">
                {activeDeck.title} · 第 {cardIndex + 1} / {activeDeck.cards.length} 张
              </p>
              <span className="pill">
                间隔 {card.interval_days} 天 · 已复习 {card.repetition_count} 次
              </span>
            </div>
            <Button type="button" size="sm" variant="ghost" onClick={() => setActiveDeck(null)}>
              返回卡组列表
            </Button>
          </header>

          <div
            className={`flashcard-box${flipped ? ' is-flipped' : ''}`}
            onClick={() => setFlipped((on) => !on)}
            role="button"
            tabIndex={0}
            aria-label={flipped ? '点击切回正面问题' : '点击翻转查看答案'}
            onKeyDown={(e) => {
              if (e.key === ' ' || e.key === 'Enter') {
                e.preventDefault()
                setFlipped((on) => !on)
              }
            }}
          >
            <span className="flashcard-side-label">{flipped ? '背面 · 核心解答' : '正面 · 问题 / 概念'}</span>
            <div className="flashcard-content">
              <MarkdownContent>{flipped ? card.back : card.front}</MarkdownContent>
            </div>
            {flipped && card.sources.length > 0 && (
              <small className="muted">
                来源：{card.sources.map((s) => s.title).join('、')}
              </small>
            )}
          </div>

          {!flipped ? (
            <div className="learning-actions">
              <Button type="button" variant="secondary" onClick={() => setFlipped(true)}>
                <RotateCw size={15} aria-hidden /> 翻转查看答案
              </Button>
            </div>
          ) : (
            <div className="flashcard-ratings" role="group" aria-label="记忆程度评分">
              <Button type="button" size="sm" variant="danger" disabled={busy} onClick={() => void rateCurrentCard(0)}>
                重来（10分钟后）
              </Button>
              <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void rateCurrentCard(1)}>
                困难
              </Button>
              <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => void rateCurrentCard(2)}>
                良好
              </Button>
              <Button type="button" size="sm" variant="primary" disabled={busy} onClick={() => void rateCurrentCard(3)}>
                熟练掌握
              </Button>
            </div>
          )}

          <div className="learning-nav">
            <Button
              type="button"
              variant="ghost"
              disabled={cardIndex === 0 || busy}
              onClick={() => { setFlipped(false); setCardIndex((i) => i - 1) }}
            >
              上一张
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={cardIndex >= activeDeck.cards.length - 1 || busy}
              onClick={() => { setFlipped(false); setCardIndex((i) => i + 1) }}
            >
              下一张 <ChevronRight size={16} aria-hidden />
            </Button>
          </div>
        </article>
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
