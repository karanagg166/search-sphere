# Third-party attribution

This project is AGPL-3.0-or-later. PyMuPDF/MuPDF remain subject to their own
[AGPL/commercial terms](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright).
Dependency wheels retain their own complete licenses and copyright notices.
The following inventory comes from distribution metadata in the locked core +
vision + OpenCV environment, excluding development tools (2026-11-13).

| Distribution | Version | Declared license metadata |
|---|---|---|
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| certifi | 2026.10.23 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| distro | 1.9.0 | Apache License, Version 2.0 |
| google-auth | 2.62.2 | Apache 2.0 |
| google-genai | 1.75.0 | Apache-2.0 |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| opencv-python-headless | 4.14.0.94 | Apache 2.0 |
| pillow | 13.0.0 | MIT-CMU |
| pyasn1 | 0.6.5 | BSD-2-Clause |
| pyasn1-modules | 0.4.2 | BSD |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| pydantic-core | 2.50.4 | MIT |
| pymupdf | 1.28.2 | Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License |
| regex | 2026.11.9 | Apache-2.0 AND CNRI-Python |
| requests | 2.34.2 | Apache-2.0 |
| sniffio | 1.3.1 | MIT OR Apache-2.0 |
| tenacity | 9.1.4 | Apache 2.0 |
| tiktoken | 0.14.0 | MIT License Copyright (c) 2022 OpenAI, Shantanu Jain Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated document (see distribution license) |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.5 | MIT |
| urllib3 | 2.8.0 | MIT |
| websockets | 16.1.1 | BSD-3-Clause |

The optional Tesseract executable and language data are distributed separately
under their upstream notices; this package does not bundle them.
Tesseract 5.5.4 / eng traineddata were used only through the local subprocess adapter.

Docling is an optional MIT-licensed library. Its transitive ML dependencies and
model artifact licenses must be reviewed in the selected processing environment
before enabling that extra. No model artifacts are redistributed here. The
Docling / TableFormer / DocumentFigureClassifier-v2.5 artifacts were not downloaded
or approved as part of this release candidate. Their exact artifact licenses and
resource/quality acceptance remain a blocker rather than an implied approval.

Grounding DINO, PaddleOCR models, TrOCR and Granite Vision are not enabled or
bundled. Google Gemini is a hosted service governed by the provider terms; an
open-source SDK license does not establish model usage, data or free-tier terms.

Search Sphere reading-order and native-table strategies were reused from this
repository. No third-party model-loading or provider implementation was copied.
