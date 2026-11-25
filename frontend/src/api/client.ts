import { ApiError, errorFromResponse, parseChatStream } from './stream'
import type { DueFlashcard, ExtractionMode, ModelConnectionKind } from './types'
import type { ReindexStatus, ReindexSubmitted } from './types'
import type { AiGenerationJobType, AiJob, SubmittedAiJob } from './types'
import type { PendingQuiz } from './types'
import type { ModelConnection, ModelConnections, ModelConnectionInput, LibraryOverview, Fragment, FragmentPage, DocumentLocation, ClassificationState, ClassificationDecision, Capabilities, ChatRequest, Citation, Conversation, Dashboard, FlashcardDeckDetail, FlashcardDeckSummary, FlashcardItem, HitTestResponse, Job, KnowledgeBase, KnowledgeBaseInput, LearningAttempt, LearningAttemptSummary, LearningResponseResult, Material, MaterialPreview, MaterialReader, Message, ModelUsageSummary, Page, RetrievalSettings, SearchItem, StudioArtifactKind, StudyNote, Turn, UploadResult, User, WrongQuestionItem } from './types'

const base = '/api/v1'
const pathId = (id: string) => encodeURIComponent(id)
type AuthResponse = { user: User; access_token: string; csrf_token: string }
/** CSRF 仅存内存，避免依赖可被 XSS 读取的 document.cookie（HttpOnly Cookie 由浏览器随请求发送）。 */
let session: AuthResponse | null = null
let csrfMemory: string | undefined
let refreshing: Promise<User> | null = null
let sessionVersion = 0
type SharedRead = { promise: Promise<unknown>; controller: AbortController; readers: number }
const sharedReads = new Map<string, SharedRead>()

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

function sharedRead<T>(path: string, signal?: AbortSignal): Promise<T> {
  if (signal?.aborted) return Promise.reject(signal.reason)
  const owner = session?.user.id
  if (!owner) return request<T>(path, { signal })
  const version = sessionVersion
  let entry = sharedReads.get(path)
  if (!entry) {
    const controller = new AbortController()
    const promise = request<T>(path, { signal: controller.signal })
    const created: SharedRead = { promise, controller, readers: 0 }
    sharedReads.set(path, created)
    entry = created
    const forget = () => { if (sharedReads.get(path) === created) sharedReads.delete(path) }
    void promise.then(forget, forget)
  }
  const read = entry
  read.readers += 1
  return new Promise<T>((resolve, reject) => {
    let finished = false
    function finish() {
      if (finished) return false
      finished = true
      signal?.removeEventListener('abort', abort)
      read.readers -= 1
      return true
    }
    function abort() {
      if (!finish()) return
      if (read.readers === 0) {
        if (sharedReads.get(path) === read) sharedReads.delete(path)
        read.controller.abort()
      }
      reject(signal?.reason)
    }
    signal?.addEventListener('abort', abort, { once: true })
    void read.promise.then(
      (result) => {
        if (!finish()) return
        if (version !== sessionVersion || session?.user.id !== owner) {
          reject(new DOMException('Session changed', 'AbortError'))
        } else resolve(result as T)
      },
      (error) => {
        if (!finish()) return
        reject(version !== sessionVersion ? new DOMException('Session changed', 'AbortError') : error)
      },
    )
  })
}

function invalidateWorkspaceReads(resources: readonly string[]) {
  for (const path of sharedReads.keys()) {
    if ((resources.includes('conversations') && (path === '/conversations' || path.startsWith('/conversations?'))) ||
        (resources.includes('materials') && path.startsWith('/knowledge-bases/'))) sharedReads.delete(path)
  }
}
const json = (method: string, body?: unknown): RequestInit => ({ method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })

function clearSession() {
  sessionVersion += 1
  for (const { controller } of sharedReads.values()) controller.abort()
  sharedReads.clear()
  session = null
  csrfMemory = undefined
  refreshing = null
}

async function currentCsrf() {
  const result = await request<{ csrf_token: string }>('/auth/csrf', json('POST'), 'cookie')
  csrfMemory = result.csrf_token
}

async function refreshWithCsrf() {
  if (!csrfToken()) await currentCsrf()
  try {
    return await request<AuthResponse>('/auth/refresh', json('POST'), 'cookie')
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401 && error.status !== 403) throw error
    await currentCsrf()
    return request<AuthResponse>('/auth/refresh', json('POST'), 'cookie')
  }
}

