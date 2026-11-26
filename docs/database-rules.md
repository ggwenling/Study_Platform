# Database rules and implementation decisions

## Baseline

The current Alembic head is `0017_conversation_llm`, with 43 ORM-mapped business tables in `study`. The initial revision creates 37 tables. Revisions `0002_personal_rag`, `0003_bearer_auth`, `0009_model_call_records`, and `0010_model_connections` add six more. The project supports PostgreSQL 16 and later. CI uses 16, and the Windows pgvector installer defaults to 18. The backend uses Python 3.12, SQLAlchemy 2, psycopg 3, `citext`, and pgvector `vector(1024)`. [Original database design](architecture/02_Database_Design.md)

`users.status` is `VARCHAR(24)`: the source dictionary says `VARCHAR(16)` but explicitly allows the 20-character `pending_verification` state. Widening this one column is the necessary correction to preserve every declared state. All other declared enum values fit their dictionary column widths.

All UUID primary keys use the database default `gen_random_uuid()`. The sole exception is the dictionary's composite `(chunk_id, model_profile_id)` primary key on `chunk_embeddings`; neither component is generated. `ai_workflow_runs.id` also has the agreed database UUID default, although the source dictionary originally suggested application generation. Callers can supply a UUID when they need a run ID before persistence.

Every timestamp column is timezone-aware (`TIMESTAMPTZ`); application/database connections must operate in UTC. `created_at` and `updated_at` default to `now()`. SQLAlchemy `onupdate=func.now()` maintains `updated_at` on ORM/Core-generated updates. Direct SQL writers must explicitly maintain it; there are no timestamp triggers.

The ORM mappings are grouped into the identity, materials, knowledge-base, conversation, quiz, learning, model-connection, model-usage, and operations modules. `models/__init__.py` imports all mapped classes to register the complete metadata. `AuditLog.metadata_` maps the physical `metadata` column because `metadata` is reserved by SQLAlchemy declarative models. Models intentionally have no eager-loading relationships; service and input layers own application validation.

## Database-enforced invariants

- FKs explicitly use `ON DELETE RESTRICT`. The initial migration creates its 37 tables before adding named FKs, which allows self-references and dependencies between tables.
- Course-root ownership uses `(course_id, owner_user_id) -> courses(id, owner_user_id)` for materials, conversations, notes, quizzes, flashcard decks, study plans, and learning events. Quiz attempts must match their quiz owner. Workflow runs must match their conversation/job owner and have exactly one of those two subjects.
- Section parents belong to the same material version; knowledge-point parents belong to the same course. Direct self-parenting is rejected. Sections referenced by chunks and extracted source questions belong to the same material version.
- Each chunk's index belongs to its declared material version. All five source/citation tables reference the complete `(chunk_id, index_version_id, material_version_id)` identity. Composite FK targets have explicit, non-partial unique constraints.
- A message's workflow run belongs to its conversation. A partial unique index allows at most one assistant message per non-null workflow run. A job-only run cannot be attached to a conversation message.
- Quiz responses reference both the attempt/quiz pair and the item/quiz pair, rejecting items from a different quiz. A wrong-question record's last response, if present, must concern the same quiz item.
- There is only one current material version per material and one active index per source version. An active index must be `ready` and have `ready_at`.
- Knowledge-point `(course_id, parent_id, canonical_name)` uniqueness uses PostgreSQL `NULLS NOT DISTINCT`, covering root nodes with a null parent. This is supported by the project's PostgreSQL 16+ floor.
- Dictionary enums are named CHECK constraints. Counters and ordinals are nonnegative, version/generation/attempt numbers and sizes are positive, probabilities are in `[0,1]`, difficulty is in `[1,5]`, section levels are in `[1,8]`, review rating is in `[0,3]`, and progress is in `[0,100]`. Quiz attempt total score cannot exceed its stored maximum; response score is nonnegative.
- PostgreSQL checks the declared JSONB shapes where a check exists: for example, course settings and workflow scope are objects, conversation scope snapshots and question options are arrays, generated note/quiz/flashcard scopes may be objects or arrays, and answer values may be objects, arrays, strings, numbers, or booleans. These checks do not validate nested keys or semantics. Several JSONB fields, including knowledge-base `embedding_binding` / `retrieval_settings`, model-connection `versions`, and model-call `usage`, have no database shape check. SQL NULL is allowed only where the column is nullable; JSON `null` does not satisfy a declared object/array/answer check.
- The initial migration installs the B-tree, partial unique, chunk lexical, and cosine HNSW indexes. HNSW is created for an empty schema, and vector dimensions are fixed at 1024. Revision `0015_note_lexical_search` adds `notes.lexical` and its GIN index. It backfills in online mode. After an offline upgrade, search can still match the body of a note whose lexical value is NULL. Revision `0016_rerank_and_kb_retrieval` adds knowledge-base retrieval settings and accepts `rerank` model connections, `reindex` jobs, and the rerank model-call purpose.

