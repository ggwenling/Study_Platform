import type { ApiErrorBody, ChatEvent } from './types'

export class ApiError extends Error {
  code: string
  request_id?: string
  retryable: boolean
  turn_id?: string
  status?: number

  constructor(body: ApiErrorBody, status?: number) {
    super(body.message)
    this.name = 'ApiError'
    this.code = body.code
    this.request_id = body.request_id
    this.retryable = body.retryable
    this.turn_id = body.turn_id
    this.status = status
  }
}

export function errorFromResponse(body: unknown, status: number): ApiError {
  if (body && typeof body === 'object' && 'code' in body && 'message' in body) {
    return new ApiError(body as ApiErrorBody, status)
  }
  return new ApiError({ code: 'http_error', message: `HTTP ${status}`, retryable: false }, status)
}

export async function* parseChatStream(stream: ReadableStream<Uint8Array>): AsyncGenerator<Exclude<ChatEvent, { type: 'error' }>> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let data: string[] = []
  const dispatch = () => {
    if (!data.length) return undefined
    const event = JSON.parse(data.join('\n')) as ChatEvent
    data = []
    if (event.type === 'error') throw new ApiError(event)
    return event
  }
  try {
    let ended = false
    while (!ended) {
      const result = await reader.read()
      ended = result.done
      buffer += decoder.decode(result.value, { stream: !ended })
      // Delay a trailing CR until the next chunk so CRLF is one line break.
      let match: RegExpExecArray | null
      const lineBreak = /\r\n|\r|\n/g
      let start = 0
      while ((match = lineBreak.exec(buffer))) {
        if (!ended && match[0] === '\r' && lineBreak.lastIndex === buffer.length) break
        const line = buffer.slice(start, match.index)
        start = lineBreak.lastIndex
        if (!line) {
          const event = dispatch()
          if (event) yield event
        } else if (line.startsWith('data:')) {
          data.push(line.slice(5).replace(/^ /, ''))
        } else if (line === 'data') {
          data.push('')
        }
      }
      buffer = buffer.slice(start)
    }
    if (buffer.startsWith('data:')) data.push(buffer.slice(5).replace(/^ /, ''))
    else if (buffer === 'data') data.push('')
    const event = dispatch()
    if (event) yield event
  } finally {
    try { await reader.cancel() } catch { /* Preserve a read or parse error. */ }
    reader.releaseLock()
  }
}
