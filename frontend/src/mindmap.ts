export interface MindmapNode {
  id: string
  text: string
  citations: number[]
  children: MindmapNode[]
}

export interface ParsedMindmapMarkdown {
  roots: MindmapNode[]
  remainder: string
}

type Bullet = { lineIndex: number; indent: number; text: string }

function headingAt(line: string) {
  const match = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line.trim())
  return match ? { depth: match[1].length, text: match[2].trim() } : null
}

function bulletAt(line: string, lineIndex: number): Bullet | null {
  const match = /^(\s*)([-*+])\s+(.+?)\s*$/.exec(line)
  if (!match) return null
  const indent = match[1].replace(/\t/g, '    ').length
  return { lineIndex, indent, text: match[3] }
}

function listBlocks(lines: string[], start: number, end: number): Bullet[][] {
  const blocks: Bullet[][] = []
  let current: Bullet[] = []
  let fence: string | null = null
  for (let index = start; index < end; index += 1) {
    const line = lines[index]
    const fenceMatch = /^\s*(```+|~~~+)/.exec(line)
    if (fenceMatch) {
      if (!fence) fence = fenceMatch[1].slice(0, 3)
      else if (fenceMatch[1].startsWith(fence)) fence = null
      continue
    }
    if (fence) continue
    const bullet = bulletAt(line, index)
    if (bullet) {
      current.push(bullet)
      continue
    }
    if (!line.trim() && current.length) continue
    if (current.length) {
      blocks.push(current)
      current = []
    }
  }
  if (current.length) blocks.push(current)
  return blocks
}

function buildTree(bullets: Bullet[]): MindmapNode[] {
  const roots: MindmapNode[] = []
  const stack: { indent: number; node: MindmapNode }[] = []
  let nextId = 1
  for (const bullet of bullets) {
    const citations: number[] = []
    const text = bullet.text.replace(/\[S(\d+)\]/g, (_match, ordinal: string) => {
      const number = Number(ordinal)
      if (!citations.includes(number)) citations.push(number)
      return ''
    }).replace(/\s{2,}/g, ' ').trim()
    if (!text) continue
    while (stack.length && stack[stack.length - 1].indent >= bullet.indent) stack.pop()
    const node: MindmapNode = { id: `mindmap-node-${nextId++}`, text, citations, children: [] }
    const parent = stack[stack.length - 1]?.node
    if (parent) parent.children.push(node)
    else roots.push(node)
    stack.push({ indent: bullet.indent, node })
  }
  return roots
}

function nodeCount(nodes: MindmapNode[]): number {
  return nodes.reduce((count, node) => count + 1 + nodeCount(node.children), 0)
}

/** Parses the first useful hierarchical unordered list, preferring the “知识层级树” section. */
export function parseMindmapMarkdown(contentMd: string): ParsedMindmapMarkdown | null {
  const lines = contentMd.split(/\r?\n/)
  const headingIndex = lines.findIndex((line) => {
    const heading = headingAt(line)
    return heading?.text.replace(/\s+/g, '') === '知识层级树'
  })
  const heading = headingIndex >= 0 ? headingAt(lines[headingIndex]) : null
  let sectionStart = headingIndex >= 0 ? headingIndex + 1 : 0
  let sectionEnd = lines.length
  if (heading) {
    for (let index = sectionStart; index < lines.length; index += 1) {
      const nextHeading = headingAt(lines[index])
      if (nextHeading && nextHeading.depth <= heading.depth) {
        sectionEnd = index
        break
      }
    }
  }

  const candidateBlocks = listBlocks(lines, sectionStart, sectionEnd)
  let selected: { roots: MindmapNode[]; bullets: Bullet[] } | null = null
  for (const bullets of candidateBlocks) {
    const roots = buildTree(bullets)
    if (nodeCount(roots) >= 2 && roots.some((node) => node.children.length > 0)) {
      selected = { roots, bullets }
      break
    }
  }
  if (!selected) return null

  const removed = new Set(selected.bullets.map((bullet) => bullet.lineIndex))
  if (headingIndex >= 0) removed.add(headingIndex)
  const remainder = lines.filter((_line, index) => !removed.has(index)).join('\n').trim()
  return { roots: selected.roots, remainder }
}
