# search-sphere-rag

Reusable, framework-agnostic RAG library extracted from Search Sphere:

- Hosted embedding adapters (Cohere & Google Gemini) with explicit document vs. query modes
- Strict vector validation (dimension, finiteness, batch completeness, input SHA-256)
- Embedding space compatibility contracts (never mix incompatible vector spaces)
- Reciprocal Rank Fusion (RRF) and token-overlap deduplication
- Hosted Cohere Rerank v3.5 adapter with deterministic hybrid fallback
- Conversation history dependency detection, query rewriting, and intent classification
- Port/Protocol interfaces for application-owned pgvector/lexical storage and authorization

Licensed under `AGPL-3.0-or-later`.
