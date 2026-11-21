import type { WrongQuestionItem } from '@/api/types'

function safeExportFilename(value: string, fallback: string): string {
  const clean = (name: string) => name.replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/g, '_').trim().replace(/[. ]+$/g, '')
  const cleaned = clean(value)
  if (cleaned && cleaned !== '.' && cleaned !== '..') return cleaned
  const safeFallback = clean(fallback)
  return safeFallback && safeFallback !== '.' && safeFallback !== '..' ? safeFallback : 'download'
}

export function exportResponseFilename(response: Response, fallback: string): string {
  const disposition = response.headers.get('content-disposition') ?? ''
  const encoded = disposition.match(/(?:^|;)\s*filename\*\s*=\s*([^;]+)/i)?.[1]?.trim()
  if (encoded) {
    const value = encoded.replace(/^"|"$/g, '')
    const payload = value.match(/^[^']*'[^']*'(.*)$/)?.[1] ?? value
    try {
      const filename = safeExportFilename(decodeURIComponent(payload), fallback)
      if (filename) return filename
    } catch {
      // Fall through to the regular filename or fallback for malformed headers.
    }
  }

  const match = disposition.match(/(?:^|;)\s*filename\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;]*))/i)
  const filename = match?.[1]?.replace(/\\(["\\])/g, '$1') ?? match?.[2]?.trim()
  return safeExportFilename(filename || fallback, fallback)
}

export async function downloadExportResponse(response: Response, fallbackFilename: string): Promise<void> {
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = exportResponseFilename(response, fallbackFilename)
  document.body.appendChild(anchor)
  try {
    anchor.click()
  } finally {
    anchor.remove()
    URL.revokeObjectURL(url)
  }
}

export function formatWrongQuestionExport(rows: WrongQuestionItem[]): string {
  const lines = ['# 错题集']
  for (const [index, row] of rows.entries()) {
    lines.push(
      `## Q${index + 1} ${row.stem}`,
      `知识库：${row.knowledge_base_name || '未指定'}`,
    )
    if (row.options?.length) lines.push(row.options.map((option) => `- ${option}`).join('\n'))
    if (row.last_answer) lines.push(`上次作答：${row.last_answer}`)
    if (row.reference_answer) lines.push(`参考答案：${row.reference_answer}`)
    if (row.explanation) lines.push(`解析：${row.explanation}`)
    if (row.last_feedback && row.last_feedback !== row.explanation) lines.push(`反馈：${row.last_feedback}`)
    lines.push(
      `错误次数：${row.wrong_count}`,
      `下次复习：${row.next_review_at ? new Date(row.next_review_at).toLocaleString('zh-CN') : '未安排'}`,
    )
    if (row.sources?.length) {
      lines.push('### 资料依据', row.sources.map((source) =>
        `- ${source.title} · ${source.location_label}${source.quote ? `\n  ${source.quote.replace(/\n/g, '\n  ')}` : ''}`,
      ).join('\n'))
    }
  }
  return lines.join('\n\n') + '\n'
}
