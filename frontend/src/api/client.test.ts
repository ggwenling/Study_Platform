import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, retryTurn, streamChat, uploadMaterial } from './client'
import { ApiError, parseChatStream } from './stream'

function byteStream(text: string): ReadableStream<Uint8Array> {
  const bytes = new TextEncoder().encode(text)
  return new ReadableStream({ start(controller) { for (const byte of bytes) controller.enqueue(new Uint8Array([byte])); controller.close() } })
}

beforeEach(() => { vi.stubGlobal('navigator', {}) })
afterEach(() => { api.clearSession(); vi.unstubAllGlobals() })

const auth = (token: string) => Response.json({ user: { id: 'u' }, access_token: token, csrf_token: 'csrf-mem' })

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
  it('sends Bearer and idempotency key for SSE', async () => {
    vi.stubGlobal('document', { cookie: 'study_csrf=abc%20123' })
    const fetchMock = vi.fn().mockResolvedValueOnce(auth('access')).mockResolvedValueOnce(Response.json({ code: 'denied', message: 'Denied', retryable: false }, { status: 403 }))
    vi.stubGlobal('fetch', fetchMock)
    await api.login({ email: 'a', password: 'b' })
    await expect(async () => { for await (const _ of streamChat({ knowledge_base_id: 'k', answer_mode: 'knowledge_only', intent: 'qa', content: 'x' }, 'key')) { /* consume */ } }).rejects.toBeInstanceOf(ApiError)
    const options = fetchMock.mock.calls[1][1]
    expect(options.credentials).toBe('omit')
    expect(options.headers.get('Authorization')).toBe('Bearer access')
    expect(options.headers.get('X-CSRF-Token')).toBeNull()
    expect(options.headers.get('Idempotency-Key')).toBe('key')
  })
})

describe('HTTP client', () => {
  it('does not restore a session from a refresh completed after logout', async () => {
    vi.stubGlobal('document', { cookie: 'study_csrf=token' })
    let finish!: (response: Response) => void
    const fetchMock = vi.fn().mockResolvedValueOnce(auth('initial'))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve }))
      .mockResolvedValueOnce(Response.json({}))
    vi.stubGlobal('fetch', fetchMock)
    await api.login({ email: 'a', password: 'b' })
    const pending = api.refresh()
    api.clearSession()
    finish(auth('stale'))
    await expect(pending).rejects.toMatchObject({ status: 401 })
    await api.me()
    expect(fetchMock.mock.calls[1][1].headers.get('Authorization')).toBeNull()
  })
  it('shares refresh across simultaneous 401s and retries with the new token', async () => {
    vi.stubGlobal('document', { cookie: 'study_csrf=token' })
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      const headers = new Headers(init.headers)
      if (url.endsWith('/login')) return auth('old')
      if (url.endsWith('/refresh')) {
        expect(headers.get('X-CSRF-Token')).toBe('csrf-mem')
        return auth('new')
      }
      expect(headers.get('X-CSRF-Token')).toBeNull()
      return headers.get('Authorization') === 'Bearer new' ? Response.json({}) : new Response(null, { status: 401 })
    })
    vi.stubGlobal('fetch', fetchMock)
    await api.login({ email: 'a', password: 'b' })
    await Promise.all([api.me(), api.content('v')])
    expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/refresh'))).toHaveLength(1)
  })
  it('recovers the CSRF token on reload and retries after another tab rotates it', async () => {
    let csrf = 'current'
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith('/csrf')) return Response.json({ csrf_token: csrf })
      if (url.endsWith('/refresh')) {
        const token = new Headers(init.headers).get('X-CSRF-Token')
        if (token !== csrf) return Response.json({code:'csrf_invalid',message:'stale'}, {status:403})
        return Response.json({user:{id:'u'}, access_token:'access', csrf_token: csrf})
      }
      return new Response(null, {status:401})
    })
    vi.stubGlobal('fetch', fetchMock)
    await api.refresh()
    csrf = 'rotated'
    await api.me().catch(() => {})
    expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/csrf'))).toHaveLength(2)
    expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/refresh'))).toHaveLength(3)
  })
  it('retries logout with the current CSRF token after another tab rotates it', async () => {
    let logoutCalls = 0
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith('/login')) return auth('old')
      if (url.endsWith('/csrf')) return Response.json({ csrf_token: 'fresh' })
      logoutCalls += 1
      if (logoutCalls === 1) return Response.json({code:'csrf_invalid',message:'stale'}, {status:403})
      expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('fresh')
      return new Response(null, {status:204})
    })
    vi.stubGlobal('fetch', fetchMock)
    await api.login({ email: 'a', password: 'b' })
    await api.logout()
    expect(logoutCalls).toBe(2)
  })
  it('treats logout as complete when another tab already cleared the cookies', async () => {
    vi.stubGlobal('navigator', { locks: { request: async (_name: string, callback: () => Promise<unknown>) => callback() } })
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith('/login')) return auth('old')
      if (url.endsWith('/csrf')) return Response.json({code:'unauthenticated', message:'expired'}, {status:401})
      return new Response(null, {status:401})
    })
    vi.stubGlobal('fetch', fetchMock)
    await api.login({ email: 'a', password: 'b' })
    await expect(api.logout()).resolves.toBeUndefined()
    await expect(api.me()).rejects.toMatchObject({ status: 401 })
    expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/refresh'))).toHaveLength(0)
  })
  it('serializes cross-tab refresh and reads the current CSRF token after locking', async () => {
    const lock = vi.fn(async (_name, callback) => callback())
    vi.stubGlobal('navigator', { locks: { request: lock } })
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith('/csrf')) return Response.json({ csrf_token: 'fresh' })
      expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('fresh')
      return Response.json({ user: { id: 'u' }, access_token: 'new', csrf_token: 'fresh' })
    })
    vi.stubGlobal('fetch', fetchMock)
    await api.refresh()
    expect(lock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/api/v1/auth/csrf', '/api/v1/auth/refresh'])
  })
  it('reports actual XHR upload progress and returns the processing job', async () => {
    vi.stubGlobal('document', { cookie: 'study_csrf=token' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(auth('upload')))
    await api.login({ email: 'a', password: 'b' })
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
    expect(xhr!.withCredentials).toBe(false)
    expect(xhr!.headers.Authorization).toBe('Bearer upload')
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
