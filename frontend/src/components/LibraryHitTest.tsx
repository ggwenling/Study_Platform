import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { FileText, Pencil, Radar, SearchCheck, TriangleAlert } from 'lucide-react'
import { api } from '@/api/client'
import type { HitTestItem, HitTestResponse, ModelConnection } from '@/api/types'
import { Button } from '@/components/arc/button/button'
import { Input } from '@/components/arc/input/input'
import { Select } from '@/components/arc/select/select'
import { Switch } from '@/components/arc/switch/switch'
import { useWorkspace } from '../WorkspaceData'
import { FragmentEditDialog } from './LibraryFragments'

const topKOptions = Array.from({ length: 20 }, (_, index) => index + 1).map((value) => ({
  value: String(value), label: `Top ${value}`,
}))

const lexicalWeightOptions = [
  { value: '0', label: '0（纯向量检索）' },
  { value: '0.4', label: '0.4（推荐混合）' },
  { value: '1', label: '1.0（词法增强）' },
]

const similarityOptions = [0.2, 0.35, 0.5, 0.65, 0.8, 0.9].map((value) => ({
  value: String(value), label: `≥ ${value.toFixed(2)}`,
}))
const candidateOptions = [5, 10, 20, 30, 50].map((value) => ({ value: String(value), label: `Top ${value}` }))
const defaultRetrievalSettings = {
  top_k: 10, lexical_weight: 0, min_similarity: 0.35,
  rerank_enabled: false, rerank_candidate_k: 20,
}