async function serializeCookieOperation<T>(action: () => Promise<T>): Promise<T> {
  if (typeof navigator !== 'undefined' && navigator.locks) {
    return navigator.locks.request('knowloop-session', async () => {
      await currentCsrf()
      return action()
    })
  }
  return action()
}

function refresh() {
  const version = sessionVersion
  if (!refreshing) {
    refreshing = serializeCookieOperation(refreshWithCsrf).then((result) => {
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
  modelConnections: () => request<ModelConnections>('/me/model-connections'),
  createModelConnection: (body: ModelConnectionInput) => request<ModelConnection>('/me/model-connections', json('POST', body)),
  updateModelConnection: (id: string, body: ModelConnectionInput) => request<ModelConnection>(`/me/model-connections/${pathId(id)}`, json('PATCH', body)),
  deleteModelConnection: (id: string) => request<void>(`/me/model-connections/${pathId(id)}`, json('DELETE')),
  testModelConnection: (id: string) => request<ModelConnection>(`/me/model-connections/${pathId(id)}/test`, json('POST')),
  defaultModelConnection: (kind: ModelConnectionKind, id: string | null) => request<void>(`/me/model-connections/defaults/${kind}`, json('PUT', { connection_id: id })),
  modelUsage: (days = 30) => request<ModelUsageSummary>('/me/model-usage' + query({ days: String(days) })),
  refresh,
  clearSession,
  invalidateWorkspaceReads,
  authOptions: () => request<{ google: boolean; password_reset: boolean }>('/auth/options', {}, 'public'),
  register: (body: { email: string; password: string; display_name: string }) => authenticate('/auth/register', body),
  login: (body: { email: string; password: string }) => authenticate('/auth/login', body),
  googleLink: (password: string) => authenticate('/auth/google/link', { password }),
  forgotPassword: (email: string) => request<{ message: string }>('/auth/password/forgot', json('POST', { email }), 'public'),
  resetPassword: async (token: string, password: string) => {
    await request<void>('/auth/password/reset', json('POST', { token, password }), 'public')
    clearSession()
  },
  logout: async () => {
    try {
      await serializeCookieOperation(() => request<void>('/auth/logout', json('POST'), 'cookie'))
    } catch (error) {
      if (!(error instanceof ApiError)) throw error
      if (error.status === 401) {
        clearSession()
        return
      }
      if (error.status !== 403 || error.code !== 'csrf_invalid') throw error
      try {
        await currentCsrf()
        await request<void>('/auth/logout', json('POST'), 'cookie')
      } catch (retryError) {
        if (!(retryError instanceof ApiError) || retryError.status !== 401) throw retryError
      }
    }
    clearSession()
  },
  knowledgeBases: (cursor?: string) => request<Page<KnowledgeBase>>('/knowledge-bases' + query({ cursor })),
  knowledgeBase: (id: string) => request<KnowledgeBase>(`/knowledge-bases/${pathId(id)}`),
  createKnowledgeBase: (body: KnowledgeBaseInput) => request<KnowledgeBase>('/knowledge-bases', json('POST', body)),
  updateKnowledgeBase: (id: string, body: KnowledgeBaseInput) => request<KnowledgeBase>(`/knowledge-bases/${pathId(id)}`, json('PATCH', body)),
  updateRetrievalSettings: (id: string, body: Partial<RetrievalSettings>) =>
    request<{ retrieval_settings: Partial<RetrievalSettings> | null }>(`/knowledge-bases/${pathId(id)}/retrieval-settings`, json('PATCH', body)),
  deleteKnowledgeBase: (id: string) => request<void>(`/knowledge-bases/${pathId(id)}`, json('DELETE')),
  libraryOverview: (id: string, signal?: AbortSignal) => request<LibraryOverview>(`/knowledge-bases/${pathId(id)}/overview`, { signal }),
  syncLibrary: (id: string) => request<LibraryOverview>(`/knowledge-bases/${pathId(id)}/sync`, json('POST')),
  startReindex: (id: string, connectionId: string | null) => request<ReindexSubmitted>(`/knowledge-bases/${pathId(id)}/reindex`, json('POST', { embedding_connection_id: connectionId })),
  reindexStatus: (id: string, signal?: AbortSignal) => request<ReindexStatus | null>(`/knowledge-bases/${pathId(id)}/reindex-status`, { signal }),
  retryReindex: (id: string) => request<ReindexStatus>(`/knowledge-bases/${pathId(id)}/reindex/retry-failed`, json('POST')),
  cancelReindex: (id: string) => request<ReindexStatus>(`/knowledge-bases/${pathId(id)}/reindex/cancel`, json('POST')),
  documentLocations: (id: string, material: string) => request<DocumentLocation[]>(`/knowledge-bases/${pathId(id)}/documents/${pathId(material)}/locations`),
  fragments: (id: string, filters: Record<string, string | undefined>, signal?: AbortSignal) =>
    request<FragmentPage>(`/knowledge-bases/${pathId(id)}/fragments` + query(filters), { signal }),
  fragment: (id: string, fragmentId: string) =>
    request<Fragment>(`/knowledge-bases/${pathId(id)}/fragments/${pathId(fragmentId)}`),
  setFragmentRetrieval: (id: string, ids: string[], enabled: boolean) =>
    request<void>(`/knowledge-bases/${pathId(id)}/fragments/retrieval`, json('PATCH', { ids, enabled })),
  classification: (id: string, material: string) =>
    request<ClassificationState>(`/knowledge-bases/${pathId(id)}/documents/${pathId(material)}/classification`),
  generateClassification: (id: string, material: string) =>
    request<ClassificationState>(`/knowledge-bases/${pathId(id)}/documents/${pathId(material)}/classification`, json('POST')),
  confirmClassification: (id: string, material: string, body: ClassificationDecision) =>
    request<ClassificationState>(`/knowledge-bases/${pathId(id)}/documents/${pathId(material)}/classification`, json('PATCH', body)),
  materials: (id: string, cursor?: string, signal?: AbortSignal) => sharedRead<Page<Material>>(`/knowledge-bases/${pathId(id)}/materials` + query({ cursor }), signal),
  webClip: (id: string, body: { url: string; title?: string }) => request<UploadResult>(`/knowledge-bases/${pathId(id)}/materials/web-clip`, json('POST', body)),
  material: (id: string) => request<Material>(`/materials/${pathId(id)}`),
  deleteMaterial: (id: string) => request<void>(`/materials/${pathId(id)}`, json('DELETE')),
  retryMaterial: (id: string) => request<UploadResult>(`/materials/${pathId(id)}/retry`, json('POST')),
  reparseMaterial: (id: string, extractionMode: ExtractionMode) =>
    request<UploadResult>(`/materials/${pathId(id)}/reparse`, json('POST', { extraction_mode: extractionMode })),
  job: (id: string) => request<Job>(`/jobs/${pathId(id)}`),
  cancelJob: (id: string) => request<Pick<Job, 'id' | 'status'>>(`/jobs/${pathId(id)}/cancel`, json('POST')),
  aiJobs: {
    list: (options: {
      knowledgeBaseId?: string
      jobTypes?: AiGenerationJobType[]
      status?: 'active' | 'all'
      limit?: number
    } = {}) => {
      const params = new URLSearchParams({ status: options.status ?? 'active', limit: String(options.limit ?? 20) })
      if (options.knowledgeBaseId) params.set('knowledge_base_id', options.knowledgeBaseId)
      for (const jobType of options.jobTypes ?? []) params.append('job_types', jobType)
      return request<AiJob[]>(`/ai-jobs?${params}`)
    },
    get: (id: string) => request<AiJob>(`/ai-jobs/${pathId(id)}`),
    cancel: (id: string) => request<AiJob>(`/ai-jobs/${pathId(id)}/cancel`, json('POST')),
  },
  preview: (id: string) => request<MaterialPreview>(`/material-versions/${pathId(id)}/preview`),
  materialReader: (id: string, chunkId?: string | null, window = 3, signal?: AbortSignal) =>
    request<MaterialReader>(`/material-versions/${pathId(id)}/reader` + query({ chunk_id: chunkId ?? undefined, window: String(window) }), { signal }),
  content: (id: string) => checkedFetch(`/material-versions/${pathId(id)}/content`),
  pageImage: (id: string, page: number) => checkedFetch(`/material-versions/${pathId(id)}/pages/${page}`),
  source: (id: string) => request<Citation>(`/sources/${pathId(id)}`),
  conversations: (cursor?: string, project?: string, archived = false) => sharedRead<Page<Conversation>>('/conversations' + query({ cursor, project_knowledge_base_id: project, archived: archived ? 'true' : undefined })),
  createConversation: (body: { title?: string; project_knowledge_base_id?: string } = {}) => request<Conversation>('/conversations', json('POST', body)),
  conversation: (id: string) => request<Conversation>(`/conversations/${pathId(id)}`),
  updateConversation: (id: string, body: { title: string }) => request<Conversation>(`/conversations/${pathId(id)}`, json('PATCH', body)),
  deleteConversation: (id: string) => request<void>(`/conversations/${pathId(id)}`, json('DELETE')),
  archiveConversation: (id: string, archived: boolean) => request<Conversation>(`/conversations/${pathId(id)}/archive`, json('PATCH', { archived })),
  messages: (id: string, cursor?: string, order: 'asc' | 'desc' = 'asc', signal?: AbortSignal) =>
    request<Page<Message>>(`/conversations/${pathId(id)}/messages` + query({ cursor, order: order === 'desc' ? order : undefined }), { signal }),
  overview: () => request<Dashboard>('/dashboard/overview'),
  search: (q: string, cursor?: string, includeContent = false, signal?: AbortSignal) => request<Page<SearchItem>>(
    '/search' + query({ q, cursor, include_content: includeContent ? 'true' : undefined }), { signal },
  ),
  capabilities: () => request<Capabilities>('/capabilities'),
  turn: (conversationId: string, turnId: string, signal?: AbortSignal) => request<Turn>(turnPath(conversationId, turnId), { signal }),
  cancelTurn: (conversationId: string, turnId: string, signal?: AbortSignal) =>
    request<Turn>(turnPath(conversationId, turnId) + '/cancel', { ...json('POST'), signal }),
  learning: {
    generateQuiz: (
      knowledgeBaseId: string,
      count: number,
      options?: { mode?: 'short_answer' | 'mcq' | 'mixed'; material_ids?: string[] | null },
    ) =>
      request<SubmittedAiJob>(
        `/learning/knowledge-bases/${pathId(knowledgeBaseId)}/quizzes/generate`,
        json('POST', { count, mode: options?.mode ?? 'short_answer', material_ids: options?.material_ids ?? null }),
      ),
    attempts: (knowledgeBaseId?: string, limit = 20, cursor?: string) =>
      request<Page<LearningAttemptSummary>>(
        '/learning/attempts' + query({ knowledge_base_id: knowledgeBaseId, limit: String(limit), cursor }),
      ),
    pendingQuizzes: (cursor?: string, signal?: AbortSignal) =>
      request<Page<PendingQuiz>>('/learning/quizzes/pending' + query({ cursor }), { signal }),
    startAttempt: (quizId: string, resumeExisting?: boolean) =>
      request<LearningAttempt>('/learning/attempts', json('POST', { quiz_id: quizId, resume_existing: resumeExisting })),
    attempt: (id: string) => request<LearningAttempt>(`/learning/attempts/${pathId(id)}`),
    submitAnswerText: (responseId: string, answerText: string) =>
      request<LearningResponseResult>(
        `/learning/responses/${pathId(responseId)}/answer-text`,
        json('POST', { answer_text: answerText }),
      ),
    submitAnswerImage: async (responseId: string, file: File, useVision = false) => {
      const form = new FormData()
      form.append('file', file)
      if (useVision) form.append('use_vision', 'true')
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
        return response.json() as Promise<LearningResponseResult>
      })
    },
    patchVerdict: (responseId: string, body: { verdict: string; reference_answer?: string | null }) =>
      request<LearningResponseResult>(
        `/learning/responses/${pathId(responseId)}/verdict`,
        json('PATCH', body),
      ),
    wrongQuestions: (knowledgeBaseId?: string, dueOnly = false) =>
      request<{ items: WrongQuestionItem[] }>(
        '/learning/wrong-questions' + query({ knowledge_base_id: knowledgeBaseId, due_only: dueOnly ? 'true' : undefined }),
      ),
    retryWrong: (body: { wrong_ids?: string[]; knowledge_base_id?: string; due_only?: boolean } = {}) =>
      request<LearningAttempt>('/learning/wrong-questions/retry', json('POST', body)),
    masterWrong: (id: string) =>
      request<{ id: string; status: string }>(`/learning/wrong-questions/${pathId(id)}/mastered`, json('POST')),
  },
  studio: {
    notes: (knowledgeBaseId: string, signal?: AbortSignal) =>
      request<{ items: StudyNote[] }>(`/knowledge-bases/${pathId(knowledgeBaseId)}/notes`, { signal }),
    createNote: (
      knowledgeBaseId: string,
      body: { title: string; content_md: string; note_type?: StudyNote['note_type']; message_id?: string | null },
    ) =>
      request<StudyNote>(`/knowledge-bases/${pathId(knowledgeBaseId)}/notes`, json('POST', body)),
    generateNote: (
      knowledgeBaseId: string,
      body: { kind: StudioArtifactKind; material_ids?: string[] | null; title?: string | null },
    ) =>
      request<SubmittedAiJob>(`/knowledge-bases/${pathId(knowledgeBaseId)}/notes/generate`, json('POST', body)),
    note: (noteId: string) => request<StudyNote>(`/notes/${pathId(noteId)}`),
    updateNote: (noteId: string, body: { title?: string; content_md?: string }) =>
      request<StudyNote>(`/notes/${pathId(noteId)}`, json('PATCH', body)),
    appendNote: (noteId: string, body: { content_md: string; section_heading?: string; message_id?: string }) =>
      request<StudyNote>(`/notes/${pathId(noteId)}/append`, json('POST', body)),
    deleteNote: (noteId: string) => request<void>(`/notes/${pathId(noteId)}`, json('DELETE')),
    ingestNote: (noteId: string) => request<UploadResult>(`/notes/${pathId(noteId)}/ingest`, json('POST')),
    exportNotes: (knowledgeBaseId: string) =>
      checkedFetch('/knowledge-bases/' + pathId(knowledgeBaseId) + '/notes/export'),
    flashcardDecks: (knowledgeBaseId?: string) =>
      request<{ items: FlashcardDeckSummary[] }>(
        '/flashcard-decks' + query({ knowledge_base_id: knowledgeBaseId }),
      ),
    exportFlashcardDeck: (deckId: string, format: 'anki_tsv' | 'markdown') =>
      checkedFetch('/flashcard-decks/' + pathId(deckId) + '/export' + query({ format })),
    generateFlashcards: (
      knowledgeBaseId: string,
      body: { count?: number; material_ids?: string[] | null; title?: string | null } = {},
    ) =>
      request<SubmittedAiJob>(
        `/knowledge-bases/${pathId(knowledgeBaseId)}/flashcard-decks/generate`,
        json('POST', body),
      ),
    flashcardDeck: (deckId: string) => request<FlashcardDeckDetail>(`/flashcard-decks/${pathId(deckId)}`),
    dueFlashcards: (knowledgeBaseId?: string, limit?: number) =>
      request<{ total_due: number; items: DueFlashcard[] }>(
        '/flashcards/due' + query({ knowledge_base_id: knowledgeBaseId, limit: limit?.toString() }),
      ),
    deleteFlashcardDeck: (deckId: string) => request<void>(`/flashcard-decks/${pathId(deckId)}`, json('DELETE')),
    reviewFlashcard: (cardId: string, rating: 0 | 1 | 2 | 3, idempotencyKey: string) =>
      request<FlashcardItem>(`/flashcards/${pathId(cardId)}/review`, json('POST', { rating, idempotency_key: idempotencyKey })),
  },
  workshop: {
    hitTest: (
      knowledgeBaseId: string,
      body: { query: string; top_k?: number; lexical_weight?: number; min_similarity?: number; rerank_enabled?: boolean; rerank_connection_id?: string | null; rerank_candidate_k?: number; material_ids?: string[] | null },
    ) =>
      request<HitTestResponse>(
        `/knowledge-bases/${pathId(knowledgeBaseId)}/hit-test`,
        json('POST', body),
      ),
    updateFragment: (knowledgeBaseId: string, chunkId: string, text: string) =>
      request<Fragment>(
        `/knowledge-bases/${pathId(knowledgeBaseId)}/fragments/${pathId(chunkId)}`,
        json('PATCH', { text }),
      ),
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
export function editTurn(conversationId: string, turnId: string, content: string, signal?: AbortSignal) { return stream(turnPath(conversationId, turnId) + '/edit', { content }, undefined, signal) }

export function uploadMaterial(knowledgeBaseId: string, file: File, onProgress?: (loaded: number, total: number) => void, signal?: AbortSignal, extractionMode: ExtractionMode = 'ocr'): Promise<UploadResult> {
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
    if (extractionMode === 'vlm') form.append('extraction_mode', 'vlm')
    xhr.send(form)
  }))
}
