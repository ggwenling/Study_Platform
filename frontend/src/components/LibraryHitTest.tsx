import { useState } from 'react'
import type { FormEvent } from 'react'
import { FileText, Pencil, Radar, SearchCheck, TriangleAlert } from 'lucide-react'
import { api } from '@/api/client'
import type { HitTestItem, HitTestResponse } from '@/api/types'
import { Button } from '@/components/arc/button/button'
import { Input } from '@/components/arc/input/input'
import { Select } from '@/components/arc/select/select'
import { useWorkspace } from '../WorkspaceData'
import { FragmentEditDialog } from './LibraryFragments'

const topKOptions = [
  { value: '3', label: 'Top 3' },
  { value: '6', label: 'Top 6' },
  { value: '10', label: 'Top 10' },
]

const lexicalWeightOptions = [
  { value: '0', label: '0（纯向量检索）' },
  { value: '0.4', label: '0.4（推荐混合）' },
  { value: '1', label: '1.0（词法增强）' },
]

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
  const { notify, report } = useWorkspace()
  const [queryText, setQueryText] = useState('')
  const [topK, setTopK] = useState('6')
  const [lexicalWeight, setLexicalWeight] = useState('0.4')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<HitTestResponse | null>(null)
  const [editingItem, setEditingItem] = useState<HitTestItem | null>(null)

  if (!api.workshop?.hitTest) return null

  async function runTest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const cleaned = queryText.trim()
    if (!cleaned || busy) return
    if (readyCount === 0) {
      notify('请先导入并完成至少 1 份资料的索引。')
      return
    }
    setBusy(true)
    try {
      const response = await api.workshop.hitTest(kbId, {
        query: cleaned,
        top_k: Number(topK),
        lexical_weight: Number(lexicalWeight),
      })
      setResult(response)
    } catch (error) {
      report(error)
    } finally {
      setBusy(false)
    }
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
        <Button type="submit" loading={busy} disabled={busy || !queryText.trim()}>
          <SearchCheck size={16} />
          开始命中测试
        </Button>
      </form>

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
        onSaved={(updated) => {
          setResult((prev) =>
            prev
              ? {
                  ...prev,
                  items: prev.items.map((it) =>
                    it.chunk_id === updated.id ? { ...it, text: updated.text } : it,
                  ),
                }
              : prev,
          )
          setEditingItem(null)
          onFragmentUpdated?.()
        }}
      />
    </section>
  )
}
