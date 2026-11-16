import { ApiError, errorFromResponse, parseChatStream } from './stream'
import type { LibraryOverview, FragmentPage, DocumentLocation, ClassificationState, ClassificationDecision, Capabilities, ChatRequest, Citation, Conversation, Dashboard, Job, KnowledgeBase, KnowledgeBaseInput, LearningAttempt, Material, MaterialPreview, Message, Page, SearchItem, Turn, UploadResult, User, WrongQuestionItem } from './types'

const base = '/api/v1'
const pathId = (id: string) => encodeURIComponent(id)
type AuthResponse = { user: User; access_token: string; csrf_token: string }
/** CSRF 仅存内存，避免依赖可被 XSS 读取的 document.cookie（HttpOnly Cookie 由浏览器随请求发送）。 */
let session: AuthResponse | null = null
let csrfMemory: string | undefined
let refreshing: Promise<User> | null = null
let sessionVersion = 0

function csrfToken(): string | undefined {
  return csrfMemory ?? session?.csrf_token
}
function headersFor(headers?: HeadersInit, csrf = false): Headers {
  const result = new Headers(headers)
  if (csrf) {
    const token = csrfToken()
    if (token) result.set('X-CSRF-Token', token)
  }
  return result
}
async function checked(response: Response): Promise<Response> {
  if (!response.ok) {
    let body: unknown
    try { body = await response.json() } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') throw error
      body = undefined
    }
    throw errorFromResponse(body, response.status)
  }
  return response
}
async function authorized<T>(send: (token?: string) => Promise<T>): Promise<T> {
  const original = session
  try {
    return await send(original?.access_token)
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 401) || !original) throw error
    if (session === original) await refresh()
    if (!session || session.user.id !== original.user.id) throw error
    return send(session.access_token)
  }
}

async function request<T>(path: string, options: RequestInit = {}, auth: 'bearer' | 'public' | 'cookie' = 'bearer'): Promise<T> {
  const headers = headersFor(options.headers, auth === 'cookie')
  if (options.body && typeof options.body === 'string') headers.set('Content-Type', 'application/json')
  const send = async (token?: string) => {
    const requestHeaders = new Headers(headers)
    if (token) requestHeaders.set('Authorization', `Bearer ${token}`)
    return checked(await fetch(base + path, { ...options, headers: requestHeaders, credentials: auth === 'bearer' ? 'omit' : 'include' }))
  }
  const response = auth === 'bearer' ? await authorized(send) : await send()
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
const json = (method: string, body?: unknown): RequestInit => ({ method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })

function clearSession() {
  sessionVersion += 1
  session = null
  csrfMemory = undefined
  refreshing = null
}

function refresh() {
  const version = sessionVersion
  if (!refreshing) {
    refreshing = request<AuthResponse>('/auth/refresh', json('POST'), 'cookie').then((result) => {
      if (version !== sessionVersion || (session && session.user.id !== result.user.id)) {
        throw new ApiError({ code: 'unauthenticated', message: '请重新登录', retryable: false }, 401)
      }
      session = result
      csrfMemory = result.csrf_token
      return result.user
    }).catch((error) => {
      if (version === sessionVersion && error instanceof ApiError && error.status === 401) session = null
      throw error
    }).finally(() => {
      if (version === sessionVersion) refreshing = null
    })
  }
  return refreshing
}

async function authenticate(path: string, body: unknown) {
  const result = await request<AuthResponse>(path, json('POST', body), 'public')
  clearSession()
  session = result
  csrfMemory = result.csrf_token
  return result.user
}
const query = (values: Record<string, string | undefined>) => {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => { if (value !== undefined) params.set(key, value) })
  const result = params.toString()
  return result ? `?${result}` : ''
}

