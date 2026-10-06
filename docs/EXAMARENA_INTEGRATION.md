# ExamArena Integration Walkthrough

This guide provides a comprehensive walkthrough for integrating **ExamArena** (an online learning and examination platform) with Search-Sphere as a multi-tenant client.

---

## 1. Architecture & Tenant Hierarchy

In ExamArena:
- **Client Application ID**: `exam_arena`
- **Tenant ID**: School or Institution (e.g. `school_cbse_101`)
- **Collection ID**: Course or Subject (e.g. `class10_physics`, `class12_chemistry`)
- **Subject / Owner ID**: Teacher or Student (e.g. `teacher_sharma_401`)
- **Document Type**: `TEXTBOOK`, `QUESTION_BANK`, `SYLLABUS`, `PAST_PAPERS`

---

## 2. Step 1: Register Service Client

An administrator provisions credentials for ExamArena:

```http
POST /api/v1/clients
Authorization: Bearer <MASTER_ADMIN_SECRET>
Content-Type: application/json

{
  "client_id": "exam_arena",
  "name": "ExamArena Learning Platform",
  "allowed_scopes": [
    "documents:read",
    "documents:write",
    "documents:delete",
    "search:execute",
    "answers:generate",
    "collections:manage"
  ],
  "authorized_tenants": ["*"],
  "rate_limit_per_minute": 600
}
```

**Response:**
```json
{
  "client_id": "exam_arena",
  "name": "ExamArena Learning Platform",
  "api_key": "ss_live_exam_arena_abc123xyz...",
  "status": "active"
}
```
*Store `api_key` securely in ExamArena's environment secrets (`SEARCH_SPHERE_API_KEY`).*

---

## 3. Step 2: Create Course Collection

ExamArena creates a dedicated collection for a course:

```typescript
import { SearchSphereClient } from "@search-sphere/client";

const client = new SearchSphereClient({
  baseUrl: process.env.SEARCH_SPHERE_URL!,
  apiKey: process.env.SEARCH_SPHERE_API_KEY!,
  clientId: "exam_arena",
  tenantId: "school_cbse_101",
});

const collection = await client.createCollection(
  "class10_physics",
  "Class 10 Physics (CBSE)",
  "Prescribed NCERT physics textbook chapters and study guides"
);
```

---

## 4. Step 3: Ingest Textbook Chapters

ExamArena uploads and indexes chapter documents:

```typescript
// 1. Upload chapter file bytes
const fileBlob = new Blob([pdfBuffer], { type: "application/pdf" });
const uploadRes = await client.uploadFile(
  fileBlob,
  "ncert_ch10_light",
  "chapter_10_light_reflection.pdf",
  "class10_physics"
);

// 2. Register document for asynchronous indexing
const doc = await client.registerDocument({
  external_document_id: "ncert_ch10_light",
  storage_key: uploadRes.storage_key,
  file_name: "chapter_10_light_reflection.pdf",
  mime_type: "application/pdf",
  file_size: uploadRes.file_size,
  collection_id: "class10_physics",
  owner_subject_id: "teacher_sharma_401",
  document_type: "TEXTBOOK",
  metadata: {
    grade: 10,
    curriculum: "CBSE",
    chapter: 10,
    unit: "Optics"
  }
});

console.log("Document enqueued for indexing:", doc.id, doc.status);
```

---

## 5. Step 4: Semantic Search Across Course Materials

Students or teachers search course material with semantic relevance:

```typescript
const searchResults = await client.search("What is Snell's Law and refractive index?", {
  collectionId: "class10_physics",
  limit: 5,
});

for (const result of searchResults.results) {
  console.log(`[Rank ${result.rank}] (Score: ${result.score.toFixed(3)})`);
  console.log(`Page: ${result.start_page} | Doc: ${result.file_name}`);
  console.log(`Snippet: ${result.text}\n`);
}
```

---

## 6. Step 5: Grounded Explanation with Citations

Generate AI-powered tutoring explanations grounded in the textbook:

```typescript
const response = await client.generateAnswer("Explain total internal reflection with conditions.", {
  collectionId: "class10_physics",
  systemPrompt: "You are an expert high school physics educator. Explain concepts clearly with formulas.",
});

console.log("Grounded Answer:\n", response.answer);
console.log("\nSource Citations:");
for (const citation of response.citations) {
  console.log(`[${citation.citation_number}] Doc: ${citation.file_name} (Page ${citation.page_number})`);
  console.log(`Excerpt: ${citation.text_snippet}\n`);
}
```

---

## 7. Security & Isolation Guarantees

1. **School A cannot access School B**: Requests with `X-Tenant-ID: school_cbse_101` strictly search Qdrant points indexed with `tenant_id="school_cbse_101"`. Points belonging to `school_icse_202` are never touched.
2. **Duplicate Chapter IDs**: If both schools upload a document with ID `ncert_ch10_light`, they receive separate database rows, separate object storage locations, and never collide.
3. **Tamper Proof**: Even if a client injects `{ "tenant_id": "other_school" }` into metadata filters, the microservice strips and enforces the authenticated tenant boundary.
