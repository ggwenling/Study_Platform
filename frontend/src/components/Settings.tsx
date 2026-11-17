import { ModelConnectionsPanel } from './ModelConnections'
import { useEffect, useState } from 'react'
import { BarChart3 } from 'lucide-react'
import { Button } from '@/components/arc/button/button'
import { EmptyState } from '@/components/arc/empty-state/empty-state'
import { api } from '@/api/client'
import type { ModelUsageSummary, User } from '@/api/types'
import { ApiError } from '@/api/stream'
import { errorMessage } from '@/api/ui'
import { useWorkspace } from '../WorkspaceData'

const purposeLabel: Record<string, string> = {
  chat: '对话',
  learning_quiz: '学习出题',
  learning_grade: '学习判题',
  query_rewrite: '检索改写',
  embedding: '向量嵌入',
  classification: '资料分类',
  connection_test: '连接测试',
}

export function Settings({ user, requireLogin }: { user: User | null; requireLogin: () => void }) {
  const { revision } = useWorkspace()
  const [summary, setSummary] = useState<ModelUsageSummary | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!user) {
      setSummary(null)
      return
    }
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
  }, [user, revision])

  if (!user) {
    return (
      <div className="placeholder-page">
        <EmptyState title="登录后查看设置" description="用量统计与个人偏好将在这里展示。" action={<Button onClick={requireLogin}>登录</Button>} />
      </div>
    )
  }

  return (
    <div className="settings-page" role="main">
      <header className="settings-header">
        <h1>设置</h1>
        <p>最近 {summary?.days ?? 30} 天的模型调用统计（仅含你的数据）。</p>
      </header>
      <ModelConnectionsPanel key={user.id} />
      {busy && !summary && <p role="status">正在加载用量…</p>}
      {error && <p className="error-text" role="alert">{error}</p>}
      {summary && (
        <div className="settings-grid">
          <section className="settings-card" aria-labelledby="usage-totals">
            <h2 id="usage-totals"><BarChart3 size={18} aria-hidden="true" /> 汇总</h2>
            <dl className="settings-kv">
              <div><dt>调用次数</dt><dd>{summary.totals.calls}</dd></div>
              <div><dt>成功</dt><dd>{summary.totals.successes}</dd></div>
              <div><dt>失败</dt><dd>{summary.totals.failures}</dd></div>
              <div><dt>备用模型</dt><dd>{summary.totals.fallback_calls}</dd></div>
              <div><dt>总 tokens</dt><dd>{summary.totals.total_tokens}</dd></div>
            </dl>
          </section>
          <section className="settings-card" aria-labelledby="usage-purpose">
            <h2 id="usage-purpose">按用途</h2>
            <ul className="settings-list">
              {summary.by_purpose.map((row) => (
                <li key={row.key}>
                  <span>{purposeLabel[row.key] ?? row.key}</span>
                  <span>{row.calls} 次 · {row.total_tokens} tokens</span>
                </li>
              ))}
              {!summary.by_purpose.length && <li>暂无记录</li>}
            </ul>
          </section>
          <section className="settings-card" aria-labelledby="usage-source">
            <h2 id="usage-source">连接来源与实际模型</h2>
            <ul className="settings-list">
              {(summary.by_connection ?? []).map((row) => <li key={row.key}><span>{row.label ?? row.key}</span><span>{row.calls} 次 · {row.total_tokens} tokens</span></li>)}
              {!summary.by_connection?.length && <li>暂无记录</li>}
            </ul>
          </section>
          <section className="settings-card" aria-labelledby="usage-model">
            <h2 id="usage-model">按模型</h2>
            <ul className="settings-list">
              {summary.by_model.map((row) => (
                <li key={row.key}>
                  <span>{row.key}</span>
                  <span>{row.calls} 次{row.fallback_calls ? ` · 备用 ${row.fallback_calls}` : ''}</span>
                </li>
              ))}
              {!summary.by_model.length && <li>暂无记录</li>}
            </ul>
          </section>
        </div>
      )}
    </div>
  )
}
