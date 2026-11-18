import { useEffect, useMemo, useState } from 'react'
import Markdown, { type Components, type Options } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import type { Citation } from '@/api/types'
import { Tooltip } from '@/components/arc/tooltip/tooltip'

const SKIP_NODE_TYPES = new Set([
  'code',
  'inlineCode',
  'math',
  'inlineMath',
  'link',
  'linkReference',
  'definition',
  'html',
])

const CITATION_TOKEN_RE = /\[(S\d+)\]/g

type MdastNode = {
  type: string
  value?: string
  children?: MdastNode[]
  data?: Record<string, unknown>
}

export function remarkCitationRefs() {
  return (tree: MdastNode) => {
    function walk(node: MdastNode) {
      if (SKIP_NODE_TYPES.has(node.type) || !Array.isArray(node.children)) return
      const nextChildren: MdastNode[] = []
      let mutated = false
      for (const child of node.children) {
        if (child.type === 'text' && typeof child.value === 'string' && child.value.includes('[S')) {
          CITATION_TOKEN_RE.lastIndex = 0
          let cursor = 0
          let match: RegExpExecArray | null
          while ((match = CITATION_TOKEN_RE.exec(child.value)) !== null) {
            mutated = true
            if (match.index > cursor) {
              nextChildren.push({ type: 'text', value: child.value.slice(cursor, match.index) })
            }
            const label = match[1]
            nextChildren.push({
              type: 'citationRef',
              value: label,
              data: {
                hName: 'sup',
                hProperties: { 'data-citation-source': label },
                hChildren: [{ type: 'text', value: label }],
              },
            })
            cursor = match.index + match[0].length
          }
          if (cursor === 0) {
            nextChildren.push(child)
          } else if (cursor < child.value.length) {
            nextChildren.push({ type: 'text', value: child.value.slice(cursor) })
          }
        } else {
          walk(child)
          nextChildren.push(child)
        }
      }
      if (mutated) node.children = nextChildren
    }
    walk(tree)
  }
}

type PluggableList = NonNullable<Options['rehypePlugins']>
let highlightPromise: Promise<PluggableList[number]> | null = null
let katexPromise: Promise<PluggableList[number]> | null = null

function loadHighlight() {
  if (!highlightPromise) {
    highlightPromise = Promise.all([
      import('rehype-highlight'),
      import('highlight.js/styles/github-dark.css'),
    ]).then(([mod]) => mod.default)
  }
  return highlightPromise
}

function loadKatex() {
  if (!katexPromise) {
    katexPromise = Promise.all([
      import('rehype-katex'),
      import('katex/dist/katex.min.css'),
    ]).then(([mod]) => mod.default)
  }
  return katexPromise
}

export function MarkdownContent({
  children,
  citations,
  onSelectCitation,
}: {
  children: string
  citations?: Citation[]
  onSelectCitation?: (citation: Citation) => void
}) {
  const hasMath = children.includes('$')
  const [highlightPlugin, setHighlightPlugin] = useState<PluggableList[number] | null>(null)
  const [katexPlugin, setKatexPlugin] = useState<PluggableList[number] | null>(null)

  useEffect(() => {
    let active = true
    void loadHighlight().then((plugin) => {
      if (active) setHighlightPlugin(() => plugin)
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!hasMath || katexPlugin) return
    let active = true
    void loadKatex().then((plugin) => {
      if (active) setKatexPlugin(() => plugin)
    })
    return () => { active = false }
  }, [hasMath, katexPlugin])

  const rehypePlugins = useMemo<PluggableList>(() => {
    const list: PluggableList = []
    if (katexPlugin) list.push(katexPlugin)
    if (highlightPlugin) list.push(highlightPlugin)
    return list
  }, [highlightPlugin, katexPlugin])

  const citationMap = useMemo(() => {
    const map = new Map<string, Citation>()
    for (const item of citations ?? []) map.set(item.source_label, item)
    return map
  }, [citations])

  const components = useMemo<Components>(() => ({
    sup({ children: supChildren, ...props }) {
      const sourceLabel = (props as Record<string, unknown>)['data-citation-source']
      if (typeof sourceLabel !== 'string') return <sup {...props}>{supChildren}</sup>
      const citation = citationMap.get(sourceLabel)
      if (!citation || !onSelectCitation) {
        return <sup className="inline-citation inline-citation-static">{sourceLabel}</sup>
      }
      const excerpt = citation.availability === 'available' && citation.quote
        ? (citation.quote.length > 96 ? `${citation.quote.slice(0, 96)}…` : citation.quote)
        : ''
      const preview = (
        <span className="inline-citation-tooltip">
          <strong>
            {citation.title} · {citation.availability === 'available' ? citation.location_label : '来源已不可用'}
          </strong>
          {excerpt && <span>{excerpt}</span>}
        </span>
      )
      return (
        <Tooltip content={preview}>
          <button
            type="button"
            className="inline-citation"
            aria-label={`查看引用 ${sourceLabel}：${citation.title}`}
            onClick={() => onSelectCitation(citation)}
          >
            {sourceLabel}
          </button>
        </Tooltip>
      )
    },
  }), [citationMap, onSelectCitation])

  return (
    <Markdown
      remarkPlugins={[remarkGfm, remarkMath, remarkCitationRefs]}
      rehypePlugins={rehypePlugins}
      components={components}
    >
      {children}
    </Markdown>
  )
}
