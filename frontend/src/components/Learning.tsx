import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, BookOpen, Camera, Check, ChevronRight, FileText, History, MessageCircle, RotateCcw, X } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { Accordion } from '@/components/arc/accordion/accordion'
import { Button } from '@/components/arc/button/button'
import { EmptyState } from '@/components/arc/empty-state/empty-state'
import { Select } from '@/components/arc/select/select'
import { Textarea } from '@/components/arc/textarea/textarea'
import { api } from '@/api/client'
import type {
  KnowledgeBase,
  LearningAttempt,
  LearningAttemptSummary,
  LearningResponseResult,
  LearningSource,
  Material,
  WrongQuestionItem,
} from '@/api/types'
import { ApiError } from '@/api/stream'
import { allPages, errorMessage } from '@/api/ui'
import {
  buildFollowUpPrompt,
  extractSubmittedAnswerText,
  formatLearningTimestamp,
  isReferenceRevealed,
  labelWrongQuestionStatus,
} from '@/learningUi'
import { MarkdownContent } from './MarkdownContent'
import { FlashcardPanel } from './FlashcardPanel'

type Props = {
  knowledgeBases: KnowledgeBase[]
  notify: (message: string) => void
}

const verdictLabel: Record<string, string> = {
  correct: '正确',
  incorrect: '错误',
  pending: '待确认',
}

const modeOptions = [
  { value: 'short_answer', label: '简答题' },
  { value: 'mcq', label: '单选题' },
  { value: 'mixed', label: '混合题型' },
]

const countOptions = [
  { value: '3', label: '3 题' },
  { value: '5', label: '5 题' },
  { value: '10', label: '10 题' },
  { value: '15', label: '15 题' },
]

