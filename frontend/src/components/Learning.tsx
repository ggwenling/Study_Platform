import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, BookOpen, Camera, Check, ChevronRight, Download, FileText, History, MessageCircle, RotateCcw, X } from 'lucide-react'
import { Link, useLocation, useNavigate } from 'react-router'
import { Accordion } from '@/components/arc/accordion/accordion'
import { Button } from '@/components/arc/button/button'
import { EmptyState } from '@/components/arc/empty-state/empty-state'
import { Select } from '@/components/arc/select/select'
import { Textarea } from '@/components/arc/textarea/textarea'
import { api } from '@/api/client'
import type {
  KnowledgeBase,
  Dashboard,
  LearningAttempt,
  LearningAttemptSummary,
  LearningResponseResult,
  LearningSource,
  Material,
  WrongQuestionItem,
} from '@/api/types'
import { ApiError } from '@/api/stream'
import { allPages, errorMessage } from '@/api/ui'
import { downloadMarkdownFile } from '@/conversationActions'
import { formatWrongQuestionExport } from '@/studyExports'
import { useWorkspace } from '../WorkspaceData'
import {
  buildFollowUpPrompt,
  extractSubmittedAnswerText,
  formatLearningTimestamp,
  isReferenceRevealed,
  labelWrongQuestionStatus,
  wrongIdsForAttempt,
  wrongReviewLabel,
} from '@/learningUi'
import { MarkdownContent } from './MarkdownContent'
import { FlashcardPanel } from './FlashcardPanel'
import { SourceReaderDrawer, type ReaderActionContext, type SourceReaderTarget } from './SourceReaderDrawer'
import { AiJobProgress } from './AiJobProgress'

