# AGENTS.md

This file defines the rules that AI coding agents must follow while working on this project.

The project is being built incrementally. Correct architecture, understandable code, testing, and learning are more important than implementing many features at once.

---

# 1. Project Overview

This project is a **document semantic-search system**.

The long-term goal is to allow a user to upload documents and later search those documents semantically.

The planned high-level flow is:

```text
User
  ↓
Upload PDF
  ↓
Validate PDF
  ↓
Store original PDF in Object Storage
  ↓
Store document metadata in PostgreSQL
  ↓
Extract text
  ↓
Clean / normalize text
  ↓
Split text into chunks
  ↓
Generate embeddings
  ↓
Store embeddings in Vector Storage
  ↓
Semantic Search
  ↓
Return relevant document chunks
```

This is the architectural direction of the project.

It does **not** mean all stages should be implemented immediately.

---

# 2. Current Development Philosophy

The most important rule in this project is:

```text
ONE SMALL FEATURE
      ↓
IMPLEMENT
      ↓
TEST
      ↓
VERIFY
      ↓
EXPLAIN
      ↓
STOP
```

Do not automatically continue to the next feature.

Do not implement future stages because they might be useful later.

Do not build the entire semantic-search pipeline in one task.

---

# 3. Development Phases

The project should evolve approximately through the following phases.

## Phase 1 — Document Storage

Goal:

Allow a user to upload a PDF and persist it safely.

Expected responsibilities:

```text
PDF Upload
    ↓
Validation
    ↓
Object Storage
    ↓
PostgreSQL Metadata
```

Phase 1 includes:

* accepting a PDF upload;
* validating the uploaded file;
* storing the original PDF in object storage;
* storing document metadata in PostgreSQL;
* returning a useful API response;
* appropriate tests.

Phase 1 does **not** include:

* PDF text extraction;
* text cleaning;
* chunking;
* embeddings;
* vector databases;
* semantic search;
* RAG;
* LLM responses.

Do not implement those until explicitly requested.

---

## Phase 2 — Text Extraction

Goal:

Extract readable text from a stored document.

Possible flow:

```text
Stored PDF
    ↓
PDF Parser
    ↓
Extracted Text
```

Do not start Phase 2 until Phase 1 is complete and the user explicitly asks to continue.

---

## Phase 3 — Text Cleaning

Goal:

Normalize extracted text before chunking.

Examples may include:

* removing unnecessary whitespace;
* normalizing line breaks;
* removing clearly useless artifacts;
* preserving meaningful document structure.

Do not aggressively modify document content.

---

## Phase 4 — Chunking

Goal:

Split cleaned document text into useful semantic-search units.

Chunking logic should be isolated from:

* PDF parsing;
* embedding generation;
* database access;
* API controllers.

Chunking should be independently testable.

---

## Phase 5 — Embeddings

Goal:

Generate vector embeddings for document chunks.

Embedding provider-specific code must be isolated behind a clear service boundary.

Do not spread embedding API calls throughout the application.

---

## Phase 6 — Vector Storage

Goal:

Persist embeddings and the metadata required to identify their source document and chunk.

Vector storage must not replace PostgreSQL's role as the application's primary relational metadata store unless intentionally redesigned.

---

## Phase 7 — Semantic Search

Goal:

Accept a search query, generate its embedding, retrieve relevant chunks, and return useful results.

Basic semantic search comes before unnecessary RAG complexity.

---

## Phase 8 — Optional RAG / Answer Generation

Only introduce LLM-generated answers if explicitly required.

Semantic search and RAG are not the same feature.

Do not automatically add an LLM simply because embeddings exist.

---

# 4. Storage Architecture

The project uses different storage systems for different responsibilities.

Do not mix their responsibilities.

## Object Storage

Object storage contains original uploaded files.

Example:

```text
documents/
    550e8400-e29b-41d4-a716-446655440000.pdf
```

