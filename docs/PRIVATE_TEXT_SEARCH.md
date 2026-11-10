# Private text search and AI processing

Native PDF text search is an explicit provider-free feature. It reads one owned
original from private Storage, extracts native PDF text in a bounded child process
and returns literal matching passages with page/bounding-box provenance. It does
not generate an answer, perform OCR, rewrite a query, load conversation history or
call a model. Search text and PDF contents are not logged or sent to Cohere.

`POST /private-search` requires the native user's bearer token and a JSON body
`{"document_id":"<owned ID>","query":"exact words or identifier","limit":5}`.
It works for an owned stored PDF even if its AI ingestion failed approval. Other
users get 404 before Storage access. Fresh PostgreSQL checks withhold results if
the document is deleted/changed or the account is disabled during processing.
No private excerpts are added to shared caches or AI conversation memory.

The child receives only a small settings header and the original bytes via stdin;
provider credentials and application environment are not inherited. Work is bound
to one PDF, 20 MiB, 100 pages, two million extracted characters, ten results,
verbatim 2,000-character snippets, one concurrent parser per process and a twelve-
second parser wall deadline. Child CPU/address space are limited to eight seconds/192 MiB and timed-out or
cancelled children are killed and awaited. Total admission/storage/search is thirty
seconds. API and worker images continue to exclude local model packages.

Image-only/scanned files explicitly report no native text; encrypted/oversized or
invalid PDFs return safe errors. Hosted OCR and generated AI answers still require
an eligible provider and server-owned approval. Unknown content is not relabeled
public, and ownership never grants provider approval. `GET /processing-options`
shows current owner-scope eligibility without sending data or reserving quota.

The configured Cohere trial is evaluation-only. Its current
[privacy policy](https://cohere.com/privacy) excludes personal-information processing
in trial/research inputs, and [pricing](https://cohere.com/pricing) restricts trial
keys from production/commercial use. This feature provides a useful private
retrieval path without changing the free-only or sensitive-content boundary.


## Repair inventory

| Issue | Before repair | Implementation / verification |
| --- | --- | --- |
| Unknown/private documents have no eligible trial provider | Confirmed account metadata: no inference-policy record and a job failed `provider_not_approved`; no document contents were read | Provider eligibility still fails closed; native private text search provides literal, cited retrieval without hosted inference |
| Upload success looks like processing success | Stored/queued files have no useful next step | Upload copy separates storage from indexing; document rows show job stages/queue age and approval failures |
| Approval error dead end | Only a retry-excluded administrator message | Explicit link to provider-free search from errors, documents and search header |
| Private scope and deletion | Backend ownership exists | Real PostgreSQL and HTTP tests deny another owner before Storage reads and withhold passages after deletion/disable/source changes |
| Offline resource use | No private native-search mode | Child wall/CPU/address-space/output limits, one concurrent parser, no inherited credentials; network-disabled image gate exercises real extraction under 512 MiB |
| Account changes in the browser | Shared query keys and asynchronous session restoration require repair | Document/conversation query keys are now owner-specific; session cancellation/cache reset follow-up is tracked separately |

Verification uses public/synthetic PDF fixtures only. This change does not approve
unknown content for Cohere, restore broken old vector indexes, add local model
weights, generate private AI answers or claim scanned OCR support.
