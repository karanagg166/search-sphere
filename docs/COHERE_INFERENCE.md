# Cohere hosted inference and the $0 boundary

The owner confirmed the configured key is Trial/Evaluation on 2026-11-06 and
authorized Cohere only. A key change invalidates approval: the runtime key must
match `COHERE_TRIAL_KEY_SHA256`. Never commit the key or put it in browser variables.
No other provider, paid key, or local model fallback is permitted.

## Availability and free limits

Authenticated model metadata and public synthetic inference verified these pinned
versions on 2026-11-06:

| Capability | Version | Executed check |
| --- | --- | --- |
| Embedding | `embed-english-light-v3.0` | Two fixture documents and a query, 384 finite normalized dimensions; correct fixture ranked first, 1.37s |
| Reranking | `rerank-v3.5` | Maternity policy fixture ranked above unrelated parking fixture, 0.78s |
| Generation | `command-a-03-2025` | Returned the expected synthetic verification code, 0.73s |
| Vision/OCR | `command-a-vision-07-2025` | Read the expected code from a generated PNG, 1.05s |

One earlier rerank request exceeded the 15s timeout. A separate bounded retry passed;
these individual timings are capability checks, not capacity or P95 measurements.
No private document or medical record was used.

[Cohere trial limits](https://docs.cohere.com/v2/docs/rate-limits) currently specify
1,000 API calls/month, chat 20 requests/minute, text embeddings 2,000 inputs/minute,
rerank 10 requests/minute, and tokenize 100 requests/minute. The application reserves
quota atomically in PostgreSQL before each HTTP request, shared by API and ingestion:
at most 900 calls/calendar month, chat 15/minute, embedding 1,500 inputs/minute,
rerank 8/minute, tokenize 80/minute. Reservations count sent attempts, including
timeouts, rather than assuming a failed HTTP response was free. A rejected minute
reservation rolls back its monthly reservation. This cannot account for use of the
same key outside this database; Cohere's trial rejection remains authoritative.

The gateway makes one request, has a 15s timeout and 3s connection timeout, rejects
redirects, validates responses, and never retries implicitly. Quota, eligibility,
invalid responses and transient unavailability have separate safe error codes.
Database budget failures deny inference before network transmission. Counters store
only a key fingerprint, counts and expiry, never input or output text.

The [embedding model contract](https://docs.cohere.com/v2/docs/cohere-embed) has a
512-token input limit. Batches contain at most 32 inputs and explicitly use
`truncate=NONE`; overflow must fail rather than silently discard evidence. Existing
500/700-token chunk settings cannot be assumed compatible. Chunking benchmarks and
the versioned reindex/cutover remain separate required work. Equal dimensions do
not make old MiniLM vectors compatible with Cohere vectors.

## Data approval is independent of billing

[Cohere's enterprise data commitments](https://cohere.com/enterprise-data-commitments)
explicitly exclude trial API keys; trial use is governed by the Terms of Use and
Privacy Policy. A free key is not approval to send confidential or medical content.
Only public and synthetic classifications are enabled by default. Sensitive content
requires both a server-owned classification allowlist and a recorded independent
approval reference. No sensitive approval has been recorded in this rollout.

`inference_scope` must be entered only after authorization and after deriving data
classification from trusted PostgreSQL records. Client metadata and instructions
cannot approve themselves. Missing context, an unconfirmed key, or an unapproved
classification fails before quota reservation and HTTP. Tests verify concurrent
scope isolation and denial. API/worker bindings still need to apply this boundary
to every inference call; adding the gateway alone does not establish that coverage.

Migration 013 adds the RLS-protected quota counter. Real PostgreSQL tests cover
concurrent reservations, restart persistence, minute limits and transaction rollback.
Mock-HTTP tests use synthetic inputs for eligibility, batch/model consistency,
invalid vectors, malformed reranking, quota/outage bounds and incomplete generation.