export function LibraryHitTest({
  kbId,
  readyCount,
  onOpenSource,
  onFragmentUpdated,
}: {
  kbId: string
  readyCount: number
  onOpenSource: (item: HitTestItem) => void
  onFragmentUpdated?: () => void
}) {
  const { notify, report, capabilities } = useWorkspace()
  const reportRef = useRef(report)
  reportRef.current = report
  const [queryText, setQueryText] = useState('')
  const [topK, setTopK] = useState(String(defaultRetrievalSettings.top_k))
  const [lexicalWeight, setLexicalWeight] = useState(String(defaultRetrievalSettings.lexical_weight))
  const [minSimilarity, setMinSimilarity] = useState(String(defaultRetrievalSettings.min_similarity))
  const [rerankEnabled, setRerankEnabled] = useState(false)
  const [rerankCandidateK, setRerankCandidateK] = useState(String(defaultRetrievalSettings.rerank_candidate_k))
  const [rerankConnectionId, setRerankConnectionId] = useState('')
  const [rerankConnections, setRerankConnections] = useState<ModelConnection[]>([])
  const [settingsLoaded, setSettingsLoaded] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)
  const [inheritsDefaults, setInheritsDefaults] = useState(true)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<HitTestResponse | null>(null)
  const [editingItem, setEditingItem] = useState<HitTestItem | null>(null)

  useEffect(() => {
    let active = true
    const defaults = { ...defaultRetrievalSettings, ...capabilities?.retrieval_defaults }
    void Promise.allSettled([api.knowledgeBase(kbId), api.modelConnections()]).then(([kbResult, connectionsResult]) => {
      if (!active) return
      const saved = kbResult.status === 'fulfilled' ? kbResult.value.retrieval_settings : null
      if (kbResult.status === 'rejected') reportRef.current(kbResult.reason)
      const rows = connectionsResult.status === 'fulfilled'
        ? connectionsResult.value.items.filter((row) => row.kind === 'rerank' && row.status === 'ready')
        : []
      if (connectionsResult.status === 'rejected') reportRef.current(connectionsResult.reason)
      setRerankConnections(rows)
      setTopK(String(saved?.top_k ?? defaults.top_k))
      setLexicalWeight(String(saved?.lexical_weight ?? defaults.lexical_weight))
      setMinSimilarity(String(saved?.min_similarity ?? defaults.min_similarity))
      setRerankEnabled(saved?.rerank_enabled ?? false)
      setRerankCandidateK(String(saved?.rerank_candidate_k ?? defaults.rerank_candidate_k))
      setRerankConnectionId(saved?.rerank_connection_id ?? 'default')
      setInheritsDefaults(!saved)
      setSettingsLoaded(true)
    })
    return () => { active = false }
  }, [kbId, capabilities?.retrieval_defaults])

  if (!api.workshop?.hitTest) return null

  async function executeHitTest(cleaned: string, clearOnError = false) {
    setBusy(true)
    try {
      const response = await api.workshop.hitTest(kbId, {
        query: cleaned,
        top_k: Number(topK),
        lexical_weight: Number(lexicalWeight),
        min_similarity: Number(minSimilarity),
        rerank_enabled: rerankEnabled,
        rerank_connection_id: rerankConnectionId === 'default' ? null : rerankConnectionId,
        rerank_candidate_k: Number(rerankCandidateK),
      })
      setResult(response)
    } catch (error) {
      if (clearOnError) setResult(null)
      report(error)
    } finally {
      setBusy(false)
    }
  }

  async function runTest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const cleaned = queryText.trim()
    if (!cleaned || busy) return
    if (readyCount === 0) {
      notify('请先导入并完成至少 1 份资料的索引。')
      return
    }
    await executeHitTest(cleaned)
  }

  async function saveRetrievalSettings() {
    setSavingSettings(true)
    try {
      const response = await api.updateRetrievalSettings(kbId, {
        top_k: Number(topK),
        lexical_weight: Number(lexicalWeight),
        min_similarity: Number(minSimilarity),
        rerank_enabled: rerankEnabled,
        rerank_connection_id: rerankConnectionId === 'default' ? null : rerankConnectionId,
        rerank_candidate_k: Number(rerankCandidateK),
      })
      setInheritsDefaults(!response.retrieval_settings)
      notify('已保存为本知识库默认检索配置。')
    } catch (error) { report(error) } finally { setSavingSettings(false) }
  }

  return (
    <section className="library-panel library-hittest-panel" aria-label="检索命中测试">
      <header className="library-section-heading">
        <Radar size={22} />
        <h2>检索命中测试</h2>
        <p>实时诊断向量余弦相似度、Jieba 词法 IDF 排名与 RRF 混合融合得分</p>
      </header>
      <form className="hit-test-form" onSubmit={(event) => void runTest(event)}>
        <div className="hit-test-query">
          <Input
            label="测试提问"
            placeholder="输入测试问题或专业术语，检验哪些片段会被召回…"
            maxLength={1000}
            value={queryText}
            onChange={(event) => setQueryText(event.target.value)}
          />
        </div>
        <Select
          label="召回数量"
          value={topK}
          onValueChange={setTopK}
          options={topKOptions}
        />
        <Select
          label="混合词法权重"
          value={lexicalWeight}
          onValueChange={setLexicalWeight}
          options={lexicalWeightOptions}
        />
        <Select
          label="最低相似度"
          value={minSimilarity}
          onValueChange={setMinSimilarity}
          options={similarityOptions}
        />
        <div className="hit-test-rerank-toggle">
          <span>启用 Rerank 重排</span>
          <Switch aria-label="启用 Rerank 重排" checked={rerankEnabled} onCheckedChange={setRerankEnabled} />
        </div>
        <Select
          label="Rerank 连接"
          value={rerankConnectionId}
          onValueChange={setRerankConnectionId}
          options={[{ value: 'default', label: '使用账户默认连接' }, ...rerankConnections.map((row) => ({ value: row.id, label: row.name }))]}
        />
        <Select
          label="Rerank 候选数"
          value={rerankCandidateK}
          onValueChange={setRerankCandidateK}
          options={candidateOptions}
        />
        <Button type="submit" loading={busy} disabled={busy || !queryText.trim()}>
          <SearchCheck size={16} />
          开始命中测试
        </Button>
      </form>

      <div className="hit-test-settings-actions">
        <span className="muted">{inheritsDefaults ? '当前继承系统默认检索配置' : '正在使用本知识库的检索配置'}</span>
        <Button type="button" variant="secondary" size="sm" loading={savingSettings} disabled={!settingsLoaded || savingSettings} onClick={() => void saveRetrievalSettings()}>
          保存为本库默认检索配置
        </Button>
      </div>
      {rerankEnabled && rerankConnections.length === 0 && (
        <p className="hit-test-rerank-error" role="status">请先在设置页添加并测试一个 Rerank 连接。</p>
      )}

      {result && (
        <div className="hit-test-results">
          <div className="hit-test-meta">
            <span>
              分词词项：
              {result.query_terms.length > 0
                ? result.query_terms.map((term) => <code key={term} className="hit-test-term">{term}</code>)
                : <span className="muted">无独立词项</span>}
            </span>
            <span className="muted numeric">
              向量阈值 ≥ {result.min_similarity_threshold.toFixed(2)} · 词法权重 {result.lexical_weight}
            </span>
          </div>

          {result.rerank_error && <p className="hit-test-rerank-error" role="alert">Rerank 未能完成，当前展示 RRF 排序：{result.rerank_error}</p>}

          {result.items.length === 0 ? (
            <p className="muted library-empty">没有召回到已启用检索的片段。</p>
          ) : (
            <ol className="hit-test-list">
              {result.items.map((item, idx) => (
                <li key={item.chunk_id} className="hit-test-card" data-below-threshold={!item.above_threshold || undefined}>
                  <div className="hit-test-card-head">
                    <span className="hit-test-rank numeric">#{idx + 1}</span>
                    <button
                      type="button"
                      className="fragment-source"
                      onClick={() => onOpenSource(item)}
                      title={item.title}
                    >
                      <FileText size={15} />
                      <span>{item.title}</span>
                      {item.location_label && item.location_label !== '—' && (
                        <small className="muted">· {item.location_label}</small>
                      )}
                    </button>
                    <div className="hit-test-badges numeric">
                      <span className="library-status">RRF {item.rrf_score.toFixed(4)}</span>
                      {result.rerank_enabled && <span className="library-status">
                        RRF #{item.rrf_rank ?? idx + 1} → 重排 #{item.rerank_rank ?? '—'}
                        {item.rerank_score !== null && item.rerank_score !== undefined ? ` (${item.rerank_score.toFixed(3)})` : ''}
                      </span>}
                      <span className="library-status">
                        向量 {item.vector_similarity !== null ? item.vector_similarity.toFixed(3) : '—'}
                        {item.vector_rank !== null ? ` (#${item.vector_rank})` : ''}
                      </span>
                      {!item.above_threshold && (
                        <span className="library-status hit-test-warn">
                          <TriangleAlert size={12} />低于阈值
                        </span>
                      )}
                      <span className="library-status">
                        词法 {item.lexical_rank !== null ? `#${item.lexical_rank} (IDF ${item.lexical_score?.toFixed(2)})` : '未命中'}
                      </span>
                    </div>
                  </div>

                  {item.matched_terms.length > 0 && (
                    <div className="hit-test-matched">
                      <small className="muted">命中词项：</small>
                      {item.matched_terms.map((term) => (
                        <code key={term} className="hit-test-term">{term}</code>
                      ))}
                    </div>
                  )}

                  <p className="hit-test-excerpt">{item.text}</p>

                  <div className="hit-test-card-actions">
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => setEditingItem(item)}
                    >
                      <Pencil size={14} />修补片段
                    </Button>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}

      <FragmentEditDialog
        kbId={kbId}
        target={editingItem ? { id: editingItem.chunk_id, title: editingItem.title, text: editingItem.text } : null}
        onClose={() => setEditingItem(null)}
        onSaved={() => {
          setEditingItem(null)
          onFragmentUpdated?.()
          const activeQuery = result?.query || queryText.trim()
          if (activeQuery) {
            void executeHitTest(activeQuery, true)
          }
        }}
      />
    </section>
  )
}