Object storage is responsible for:

* original PDFs;
* potentially future derived files if needed.

Object storage is **not** responsible for application metadata.

---

## PostgreSQL

PostgreSQL stores relational application data.

For documents this may include:

```text
id
original_filename
storage_key
mime_type
file_size
status
created_at
updated_at
```

The actual schema must be designed when implementing the database feature.

Do not create speculative columns for future features unless they are currently required.

Do not store entire PDF binaries in PostgreSQL unless there is a strong project requirement to do so.

---

## Vector Storage

Vector storage is introduced later.

It is responsible for:

```text
embedding vector
chunk identifier
document identifier
chunk metadata required for retrieval
```

Do not introduce a vector database during Phase 1.

---

# 5. Source of Truth

Prefer clear ownership of data.

A likely direction is:

```text
PostgreSQL
    ↓
Document identity and processing state

Object Storage
    ↓
Original document

Vector Storage
    ↓
Searchable vector representation
```

Do not duplicate the same data across systems without a clear reason.

---

# 6. Document Identity

Every uploaded document should eventually have a stable internal identifier.

Do not rely on the original filename as the primary identity.

Bad:

```text
machine-learning.pdf
```

Better conceptually:

```text
document_id = UUID

storage_key =
documents/<document_id>.pdf
```

The original filename should still be stored as metadata when useful.

---

# 7. Document Processing Status

Documents will eventually pass through multiple processing stages.

Possible future statuses may include:

```text
uploaded
stored
extracting
processing
ready
failed
```

Do not implement every possible status immediately.

During each phase, introduce only statuses that the current implementation genuinely needs.

Avoid designing a complex state machine prematurely.

---

# 8. Backend Architecture

Backend code must have clear responsibility boundaries.

Prefer this direction:

```text
API / Route
     ↓
Application Service
     ↓
Repository / Storage Interface
     ↓
Infrastructure
     ↓
Database / Object Storage / External Provider
```

For document upload, a flow might eventually look like:

```text
POST /documents
      ↓
Document Route
      ↓
Document Upload Service
      ↓
File Validation
      ↓
Object Storage
      ↓
Document Repository
      ↓
PostgreSQL
```

The route/controller should mainly handle:

* request input;
* calling application logic;
* mapping results to HTTP responses.

It should not contain the complete business workflow.

---

# 9. Domain-Based Organization

Prefer organizing code around project capabilities instead of generic dumping grounds.

Good domain names include:

```text
documents
ingestion
storage
parsing
chunking
embeddings
search
```

Avoid generic folders or files such as:

```text
helpers
misc
stuff
common_logic
manager
everything_service
```

unless they genuinely represent a clear responsibility.

---

# 10. Project Structure

Do **not** force a directory structure before inspecting the existing repository and technology stack.

Always inspect the repository first.

Follow existing conventions when they are reasonable.

A possible backend structure may evolve toward:

```text
backend/
└── app/
    ├── documents/
    │   ├── api/
    │   ├── schemas/
    │   ├── services/
    │   └── repositories/
    │
    ├── ingestion/
    │   ├── parsing/
    │   ├── cleaning/
    │   └── chunking/
    │
    ├── embeddings/
    │
    ├── search/
    │
    ├── infrastructure/
    │   ├── database/
    │   ├── object_storage/
    │   └── vector_store/
    │
    └── core/
```

This is guidance, not a mandatory structure.

Do not create empty folders for future phases.

For example, during Phase 1, do not create:

```text
embeddings/
vector_store/
search/
chunking/
```

just because they will probably exist later.

Create structure when the feature actually requires it.

---

# 11. Frontend Structure

If the project contains a frontend, use component-based design.

Do not place an entire page and all application logic inside one component.

Prefer a direction such as:

```text
Page
  ↓
Feature Component
  ↓
Frontend Service / API Client
  ↓
Backend API
```

For document uploads, future UI might contain:

