export interface ApiErrorBody { code: string; message: string; request_id?: string; retryable: boolean; turn_id?: string }
export interface Page<T> { items: T[]; next_cursor: string | null }
export interface User { id: string; email: string; display_name: string; avatar_url: string | null }
export interface Scope { knowledge_base_id: string; knowledge_base_name: string; answer_mode: 'knowledge_only' | 'with_general'; intent?: 'qa' | 'summary'; selected_material_ids?: string[] | null; index_version_ids: string[] }
export interface Citation { id: string; source_label: string; material_id: string; material_version_id: string; knowledge_base_id: string; title: string; quote: string; quote_status?: 'verified' | 'unverified' | 'unavailable' | null; locator: Record<string, unknown>; location_label: string; version_no: number; availability: 'available' | 'unavailable'; unavailable_reason?: string | null; content_url?: string | null; preview_url?: string | null; source_url?: string | null; chunk_id?: string | null }
export interface Message { id: string; conversation_id: string; sequence_no: number; role: 'user' | 'assistant' | 'system_summary'; content: string; status: 'pending' | 'streaming' | 'completed' | 'failed' | 'stopped'; created_at: string; scope: Scope | null; citations: Citation[]; turn_id: string | null }
export interface ChatRequest { conversation_id?: string | null; knowledge_base_id: string; answer_mode?: 'knowledge_only' | 'with_general'; intent?: 'qa' | 'summary'; content: string; material_ids?: string[] | null }
export type ChatPhase = 'working' | 'searching' | 'solving' | 'composing'
export type ChatEvent =
  | { type: 'meta'; conversation_id: string; turn_id: string; user_message_id: string; assistant_message_id: string; scope: Scope; replay?: boolean }
  | { type: 'phase'; turn_id: string; phase: ChatPhase }
  | { type: 'delta'; turn_id: string; delta: string }
  | { type: 'sources'; turn_id: string; citations: Citation[] }
  | { type: 'done'; turn_id: string; message: Message; scope: Scope; usage?: Record<string, unknown> | null }
  | ({ type: 'error' } & ApiErrorBody)
  | { type: 'stopped'; turn_id: string; message?: Message }
export interface UploadResult { material_id: string; version_id: string; job_id: string; status: string }
export interface KnowledgeBase { id: string; name: string; description: string | null; created_at: string; updated_at: string; embedding_connection_id?: string | null; embedding_binding?: { model: string; base_url: string; dimensions: number } | null }
export interface KnowledgeBaseInput { name: string; description?: string | null }
export interface Conversation { id: string; project_knowledge_base_id: string | null; title: string | null; created_at: string; updated_at: string; archived_at?: string | null }
export interface SearchItem {
  id: string
  type: 'knowledge_base' | 'material' | 'conversation' | 'fragment' | 'note'
  title: string
  knowledge_base_id: string | null
  excerpt?: string
  locator?: Record<string, unknown>
  retrieval_enabled?: boolean
}
export interface Dashboard {
  knowledge_base_count: number
  material_count: number
  conversation_count: number
  completed_qa_count: number
  due_flashcard_count: number
  due_wrong_question_count: number
  activity: { date: string; count: number }[]
}
export interface Capabilities { rag: boolean; library_rag?: Record<string, boolean>; ocr: boolean; answer_modes: string[]; upload_mime_types: string[]; max_upload_bytes: number }
export interface ModelUsageBucket { key: string; label?: string | null; calls: number; successes: number; failures: number; fallback_calls: number; total_tokens: number }
export interface ModelUsageTotals { calls: number; successes: number; failures: number; fallback_calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number }
export interface ModelUsageSummary { days: number; since: string; totals: ModelUsageTotals; by_day: ModelUsageBucket[]; by_purpose: ModelUsageBucket[]; by_model: ModelUsageBucket[]; by_connection: ModelUsageBucket[] }
export interface Turn { turn_id: string; conversation_id: string; status: string; scope: Scope; messages: Message[]; error_code: string | null }

export interface LearningSource {
  chunk_id?: string | null
  material_id: string
  material_version_id: string
  knowledge_base_id?: string | null
  title: string
  location_label: string
  locator?: Record<string, unknown>
  source_url?: string | null
  availability: 'available' | 'unavailable'
  quote?: string | null
  quote_status?: 'verified' | 'unverified' | 'unavailable' | null
}

export interface LearningResponseResult {
  response_id: string
  verdict: string | null
  verdict_source: string | null
  confidence: number | null
  feedback: string | null
  answer: Record<string, unknown> | null
  reference_answer: string | null
  explanation?: string | null
  sources?: LearningSource[]
}

export interface LearningItem {
  response_id: string
  quiz_item_id: string
  origin_wrong_id?: string | null
  knowledge_base_id?: string | null
  ordinal: number
  stem: string
  item_type: string
  options?: string[]
  reference_answer?: string | null
  explanation?: string | null
  verdict: string | null
  verdict_source: string | null
  confidence: number | null
  feedback: string | null
  answer: Record<string, unknown> | null
  sources?: LearningSource[]
}

export interface LearningAttempt {
  id: string
  quiz_id: string
  title?: string | null
  knowledge_base_id: string | null
  status: string
  items: LearningItem[]
}