type Props = {
  knowledgeBases: KnowledgeBase[]
  notify: (message: string) => void
  overview: Dashboard | null
  refreshOverview: () => void
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

function SourceList({ sources, onOpenSource, disabled = false }: {
  sources?: LearningSource[]
  onOpenSource: (source: LearningSource) => void
  disabled?: boolean
}) {
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
              {src.availability === 'available' && <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={() => onOpenSource(src)}>查看原文</Button>}
            </div>
            {src.quote_status === 'unverified' && <small>原文未验证</small>}
            {src.quote && <p className="learning-source-quote">{src.quote}</p>}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function Learning({ knowledgeBases, notify, overview, refreshOverview }: Props) {
  const { capabilities, aiJobs } = useWorkspace()
  const navigate = useNavigate()
  const location = useLocation()
  const historyState = location.state as {
    tab?: 'practice' | 'history' | 'wrong' | 'flashcards'
    deckId?: string
    kbId?: string
    attemptId?: string
    startDueQueue?: boolean
    wrongFilter?: 'all' | 'due'
  } | null
  const [kbId, setKbId] = useState<string>(historyState?.kbId ?? '')
  const [quizMode, setQuizMode] = useState<'short_answer' | 'mcq' | 'mixed'>('mixed')
  const [quizCount, setQuizCount] = useState('5')
  const [materials, setMaterials] = useState<Material[]>([])
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<string[]>([])
  const [scoped, setScoped] = useState(false)
  const [materialsStatus, setMaterialsStatus] = useState<{ kbId: string; ready: boolean }>({ kbId: '', ready: false })
  const [materialsError, setMaterialsError] = useState('')
  const [materialsRetry, setMaterialsRetry] = useState(0)
  const [attempt, setAttempt] = useState<LearningAttempt | null>(null)
  const [attempts, setAttempts] = useState<LearningAttemptSummary[]>([])
  const [attemptsCursor, setAttemptsCursor] = useState<string | null>(null)
  const [loadingMoreAttempts, setLoadingMoreAttempts] = useState(false)
  const [deckCount, setDeckCount] = useState(0)
  const [index, setIndex] = useState(0)
  const [textDrafts, setTextDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [useVisionForImage, setUseVisionForImage] = useState(false)
  const [wrongs, setWrongs] = useState<WrongQuestionItem[]>([])
  const dailyOverview = overview
  const [wrongKbFilter, setWrongKbFilter] = useState<string>('all')
  const [wrongDueOnly, setWrongDueOnly] = useState(historyState?.wrongFilter === 'due')
  const [startDueQueue, setStartDueQueue] = useState(Boolean(historyState?.startDueQueue))
  const [expandedWrongIds, setExpandedWrongIds] = useState<string[]>([])
  const [readerTarget, setReaderTarget] = useState<SourceReaderTarget | null>(null)
  const [tab, setTab] = useState<'practice' | 'flashcards' | 'history' | 'wrong'>(historyState?.tab ?? 'practice')

  function openSource(source: LearningSource) {
    if (source.availability !== 'available') return
    setReaderTarget({
      materialVersionId: source.material_version_id,
      materialId: source.material_id,
      knowledgeBaseId: source.knowledge_base_id ?? undefined,
      knowledgeBaseName: knowledgeBases.find((library) => library.id === source.knowledge_base_id)?.name,
      chunkId: source.chunk_id,
      title: source.title,
      quote: source.quote,
      locator: source.locator,
      locationLabel: source.location_label,
      sourceUrl: source.source_url,
    })
  }

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
      setAttemptsCursor(page.next_cursor)
    } catch (error) {
      if (error instanceof ApiError) notify(errorMessage(error))
    }
  }, [notify])

  const refreshDailyOverview = useCallback(() => refreshOverview(), [refreshOverview])

  async function loadMoreAttempts() {
    if (!attemptsCursor || loadingMoreAttempts) return
    setLoadingMoreAttempts(true)
    try {
      const page = await api.learning.attempts(undefined, 20, attemptsCursor)
      setAttempts((current) => {
        const existing = new Set(current.map((row) => row.id))
        return [...current, ...page.items.filter((row) => !existing.has(row.id))]
      })
      setAttemptsCursor(page.next_cursor)
    } catch (error) {
      if (error instanceof ApiError) notify(errorMessage(error))
    } finally {
      setLoadingMoreAttempts(false)
    }
  }

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
    const state = location.state as typeof historyState
    setTab(state?.tab ?? 'practice')
    setStartDueQueue(Boolean(state?.startDueQueue))
    setWrongDueOnly(state?.wrongFilter === 'due')
    if (state?.kbId) setKbId(state.kbId)
    if (state?.attemptId) void openAttemptById(state.attemptId)
  }, [location.key])

  useEffect(() => {
    setSelectedMaterialIds([])
    setScoped(false)
  }, [kbId])

  useEffect(() => {
    setMaterialsStatus({ kbId, ready: false })
    setMaterialsError('')
    if (!kbId) {
      setMaterials([])
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
    if (scoped && (materialsStatus.kbId !== kbId || !materialsStatus.ready || !selectedMaterialIds.length || selectedMaterialIds.length > 50 || selectedMaterialIds.some((id) => !materials.some((row) => row.id === id)))) {
      notify('所选资料范围尚未确认，请检查资料状态或选择使用全库。')
      return
    }
    setBusy(true)
    try {
      const job = await api.learning.generateQuiz(kbId, Number(quizCount) || 5, {
        mode: quizMode,
        material_ids: scoped ? selectedMaterialIds : null,
      })
      aiJobs.track(job)
      notify(`已提交「${job.title}」出题任务。`)
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
      await Promise.all([refreshWrongs(), refreshAttempts(), refreshDailyOverview()])
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '提交失败')
    } finally {
      setBusy(false)
    }
  }

  async function onImage(file: File) {
    if (!attempt) return
    const item = attempt.items[index]
    const useVision = capabilities?.vision === true && useVisionForImage
    setUseVisionForImage(false)
    setBusy(true)
    try {
      const result = useVision
        ? await api.learning.submitAnswerImage(item.response_id, file, true)
        : await api.learning.submitAnswerImage(item.response_id, file)
      applyResponseUpdate(item.response_id, result)
      await Promise.all([refreshWrongs(), refreshAttempts(), refreshDailyOverview()])
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
      await Promise.all([refreshWrongs(), refreshAttempts(), refreshDailyOverview()])
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '更新失败')
    } finally {
      setBusy(false)
    }
  }

  async function startWrongRetry(wrongIds?: string[], targetKb?: string, dueOnly = false) {
    if (wrongIds && !wrongIds.length) {
      notify('本轮错题尚未同步到错题本，暂时无法重练。')
      return
    }
    setBusy(true)
    try {
      const next = await api.learning.retryWrong({
        wrong_ids: wrongIds,
        knowledge_base_id: targetKb && targetKb !== 'all' ? targetKb : undefined,
        ...(dueOnly ? { due_only: true } : {}),
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
      await Promise.all([refreshWrongs(), refreshDailyOverview()])
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
    if (!params.targetKbId || !knowledgeBases.some((kb) => kb.id === params.targetKbId)) {
      notify('原知识库不可用，无法追问此题。')
      return
    }
    const prompt = buildFollowUpPrompt({
      stem: params.stem,
      userAnswer: params.userAnswer,
      referenceAnswer: params.referenceAnswer,
      feedback: params.feedback,
    })
    void navigate('/', {
      state: {
        preferredLibrary: params.targetKbId,
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
  const visibleWrongs = wrongDueOnly ? filteredWrongs.filter((row) => row.is_due) : filteredWrongs
  const currentWrongIds = attempt ? wrongIdsForAttempt(attempt, wrongs) : []
  const completedAttempt = Boolean(attempt?.items.length && attempt.items.every((item) => Boolean(item.verdict)))
  const recapCounts = attempt?.items.reduce((counts, item) => {
    if (item.verdict === 'correct') counts.correct += 1
    else if (item.verdict === 'incorrect') counts.incorrect += 1
    else if (item.verdict === 'pending') counts.pending += 1
    return counts
  }, { correct: 0, incorrect: 0, pending: 0 })

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

      <section className="learning-daily-overview" aria-label="今日复习概览">
        <div>
          <strong>今日待复习</strong>
          <span>闪卡 <b>{dailyOverview?.due_flashcard_count ?? '—'}</b></span>
          <span>错题 <b>{dailyOverview?.due_wrong_question_count ?? '—'}</b></span>
        </div>
        <div className="learning-daily-actions">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={!dailyOverview || dailyOverview.due_flashcard_count === 0}
            onClick={() => { setTab('flashcards'); setStartDueQueue(true) }}
          >开始今日到期闪卡</Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={!dailyOverview || dailyOverview.due_wrong_question_count === 0}
            onClick={() => void startWrongRetry(undefined, 'all', true)}
          >重练今日到期错题</Button>
        </div>
      </section>

      <AiJobProgress
        jobs={aiJobs.jobs.filter((job) => job.job_type === 'quiz')}
        cancel={aiJobs.cancel}
      />

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

          {materialsError && <div role="alert">资料状态读取失败：{materialsError} <Button type="button" size="sm" variant="ghost" onClick={() => setMaterialsRetry((value) => value + 1)}>重新读取资料</Button></div>}
          {(materials.length > 0 || scoped) && (
            <Accordion
              defaultOpen={-1}
              items={[
                {
                  title: scoped
                    ? `出题资料范围 · 已勾选 ${selectedMaterialIds.length} 份`
                    : `出题资料范围 · 默认全库（共 ${materials.length} 份就绪资料）`,
                  content: (
                    <div className="learning-material-picker">
                      <p className="muted">最多选择 50 份资料。{scoped && !selectedMaterialIds.length ? '请重新选择资料或使用全库。' : ''}</p>
                      {selectedMaterialIds.filter((id) => !materials.some((row) => row.id === id)).map((id) => <p role="alert" key={id}>已选资料不可用：{id} <Button type="button" size="sm" variant="ghost" onClick={() => setSelectedMaterialIds((prev) => prev.filter((item) => item !== id))}>移除</Button></p>)}
                      {scoped && (
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() => { setSelectedMaterialIds([]); setScoped(false) }}
                        >
                          使用全库
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
                                disabled={materialsStatus.kbId !== kbId || !materialsStatus.ready || (!selectedMaterialIds.includes(mat.id) && selectedMaterialIds.length >= 50)}
                                onChange={(e) => {
                                  if (e.target.checked) { setScoped(true); setSelectedMaterialIds((prev) => prev.length < 50 ? [...prev, mat.id] : prev) }
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
                  {capabilities?.vision && (
                    <label>
                      <input
                        type="checkbox"
                        aria-label="视觉模型辅助识别手写公式/草图（可选，可能产生费用）"
                        disabled={busy}
                        checked={useVisionForImage}
                        onChange={(event) => setUseVisionForImage(event.target.checked)}
                      />{' '}
                      视觉模型辅助识别手写公式/草图（可选，可能产生费用）
                    </label>
                  )}
                </div>
              </div>
            )}

            {currentSubmittedText && current.answer?.type === 'image' && (
              <p className="learning-ocr-preview">图片识别文字：{currentSubmittedText}</p>
            )}
            {current.answer?.extraction === 'vlm' && (
              <p className="learning-feedback" role="status">
                视觉模型辅助识别{current.verdict === 'pending' ? '，结果待确认，请手动判题。' : '（可手动改判）'}
              </p>
            )}
            {current.answer?.extraction === 'vlm_failed' && (
              <p className="learning-feedback" role="status">
                视觉模型不可用，已转为待确认。
              </p>
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

            <SourceList sources={current.sources} onOpenSource={openSource} disabled={!isReferenceRevealed(current)} />

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
                    targetKbId: current.knowledge_base_id,
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
        {attempt && completedAttempt && recapCounts && <section className="learning-practice-recap" aria-label="本轮练习复盘">
          <h2>本轮练习复盘</h2>
          <p role="status">正确 {recapCounts.correct} 题 · 错误 {recapCounts.incorrect} 题 · 待确认 {recapCounts.pending} 题</p>
          <div className="learning-practice-recap-actions">
            {recapCounts.incorrect > 0 && <Button
              type="button"
              variant="secondary"
              disabled={busy || currentWrongIds.length !== recapCounts.incorrect}
              onClick={() => void startWrongRetry(currentWrongIds)}
            >
              {currentWrongIds.length === recapCounts.incorrect ? `立即重练本卷错题（${recapCounts.incorrect}）` : '本卷错题暂不可重练'}
            </Button>}
            {recapCounts.pending > 0 && <Button
              type="button"
              variant="ghost"
              onClick={() => setIndex(attempt.items.findIndex((item) => item.verdict === 'pending'))}
            >查看待确认题目</Button>}
            <Button type="button" variant="ghost" disabled={busy} onClick={() => void startPractice()}>再出一套新题</Button>
          </div>
          {recapCounts.incorrect > currentWrongIds.length && <p className="muted">部分本轮错题尚未同步到错题本，暂不发送不完整的重练范围。</p>}
        </section>}
      </section>

      <section
        id="learning-panel-flashcards"
        className="learning-panel"
        role="tabpanel"
        aria-labelledby="learning-tab-flashcards"
        hidden={tab !== 'flashcards'}
      >
        <FlashcardPanel
          active={tab === 'flashcards'}
          knowledgeBases={knowledgeBases}
          defaultKbId={kbId}
          initialDeckId={historyState?.deckId}
          notify={notify}
          onDeckCountChange={setDeckCount}
          startDueQueue={startDueQueue}
          onDueQueueStarted={() => setStartDueQueue(false)}
          onOverviewRefresh={refreshDailyOverview}
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
        {attemptsCursor && <Button
          type="button"
          variant="secondary"
          loading={loadingMoreAttempts}
          disabled={loadingMoreAttempts}
          onClick={() => void loadMoreAttempts()}
        >加载更多历史记录</Button>}
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
            <Select
              label="复习范围"
              value={wrongDueOnly ? 'due' : 'all'}
              onValueChange={(value) => setWrongDueOnly(value === 'due')}
              options={[
                { value: 'all', label: '全部未掌握' },
                { value: 'due', label: '今日到期' },
              ]}
            />
            <Button
              type="button"
              variant="secondary"
              loading={busy}
              disabled={busy || visibleWrongs.length === 0}
              onClick={() => void startWrongRetry(undefined, wrongKbFilter, wrongDueOnly)}
            >
              <RotateCcw size={16} aria-hidden /> 错题重练（{visibleWrongs.length}）
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={busy || visibleWrongs.length === 0}
              onClick={() => {
                downloadMarkdownFile('错题集.md', formatWrongQuestionExport(visibleWrongs))
                notify('已导出错题集 Markdown。')
              }}
            >
              <Download size={16} aria-hidden /> 导出错题集 (.md)
            </Button>
          </div>
        )}

        {visibleWrongs.length === 0 ? (
          <EmptyState
            title={wrongDueOnly ? '今日没有待重练错题' : '暂无错题'}
            description={wrongDueOnly ? '到期的错题会显示在这里。' : '判错的题目会自动收录到这里，支持查看解析与错题重练。'}
          />
        ) : (
          <ul className="learning-wrong-list">
            {visibleWrongs.map((row) => {
              const expanded = expandedWrongIds.includes(row.id)
              return (
                <li key={row.id}>
                  <div className="learning-wrong-head">
                    <div>
                      <p>{row.stem}</p>
                      <span className="learning-wrong-meta">
                        {row.knowledge_base_name ? `${row.knowledge_base_name} · ` : ''}
                        错 {row.wrong_count} 次 · <span className="pill">{labelWrongQuestionStatus(row.status)}</span>
                        <span className="learning-review-schedule">{wrongReviewLabel(row)}</span>
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
                      <SourceList sources={row.sources} onOpenSource={openSource} />
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
      <SourceReaderDrawer open={Boolean(readerTarget)} target={readerTarget} onOpenChange={(open) => { if (!open) setReaderTarget(null) }}
        onAskFollowUp={(context: ReaderActionContext) => {
          const prompt = `请根据以下资料摘录回答我的问题。\n\n来源：${context.title} · ${context.locationLabel}\n\n${context.excerpt}\n\n我的问题：`
          void navigate('/', { state: { preferredLibrary: context.knowledgeBaseId, initialDraft: prompt, initialMaterialIds: [context.materialId] } })
        }} />
    </div>
  )
}
