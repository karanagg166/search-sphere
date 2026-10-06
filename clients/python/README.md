# Search-Sphere Python Client SDK

A lightweight Python client for the Search-Sphere multi-tenant RAG and semantic search microservice.

## Installation

```bash
pip install search-sphere-client
```

## Quick Start

```python
from search_sphere import SearchSphereClient

# Initialize client
client = SearchSphereClient(
    base_url="https://rag.yourdomain.com",
    api_key="ssp_live_...",
    client_id="exam_arena",
    tenant_id="school_123",
)

# 1. Create a Collection (e.g. course/subject)
collection = client.create_collection(
    collection_id="physics_class_10",
    name="10th Grade Physics",
    description="NCERT and supplementary physics textbooks",
)

# 2. Upload and Ingest a Document
with open("gravitation_chapter.pdf", "rb") as f:
    upload_res = client.upload_file(
        file=f,
        document_id="doc_gravitation_ch7",
        collection_id="physics_class_10",
        filename="gravitation_chapter.pdf",
    )

doc = client.register_document(
    external_document_id="doc_gravitation_ch7",
    storage_key=upload_res["storage_key"],
    file_name="gravitation_chapter.pdf",
    mime_type=upload_res["mime_type"],
    file_size=upload_res["file_size"],
    collection_id="physics_class_10",
    document_type="TEXTBOOK",
)

# 3. Two-Stage Reranked Semantic Search
search_res = client.search(
    query="What is universal gravitation formula?",
    collection_id="physics_class_10",
    limit=5,
)
for chunk in search_res.results:
    print(f"[{chunk.rank}] (Score: {chunk.score:.3f}) {chunk.text[:120]}...")

# 4. Generate Grounded Answer with Citations
answer_res = client.generate_answer(
    query="Explain Kepler's Third Law in simple terms with formula.",
    collection_id="physics_class_10",
    system_prompt="You are a friendly high school physics tutor.",
)
print("Answer:", answer_res.answer)
for c in answer_res.citations:
    print(f"[{c.citation_number}] Doc: {c.document_id}, Page: {c.page_number}")
```
