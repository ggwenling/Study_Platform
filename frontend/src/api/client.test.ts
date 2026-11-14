import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, retryTurn, streamChat, uploadMaterial } from './client'
import { ApiError, parseChatStream } from './stream'

function byteStream(text: string): ReadableStream<Uint8Array> {
  const bytes = new TextEncoder().encode(text)
  return new ReadableStream({ start(controller) { for (const byte of bytes) controller.enqueue(new Uint8Array([byte])); controller.close() } })
}

afterEach(() => { vi.unstubAllGlobals() })

describe('chat stream', () => {
  it('decodes split UTF-8, CRLF, heartbeats and an unterminated final frame', async () => {
    const stream = byteStream(': heartbeat\r\n\r\ndata: {"type":"delta","turn_id":"t","delta":"中文"}\r\n\r\ndata: {"type":"stopped","turn_id":"t"}')
    const events = []
    for await (const event of parseChatStream(stream)) events.push(event)
    expect(events).toEqual([{ type: 'delta', turn_id: 't', delta: '中文' }, { type: 'stopped', turn_id: 't' }])
  })
  it('throws an ApiError for an error frame and keeps the turn id', async () => {
    const stream = byteStream('data: {"type":"error","turn_id":"t","code":"failed","message":"失败","retryable":true}\n\n')
    await expect(async () => { for await (const _ of parseChatStream(stream)) { /* consume */ } }).rejects.toMatchObject({ name: 'ApiError', code: 'failed', turn_id: 't', retryable: true })
  })
  it('sends credentials, CSRF and idempotency key and reports HTTP errors', async () => {
    vi.stubGlobal('document', { cookie: 'study_csrf=abc%20123' })
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 'denied', message: 'Denied', retryable: false }), { status: 401 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(async () => { for await (const _ of streamChat({ knowledge_base_id: 'k', answer_mode: 'knowledge_only', intent: 'qa', content: 'x' }, 'key')) { /* consume */ } }).rejects.toBeInstanceOf(ApiError)
    const options = fetchMock.mock.calls[0][1]
    expect(options.credentials).toBe('include')
    expect(options.headers.get('X-CSRF-Token')).toBe('abc 123')
    expect(options.headers.get('Idempotency-Key')).toBe('key')
  })
})

describe('HTTP client', () => {
  it('sends CSRF for writes but not login', async () => {
    vi.stubGlobal('document', { cookie: 'study_csrf=token' })
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response('{}')))
    vi.stubGlobal('fetch', fetchMock)
    await api.createKnowledgeBase({ name: 'test' })
    await api.login({ email: 'a', password: 'b' })
    expect(fetchMock.mock.calls[0][1].headers.get('X-CSRF-Token')).toBe('token')
    expect(fetchMock.mock.calls[1][1].headers.get('X-CSRF-Token')).toBeNull()
  })
  it('reports actual XHR upload progress and returns the processing job', async () => {
    vi.stubGlobal('document', { cookie: 'study_csrf=token' })
    let xhr: MockXHR
    class MockXHR {
      upload = { onprogress: undefined as ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | undefined }
      withCredentials = false
      status = 202
      responseText = JSON.stringify({ material_id: 'm', version_id: 'v', job_id: 'j', status: 'queued' })
      onload?: () => void
      onerror?: () => void
      onabort?: () => void
      headers: Record<string, string> = {}
      method = ''; url = ''; body?: FormData
      constructor() { xhr = this }
      open(method: string, url: string) { this.method = method; this.url = url }
      setRequestHeader(name: string, value: string) { this.headers[name] = value }
      send(body: FormData) { this.body = body }
      abort() { this.onabort?.() }
    }
    vi.stubGlobal('XMLHttpRequest', MockXHR)
    const progress = vi.fn()
    const result = uploadMaterial('kb', new File(['abc'], 'a.txt'), progress)
    xhr!.upload.onprogress?.({ lengthComputable: true, loaded: 3, total: 3 })
    xhr!.onload?.()
    expect(progress).toHaveBeenCalledWith(3, 3)
    expect(xhr!.withCredentials).toBe(true)
    expect(xhr!.headers['X-CSRF-Token']).toBe('token')
    expect(xhr!.body?.get('file')).toBeInstanceOf(File)
    await expect(result).resolves.toMatchObject({ job_id: 'j', status: 'queued' })
  })
})