export interface LearningAttemptSummary {
  id: string
  quiz_id: string
  title: string
  knowledge_base_id: string
  knowledge_base_name: string | null
  status: string
  item_count: number
  answered_count: number
  correct_count: number
  started_at: string
  updated_at: string
}

export interface WrongQuestionItem {
  id: string
  quiz_item_id: string
  knowledge_base_id?: string | null
  knowledge_base_name?: string | null
  item_type?: string
  stem: string
  options?: string[]
  reference_answer?: string | null
  explanation?: string | null
  last_feedback?: string | null
  last_answer?: string | null
  sources?: LearningSource[]
  status: string
  wrong_count: number
  last_wrong_at: string
  next_review_at: string | null
  is_due: boolean
}

export interface Material { id: string; knowledge_base_id: string; title: string; mime_type: string | null; byte_size: number | null; status: string; version_id: string | null; job_id: string | null; created_at: string; error_code: string | null; error_message: string | null; source_url?: string | null }
export interface Job { id: string; status: string; phase: string | null; progress: number; error_code: string | null; attempt_count: number }
export interface MaterialPreview { version_id: string; mime_type: string; page_count: number | null; sections: { text: string; locator: Record<string, unknown> }[] }
export interface MaterialReaderChunk { id: string; ordinal: number; text: string; locator: Record<string, unknown>; location_label: string; is_target: boolean }
export interface MaterialReader {
  material_id: string
  version_id: string
  knowledge_base_id: string
  title: string
  mime_type: string | null
  source_url: string | null
  page_count: number | null
  preview_page_count: number
  can_render_pages: boolean
  content_url: string | null
  target_chunk_id: string | null
  target_page: number | null
  target_quote: string | null
  chunks: MaterialReaderChunk[]
  total_chunks: number
  has_prev: boolean
  has_next: boolean
}
export interface LibraryOverview {
  file_count: number
  ready_count: number
  parsed_count: number
  fragment_count: number
  cut_count: number
  embedded_count: number
  ocr_count: number
  ocr_queue: number
  ocr_material_ids: string[]
  processing_count: number
  failed_count: number
  last_synced_at: string | null
  topics: string[]
  tags: string[]
  activity: Dashboard['activity']
}
export interface DocumentLocation { id: string; heading: string | null; locator: Record<string, unknown>; count: number }
export type Fragment = {
  id: string; text: string; material_id: string; title: string; mime_type: string;
  version_id: string; locator: Record<string, unknown>; estimated_tokens: number;
  retrieval_enabled: boolean; status: string; ready_at: string
}
export interface FragmentPage { total: number; page_size: number; items: Fragment[] }
export interface Classification { topic: string | null; tags: string[] }
export interface ClassificationState extends Classification {
  ready: boolean; suggestion_id: string | null; status: string; suggestion: Classification | null
}
export interface ClassificationDecision extends Classification { suggestion_id: string; ignore?: boolean }
export interface ModelConnection {
  id: string; name: string; kind: 'llm' | 'embedding'; base_url: string; model: string;
  dimensions: number | null; key_configured: boolean; key_suffix: string; revision: number;
  status: 'draft' | 'testing' | 'ready' | 'failed'; is_default: boolean;
  tested_at: string | null; error_code: string | null; libraries: { id: string; name: string }[]
}
export interface ModelConnections {
  items: ModelConnection[]; encryption_available: boolean;
  platform_llm: string | null; platform_embedding: string | null
}
export interface ModelConnectionInput {
  name: string; kind: 'llm' | 'embedding'; base_url: string; model: string; api_key?: string
}

export type StudioArtifactKind = 'study_guide' | 'mindmap' | 'formula_sheet'

export interface StudyNote {
  id: string
  knowledge_base_id: string
  knowledge_base_name: string | null
  title: string
  note_type: 'summary' | 'chapter_summary' | 'formula_sheet' | 'exam_guide'
  artifact_kind: string
  content_md: string
  edit_revision: number
  status: string
  ingested_material_id: string | null
  citations: Citation[]
  created_at: string
  updated_at: string
}

export interface FlashcardItem {
  id: string
  deck_id: string
  front: string
  back: string
  ease_factor: number
  interval_days: number
  repetition_count: number
  due_at: string
  is_due: boolean
  sources: { title: string; availability: 'available' | 'unavailable' }[]
}

export interface FlashcardDeckSummary {
  id: string
  knowledge_base_id: string
  knowledge_base_name: string | null
  title: string
  card_count: number
  due_count: number
  created_at: string
}

export interface DueFlashcard extends FlashcardItem {
  deck_title: string
  knowledge_base_id: string
  knowledge_base_name: string | null
}

export interface FlashcardDeckDetail extends FlashcardDeckSummary {
  cards: FlashcardItem[]
}

export interface HitTestItem {
  chunk_id: string
  material_id: string
  version_id: string
  title: string
  locator: Record<string, unknown>
  location_label: string
  text: string
  retrieval_enabled: boolean
  vector_rank: number | null
  vector_similarity: number | null
  above_threshold: boolean
  lexical_rank: number | null
  lexical_score: number | null
  matched_terms: string[]
  rrf_score: number
}

export interface HitTestResponse {
  query: string
  query_terms: string[]
  lexical_weight: number
  min_similarity_threshold: number
  items: HitTestItem[]
}