export const api = {
  me: () => request<User>('/me'),
  refresh,
  clearSession,
  authOptions: () => request<{ google: boolean; password_reset: boolean }>('/auth/options', {}, 'public'),
  register: (body: { email: string; password: string; display_name: string }) => authenticate('/auth/register', body),
  login: (body: { email: string; password: string }) => authenticate('/auth/login', body),
  googleLink: (password: string) => authenticate('/auth/google/link', { password }),
  forgotPassword: (email: string) => request<{ message: string }>('/auth/password/forgot', json('POST', { email }), 'public'),
  resetPassword: async (token: string, password: string) => {
    await request<void>('/auth/password/reset', json('POST', { token, password }), 'public')
    clearSession()
  },
  logout: async () => { await request<void>('/auth/logout', json('POST'), 'cookie'); clearSession() },
  knowledgeBases: (cursor?: string) => request<Page<KnowledgeBase>>('/knowledge-bases' + query({ cursor })),
  knowledgeBase: (id: string) => request<KnowledgeBase>(`/knowledge-bases/${pathId(id)}`),
  createKnowledgeBase: (body: KnowledgeBaseInput) => request<KnowledgeBase>('/knowledge-bases', json('POST', body)),
  updateKnowledgeBase: (id: string, body: KnowledgeBaseInput) => request<KnowledgeBase>(`/knowledge-bases/${pathId(id)}`, json('PATCH', body)),
  deleteKnowledgeBase: (id: string) => request<void>(`/knowledge-bases/${pathId(id)}`, json('DELETE')),
  libraryOverview: (id: string) => request<LibraryOverview>(`/knowledge-bases/${pathId(id)}/overview`),
  syncLibrary: (id: string) => request<LibraryOverview>(`/knowledge-bases/${pathId(id)}/sync`, json('POST')),
  documentLocations: (id: string, material: string) => request<DocumentLocation[]>(`/knowledge-bases/${pathId(id)}/documents/${pathId(material)}/locations`),
  fragments: (id: string, filters: Record<string, string | undefined>, signal?: AbortSignal) =>
    request<FragmentPage>(`/knowledge-bases/${pathId(id)}/fragments` + query(filters), { signal }),
  setFragmentRetrieval: (id: string, ids: string[], enabled: boolean) =>
    request<void>(`/knowledge-bases/${pathId(id)}/fragments/retrieval`, json('PATCH', { ids, enabled })),
  classification: (id: string, material: string) =>
    request<ClassificationState>(`/knowledge-bases/${pathId(id)}/documents/${pathId(material)}/classification`),
  generateClassification: (id: string, material: string) =>
    request<ClassificationState>(`/knowledge-bases/${pathId(id)}/documents/${pathId(material)}/classification`, json('POST')),
  confirmClassification: (id: string, material: string, body: ClassificationDecision) =>
    request<ClassificationState>(`/knowledge-bases/${pathId(id)}/documents/${pathId(material)}/classification`, json('PATCH', body)),
  materials: (id: string, cursor?: string, signal?: AbortSignal) => request<Page<Material>>(`/knowledge-bases/${pathId(id)}/materials` + query({ cursor }), { signal }),
  material: (id: string) => request<Material>(`/materials/${pathId(id)}`),
  deleteMaterial: (id: string) => request<void>(`/materials/${pathId(id)}`, json('DELETE')),
  retryMaterial: (id: string) => request<UploadResult>(`/materials/${pathId(id)}/retry`, json('POST')),
  job: (id: string) => request<Job>(`/jobs/${pathId(id)}`),
  cancelJob: (id: string) => request<Pick<Job, 'id' | 'status'>>(`/jobs/${pathId(id)}/cancel`, json('POST')),
  preview: (id: string) => request<MaterialPreview>(`/material-versions/${pathId(id)}/preview`),
  content: (id: string) => checkedFetch(`/material-versions/${pathId(id)}/content`),
  pageImage: (id: string, page: number) => checkedFetch(`/material-versions/${pathId(id)}/pages/${page}`),
  source: (id: string) => request<Citation>(`/sources/${pathId(id)}`),
  conversations: (cursor?: string, project?: string) => request<Page<Conversation>>('/conversations' + query({ cursor, project_knowledge_base_id: project })),
  createConversation: (body: { title?: string; project_knowledge_base_id?: string } = {}) => request<Conversation>('/conversations', json('POST', body)),
  conversation: (id: string) => request<Conversation>(`/conversations/${pathId(id)}`),
  updateConversation: (id: string, body: { title: string }) => request<Conversation>(`/conversations/${pathId(id)}`, json('PATCH', body)),
  deleteConversation: (id: string) => request<void>(`/conversations/${pathId(id)}`, json('DELETE')),
  messages: (id: string, cursor?: string) => request<Page<Message>>(`/conversations/${pathId(id)}/messages` + query({ cursor })),
  overview: () => request<Dashboard>('/dashboard/overview'),
  search: (q: string, cursor?: string) => request<Page<SearchItem>>('/search' + query({ q, cursor })),
  capabilities: () => request<Capabilities>('/capabilities'),
  turn: (conversationId: string, turnId: string, signal?: AbortSignal) => request<Turn>(turnPath(conversationId, turnId), { signal }),
  cancelTurn: (conversationId: string, turnId: string) => request<Turn>(turnPath(conversationId, turnId) + '/cancel', json('POST')),
  learning: {
    generateQuiz: (knowledgeBaseId: string, count: number) =>
      request<{ quiz_id: string; title: string; item_count: number }>(
        `/learning/knowledge-bases/${pathId(knowledgeBaseId)}/quizzes/generate`,
        json('POST', { count }),
      ),
    startAttempt: (quizId: string) =>
      request<LearningAttempt>('/learning/attempts', json('POST', { quiz_id: quizId })),
    attempt: (id: string) => request<LearningAttempt>(`/learning/attempts/${pathId(id)}`),
    submitAnswerImage: async (responseId: string, file: File) => {
      const form = new FormData()
      form.append('file', file)
      return authorized(async (token) => {
        const headers = new Headers()
        if (token) headers.set('Authorization', `Bearer ${token}`)
        const response = await checked(
          await fetch(`${base}/learning/responses/${pathId(responseId)}/answer-image`, {
            method: 'POST',
            headers,
            body: form,
            credentials: 'omit',
          }),
        )
        return response.json() as Promise<{
        response_id: string
        verdict: string | null
        verdict_source: string | null
        confidence: number | null
        feedback: string | null
        answer: Record<string, unknown>
        reference_answer: string | null
      }>
      })
    },
    patchVerdict: (responseId: string, body: { verdict: string; reference_answer?: string | null }) =>
      request<{ response_id: string; verdict: string; verdict_source: string; reference_answer: string | null }>(
        `/learning/responses/${pathId(responseId)}/verdict`,
        json('PATCH', body),
      ),
    wrongQuestions: () => request<{ items: WrongQuestionItem[] }>('/learning/wrong-questions'),
    masterWrong: (id: string) =>
      request<{ id: string; status: string }>(`/learning/wrong-questions/${pathId(id)}/mastered`, json('POST')),
  },
}
function turnPath(conversationId: string, turnId: string) { return `/conversations/${pathId(conversationId)}/turns/${pathId(turnId)}` }
function fetchProtected(path: string, options?: RequestInit) {
  return authorized(async (token) => {
    const headers = new Headers(options?.headers)
    if (token) headers.set('Authorization', `Bearer ${token}`)
    return checked(await fetch(base + path, { ...options, headers, credentials: 'omit' }))
  })
}
function checkedFetch(path: string) { return fetchProtected(path) }