```text
documents/
    DocumentUpload.tsx
    DocumentList.tsx
    DocumentStatus.tsx
```

Only create components that are actually required.

Do not create speculative UI for future search or embeddings features.

---

# 12. Single Responsibility

Every important module should have one primary responsibility.

Bad:

```text
DocumentManager
    uploads files
    stores database rows
    parses PDFs
    cleans text
    creates chunks
    generates embeddings
    performs search
```

Better:

```text
DocumentUploadService
ObjectStorage
DocumentRepository
PdfParser
TextCleaner
ChunkingService
EmbeddingService
VectorRepository
SemanticSearchService
```

These are architectural concepts.

Do not create all of these classes immediately.

Create them only when their feature is being implemented.

---

# 13. One Feature at a Time

When the user requests a feature, implement only that feature.

Example project roadmap:

```text
1. Upload PDF
2. Store PDF
3. Store metadata
4. Extract text
5. Clean text
6. Chunk text
7. Generate embeddings
8. Store vectors
9. Semantic search
```

If the current task is:

```text
Upload PDF
```

do not also implement:

```text
chunking
embeddings
vector storage
search
```

After finishing the requested feature:

```text
test
verify
explain
stop
```

Wait for the user to request the next feature.

---

# 14. Feature Development Workflow

Every feature should follow this process:

```text
Understand request
      ↓
Inspect existing code
      ↓
Understand current architecture
      ↓
Design smallest clean change
      ↓
Implement
      ↓
Write/update tests
      ↓
Run relevant tests
      ↓
Fix failures
      ↓
Review diff
      ↓
Explain implementation
      ↓
Stop
```

Do not start coding before understanding existing code.

---

# 15. Keep Changes Small

Make the smallest reasonable change that fully implements the requested feature.

Do not unnecessarily:

* rename unrelated files;
* restructure unrelated folders;
* replace existing libraries;
* rewrite working components;
* change formatting throughout the repository;
* refactor unrelated code;
* introduce infrastructure for future features.

Feature changes should remain easy to review.

---

# 16. Tests Are Required

New behavior should have appropriate tests.

Use the test type appropriate to the feature.

Possible tests include:

* unit tests;
* integration tests;
* API tests;
* repository tests;
* component tests;
* end-to-end tests.

Not every feature requires all test types.

---

## Upload Tests

For document upload functionality, important cases may include:

```text
valid PDF
missing file
unsupported file type
empty file
oversized file
storage failure
database failure
```

Implement only tests relevant to current behavior.

---

## Unit Tests

Use unit tests for isolated business logic such as:

```text
file validation
text cleaning
chunking
transformations
status transitions
```

---

## Integration Tests

Use integration tests when behavior depends on infrastructure such as:

```text
PostgreSQL
Object Storage
Vector Storage
```

Do not mock everything when the purpose of the test is to verify integration.

---

## Bug Fixes

For a bug:

```text
Reproduce bug
     ↓
Add failing regression test
     ↓
Fix bug
     ↓
Confirm test passes
```

Do not weaken a valid test simply to make the suite pass.

---

# 17. Test Reporting

After implementing a feature, report:

```text
Tests added:
- ...

Tests executed:
- ...

Passed:
- ...

Failed:
- ...
```

Never claim a test passed if it was not actually executed.

If tests cannot run, explain the reason.

---

# 18. File Upload Security

Uploaded files are untrusted input.

Never trust:

* filename;
* file extension;
* client-provided MIME type;
* metadata;
* request parameters.

Validate files at the application boundary.

For PDF uploads, validation may include:

* expected file type;
* allowed size;
* non-empty file;
* safe generated storage key.

Do not use the user's filename directly as the storage key.

---

# 19. Filenames and Storage Keys

Preserve the original filename for display if useful.

Generate a safe internal storage identifier separately.

Example:

```text
Original filename:
research-paper.pdf

Internal ID:
550e8400-e29b-41d4-a716-446655440000

Storage key:
documents/550e8400-e29b-41d4-a716-446655440000.pdf
```

Never depend on user-controlled filenames for uniqueness.

---

# 20. Database Changes

Database schema changes must use migrations.

Never rely on manually created local database tables.

When modifying schema:

* create a migration;
* consider existing data;
* use correct constraints;
* add indexes only when justified;
* keep migrations understandable;
* consider rollback behavior where supported.

Do not add fields only because they might be useful someday.

---

# 21. Database Access

Keep database queries out of controllers/routes.

Prefer:

```text
Route
  ↓
Service
  ↓
Repository
  ↓
Database
```

Repositories handle persistence concerns.

Services handle application behavior.

Routes handle HTTP concerns.

---

# 22. Object Storage Access

Object-storage-specific SDK usage should not spread throughout the application.

Prefer a clear abstraction such as:

```text
ObjectStorage
```

with operations conceptually similar to:

```text
store()
delete()
exists()
```

The exact interface should match actual requirements.

Do not create abstractions with dozens of unused methods.

---

# 23. Future PDF Parsing

When PDF extraction is introduced, parsing logic must remain separate from:

* HTTP routes;
* PostgreSQL persistence;
* object storage;
* chunking;
* embeddings.

Conceptual flow:

```text
Document
    ↓
Load file
    ↓
PdfParser
    ↓
Extracted text
```

Do not implement parser logic during Phase 1.

---

# 24. Future Text Cleaning

Cleaning should preserve meaning.

Avoid transformations that could destroy:

* headings;
* paragraphs;
* lists;
* important punctuation;
* document structure.

Cleaning should be deterministic and testable.

---

# 25. Future Chunking

Chunking is an important semantic-search component.

Do not bury chunking logic inside an API controller or embedding service.

Chunk metadata may eventually contain information such as:

```text
chunk_id
document_id
chunk_index
text
page_number
```

Only store metadata actually required by the implementation.

---

# 26. Future Embeddings

Embedding generation belongs behind a dedicated boundary.

Prefer:

```text
EmbeddingService
```

rather than calling an external embedding API from many places.

Do not make the entire codebase dependent on one provider's response format.

Do not introduce provider abstractions unless they provide real value.

---

# 27. Future Vector Search

Vector storage and relational storage are different responsibilities.

Do not treat vector storage as a replacement for all application persistence.

Search results must retain enough information to trace a vector back to:

```text
chunk
  ↓
document
```

---

# 28. Async Processing

Do not introduce workers, queues, Redis, Kafka, Celery, or similar systems prematurely.

Simple operations can initially remain synchronous when appropriate.

Background processing may become useful later for operations such as:

```text
large PDF parsing
chunk generation
embedding generation
bulk ingestion
reprocessing
```

Introduce background processing only when the project genuinely needs it.

---

# 29. Error Handling

Expected failures must be handled explicitly.

Examples include:

```text
invalid upload
unsupported file
storage unavailable
database unavailable
PDF parsing failure
embedding provider failure
vector storage failure
```

Do not silently swallow exceptions.

Client-facing messages should be understandable.

Internal errors should contain enough context for debugging without exposing secrets.

---

# 30. Logging

Important backend operations should have useful structured context.

Useful context may include:

```text
document_id
operation
processing_stage
error_type
request_id
```

Do not unnecessarily log:

* full document contents;
* passwords;
* API keys;
* tokens;
* credentials;
* sensitive data.

---

# 31. Configuration

Configuration that changes between environments belongs in environment configuration.

Examples:

```text
DATABASE_URL
OBJECT_STORAGE_ENDPOINT
OBJECT_STORAGE_BUCKET
OBJECT_STORAGE_ACCESS_KEY
OBJECT_STORAGE_SECRET_KEY
EMBEDDING_API_KEY
VECTOR_DATABASE_URL
```

