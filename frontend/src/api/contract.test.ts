import { afterEach, expect, it, vi } from 'vitest'
import contractText from '../../../contracts/frontend.openapi.json?raw'
import { api, retryTurn, streamChat, uploadMaterial } from './client'

const contract = JSON.parse(contractText) as { paths: Record<string, Record<string, unknown>>; components: { schemas: Record<string, { required?: string[]; properties: Record<string, unknown> }> } }

afterEach(() => vi.unstubAllGlobals())

it('uses paths and methods present in the backend OpenAPI contract', async () => {
  vi.stubGlobal('document', { cookie: 'study_csrf=token' })
  const calls: [string, string][] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit = {}) => {
    calls.push([url.split('?')[0], (options.method ?? 'GET').toLowerCase()])
    if (url.endsWith('/stream') || url.endsWith('/retry') && url.includes('/turns/')) {
      return new Response('data: {"type":"stopped","turn_id":"t"}\n\n')
    }
    return new Response('{}')
  }))
  class MockXHR {
    withCredentials = false
    upload = { onprogress: undefined }
    status = 202
    responseText = '{"material_id":"m","version_id":"v","job_id":"j","status":"queued"}'
    onload?: () => void
    open(method: string, url: string) { calls.push([url, method.toLowerCase()]) }
    setRequestHeader() { /* no-op */ }
    send() { this.onload?.() }
  }
  vi.stubGlobal('XMLHttpRequest', MockXHR)
  await api.me(); await api.register({ email: 'a', password: 'b', display_name: 'c' }); await api.login({ email: 'a', password: 'b' }); await api.logout()
  await api.knowledgeBases(); await api.knowledgeBase('id'); await api.createKnowledgeBase({ name: 'x' }); await api.updateKnowledgeBase('id', { name: 'x' }); await api.deleteKnowledgeBase('id')
  await api.materials('id'); await uploadMaterial('id', new File(['x'], 'x.txt')); await api.material('id'); await api.deleteMaterial('id'); await api.retryMaterial('id')
  await api.job('id'); await api.cancelJob('id'); await api.preview('id'); await api.content('id'); await api.pageImage('id', 1); await api.source('id')
  await api.conversations(); await api.createConversation(); await api.conversation('id'); await api.updateConversation('id', { title: 'x' }); await api.deleteConversation('id'); await api.messages('id')
  await api.overview(); await api.search('q'); await api.capabilities(); await api.turn('id', 'id'); await api.cancelTurn('id', 'id')
  for await (const _ of streamChat({ knowledge_base_id: 'id', answer_mode: 'knowledge_only', intent: 'qa', content: 'x' }, 'key')) { /* consume */ }
  for await (const _ of retryTurn('id', 'id')) { /* consume */ }
  expect(calls).toHaveLength(33)
  for (const [url, method] of calls) {
    const path = Object.keys(contract.paths).find((template) => new RegExp('^' + template.replace(/\{[^}]+\}/g, '[^/]+') + '$').test(url))
    expect(path, `${method.toUpperCase()} ${url}`).toBeDefined()
    expect(contract.paths[path!][method], `${method.toUpperCase()} ${url}`).toBeDefined()
  }
})

it('publishes the fields consumed by the frontend', () => {
  const models: Record<string, string[]> = {
    TurnDTO: ['turn_id', 'conversation_id', 'status', 'scope', 'messages', 'error_code'],
    MessageDTO: ['id', 'conversation_id', 'sequence_no', 'role', 'content', 'status', 'created_at', 'scope', 'citations', 'turn_id'],
    CitationDTO: ['id', 'source_label', 'material_id', 'material_version_id', 'knowledge_base_id', 'title', 'quote', 'locator', 'location_label', 'version_no', 'availability'],
    ScopeDTO: ['knowledge_base_id', 'knowledge_base_name', 'answer_mode', 'index_version_ids'],
    UploadResponse: ['material_id', 'version_id', 'job_id', 'status'],
    UserDTO: ['id', 'email', 'display_name'],
    KnowledgeBaseDTO: ['id', 'name', 'description', 'created_at', 'updated_at'],
    KnowledgeBasePage: ['items', 'next_cursor'],
    ConversationDTO: ['id', 'title', 'created_at', 'updated_at'],
    ConversationPage: ['items', 'next_cursor'],
    MessagePage: ['items', 'next_cursor'],
    MaterialPage: ['items', 'next_cursor'],
    JobResponse: ['id', 'status', 'phase', 'progress', 'error_code', 'attempt_count'],
    JobCancelResponse: ['id', 'status'],
    MaterialPreviewResponse: ['version_id', 'mime_type', 'page_count', 'sections'],
    PreviewSection: ['text', 'locator'],
    SearchPage: ['items', 'next_cursor'],
    SearchItem: ['id', 'type', 'title'],
    DashboardDTO: ['knowledge_base_count', 'material_count', 'conversation_count', 'completed_qa_count', 'activity'],
    ActivityDTO: ['date', 'count'],
    CapabilitiesDTO: ['rag', 'ocr', 'answer_modes', 'upload_mime_types', 'max_upload_bytes'],
    ChatRequest: ['knowledge_base_id', 'content'],
    RegisterBody: ['email', 'password', 'display_name'],
    LoginBody: ['email', 'password'],
    NameBody: ['name'],
    MaterialResponse: ['id', 'knowledge_base_id', 'title', 'mime_type', 'byte_size', 'status', 'version_id', 'job_id', 'created_at', 'error_code', 'error_message'],
  }
  for (const [name, fields] of Object.entries(models)) {
    expect(contract.components.schemas[name]?.required, name).toEqual(expect.arrayContaining(fields))
  }
  expect(contract.components.schemas.UserDTO.properties).toHaveProperty('avatar_url')
  expect(contract.components.schemas.SearchItem.properties).toHaveProperty('knowledge_base_id')
  expect(contract.components.schemas.ChatRequest.properties).toEqual(expect.objectContaining({
    conversation_id: expect.any(Object), answer_mode: expect.any(Object), intent: expect.any(Object), material_ids: expect.any(Object),
  }))
})
