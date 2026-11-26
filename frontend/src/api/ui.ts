import { ApiError } from './stream'
import type { Page } from './types'

export async function allPages<T>(load: (cursor?: string) => Promise<Page<T>>): Promise<T[]> {
  const items: T[] = []
  let cursor: string | undefined
  do {
    const page = await load(cursor)
    items.push(...page.items)
    cursor = page.next_cursor ?? undefined
  } while (cursor)
  return items
}

export function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.code === 'stream_interrupted') return '回答连接中断，请查看已保存的轮次。'
    if (error.status === 403) return '请求未获授权，请刷新页面后重试。'
    if (error.status === 404) return '该资源不存在或已不可访问。'
    if (error.status && error.status >= 500 && error.code === 'http_error') return '暂时无法连接后端，请稍后重试。'
    return error.message
  }
  return '请求失败，请检查连接后重试。'
}

const chatModelFailure: Record<string, string> = {
  model_auth_failed: '模型服务拒绝了认证。请检查 Key 和模型访问权限。',
  model_quota_unavailable: '模型服务额度不足或请求受限。',
  model_timeout: '模型服务连接超时。',
  model_service_error: '模型服务暂时不可用。',
  model_response_invalid: '模型服务返回了无法使用的内容。',
  model_response_empty: '模型未返回内容。',
  model_decryption_failed: '凭据无法解密，请检查模型连接设置。',
  model_test_required: '连接需要重新测试。',
  model_not_configured: '请配置一个可用的模型。',
  chat_model_unavailable: '所选个人模型已不可用。',
  not_found: '原连接或其他资源已不可用。',
}

export function chatModelError(code?: string | null) {
  return code ? chatModelFailure[code] : undefined
}

export function locatorLabel(locator: Record<string, unknown>) {
  const label = locationLabel(locator)
  return locator.extraction === 'vlm' ? `${label} · 视觉解析（VLM）` : label
}

function locationLabel(locator: Record<string, unknown>) {
  if (typeof locator.slide === 'number') return `第 ${locator.slide} 页幻灯片${locator.notes === true ? ' · 备注' : ''}`
  if (typeof locator.heading === 'string' && locator.heading) {
    return typeof locator.start_line === 'number' ? `${locator.heading} · 第 ${locator.start_line} 行` : locator.heading
  }
  if (typeof locator.page === 'number') return `第 ${locator.page} 页`
  if (typeof locator.image === 'number') return `图片 ${locator.image}`
  if (typeof locator.paragraph === 'number') return `段落 ${locator.paragraph}`
  if (Array.isArray(locator.table_path) && locator.table_path.length) {
    const cells = locator.table_path.map(({ table, row, column }) =>
      `表 ${table} · ${row} 行${typeof column === 'number' ? ` ${column} 列` : ''}`)
    return cells.join(' / ')
  }
  if (typeof locator.start_line === 'number') return `第 ${locator.start_line} 行`
  if (typeof locator.line_start === 'number') return `第 ${locator.line_start} 行`
  if (typeof locator.line === 'number') return `第 ${locator.line} 行`
  return '—'
}

export function safeExternalUrl(value?: string | null) {
  if (!value) return null
  try {
    const source = value.trim()
    if (!/^https?:\/\//i.test(source)) return null
    const url = new URL(source)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null
    return url.href
  } catch {
    return null
  }
}