Only add variables needed by the current feature.

Never commit secrets.

Provide safe `.env.example` values where appropriate.

---

# 32. Dependencies

Before adding a dependency:

1. Inspect existing dependencies.
2. Check whether existing tools already solve the problem.
3. Confirm the new package is necessary.
4. Prefer actively maintained libraries.
5. Avoid large dependencies for trivial functionality.

Explain important new dependencies after implementation.

---

# 33. Types and Validation

Use strong typing supported by the project's language.

External input must be validated.

For Python:

* use type hints;
* use appropriate request/response schemas;
* validate data at boundaries.

For TypeScript:

* avoid unnecessary `any`;
* define meaningful interfaces/types;
* validate external data.

Do not create duplicate representations of the same model without a reason.

---

# 34. Naming

Names should reflect the domain.

Prefer:

```text
DocumentRepository
DocumentUploadService
PdfParser
ChunkingService
EmbeddingService
SemanticSearchService
storageKey
documentId
originalFilename
```

Avoid vague names such as:

```text
Manager
Helper
Thing
Data
Temp
Utils2
Misc
Stuff
Processor
```

when a more specific name is available.

A name such as `DocumentProcessor` should only be used if its responsibility is clearly defined.

---

# 35. File Size

Source files should normally stay reasonably small.

As a guideline:

```text
API routes/controllers     50–200 lines
services                   50–250 lines
repositories               50–250 lines
components                 50–200 lines
utilities                  20–150 lines
```

A source file should normally not exceed approximately **350 lines**.

This is not a mechanical rule.

Do not split a coherent file simply to satisfy a number.

Split files when they contain multiple responsibilities.

Generated and vendor files are excluded.

---

# 36. Avoid God Objects

Do not create:

```text
DocumentManager
SearchManager
AppService
Utils
Helpers
```

that gradually collect unrelated behavior.

When responsibilities become separate, separate them intentionally.

---

# 37. Avoid Premature Abstraction

Do not create:

* interfaces with one trivial implementation without reason;
* generic factories that are not needed;
* plugin systems before multiple implementations exist;
* event buses for simple function calls;
* dozens of DTO layers for tiny operations.

Prefer the simplest architecture that preserves clear boundaries.

---

# 38. Avoid Premature Optimization

Do not introduce complexity merely because the project may eventually handle millions of documents.

First build correct behavior.

Optimize when requirements or measurements justify it.

Future scaling concerns may include:

```text
background jobs
batch embeddings
connection pooling
caching
partitioning
distributed workers
vector indexes
rate limiting
```

These are future decisions, not Phase 1 requirements.

---

# 39. Preserve Existing Behavior

Before modifying working code, understand why it exists.

Changes should not unintentionally break existing functionality.

When modifying shared behavior, run appropriate regression tests.

---

# 40. Comments

Comments should explain **why**, not obvious syntax.

Bad:

```python
# Save document
save_document(document)
```

Useful:

```python
# Store the generated storage key rather than the user-provided filename
# so two documents with the same filename cannot overwrite each other.
save_document(document)
```

Prefer clear code over excessive comments.

---

# 41. No Fake Implementations

Do not create placeholder behavior and present it as finished functionality.

Examples:

* fake uploads;
* fake database persistence;
* random embeddings;
* hardcoded search results;
* mocked production behavior.

Mocks are allowed in tests.

Temporary production behavior must be clearly identified.

---

# 42. Do Not Change Architecture Silently

Explain meaningful architectural decisions.

Examples include:

* introducing object storage;
* adding PostgreSQL;
* changing database schema;
* introducing repositories;
* adding a background queue;
* introducing a vector database;
* changing API contracts;
* adding an embedding provider.

Explain:

```text
what changed
why it changed
what responsibility it has
what impact it has
```

---

# 43. Git Rules

Never push code automatically.

Do not run:

