import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { ChevronDown, ChevronRight, Minus, Plus, Scan } from 'lucide-react'
import type { Citation } from '@/api/types'
import { Button } from '@/components/arc/button/button'
import { MarkdownContent } from './MarkdownContent'
import { parseMindmapMarkdown } from '@/mindmap'
import type { MindmapNode } from '@/mindmap'
import styles from './MindmapView.module.css'

type Point = { x: number; y: number }
type Edge = { id: string; path: string }

function flatten(nodes: MindmapNode[]): MindmapNode[] {
  return nodes.flatMap((node) => [node, ...flatten(node.children)])
}

function visibleEdges(nodes: MindmapNode[], collapsed: Set<string>): [MindmapNode, MindmapNode][] {
  const result: [MindmapNode, MindmapNode][] = []
  for (const node of nodes) {
    if (collapsed.has(node.id)) continue
    for (const child of node.children) {
      result.push([node, child])
    }
    result.push(...visibleEdges(node.children, collapsed))
  }
  return result
}

export function MindmapView({
  contentMd,
  citations = [],
  onSelectCitation,
}: {
  contentMd: string
  citations?: Citation[]
  onSelectCitation?: (citation: Citation) => void
}) {
  const parsed = useMemo(() => parseMindmapMarkdown(contentMd), [contentMd])
  const [sourceView, setSourceView] = useState(false)
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(() => new Set())
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState<Point>({ x: 24, y: 24 })
  const [panning, setPanning] = useState(false)
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 })
  const [edges, setEdges] = useState<Edge[]>([])
  const viewportRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const panStart = useRef<{ pointerId: number; x: number; y: number; origin: Point } | null>(null)
  const nodes = useMemo(() => parsed ? flatten(parsed.roots) : [], [parsed])
  const citationMap = useMemo(() => new Map(citations.map((citation) => [citation.source_label, citation])), [citations])

  useLayoutEffect(() => {
    setSourceView(false)
    setCollapsedIds((current) => current.size ? new Set() : current)
    setZoom((current) => current === 1 ? current : 1)
    setPan((current) => current.x === 24 && current.y === 24 ? current : { x: 24, y: 24 })
  }, [contentMd])

  const measure = useCallback(() => {
    const viewport = viewportRef.current
    const canvas = canvasRef.current
    if (!viewport || !canvas || !parsed) return
    setCanvasSize((current) => current.width === canvas.offsetWidth && current.height === canvas.offsetHeight
      ? current
      : { width: canvas.offsetWidth, height: canvas.offsetHeight })
    const canvasRect = canvas.getBoundingClientRect()
    const elements = new Map<string, HTMLElement>()
    canvas.querySelectorAll<HTMLElement>('[data-map-node-id]').forEach((element) => {
      const id = element.dataset.mapNodeId
      if (id) elements.set(id, element)
    })
    const nextEdges = visibleEdges(parsed.roots, collapsedIds).flatMap(([parent, child]) => {
      const parentElement = elements.get(parent.id)
      const childElement = elements.get(child.id)
      if (!parentElement || !childElement) return []
      const parentRect = parentElement.getBoundingClientRect()
      const childRect = childElement.getBoundingClientRect()
      const x1 = (parentRect.right - canvasRect.left) / zoom
      const y1 = (parentRect.top + parentRect.height / 2 - canvasRect.top) / zoom
      const x2 = (childRect.left - canvasRect.left) / zoom
      const y2 = (childRect.top + childRect.height / 2 - canvasRect.top) / zoom
      const bend = x1 + (x2 - x1) / 2
      return [{ id: `${parent.id}:${child.id}`, path: `M ${x1} ${y1} C ${bend} ${y1}, ${bend} ${y2}, ${x2} ${y2}` }]
    })
    setEdges(nextEdges)
  }, [collapsedIds, parsed, zoom])

  useLayoutEffect(() => {
    measure()
    const viewport = viewportRef.current
    const canvas = canvasRef.current
    if (!viewport || !canvas || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(viewport)
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [measure, sourceView])

  function toggleNode(id: string) {
    setCollapsedIds((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function panStartAt(event: ReactPointerEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).closest('button')) return
    panStart.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, origin: pan }
    event.currentTarget.setPointerCapture(event.pointerId)
    setPanning(true)
  }

  function panTo(event: ReactPointerEvent<HTMLDivElement>) {
    const start = panStart.current
    if (!start || start.pointerId !== event.pointerId) return
    setPan({ x: start.origin.x + event.clientX - start.x, y: start.origin.y + event.clientY - start.y })
  }

  function finishPan(event: ReactPointerEvent<HTMLDivElement>) {
    if (panStart.current?.pointerId !== event.pointerId) return
    panStart.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    setPanning(false)
  }

  function fitCanvas() {
    const viewport = viewportRef.current
    if (!viewport || !canvasSize.width || !canvasSize.height) return
    const scale = Math.min((viewport.clientWidth - 48) / canvasSize.width, (viewport.clientHeight - 48) / canvasSize.height)
    setZoom(Math.max(0.45, Math.min(1, scale)))
    setPan({ x: 24, y: 24 })
  }

  function renderNode(node: MindmapNode) {
    const collapsed = collapsedIds.has(node.id)
    return <li key={node.id} className={styles.branch}>
      <div className={styles.nodeCard} data-map-node-id={node.id}>
        {node.children.length ? <Button
          type="button"
          variant="ghost"
          size="sm"
          className={styles.nodeToggle}
          aria-expanded={!collapsed}
          aria-label={`${collapsed ? '展开' : '折叠'}：${node.text}`}
          onClick={() => toggleNode(node.id)}
        >
          {collapsed ? <ChevronRight size={14} aria-hidden /> : <ChevronDown size={14} aria-hidden />}
          <span>{node.text}</span>
        </Button> : <span className={styles.nodeText}>{node.text}</span>}
        {node.citations.length > 0 && <div className={styles.citations} aria-label="节点来源">
          {node.citations.map((number) => {
            const label = `S${number}`
            const citation = citationMap.get(label)
            return citation && onSelectCitation
              ? <Button
                  key={label}
                  type="button"
                  size="sm"
                  variant="secondary"
                  className={styles.citation}
                  aria-label={`查看引用 ${label}：${citation.title}`}
                  onClick={() => onSelectCitation(citation)}
                >[{label}]</Button>
              : <span key={label} className={styles.citationStatic}>[{label}]</span>
          })}
        </div>}
        {node.children.length > 0 && <span className={styles.childCount}>{node.children.length} 个分支</span>}
      </div>
      {node.children.length > 0 && !collapsed && <ul className={styles.descendants}>{node.children.map(renderNode)}</ul>}
    </li>
  }

  if (!parsed) return <div className={styles.fallback} data-mindmap-fallback="true">
    <p role="status">未识别到多层知识结构，已切换为 Markdown 原文。</p>
    <MarkdownContent citations={citations} onSelectCitation={onSelectCitation}>{contentMd}</MarkdownContent>
  </div>

  if (sourceView) return <div className={styles.wrapper}>
    <div className={styles.toolbar}>
      <Button type="button" size="sm" variant="secondary" onClick={() => setSourceView(false)}>脑图视图</Button>
    </div>
    <div className={styles.source}>
      <MarkdownContent citations={citations} onSelectCitation={onSelectCitation}>{contentMd}</MarkdownContent>
    </div>
  </div>

  return <div className={styles.wrapper}>
    <div className={styles.toolbar} aria-label="知识导图工具栏">
      <div className={styles.toolGroup} aria-label="缩放">
        <Button type="button" size="sm" variant="secondary" aria-label="缩小" onClick={() => setZoom((value) => Math.max(0.45, Math.round((value - 0.1) * 100) / 100))}><Minus size={14} aria-hidden /></Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => { setZoom(1); setPan({ x: 24, y: 24 }) }}>{Math.round(zoom * 100)}%</Button>
        <Button type="button" size="sm" variant="secondary" aria-label="放大" onClick={() => setZoom((value) => Math.min(2, Math.round((value + 0.1) * 100) / 100))}><Plus size={14} aria-hidden /></Button>
        <Button type="button" size="sm" variant="ghost" onClick={fitCanvas}><Scan size={14} aria-hidden /> 适配画布</Button>
      </div>
      <div className={styles.toolGroup} aria-label="节点折叠">
        <Button type="button" size="sm" variant="ghost" onClick={() => setCollapsedIds(new Set())}>全部展开</Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setCollapsedIds(new Set(nodes.filter((node) => node.children.length).map((node) => node.id)))}>全部折叠</Button>
      </div>
      <Button type="button" size="sm" variant="secondary" onClick={() => setSourceView(true)}>Markdown 原文</Button>
    </div>

    <div
      ref={viewportRef}
      className={`${styles.viewport} ${panning ? styles.panning : ''}`}
      role="region"
      aria-label="可拖动画布中的知识导图"
      tabIndex={0}
      onPointerDown={panStartAt}
      onPointerMove={panTo}
      onPointerUp={finishPan}
      onPointerCancel={finishPan}
    >
      <div ref={canvasRef} className={styles.canvas} style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}>
        <svg className={styles.connections} viewBox={`0 0 ${canvasSize.width || 1} ${canvasSize.height || 1}`} aria-hidden="true">
          {edges.map((edge) => <path key={edge.id} d={edge.path} />)}
        </svg>
        <ul className={styles.roots}>{parsed.roots.map(renderNode)}</ul>
      </div>
    </div>
    {parsed.remainder && <div className={styles.remainder}>
      <MarkdownContent citations={citations} onSelectCitation={onSelectCitation}>{parsed.remainder}</MarkdownContent>
    </div>}
  </div>
}
