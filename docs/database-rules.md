# Database rules and implementation decisions

## Baseline

The source is `02_Database_Design.md` version 2.0 (2026-11-14). The implementation contains exactly 37 business tables in `study`. The approved local target is PostgreSQL **18**, a deliberate change from the document's PostgreSQL 16 baseline. Python 3.12, SQLAlchemy 2, psycopg 3, `citext`, and pgvector `vector(1024)` are the agreed stack. No pre-existing KnowFlow/Flyway schema is reused.

`users.status` is `VARCHAR(24)`: the source dictionary says `VARCHAR(16)` but explicitly allows the 20-character `pending_verification` state. Widening this one column is the necessary correction to preserve every declared state. All other declared enum values fit their dictionary column widths.

All UUID primary keys use the database default `gen_random_uuid()`. The sole exception is the dictionary's composite `(chunk_id, model_profile_id)` primary key on `chunk_embeddings`; neither component is generated. `ai_workflow_runs.id` also has the agreed database UUID default, although the source dictionary originally suggested application generation. Callers can supply a UUID when they need a run ID before persistence.

Every timestamp column is timezone-aware (`TIMESTAMPTZ`); application/database connections must operate in UTC. `created_at` and `updated_at` default to `now()`. SQLAlchemy `onupdate=func.now()` maintains `updated_at` on ORM/Core-generated updates. Direct SQL writers must explicitly maintain it; there are no timestamp triggers.

The six model modules follow the six dictionary domains. `models/__init__.py` imports all models to register the complete metadata. `AuditLog.metadata_` maps the physical `metadata` column because `metadata` is reserved by SQLAlchemy declarative models. Models intentionally have no eager-loading relationships or application validation layer.

## Database-enforced invariants

- Every FK explicitly uses `ON DELETE RESTRICT`. The initial migration creates every table, then adds named FKs, which handles self-reference and future dependency cycles without implicit metadata creation. The ORM marks FKs `use_alter=True` as well.
- Course-root ownership uses `(course_id, owner_user_id) -> courses(id, owner_user_id)` for materials, conversations, notes, quizzes, flashcard decks, study plans, and learning events. Quiz attempts must match their quiz owner. Workflow runs must match their conversation/job owner and have exactly one of those two subjects.
- Section parents belong to the same material version; knowledge-point parents belong to the same course. Direct self-parenting is rejected. Sections referenced by chunks and extracted source questions belong to the same material version.
- Each chunk's index belongs to its declared material version. All five source/citation tables reference the complete `(chunk_id, index_version_id, material_version_id)` identity. Composite FK targets have explicit, non-partial unique constraints.
- A message's workflow run belongs to its conversation. A partial unique index allows at most one assistant message per non-null workflow run. A job-only run cannot be attached to a conversation message.
- Quiz responses reference both the attempt/quiz pair and the item/quiz pair, rejecting items from a different quiz. A wrong-question record's last response, if present, must concern the same quiz item.
- There is only one current material version per material and one active index per source version. An active index must be `ready` and have `ready_at`.
- Knowledge-point `(course_id, parent_id, canonical_name)` uniqueness uses PostgreSQL `NULLS NOT DISTINCT`, covering root nodes with a null parent. PostgreSQL 18 supports this syntax.
- Dictionary enums are named CHECK constraints. Counters and ordinals are nonnegative, version/generation/attempt numbers and sizes are positive, probabilities are in `[0,1]`, difficulty is in `[1,5]`, section levels are in `[1,8]`, review rating is in `[0,3]`, and progress is in `[0,100]`. Quiz attempt total score cannot exceed its stored maximum; response score is nonnegative.
- JSONB object payload/configuration/state fields have basic `jsonb_typeof` checks. Options and conversation scope snapshots are arrays. Unspecified generated `source_scope` structures accept objects or arrays; workflow scope is an object as implied by its `{}` default. Answer values may be objects, arrays, strings, numbers, or booleans, without imposing an undocumented answer schema. SQL NULL is allowed only where the dictionary permits it; a JSON `null` is not an answer or object payload.
- Required B-tree, partial unique, GIN lexical, and cosine HNSW indexes are installed in the initial migration. HNSW is created even for an empty database as explicitly agreed. Vector dimensions are physically fixed at 1024.

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

## Alembic and LangGraph boundaries

`0001_initial_schema.py` contains frozen, explicit Alembic DDL. It imports no application models and never calls `Base.metadata.create_all()`. Downgrade explicitly removes indexes, then FKs, then business tables in reverse order. It preserves shared `citext` and `vector` extensions and the `study` schema containing Alembic's own version table.

Alembic creates only the `study_graph` schema; no LangGraph internal tables are guessed or declared as business models. The pinned official PostgreSQL checkpointer's separate setup step owns those tables and its migrations. Its teardown must remove its own tables before this initial revision can drop `study_graph`; downgrade intentionally uses no `CASCADE`. Production downgrade and purge require an operational plan.

The metadata/DDL checks can run without a database. Actual PostgreSQL upgrade/downgrade, extension availability, vector operations, invalid-row constraint cases, and schema drift checks must run against the expressly configured local test database; model compilation is not a substitute for those integration checks.