describe('stream interruption and retry', () => {
  it('reports a disconnected stream with its turn id without retrying', async () => {
    vi.stubGlobal('document', { cookie: 'study_csrf=token' })
    const fetchMock = vi.fn().mockResolvedValue(new Response(byteStream('data: {"type":"delta","turn_id":"t","delta":"hi"}\n\n')))
    vi.stubGlobal('fetch', fetchMock)
    const received: unknown[] = []
    await expect(async () => { for await (const event of streamChat({ knowledge_base_id: 'k', answer_mode: 'knowledge_only', intent: 'qa', content: 'x' }, 'key')) received.push(event) }).rejects.toMatchObject({ code: 'stream_interrupted', turn_id: 't', retryable: true })
    expect(received).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it('sends a retry only when requested', async () => {
    vi.stubGlobal('document', { cookie: 'study_csrf=token' })
    const fetchMock = vi.fn().mockResolvedValue(new Response(byteStream('data: {"type":"stopped","turn_id":"t"}\n\n')))
    vi.stubGlobal('fetch', fetchMock)
    const events = []
    for await (const event of retryTurn('c', 't')) events.push(event)
    expect(events).toEqual([{ type: 'stopped', turn_id: 't' }])
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/conversations/c/turns/t/retry')
    expect(fetchMock.mock.calls[0][1].headers.get('Idempotency-Key')).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it('passes abort signals through to fetch', async () => {
    vi.stubGlobal('document', { cookie: '' })
    const controller = new AbortController()
    const fetchMock = vi.fn().mockRejectedValue(new DOMException('Aborted', 'AbortError'))
    vi.stubGlobal('fetch', fetchMock)
    await expect(async () => { for await (const _ of streamChat({ knowledge_base_id: 'k', answer_mode: 'knowledge_only', intent: 'qa', content: 'x' }, 'key', controller.signal)) { /* consume */ } }).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it('preserves a transport error even when stream cancellation fails', async () => {
    const failure = new Error('connection lost')
    const stream = new ReadableStream<Uint8Array>({ pull(controller) { controller.error(failure) } })
    await expect(async () => { for await (const _ of parseChatStream(stream)) { /* consume */ } }).rejects.toBe(failure)
  })
})

describe('stream and upload edge cases', () => {
  it('joins multiple data lines and decodes multiple frames in one chunk', async () => {
    const stream = byteStream('data: {"type":"delta",\r\ndata: "turn_id":"t","delta":"中文"}\r\n\r\ndata: {"type":"stopped","turn_id":"t"}\r\n\r\n')
    const events = []
    for await (const event of parseChatStream(stream)) events.push(event)
    expect(events).toEqual([{ type: 'delta', turn_id: 't', delta: '中文' }, { type: 'stopped', turn_id: 't' }])
  })
  it('stops consuming after a terminal event even if another frame follows', async () => {
    vi.stubGlobal('document', { cookie: '' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('data: {"type":"stopped","turn_id":"t"}\n\ndata: {"type":"error","code":"late","message":"late","retryable":false}\n\n')))
    const events = []
    for await (const event of streamChat({ knowledge_base_id: 'k', answer_mode: 'knowledge_only', intent: 'qa', content: 'x' }, 'key')) events.push(event)
    expect(events).toEqual([{ type: 'stopped', turn_id: 't' }])
  })
  it('propagates an abort while consuming an error response body', async () => {
    vi.stubGlobal('document', { cookie: '' })
    const abort = new DOMException('Aborted', 'AbortError')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: () => Promise.reject(abort) }))
    await expect(async () => { for await (const _ of streamChat({ knowledge_base_id: 'k', answer_mode: 'knowledge_only', intent: 'qa', content: 'x' }, 'key')) { /* consume */ } }).rejects.toBe(abort)
  })
  it('propagates an abort while reading the response stream', async () => {
    vi.stubGlobal('document', { cookie: '' })
    const controller = new AbortController()
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => new Response(new ReadableStream({
      start(stream) { options.signal?.addEventListener('abort', () => stream.error(new DOMException('Aborted', 'AbortError'))) },
    }))))
    const iterator = streamChat({ knowledge_base_id: 'k', answer_mode: 'knowledge_only', intent: 'qa', content: 'x' }, 'key', controller.signal)
    const next = iterator.next()
    await Promise.resolve()
    controller.abort()
    await expect(next).rejects.toMatchObject({ name: 'AbortError' })
  })
  it.each(['not json', '{}', '{"material_id":"m","version_id":"v","job_id":5,"status":"queued"}'])('rejects malformed successful upload: %s', async (responseText) => {
    vi.stubGlobal('document', { cookie: '' })
    class MockXHR {
      upload = { onprogress: undefined }
      status = 202
      responseText = responseText
      onload?: () => void
      open() { /* no-op */ }
      setRequestHeader() { /* no-op */ }
      send() { this.onload?.() }
    }
    vi.stubGlobal('XMLHttpRequest', MockXHR)
    await expect(uploadMaterial('kb', new File(['x'], 'x.txt'))).rejects.toMatchObject({ name: 'ApiError', code: 'invalid_response' })
  })
  it('throws an ApiError for an error event in an HTTP 200 stream', async () => {
    vi.stubGlobal('document', { cookie: '' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('data: {"type":"error","turn_id":"t","code":"failed","message":"失败","retryable":true}\n\n')))
    await expect(async () => { for await (const _ of streamChat({ knowledge_base_id: 'k', answer_mode: 'knowledge_only', intent: 'qa', content: 'x' }, 'key')) { /* consume */ } }).rejects.toMatchObject({ name: 'ApiError', code: 'failed', turn_id: 't' })
  })
})
