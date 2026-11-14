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