## Rules reserved for application services

The dictionary has no columns that can express all cross-resource relationships with a declarative FK. No extra API-derived columns or complex triggers have been invented. The following are mandatory service-level rules for later feature implementation:

- Detect arbitrary-length cycles in section and knowledge-point trees; direct self-parent checks alone are insufficient.
- Verify course and owner consistency between cited sources and knowledge points/messages/notes/quiz items/flashcards. The database proves source lineage but does not prove the source is authorized for the destination.
- Verify quiz-item, flashcard, mastery-state, and study-plan-item knowledge points belong to the corresponding course and user; flashcard review owners belong to their card's deck; wrong-question owners and last-response attempt owners agree.
- Verify response score does not exceed the referenced quiz item's maximum, and summed scores/maxima match the immutable grading snapshot. These are cross-row rules.
- Validate JSON keys, nested schemas, bounded text lengths where the dictionary specifies no numeric cap, locator offsets, hashes, source-scope IDs, model profiles, and answer/rubric semantics through the domain input layer. Generic TEXT columns have no invented maximum length.
- Enforce immutable source text, version/index snapshots, citations, generated-note snapshots, ready quiz items, and append-only learning/review/audit history. RESTRICT FKs prevent unsafe referenced deletion, but ordinary database DML privileges do not make rows immutable.
- Switch current/active versions atomically only after upload/manifest validation. Serialize conversation sequences, answer submission, review state updates, and idempotent completion in transactions.
- Match job completion on current fencing, running status, no cancellation, and a live lease; a stale worker must affect zero rows and must not publish an index. Job resources and polymorphic event subjects require existence/ownership validation.
- Derive workflow `thread_id`, namespace, and frozen source scope on the server; authorize every resume and retrieval. A thread identifier is not an authorization token.
- Enforce deleted-root visibility and citation redaction immediately, then perform controlled purge, object deletion, checkpoint cleanup, and audit retention. No cascading purge is added.

## Runtime coverage and legacy model inventory

Mapped models define schema. Runtime use is a separate question. Current quiz APIs/services use quiz items, sources, attempts, responses, and wrong questions. Flashcard APIs/services use decks, cards, sources, and reviews. Flashcard reviews are written and queried for idempotency. No review-history listing API was found.

| Models / tables | Runtime coverage | Future reuse conditions |
| --- | --- | --- |
| `KnowledgePoint`, `KnowledgePointSource` | No application read/write paths found. The `source` value permits `ai` or `manual`, with no manual-entry API. `knowledge_point_id` is optional on quiz items and flashcards and remains an FK target in mastery/plan tables. Current services omit it. | Add authenticated APIs, validate owner/course and evidence-source relationships, and detect tree cycles. |
| `SourceQuestion` | Schema reservation for extracted source questions. Ingestion stores sections/chunks, and quiz generation writes generated items to `quiz_items`. No `source_questions` writer, reader, or API was found. | Connect extraction to a material-version snapshot, validate section/version identity and answer/locator shape, then define how extracted questions feed quizzes. |
| `MasteryState` | No application read/write paths found. The active wrong-question flow stores `open` / `reviewing` / `mastered` on `WrongQuestion` and does not write `mastery_states`. | Define estimator evidence/version semantics and validate ownership/course links before using mastery to schedule review. |
| `StudyPlan`, `StudyPlanItem` | No plan API, service, worker, or frontend path found. Their foreign keys are schema only today. | Add plan lifecycle/scheduling, validate owner/course/knowledge-point links, and make transitions transactional and idempotent. |
| `LearningEvent`, `OutboxEvent`, `AuditLog` | Mapped tables with no application read/write call sites found. | Define event immutability, outbox delivery/retry, and audit retention/access before wiring these tables. |

Coverage reflects source inspection. Row counts are outside this inventory.

## Alembic and LangGraph boundaries

`0001_initial_schema.py` contains frozen, explicit DDL for the original 37 tables. The revision chain through `0017_conversation_llm` adds six tables and evolves columns, checks, and indexes to the current 43-table metadata contract. Migrations do not call `Base.metadata.create_all()`. The initial revision explicitly creates the `study` schema and its tables. Downgrade removes indexes, then FKs, then business tables in reverse order. It preserves shared `citext` and `vector` extensions and the `study` schema containing Alembic's own version table.

Alembic manages the `study` schema. The initial revision also creates the `study_graph` namespace, while the standalone `setup_checkpointer.py` step lets the pinned official PostgreSQL checkpointer create and migrate its internal tables. Those tables are not business models. Checkpointer teardown must remove them before `study_graph` can be dropped. Downgrade intentionally uses no `CASCADE`. Production downgrade and purge require an operational plan.

Check schema and metadata parity with [test_database_schema.py](../backend/tests/integration/test_database_schema.py). Check online and offline upgrade plus downgrade behavior in a disposable `*_test` database with [test_migration_roundtrip.py](../backend/tests/integration/test_migration_roundtrip.py).
