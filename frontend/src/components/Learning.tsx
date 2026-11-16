import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, BookOpen, Camera, Check, ChevronRight, X } from 'lucide-react'
import { Button } from '@/components/arc/button/button'
import { EmptyState } from '@/components/arc/empty-state/empty-state'
import { api } from '@/api/client'
import type { KnowledgeBase, LearningAttempt, WrongQuestionItem } from '@/api/types'
import { ApiError } from '@/api/stream'
import { errorMessage } from '@/api/ui'
import { isReferenceRevealed } from '@/learningUi'

type Props = {
  knowledgeBases: KnowledgeBase[]
  notify: (message: string) => void
}

const verdictLabel: Record<string, string> = {
  correct: '正确',
  incorrect: '错误',
  pending: '待确认',
}

export function Learning({ knowledgeBases, notify }: Props) {
  const [kbId, setKbId] = useState<string>('')
  const [attempt, setAttempt] = useState<LearningAttempt | null>(null)
  const [index, setIndex] = useState(0)
  const [busy, setBusy] = useState(false)
  const [wrongs, setWrongs] = useState<WrongQuestionItem[]>([])
  const [tab, setTab] = useState<'practice' | 'wrong'>('practice')

  const refreshWrongs = useCallback(async () => {
    try {
      const page = await api.learning.wrongQuestions()
      setWrongs(page.items)
    } catch (error) {
      if (error instanceof ApiError) notify(errorMessage(error))
    }
  }, [notify])

  useEffect(() => {
    if (knowledgeBases.length && !kbId) setKbId(knowledgeBases[0].id)
  }, [knowledgeBases, kbId])

  useEffect(() => {
    void refreshWrongs()
  }, [refreshWrongs])

  async function startPractice() {
    if (!kbId) {
      notify('请先选择知识库')
      return
    }
    setBusy(true)
    try {
      const gen = await api.learning.generateQuiz(kbId, 5)
      const next = await api.learning.startAttempt(gen.quiz_id)
      setAttempt(next)
      setIndex(0)
      setTab('practice')
      notify('已生成练习题')
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '出题失败')
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
      setAttempt((prev) => {
        if (!prev) return prev
        const items = prev.items.map((row) =>
          row.response_id === item.response_id
            ? { ...row, verdict: result.verdict, feedback: result.feedback, confidence: result.confidence, answer: result.answer, reference_answer: result.reference_answer ?? row.reference_answer }
            : row,
        )
        return { ...prev, items }
      })
      await refreshWrongs()
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
      setAttempt((prev) => {
        if (!prev) return prev
        const items = prev.items.map((row) =>
          row.response_id === item.response_id
            ? {
                ...row,
                verdict,
                verdict_source: 'user',
                reference_answer: result.reference_answer ?? row.reference_answer,
              }
            : row,
        )
        return { ...prev, items }
      })
      await refreshWrongs()
    } catch (error) {
      notify(error instanceof ApiError ? errorMessage(error) : '更新失败')
    } finally {
      setBusy(false)
    }
  }

  const current = attempt?.items[index]

  return (
    <div className="learning-page">
      <header className="learning-header">
        <h1>学习</h1>
        <p>基于所选知识库练习，拍照作答，可手动更正判题结果。</p>
      </header>
      <div className="learning-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'practice'} className={tab === 'practice' ? 'active' : ''} onClick={() => setTab('practice')}>练习</button>
        <button type="button" role="tab" aria-selected={tab === 'wrong'} className={tab === 'wrong' ? 'active' : ''} onClick={() => setTab('wrong')}>错题本 ({wrongs.length})</button>
      </div>

      {tab === 'practice' ? (
        <section className="learning-panel">
          <label className="learning-field">
            <span>当前知识库</span>
            <select value={kbId} onChange={(e) => setKbId(e.target.value)} disabled={busy}>
              {knowledgeBases.map((kb) => (
                <option key={kb.id} value={kb.id}>{kb.name}</option>
              ))}
            </select>
          </label>
          <Button type="button" disabled={busy || !kbId} onClick={() => void startPractice()}>
            从当前库出题
          </Button>
          {!attempt ? (
            <EmptyState title="尚未开始练习" description="选择题库后点击「从当前库出题」。资料需已完成索引。" icon={<BookOpen aria-hidden />} />
          ) : current ? (
            <article className="learning-card">
              <p className="learning-meta">第 {index + 1} / {attempt.items.length} 题</p>
              <h2>{current.stem}</h2>
              {isReferenceRevealed(current) && current.reference_answer ? (
                <p className="learning-ref">参考：{current.reference_answer}</p>
              ) : null}
              <div className={`learning-verdict verdict-${current.verdict ?? 'none'}`}>
                <AlertCircle aria-hidden size={18} />
                <span>
                  {current.verdict ? verdictLabel[current.verdict] : '未作答'}
                  {current.confidence != null ? ` · 置信 ${Math.round(current.confidence * 100)}%` : ''}
                </span>
              </div>
              {current.feedback ? <p className="learning-feedback">{current.feedback}</p> : null}
              <label className="learning-upload">
                <Camera aria-hidden size={18} />
                <span>拍照 / 上传作答图片</span>
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
              <div className="learning-actions">
                <Button type="button" variant="secondary" disabled={busy} onClick={() => void setVerdict('correct')}><Check aria-hidden size={16} /> 判对</Button>
                <Button type="button" variant="secondary" disabled={busy} onClick={() => void setVerdict('incorrect')}><X aria-hidden size={16} /> 判错</Button>
                <Button type="button" variant="ghost" disabled={busy} onClick={() => void setVerdict('pending')}>标为待确认</Button>
              </div>
              <div className="learning-nav">
                <Button type="button" variant="ghost" disabled={index === 0 || busy} onClick={() => setIndex((i) => i - 1)}>上一题</Button>
                <Button type="button" variant="ghost" disabled={index >= attempt.items.length - 1 || busy} onClick={() => setIndex((i) => i + 1)}>
                  下一题 <ChevronRight aria-hidden size={16} />
                </Button>
              </div>
            </article>
          ) : null}
        </section>
      ) : (
        <section className="learning-panel">
          {wrongs.length === 0 ? (
            <EmptyState title="暂无错题" description="判错的题目会自动出现在这里。" />
          ) : (
            <ul className="learning-wrong-list">
              {wrongs.map((row) => (
                <li key={row.id}>
                  <p>{row.stem}</p>
                  <span>错 {row.wrong_count} 次 · {row.status}</span>
                  <Button type="button" variant="ghost" onClick={() => void api.learning.masterWrong(row.id).then(refreshWrongs)}>已掌握</Button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  )
}