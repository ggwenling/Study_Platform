import { api } from '@/api/client'
import type { Conversation, Message, Scope } from '@/api/types'
import { allPages } from '@/api/ui'

export function scopeSummaryText(scope: Scope): string {
  const parts = [scope.knowledge_base_name]
  if (scope.intent === 'summary') parts.push('摘要提炼')
  if (scope.selected_material_ids?.length) parts.push(`限定 ${scope.selected_material_ids.length} 份资料`)
  parts.push(scope.answer_mode === 'with_general' ? '通用知识补充' : '仅知识库')
  return parts.join(' · ')
}

export function formatConversationMarkdown(title: string | null | undefined, messages: Message[]): string {
  const safeTitle = (title ?? '').trim() || '未命名对话'
  const blocks = messages
    .filter((message) => message.role !== 'system_summary')
    .sort((a, b) => a.sequence_no - b.sequence_no)
    .map((message) => {
      const heading = message.role === 'user' ? '## 提问' : '## 知循回答'
      const scopeLine = message.scope ? `\n\n> 范围：${scopeSummaryText(message.scope)}` : ''
      const body = (message.content || '').trim() || '_（无内容）_'
      const refs = message.role === 'assistant' && message.citations.length > 0
        ? '\n\n**引用依据**\n' + message.citations.map((citation) => {
          const detail = citation.availability === 'available' ? citation.location_label : '来源已不可用'
          return `- [${citation.source_label}] ${citation.title}（${detail}）`
        }).join('\n')
        : ''
      return `${heading}${scopeLine}\n\n${body}${refs}`
    })
  if (!blocks.length) {
    return `# ${safeTitle}\n\n_（暂无消息记录）_\n`
  }
  return `# ${safeTitle}\n\n${blocks.join('\n\n---\n\n')}\n`
}

export function sanitizeMarkdownFilename(title: string | null | undefined): string {
  const cleaned = (title ?? '')
    .trim()
    .replace(/[\\/:*?"<>|]/g, '-')
    .replace(/\s+/g, ' ')
    .slice(0, 80)
    .trim()
  const base = cleaned || '未命名对话'
  return base.toLowerCase().endsWith('.md') ? base : `${base}.md`
}

export function downloadMarkdownFile(filename: string, content: string): void {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

export async function exportConversationMarkdown(
  conversationId: string,
  known?: { conversation?: Conversation | null; messages?: Message[] },
): Promise<void> {
  const [conversation, messages] = await Promise.all([
    known?.conversation ? Promise.resolve(known.conversation) : api.conversation(conversationId),
    known?.messages ? Promise.resolve(known.messages) : allPages((cursor) => api.messages(conversationId, cursor)),
  ])
  const title = conversation?.title || messages.find((item) => item.role === 'user')?.content.slice(0, 80) || '未命名对话'
  const markdown = formatConversationMarkdown(title, messages)
  downloadMarkdownFile(sanitizeMarkdownFilename(title), markdown)
}