```bash
git push
git push origin <branch>
git push --force
git push --force-with-lease
```

unless the user explicitly requests a push.

Completing a feature does not grant permission to push.

---

# 44. Git Safety

Safe inspection commands may be used when needed:

```bash
git status
git diff
git log
git branch
```

Do not run destructive commands such as:

```bash
git reset --hard
git clean -fd
git checkout -- .
git restore .
```

without explicit user permission.

Never force-push unless explicitly requested.

---

# 45. Commits

Do not automatically assume the user wants a commit.

Before committing, review the changes.

When asked to commit:

* include only relevant feature changes;
* use a clear commit message;
* do not include unrelated modifications.

Commit and push are separate permissions.

Permission to commit does not mean permission to push.

---

# 46. Do Not Modify Unrelated Code

While implementing one feature, do not:

* fix unrelated bugs;
* reformat unrelated files;
* rename unrelated code;
* upgrade unrelated dependencies;
* redesign unrelated pages;
* introduce future architecture.

Mention unrelated problems if important, but leave them unchanged unless requested.

---

# 47. API Design

APIs should be predictable.

Use consistent:

* resource naming;
* request schemas;
* response schemas;
* HTTP status codes;
* validation errors;
* error response structures.

For example, document-related endpoints should be designed around the `document` resource rather than arbitrary action names where practical.

Avoid leaking internal database implementation details through APIs.

---

# 48. Feature Completion Definition

A feature is complete only when:

```text
Implementation complete
        +
Correct architecture
        +
Validation handled
        +
Expected failures handled
        +
Tests added
        +
Relevant tests executed
        +
Tests passing
        +
Diff reviewed
        +
No unrelated changes
        +
Flow explained to user
```

Code written does not automatically mean feature complete.

---

# 49. Required End-of-Feature Explanation

After completing a feature, explain it in beginner-friendly language.

Include:

```text
Feature completed:

What was added:

Files changed:

Why those files exist:

Flow:

Tests added:

Tests executed:

Test result:

Important architectural decision:

Next feature:
Not started.
```

Use a simple flow diagram when useful.

Example:

```text
User
 ↓
POST /documents
 ↓
Document API
 ↓
DocumentUploadService
 ↓
File validation
 ↓
Object Storage
 ↓
DocumentRepository
 ↓
PostgreSQL
 ↓
API response
```

Do not automatically start the next feature.

---

# 50. Current Project Rule

At the current stage of the project, focus only on **document upload and storage**.

Current intended flow:

```text
User selects PDF
      ↓
Upload request
      ↓
Backend validation
      ↓
Generate document ID / safe storage key
      ↓
Store original PDF in object storage
      ↓
Store document metadata in PostgreSQL
      ↓
Return document response
```

Do not currently implement:

```text
PDF extraction
text cleaning
chunking
embeddings
vector database
semantic search
RAG
LLM answering
background workers
distributed processing
```

unless the user explicitly changes the requested phase.

---

# Core Rules

The rules that override everything else are:

```text
UNDERSTAND THE EXISTING PROJECT FIRST

ONE FEATURE AT A TIME

DO NOT BUILD FUTURE PHASES EARLY

OBJECT STORAGE = ORIGINAL FILES

POSTGRESQL = APPLICATION / DOCUMENT METADATA

VECTOR STORAGE = EMBEDDINGS AND VECTOR RETRIEVAL

KEEP ROUTES, BUSINESS LOGIC, AND STORAGE RESPONSIBILITIES SEPARATE

CREATE FOLDERS ONLY WHEN THEY ARE NEEDED

USE DOMAIN-BASED ORGANIZATION

TEST EVERY IMPORTANT FEATURE

EXPLAIN EVERY COMPLETED FEATURE

DO NOT MODIFY UNRELATED CODE

DO NOT PUSH WITHOUT EXPLICIT PERMISSION

AFTER COMPLETING THE CURRENT FEATURE, STOP
```