function SourceList({ sources }: { sources?: LearningSource[] }) {
  if (!sources?.length) return null
  return (
    <div className="learning-sources" aria-label="题目资料依据">
      <span className="learning-sources-label">资料依据</span>
      <ul>
        {sources.map((src, idx) => (
          <li key={`${src.chunk_id}-${idx}`}>
            <div className="learning-source-head">
              <FileText size={14} aria-hidden />
              <strong>{src.title}</strong>
              <small>{src.availability === 'available' ? src.location_label : '来源已不可用'}</small>
            </div>
            {src.quote && <p className="learning-source-quote">{src.quote}</p>}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function Learning({ knowledgeBases, notify }: Props) {
  const navigate = useNavigate()
  const historyState = (typeof window !== 'undefined' ? window.history.state?.usr : null) as {
    tab?: 'practice' | 'history' | 'wrong' | 'flashcards'
    deckId?: string
    kbId?: string
  } | null
  const [kbId, setKbId] = useState<string>(historyState?.kbId ?? '')
  const [quizMode, setQuizMode] = useState<'short_answer' | 'mcq' | 'mixed'>('mixed')
  const [quizCount, setQuizCount] = useState('5')
  const [materials, setMaterials] = useState<Material[]>([])
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<string[]>([])
  const [attempt, setAttempt] = useState<LearningAttempt | null>(null)
  const [attempts, setAttempts] = useState<LearningAttemptSummary[]>([])
  const [deckCount, setDeckCount] = useState(0)
  const [index, setIndex] = useState(0)
  const [textDrafts, setTextDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [wrongs, setWrongs] = useState<WrongQuestionItem[]>([])
  const [wrongKbFilter, setWrongKbFilter] = useState<string>('all')
  const [expandedWrongIds, setExpandedWrongIds] = useState<string[]>([])
  const [tab, setTab] = useState<'practice' | 'flashcards' | 'history' | 'wrong'>(historyState?.tab ?? 'practice')

  const refreshWrongs = useCallback(async () => {
    try {
      const page = await api.learning.wrongQuestions()
      setWrongs(page.items)
    } catch (error) {
      if (error instanceof ApiError) notify(errorMessage(error))
    }
  }, [notify])

  const refreshAttempts = useCallback(async () => {
    try {
      const page = await api.learning.attempts(undefined, 20)
      setAttempts(page.items)
    } catch (error) {
      if (error instanceof ApiError) notify(errorMessage(error))
    }
  }, [notify])

  useEffect(() => {
    if (!knowledgeBases.length) {
      if (kbId) setKbId('')
      return
    }
    if (!kbId || !knowledgeBases.some((kb) => kb.id === kbId)) {
      setKbId(knowledgeBases[0].id)
    }
  }, [knowledgeBases, kbId])

  useEffect(() => {
    if (wrongKbFilter !== 'all' && !knowledgeBases.some((kb) => kb.id === wrongKbFilter)) {
      setWrongKbFilter('all')
    }
  }, [knowledgeBases, wrongKbFilter])

  useEffect(() => {
    void refreshWrongs()
    void refreshAttempts()
  }, [refreshWrongs, refreshAttempts])

  useEffect(() => {
    setSelectedMaterialIds([])
    if (!kbId) {
      setMaterials([])
      return
    }
    const controller = new AbortController()
    void allPages((cursor) => api.materials(kbId, cursor, controller.signal)).then(
      (rows) => {
        if (!controller.signal.aborted) {
          setMaterials(rows.filter((item) => item.status === 'ready'))
        }
      },
      () => {
        if (!controller.signal.aborted) setMaterials([])
      },
    )
    return () => controller.abort()
  }, [kbId])

  function applyResponseUpdate(responseId: string, result: LearningResponseResult) {
    setAttempt((prev) => {
      if (!prev) return prev
      const items = prev.items.map((row) =>
        row.response_id === responseId
          ? {
              ...row,
              verdict: result.verdict,
              verdict_source: result.verdict_source ?? row.verdict_source,
              confidence: result.confidence,
              feedback: result.feedback,
              answer: result.answer ?? row.answer,
              reference_answer: result.reference_answer ?? row.reference_answer,
              explanation: result.explanation ?? row.explanation,
              sources: result.sources?.length ? result.sources : row.sources,
            }
          : row,
      )
      return { ...prev, items }
    })
  }

  async function openAttemptById(attemptId: string) {
    setBusy(true)
    try {
      const loaded = await api.learning.attempt(attemptId)
      setAttempt(loaded)
      const firstUnanswered = loaded.items.findIndex((item) => !item.verdict)
      setIndex(firstUnanswered >= 0 ? firstUnanswered : 0)
      setTab('practice')
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '无法加载练习记录')
    } finally {
      setBusy(false)
    }
  }

  async function startPractice() {
    if (!kbId) {
      notify('请先选择知识库')
      return
    }
    setBusy(true)
    try {
      const gen = await api.learning.generateQuiz(kbId, Number(quizCount) || 5, {
        mode: quizMode,
        material_ids: selectedMaterialIds.length ? selectedMaterialIds : null,
      })
      const next = await api.learning.startAttempt(gen.quiz_id)
      setAttempt(next)
      setIndex(0)
      setTab('practice')
      await refreshAttempts()
      notify('已生成练习题')
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '出题失败')
    } finally {
      setBusy(false)
    }
  }

  async function submitText(answerText: string) {
    if (!attempt) return
    const item = attempt.items[index]
    const trimmed = answerText.trim()
    if (!trimmed) {
      notify('请先输入作答内容')
      return
    }
    setBusy(true)
    try {
      const result = await api.learning.submitAnswerText(item.response_id, trimmed)
      applyResponseUpdate(item.response_id, result)
      await Promise.all([refreshWrongs(), refreshAttempts()])
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '提交失败')
    } finally {
      setBusy(false)
    }
  }

  async function onImage(file: File) {
    if (!attempt) return
    const item = attempt.items[index]
    setBusy(true)
    try {
      const result = await api.learning.submitAnswerImage(item.response_id, file)
      applyResponseUpdate(item.response_id, result)
      await Promise.all([refreshWrongs(), refreshAttempts()])
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '提交失败')
    } finally {
      setBusy(false)
    }
  }

  async function setVerdict(verdict: 'correct' | 'incorrect' | 'pending') {
    if (!attempt) return
    const item = attempt.items[index]
    setBusy(true)
    try {
      const result = await api.learning.patchVerdict(item.response_id, { verdict })
      applyResponseUpdate(item.response_id, { ...result, verdict_source: 'user' })
      await Promise.all([refreshWrongs(), refreshAttempts()])
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '更新失败')
    } finally {
      setBusy(false)
    }
  }

  async function startWrongRetry(wrongIds?: string[], targetKb?: string) {
    setBusy(true)
    try {
      const next = await api.learning.retryWrong({
        wrong_ids: wrongIds,
        knowledge_base_id: targetKb && targetKb !== 'all' ? targetKb : undefined,
      })
      setAttempt(next)
      setIndex(0)
      setTab('practice')
      await refreshAttempts()
      notify('已开启错题重练')
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '无法创建错题重练')
    } finally {
      setBusy(false)
    }
  }

  async function markMastered(wrongId: string) {
    if (busy) return
    setBusy(true)
    try {
      await api.learning.masterWrong(wrongId)
      await refreshWrongs()
      notify('已标记为掌握')
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  function askInChat(params: {
    targetKbId?: string | null
    stem: string
    userAnswer?: string | null
    referenceAnswer?: string | null
    feedback?: string | null
  }) {
    const prompt = buildFollowUpPrompt({
      stem: params.stem,
      userAnswer: params.userAnswer,
      referenceAnswer: params.referenceAnswer,
      feedback: params.feedback,
    })
    void navigate('/', {
      state: {
        preferredLibrary: params.targetKbId || kbId || undefined,
        initialDraft: prompt,
      },
    })
  }

  const current = attempt?.items[index]
  const currentSubmittedText = current ? extractSubmittedAnswerText(current.answer) : ''
  const currentDraft = current ? (textDrafts[current.response_id] ?? currentSubmittedText) : ''
  const resumableAttempt = !attempt
    ? attempts.find((row) => row.knowledge_base_id === kbId && row.answered_count < row.item_count)
    : undefined
  const filteredWrongs = wrongKbFilter === 'all'
    ? wrongs
    : wrongs.filter((row) => row.knowledge_base_id === wrongKbFilter)

  if (!knowledgeBases.length) {
    return (
      <div className="learning-page">
        <header className="learning-header">
          <h1>学习</h1>
          <p>基于所选知识库出题练习，支持在线选择/文字作答、拍照判题与错题复盘。</p>
        </header>
        <EmptyState
          title="还没有知识库"
          description="先创建知识库并导入、索引资料，再从这里出题练习。"
          icon={<BookOpen aria-hidden />}
          action={<Link to="/knowledge"><Button>前往知识库</Button></Link>}
        />
      </div>
    )
  }

  return (
    <div className="learning-page">
      <header className="learning-header">
        <h1>学习</h1>
        <p>基于所选知识库出题练习，支持在线选择/文字作答、拍照判题与错题复盘。</p>
      </header>

      <div className="learning-tabs" role="tablist" aria-label="学习视图">
        <button
          type="button"
          role="tab"
          id="learning-tab-practice"
          aria-selected={tab === 'practice'}
          aria-controls="learning-panel-practice"
          className={tab === 'practice' ? 'active' : ''}
          onClick={() => setTab('practice')}
        >
          练习
        </button>
        <button
          type="button"
          role="tab"
          id="learning-tab-flashcards"
          aria-selected={tab === 'flashcards'}
          aria-controls="learning-panel-flashcards"
          className={tab === 'flashcards' ? 'active' : ''}
          onClick={() => setTab('flashcards')}
        >
          闪卡复习{deckCount > 0 ? ` (${deckCount})` : ''}
        </button>
        <button
          type="button"
          role="tab"
          id="learning-tab-history"
          aria-selected={tab === 'history'}
          aria-controls="learning-panel-history"
          className={tab === 'history' ? 'active' : ''}
          onClick={() => setTab('history')}
        >
          历史练习 ({attempts.length})
        </button>
        <button
          type="button"
          role="tab"
          id="learning-tab-wrong"
          aria-selected={tab === 'wrong'}
          aria-controls="learning-panel-wrong"
          className={tab === 'wrong' ? 'active' : ''}
          onClick={() => setTab('wrong')}
        >
          错题本 ({wrongs.length})
        </button>
      </div>

      <section
        id="learning-panel-practice"
        className="learning-panel"
        role="tabpanel"
        aria-labelledby="learning-tab-practice"
        hidden={tab !== 'practice'}
      >
        <div className="learning-config">
          <div className="learning-config-grid">
            <Select
              label="当前知识库"
              value={kbId}
              onValueChange={setKbId}
              options={knowledgeBases.map((kb) => ({ value: kb.id, label: kb.name }))}
            />
            <Select
              label="题型模式"
              value={quizMode}
              onValueChange={(value) => setQuizMode(value as 'short_answer' | 'mcq' | 'mixed')}
              options={modeOptions}
            />
            <Select
              label="题目数量"
              value={quizCount}
              onValueChange={setQuizCount}
              options={countOptions}
            />
          </div>

          {materials.length > 0 && (
            <Accordion
              defaultOpen={-1}
              items={[
                {
                  title: selectedMaterialIds.length
                    ? `出题资料范围 · 已勾选 ${selectedMaterialIds.length} 份`
                    : `出题资料范围 · 默认全库（共 ${materials.length} 份就绪资料）`,
                  content: (
                    <div className="learning-material-picker">
                      {selectedMaterialIds.length > 0 && (
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() => setSelectedMaterialIds([])}
                        >
                          清除限定
                        </Button>
                      )}
                      <ul>
                        {materials.map((mat) => (
                          <li key={mat.id}>
                            <label>
                              <input
                                type="checkbox"
                                className="material-scope-checkbox"
                                checked={selectedMaterialIds.includes(mat.id)}
                                onChange={(e) => {
                                  if (e.target.checked) setSelectedMaterialIds((prev) => [...prev, mat.id])
                                  else setSelectedMaterialIds((prev) => prev.filter((id) => id !== mat.id))
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
          )}

          <div className="learning-config-actions">
            <Button type="button" loading={busy} disabled={busy || !kbId} onClick={() => void startPractice()}>
              从当前库出题
            </Button>
            {resumableAttempt && (
              <Button
                type="button"
                variant="secondary"
                disabled={busy}
                onClick={() => void openAttemptById(resumableAttempt.id)}
              >
                <History size={16} aria-hidden />
                继续上次练习（已答 {resumableAttempt.answered_count}/{resumableAttempt.item_count}）
              </Button>
            )}
          </div>
        </div>

        {!attempt ? (
          <EmptyState
            title="尚未开始练习"
            description="选择题库、题型与题量后点击「从当前库出题」，或从历史记录恢复上次练习。"
            icon={<BookOpen aria-hidden />}
          />
        ) : current ? (
          <article className="learning-card">
            <header className="learning-card-top">
              <div>
                <p className="learning-meta">
                  {attempt.title ? `${attempt.title} · ` : ''}第 {index + 1} / {attempt.items.length} 题
                </p>
                <span className="pill">{current.item_type === 'mcq' ? '单选题' : '简答题'}</span>
              </div>
              <div className="learning-progress-dots" role="navigation" aria-label="题号导航">
                {attempt.items.map((item, idx) => (
                  <button
                    key={item.response_id}
                    type="button"
                    className={`learning-dot verdict-${item.verdict ?? 'none'}${idx === index ? ' current' : ''}`}
                    aria-label={`第 ${idx + 1} 题`}
                    aria-current={idx === index ? 'step' : undefined}
                    onClick={() => setIndex(idx)}
                  >
                    {idx + 1}
                  </button>
                ))}
              </div>
            </header>

            <div className="learning-stem">
              <MarkdownContent>{current.stem}</MarkdownContent>
            </div>

            {current.item_type === 'mcq' && current.options && current.options.length > 0 ? (
              <div className="learning-mcq-options" role="group" aria-label="选项列表">
                {current.options.map((opt) => {
                  const chosen = currentSubmittedText.trim() === opt.trim()
                    || (currentSubmittedText.trim().length === 1 && opt.trim().toUpperCase().startsWith(`${currentSubmittedText.trim().toUpperCase()}.`))
                  return (
                    <button
                      key={opt}
                      type="button"
                      disabled={busy}
                      className={`learning-mcq-option${chosen ? ' selected' : ''}`}
                      aria-pressed={chosen}
                      onClick={() => void submitText(opt)}
                    >
                      {opt}
                    </button>
                  )
                })}
              </div>
            ) : (
              <div className="learning-text-answer">
                <Textarea
                  label="在线文字作答"
                  rows={3}
                  maxLength={4000}
                  disabled={busy}
                  placeholder="输入你的解答或关键词（Ctrl + Enter 提交）…"
                  value={currentDraft}
                  onChange={(event) =>
                    setTextDrafts((prev) => ({ ...prev, [current.response_id]: event.target.value }))
                  }
                  onKeyDown={(event) => {
                    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                      event.preventDefault()
                      void submitText(currentDraft)
                    }
                  }}
                />
                <div className="learning-answer-submit-row">
                  <Button
                    type="button"
                    variant="secondary"
                    loading={busy}
                    disabled={busy || !currentDraft.trim()}
                    onClick={() => void submitText(currentDraft)}
                  >
                    提交文字作答
                  </Button>
                  <label className="learning-upload">
                    <Camera aria-hidden size={17} />
                    <span>拍照 / 上传手写图片</span>
                    <input
                      type="file"
                      accept="image/png,image/jpeg"
                      capture="environment"
                      disabled={busy}
                      onChange={(e) => {
                        const file = e.target.files?.[0]
                        if (file) void onImage(file)
                        e.target.value = ''
                      }}
                    />
                  </label>
                </div>
              </div>
            )}

            {currentSubmittedText && current.answer?.type === 'image' && (
              <p className="learning-ocr-preview">图片识别文字：{currentSubmittedText}</p>
            )}

            <div className={`learning-verdict verdict-${current.verdict ?? 'none'}`}>
              <AlertCircle aria-hidden size={18} />
              <span>
                {current.verdict ? verdictLabel[current.verdict] : '未作答'}
                {current.confidence != null ? ` · 置信 ${Math.round(current.confidence * 100)}%` : ''}
              </span>
            </div>

            {current.feedback ? <p className="learning-feedback">{current.feedback}</p> : null}
            {isReferenceRevealed(current) && current.reference_answer ? (
              <p className="learning-ref">参考答案：{current.reference_answer}</p>
            ) : null}
            {isReferenceRevealed(current) && current.explanation && current.explanation !== current.feedback ? (
              <p className="learning-ref">解析：{current.explanation}</p>
            ) : null}

            <SourceList sources={current.sources} />

            <div className="learning-actions">
              <Button type="button" variant="secondary" size="sm" disabled={busy} onClick={() => void setVerdict('correct')}>
                <Check aria-hidden size={15} /> 判对
              </Button>
              <Button type="button" variant="secondary" size="sm" disabled={busy} onClick={() => void setVerdict('incorrect')}>
                <X aria-hidden size={15} /> 判错
              </Button>
              <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void setVerdict('pending')}>
                标为待确认
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() =>
                  askInChat({
                    targetKbId: attempt.knowledge_base_id,
                    stem: current.stem,
                    userAnswer: currentSubmittedText,
                    referenceAnswer: isReferenceRevealed(current) ? current.reference_answer : null,
                    feedback: current.feedback,
                  })
                }
              >
                <MessageCircle size={15} aria-hidden /> 去对话追问
              </Button>
            </div>

            <div className="learning-nav">
              <Button type="button" variant="ghost" disabled={index === 0 || busy} onClick={() => setIndex((i) => i - 1)}>
                上一题
              </Button>
              <Button
                type="button"
                variant="ghost"
                disabled={index >= attempt.items.length - 1 || busy}
                onClick={() => setIndex((i) => i + 1)}
              >
                下一题 <ChevronRight aria-hidden size={16} />
              </Button>
            </div>
          </article>
        ) : null}
      </section>

      <section
        id="learning-panel-flashcards"
        className="learning-panel"
        role="tabpanel"
        aria-labelledby="learning-tab-flashcards"
        hidden={tab !== 'flashcards'}
      >
        <FlashcardPanel
          knowledgeBases={knowledgeBases}
          defaultKbId={kbId}
          initialDeckId={historyState?.deckId}
          notify={notify}
          onDeckCountChange={setDeckCount}
        />
      </section>

      <section
        id="learning-panel-history"
        className="learning-panel"
        role="tabpanel"
        aria-labelledby="learning-tab-history"
        hidden={tab !== 'history'}
      >
        {attempts.length === 0 ? (
          <EmptyState title="暂无历史练习" description="生成练习题后，进度会自动保存在这里。" />
        ) : (
          <ul className="learning-history-list">
            {attempts.map((row) => (
              <li key={row.id}>
                <div>
                  <strong>{row.title}</strong>
                  <span className="learning-wrong-meta">
                    {row.knowledge_base_name ? `${row.knowledge_base_name} · ` : ''}
                    已答 {row.answered_count} / {row.item_count} · 正确 {row.correct_count}
                    <time dateTime={row.updated_at}> · {formatLearningTimestamp(row.updated_at)}</time>
                  </span>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => void openAttemptById(row.id)}
                >
                  {row.answered_count < row.item_count ? '继续练习' : '查看详情'}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section
        id="learning-panel-wrong"
        className="learning-panel"
        role="tabpanel"
        aria-labelledby="learning-tab-wrong"
        hidden={tab !== 'wrong'}
      >
        {wrongs.length > 0 && (
          <div className="learning-wrong-toolbar">
            <Select
              label="按知识库筛选错题"
              value={wrongKbFilter}
              onValueChange={setWrongKbFilter}
              options={[
                { value: 'all', label: '全部知识库' },
                ...knowledgeBases.map((kb) => ({ value: kb.id, label: kb.name })),
              ]}
            />
            <Button
              type="button"
              variant="secondary"
              loading={busy}
              disabled={busy || filteredWrongs.length === 0}
              onClick={() => void startWrongRetry(undefined, wrongKbFilter)}
            >
              <RotateCcw size={16} aria-hidden /> 错题重练（{filteredWrongs.length}）
            </Button>
          </div>
        )}

        {filteredWrongs.length === 0 ? (
          <EmptyState title="暂无错题" description="判错的题目会自动收录到这里，支持查看解析与错题重练。" />
        ) : (
          <ul className="learning-wrong-list">
            {filteredWrongs.map((row) => {
              const expanded = expandedWrongIds.includes(row.id)
              return (
                <li key={row.id}>
                  <div className="learning-wrong-head">
                    <div>
                      <p>{row.stem}</p>
                      <span className="learning-wrong-meta">
                        {row.knowledge_base_name ? `${row.knowledge_base_name} · ` : ''}
                        错 {row.wrong_count} 次 · {labelWrongQuestionStatus(row.status)}
                        <time dateTime={row.last_wrong_at}> · 最近 {formatLearningTimestamp(row.last_wrong_at)}</time>
                      </span>
                    </div>
                  </div>

                  {expanded && (
                    <div className="learning-wrong-detail">
                      {row.last_answer && <p className="muted">上次作答：{row.last_answer}</p>}
                      {row.reference_answer && <p className="learning-ref">参考答案：{row.reference_answer}</p>}
                      {row.last_feedback && <p className="learning-feedback">{row.last_feedback}</p>}
                      {row.explanation && row.explanation !== row.last_feedback && (
                        <p className="learning-ref">解析：{row.explanation}</p>
                      )}
                      <SourceList sources={row.sources} />
                    </div>
                  )}

                  <div className="learning-actions">
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        setExpandedWrongIds((prev) =>
                          prev.includes(row.id) ? prev.filter((id) => id !== row.id) : [...prev, row.id],
                        )
                      }
                    >
                      {expanded ? '收起解析' : '查看解析与依据'}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      disabled={busy}
                      onClick={() => void startWrongRetry([row.id])}
                    >
                      <RotateCcw size={14} aria-hidden /> 重练此题
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        askInChat({
                          targetKbId: row.knowledge_base_id,
                          stem: row.stem,
                          userAnswer: row.last_answer,
                          referenceAnswer: row.reference_answer,
                          feedback: row.last_feedback,
                        })
                      }
                    >
                      <MessageCircle size={14} aria-hidden /> 去对话追问
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() => void markMastered(row.id)}
                    >
                      已掌握
                    </Button>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )
}
