# Extraction and chunking capabilities

Status distinguishes implemented code, local real-input evidence and unavailable
production inference. An adapter contract test is not model accuracy evidence.

| Capability | Implementation | Current evidence / restriction |
|---|---|---|
| Native digital PDF text | PyMuPDF, original paragraphs, sentence offsets | Real PDFs; deterministic multilingual strings supported when present in the text layer |
| Heading hierarchy | Font/numbering heuristic; Docling headings in optional adapter | Cross-page regression; native heading levels are heuristic, not guaranteed |
| Multi-column reading order | Reused whitespace partition algorithm | Deterministic geometry; complex overlapping layouts remain a limitation |
| PDF rotation and transforms | Preserve rotation, crop box and PDF-to-page matrix | Real rotated PDF regression; visual orientation inference is unavailable |
| Validation | Bytes/pages/objects/streams/dimensions, Flate expansion, encrypted/repaired PDF rejection | Real malformed/encrypted/oversized/expansion tests; OS isolation required against native parser exploits |
| Docling region detection | Optional real converter adapter | Not loaded on 512 MiB Render; no local model execution claimed |
| Figure classifier | Docling picture classification enabled in optional adapter | Default classifier model/artifact version not yet live-validated; v2.5 accuracy unaccepted |
| Native ruled tables | Rows, headers, units, cell coordinates | Real two-page gold table; merged-cell candidates deferred to structural provider |
| Cross-page tables | Adjacent pages, bottom/top geometry, aligned columns, headers, section and caption compatibility | Real merge and negative matching rules; split-row reconciliation not implemented |
| TableFormer accurate | Configured in Docling adapter; merged spans retained as cells | Unverified until optional model artifacts and sufficient memory are available |
| Borderless/rotated/raster/nested-header/handwritten tables | Hosted Cohere rectangular view plus merged-cell schema; optional structural provider | Hosted output is unverified; no comprehensive accuracy promise; PP-StructureV3 unavailable |
| Panel proposals | OpenCV grayscale, threshold, morphology, contours, rectangles, lines, full whitespace gutter | Real two-panel image; parent retained; proposals never prove complete separation |
| Grounding DINO second detector | No verified free endpoint | Unavailable; no weight download or invented hosting assumption |
| Charts | Existing approved hosted Cohere path, optional GenAI adapter, typed axes/legend/values with exact-label vs estimate vs unreadable evidence | Actual Cohere raster fixture: one chart, 2/2 visible labels; broad type-specific benchmarks remain required. Optional Gemini account not configured. Interpretation remains PARTIAL |
| Bar/column/stacked/line/area/pie/donut/histogram/scatter/bubble/heatmap/box/violin/waterfall/radar/Sankey/dual-axis/scientific | Common chart schema and configurable remote interpretation | No type-specific accuracy benchmarks yet; none accepted as numerically reliable |
| Granite Vision / PP-Chart2Table | No free inference endpoint verified | Unavailable; no local VLM loads |
| Diagrams/flowcharts/UML/network/scientific/engineering | Dedicated guarded Cohere node/edge extraction; optional GenAI routing | Live synthetic diagram: 3/3 labels and 2/2 directed relationships; graph integrity checked. Independent visible-arrow verification unavailable |
| Image understanding | Existing guarded Cohere plus optional GenAI; description separate from literal OCR | Actual mixed chart/diagram raster fixture passed; broad photo/OCR acceptance remains open; source retained |
| Printed English OCR | Optional Tesseract subprocess | Real raster OCR test; eng language installed locally; not included in Render image |
| Paddle detection/recognition/orientation | Not enabled | No measured free-Render deployment; interface accepts alternative OCR providers |
| Devanagari/other OCR languages | Language-configurable Tesseract adapter | Requires installed traineddata; only eng tested, not a multilingual accuracy claim |
| Handwriting / TrOCR | No free compatible endpoint verified | Unavailable, not silently treated as successful printed OCR |
| Cloud Vision DOCUMENT_TEXT_DETECTION | Not integrated | Free allowance does not establish a zero-billing account; account/capability unverified |
| OCR/VLM merging | Deduplicate identical text+box; literal OCR preserved separately | Conflicting descriptions retained; no semantic overwrite |
| Prose chunking | Section-scoped sentence windows, 0–2 sentence overlap, actual tokenizer | Oversized sentences split with explicit fragment flag; Unicode retained |
| Table chunking | Whole small table, complete row groups, repeated headers/title/units | Every chunk points to whole table; oversize row fails without truncation |
| Chart chunking | Concise main representation and linked value chunks | Full chart object is retained; no oversized JSON embedding |
| Image chunking | Description plus linked exact OCR chunks | Empty/unextracted pictures produce no fake searchable chunk |
| Diagram chunking | Main description, connected edge units with both endpoint labels; isolated nodes | Oversized relation fails; graph remains available through parent object |
| Persistence | Storage-independent JSON, private directory adapter, Search Sphere JSONB bridge and private asset links | No dependency on web application; production uses existing private object storage |
| Indexing/RAG | Existing Search Sphere adapter; same model/dimensions/scopes | No existing-vector re-embedding or migration; real native synthetic indexing/RAG/deletion journey passed at deployed 19c8f56. Broad visual accuracy remains unaccepted |

Quality policy: native text must preserve exact sentence spans and valid source
coordinates; tables must have rectangular row views plus original cell structure;
continuation defaults to confidence >=0.9 and requires geometry and header/context
evidence. Uncertain figures and generated numbers never become verified facts.
Invalid provider JSON produces a safe categorized error. Provider timeouts and
429 responses remain distinguishable retryable outcomes. No paid fallback exists.

Limitations: native layout is an explicit fallback, not Docling-equivalent
coverage. Borderless tables can resemble prose and may not be discovered; formulas
remain native text rather than mathematical structure. There is no universal
content-completeness guarantee on arbitrary PDFs. Use the optional structural
profile for those requirements and keep its capability gate closed until evaluated.
