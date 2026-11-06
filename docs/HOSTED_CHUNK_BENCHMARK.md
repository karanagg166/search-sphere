# Hosted token limits and initial chunk benchmark

The embedding and query model is pinned to Cohere `embed-english-light-v3.0`
(384 dimensions, 512 input tokens). Hosted `/v1/tokenize` with that exact model was
verified with public synthetic text. The coarse deterministic tiktoken vocabulary
is baked into the production image; cold ingestion requires no tokenizer download.
Final evidence is counted using Cohere before embedding, oversized evidence is
split while retaining provenance, and embedding uses `truncate=NONE`.

The initial service setting is 400 target / 480 maximum coarse tokens, with 60
coarse tokens of overlap, cosine distance 0.5 and percentile 80. The actual hosted
512-token cap is authoritative for every final chunk. These are initial values
supported by a small benchmark, not a capacity or general accuracy claim.

Executed 2026-11-06 using the confirmed Cohere trial key, isolated synthetic
PostgreSQL quota counters and only the generated public fixture in
`scripts/benchmark_hosted_chunking.py`. No uploaded or private data was used.
Four labeled policy/class-note questions and a long token-boundary fixture were
compared against the notes' 500/700/60 setting and the provisional 350/400 setting.

| Coarse target/max | Policy chunks | Dense marker Recall@1 (4 questions) | Long fixture max actual tokens before fitting | Over-limit chunks | Long fixture chunks after fitting |
|---|---:|---:|---:|---:|---:|
| 500/700 | 4 | 1.00 | 700 | 1 | 4 |
| 350/400 | 4 | 1.00 | 400 | 0 | 4 |
| 400/480 | 4 | 1.00 | 480 | 0 | 3 |

The 700-token chunk was split into verified chunks of at most 350 actual tokens.
All long-fixture runs preserved the final unique marker. The small policy fixture
produced the same boundaries in all three settings; it does not distinguish their
broader retrieval quality. Sequential latency (8.36s, 0.84s, 0.82s) shares token-count
caching and provider warmup and is **not** a P95 comparison. Hybrid/rerank evaluation,
answer/citation accuracy, refusal quality, and larger labeled corpora remain open.

Adjacent-unit semantic comparison is bounded to 32 units. Larger documents sample
16 actual adjacent seams across the document; unsampled seams use structural and
token rules. Semantic features may use a verified leading segment when an oversized
unit cannot be embedded; the evidence itself is retained and independently split.
The benchmark does not establish a semantic-boundary advantage over structural
chunking; that comparison remains in the evaluation phase.

Cohere tokenizer calls use the same policy, authorization recheck, trial-key approval,
shared monthly call cap, and error handling as other inference. Only digests and
counts are cached inside one authorized request/job; no private passage cache is
created. Background ingestion waits for tokenizer admission within a bounded
65-second window; failed reservations can retry before HTTP. Provider errors and
quota responses are not automatically replayed. Each wait releases the DB session.

Offline checks cover exact counts, invalid responses, per-scope caching/revocation,
Unicode preservation, oversized evidence, semantic sampling, admission, and quota
failures. Production-image checks exercise the deterministic tokenizer with network
access disabled; observed import/tokenizer/health peak RSS was 181 MiB under a
512 MiB container limit. This is a local measurement, not production P95 memory.
