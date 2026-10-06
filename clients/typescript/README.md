# Search-Sphere TypeScript Client SDK

A lightweight, zero-dependency TypeScript/JavaScript client for the Search-Sphere multi-tenant RAG and semantic search microservice. Works in Node.js, Next.js, and browser environments.

## Installation

```bash
npm install @search-sphere/client
# or
pnpm add @search-sphere/client
```

## Quick Start

```typescript
import { SearchSphereClient } from "@search-sphere/client";

// Initialize client
const client = new SearchSphereClient({
  baseUrl: process.env.SEARCH_SPHERE_API_URL || "https://rag.yourdomain.com",
  apiKey: process.env.SEARCH_SPHERE_API_KEY!,
  clientId: "exam_arena",
  tenantId: "school_123",
});

// 1. Create a Collection (e.g. course/subject)
const collection = await client.createCollection(
  "physics_class_10",
  "10th Grade Physics",
  "NCERT and supplementary physics textbooks"
);

// 2. Upload Document Bytes
const fileBlob = new Blob([/* file bytes */], { type: "application/pdf" });
const uploadRes = await client.uploadFile(
  fileBlob,
  "doc_gravitation_ch7",
  "gravitation_chapter.pdf",
  "physics_class_10"
);

// 3. Register and Enqueue Ingestion
const doc = await client.registerDocument({
  external_document_id: "doc_gravitation_ch7",
  storage_key: uploadRes.storage_key,
  file_name: "gravitation_chapter.pdf",
  mime_type: uploadRes.mime_type,
  file_size: uploadRes.file_size,
  collection_id: "physics_class_10",
  document_type: "TEXTBOOK",
});

// 4. Two-Stage Reranked Semantic Search
const searchRes = await client.search("What is universal gravitation formula?", {
  collectionId: "physics_class_10",
  limit: 5,
});
for (const chunk of searchRes.results) {
  console.log(`[Rank ${chunk.rank}] (Score: ${chunk.score}) ${chunk.text.slice(0, 100)}...`);
}

// 5. Generate Grounded Answer with Citations
const answerRes = await client.generateAnswer("Explain Kepler's Third Law in simple terms.", {
  collectionId: "physics_class_10",
  systemPrompt: "You are an encouraging high school physics tutor.",
});
console.log("Answer:", answerRes.answer);
for (const citation of answerRes.citations) {
  console.log(`[${citation.citation_number}] Doc: ${citation.document_id}, Page: ${citation.page_number}`);
}
```
