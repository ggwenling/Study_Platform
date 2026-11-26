import { useEffect, useState } from 'react'
import { BarChart3 } from 'lucide-react'
import { Accordion } from '@/components/arc/accordion/accordion'
import { Button } from '@/components/arc/button/button'
import { EmptyState } from '@/components/arc/empty-state/empty-state'
import { api } from '@/api/client'
import type { ModelUsageBucket, ModelUsageSummary, User } from '@/api/types'
import { ApiError } from '@/api/stream'
import { errorMessage } from '@/api/ui'
import { useWorkspace } from '../WorkspaceData'
import { ModelConnectionsPanel } from './ModelConnections'

const number = new Intl.NumberFormat('zh-CN')
const percent = new Intl.NumberFormat('zh-CN', { style: 'percent', maximumFractionDigits: 1 })
const purposeLabel: Record<string, string> = {
  chat: '对话', learning_quiz: '学习出题', learning_grade: '学习判题', query_rewrite: '检索改写', rerank: 'Rerank 重排',
  embedding: '向量嵌入', classification: '资料分类', connection_test: '连接测试',
  vision_ingest: '资料视觉解析', vision_grade: '视觉判题',
}

function UsageBreakdown({ title, rows }: { title: string; rows: ModelUsageBucket[] }) {
  return <section className="usage-breakdown">
    <h4>{title}</h4>
    <ul className="settings-list">
      {rows.map((row) => <li key={row.key}>
        <div><strong>{row.label ?? row.key}</strong>{row.fallback_calls > 0 && <small>备用 {number.format(row.fallback_calls)} 次</small>}</div>
        <span>{number.format(row.calls)} 次 · {number.format(row.total_tokens)} tokens</span>
      </li>)}
      {!rows.length && <li className="muted">暂无记录</li>}
    </ul>
  </section>
}

export function Settings({ user, requireLogin, openSetup }: { user: User | null; requireLogin: () => void; openSetup?: boolean }) {
  const { revisions } = useWorkspace()
  const [summary, setSummary] = useState<ModelUsageSummary | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!user) { setSummary(null); return }
    let active = true
    setBusy(true)
    setError('')
    void api.modelUsage(30).then(
      (value) => { if (active) setSummary(value) },
      (failure) => {
        if (!active) return
        setSummary(null)
        setError(failure instanceof ApiError ? errorMessage(failure) : '无法加载用量')
      },
    ).finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [user, revisions.usage, attempt])

  if (!user) return <div className="placeholder-page">
    <EmptyState title="登录后查看设置" description="管理个人模型连接，查看自己的调用用量。" action={<Button onClick={requireLogin}>登录</Button>} />
  </div>

  return <div className="settings-page">
    <header className="settings-header">
      <h1>设置</h1>
      <p>管理当前使用的模型连接，查看个人用量。</p>
    </header>
    <ModelConnectionsPanel key={user.id} openSetup={openSetup} />
    <section className="settings-card settings-usage" aria-labelledby="usage-title" aria-busy={busy}>
      <header className="usage-heading">
        <h2 id="usage-title"><BarChart3 size={19} aria-hidden="true" />用量概览</h2>
        <span>最近 {summary?.days ?? 30} 天 · 仅你的数据</span>
      </header>
      <p className="usage-note">模型调用包含向量化、资料分类和连接测试，不等同于问答次数。</p>
      {busy && !summary && <p role="status">正在加载用量…</p>}
      {error && <div className="usage-error" role="alert"><p className="error-text">{error}</p><Button variant="secondary" size="sm" onClick={() => setAttempt((value) => value + 1)}>重试用量</Button></div>}
      {summary && <>
        <dl className="settings-kv">
          <div><dt>模型调用次数</dt><dd>{number.format(summary.totals.calls)}</dd></div>
          <div><dt>成功率</dt><dd>{summary.totals.calls ? percent.format(summary.totals.successes / summary.totals.calls) : '—'}</dd></div>
          <div><dt>失败次数</dt><dd>{number.format(summary.totals.failures)}</dd></div>
          <div><dt>总 tokens</dt><dd>{number.format(summary.totals.total_tokens)}</dd></div>
        </dl>
        <section className="usage-purposes" aria-labelledby="usage-purpose-title">
          <h3 id="usage-purpose-title">按用途</h3>
          <ul className="usage-purpose-list">
            {summary.by_purpose.map((row) => {
              const share = summary.totals.calls ? row.calls / summary.totals.calls : 0
              return <li key={row.key}>
                <div><span>{purposeLabel[row.key] ?? row.key}</span><span>{percent.format(share)}<small>{number.format(row.calls)} 次</small></span></div>
                <div className="usage-track" aria-hidden="true"><span style={{ width: `${share * 100}%` }} /></div>
              </li>
            })}
          </ul>
          {!summary.totals.calls && <p className="muted">暂无模型调用记录。开始问答或测试连接后，用量会显示在这里。</p>}
        </section>
        <Accordion defaultOpen={-1} items={[{ title: '详细用量', content: <>
          <p className="usage-note">成功 {number.format(summary.totals.successes)} 次 · 备用模型调用 {number.format(summary.totals.fallback_calls)} 次</p>
          <div className="settings-grid">
            <UsageBreakdown title="按连接来源" rows={summary.by_connection ?? []} />
            <UsageBreakdown title="按实际模型" rows={summary.by_model} />
          </div>
        </> }]} />
      </>}
    </section>
  </div>
}