async function* stream(path: string, body: unknown, idempotencyKey?: string, signal?: AbortSignal) {
  const headers = { 'Content-Type': 'application/json', ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}) }
  const response = await fetchProtected(path, { method: 'POST', headers, body: JSON.stringify(body), signal })
  if (!response.body) throw new ApiError({ code: 'empty_stream', message: 'Response has no stream', retryable: false })
  let turnId: string | undefined
  let complete = false
  for await (const event of parseChatStream(response.body)) {
    if ('turn_id' in event) turnId = event.turn_id
    if (event.type === 'done' || event.type === 'stopped') complete = true
    yield event
    if (complete) return
  }
  if (!complete) throw new ApiError({ code: 'stream_interrupted', message: 'Chat stream ended before completion', retryable: true, turn_id: turnId })
}
export function streamChat(body: ChatRequest, idempotencyKey: string, signal?: AbortSignal) { return stream('/chat/stream', body, idempotencyKey, signal) }
export function retryTurn(conversationId: string, turnId: string, signal?: AbortSignal) { return stream(turnPath(conversationId, turnId) + '/retry', {}, undefined, signal) }

export function uploadMaterial(knowledgeBaseId: string, file: File, onProgress?: (loaded: number, total: number) => void, signal?: AbortSignal): Promise<UploadResult> {
  return authorized((token) => new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const abort = () => xhr.abort()
    if (signal?.aborted) { reject(new DOMException('Aborted', 'AbortError')); return }
    signal?.addEventListener('abort', abort, { once: true })
    xhr.open('POST', `${base}/knowledge-bases/${pathId(knowledgeBaseId)}/materials`)
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) onProgress?.(event.loaded, event.total) }
    xhr.onload = () => {
      signal?.removeEventListener('abort', abort)
      let body: unknown
      try { body = JSON.parse(xhr.responseText) } catch { body = undefined }
      if (xhr.status >= 200 && xhr.status < 300) {
        if (body && typeof body === 'object' && ['material_id', 'version_id', 'job_id', 'status'].every((key) => typeof (body as Record<string, unknown>)[key] === 'string')) resolve(body as UploadResult)
        else reject(new ApiError({ code: 'invalid_response', message: 'Invalid upload response', retryable: false }, xhr.status))
      } else reject(errorFromResponse(body, xhr.status))
    }
    xhr.onerror = () => { signal?.removeEventListener('abort', abort); reject(new TypeError('Network request failed')) }
    xhr.onabort = () => { signal?.removeEventListener('abort', abort); reject(new DOMException('Aborted', 'AbortError')) }
    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  }))
}
