# Standalone Search Sphere

The November 10 scope supersedes the three-application chatbot rollout. Search
Sphere is an independent application. Quick-Clinic and ExamArena will implement
their chatbots separately; their delivery and credentials are not release inputs.

## Baseline and change

| Requirement | Before this change | Current implementation / verification |
| --- | --- | --- |
| Native login, private PDF upload, indexing, search and cited Q&A | Working for approved public/synthetic fixtures | Retained native routes and PostgreSQL worker; authenticated production PDF gate required after deployment |
| Per-user documents and conversations | Working | Owner-scoped PostgreSQL retrieval, private Storage, authorized history and citation checks retained |
| External medical and `/api/v1` chatbot routes | Mounted in the production app | Removed from `src.main:app`; regression and image gates require absence and HTTP 404 |
| External ingestion jobs | Claimed by the shared worker | Default worker claims/recovers/adopts standalone jobs only; real PostgreSQL test proves queued/expired external jobs remain untouched |
| Consumer credentials and release dependencies | Medical secret in Render blueprint and a collection integration workflow | Removed blueprint input and collection workflow; native release and post-deploy jobs retained |
| Historical client data | Existing tables, originals and receipts | No destructive migration; preserved without exposing the retired HTTP surface |
| Unapproved/private model processing | Blocked | Remains blocked; document ownership does not establish provider eligibility |

The old adapter modules and schema history are retained for migration/data safety.
Historical adapter tests construct `apps/api/legacy_test_app.py`, a test-only app
outside `src` which is not copied into API or worker images. Those tests do not
remount consumer routes on production. No environment switch enables them there.
Already-authorized deletion receipts may finish physical cleanup; this does not
permit new consumer uploads or inference.

The native release gate verifies the exact Render revision, a new PDF, indexed
status, unique-marker retrieval, grounded page citations, native/conversation SSE
completion, saved turns, and zero physical vectors/deleted private originals.
It now also rejects any advertised or reachable retired consumer route. It does
not require Quick-Clinic or ExamArena credentials, deployments or workflows.

Use only Supabase PostgreSQL/pgvector and private Storage with hosted Cohere trial
inference for approved public/synthetic evaluation. No paid fallback is enabled.
Unknown or sensitive originals require an eligible, explicitly approved provider;
the successful synthetic test cannot approve an ordinary user's documents.

[The prior acceptance matrix](ACCEPTANCE_MATRIX.md) and
[rollout ledger](ROLLOUT_REQUIREMENTS.md) preserve historical evidence. Their
three-consumer and future-client requirements are superseded by this scope. Private
student files and medical chatbot work are outside Search Sphere's current scope.
