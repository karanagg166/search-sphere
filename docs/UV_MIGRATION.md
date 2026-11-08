# Workspace Python tooling migration — 2026-11-08

The owner requested uv globally and across every project under the workspace.
uv 0.13.7 is installed through Homebrew. The Python rule is present in the home,
workspace and Codex `AGENTS.md` files and the existing Antigravity/Gemini global
`GEMINI.md`. Existing instructions were preserved. JavaScript remains pnpm-only.

Existing requirements and constraints remain authoritative: installation uses
`uv pip install` against those files. Local setup creates a virtual environment
with `uv venv`; `--system` is restricted to disposable containers and CI runners.
No direct pip installer or bootstrap remains in the migrated active setup/build
paths. Historical records of earlier pip executions retain their original facts;
the `pip-audit` security scanner remains enabled and is installed through uv.

| Project | Change and status | Verification |
| --- | --- | --- |
| Search Sphere | [#82](https://github.com/karanagg166/search-sphere/pull/82), merged `6d8713c` | API/worker Docker builds and network-disabled hosted-image checks at 512 MiB pass; about 191 MiB peak RSS; 160 focused runtime/release/receipt/monitor tests; [PR CI](https://github.com/karanagg166/search-sphere/actions/runs/42489608323) and [main CI](https://github.com/karanagg166/search-sphere/actions/runs/42489927187) pass. |
| ExamArena | [#43](https://github.com/karanagg166/ExamArena/pull/43), merged `c95aeb6` | API/worker Python 3.11 Docker builds, dependency checks, offline API import and 63 consumer tests against disposable PostgreSQL pass. [PR CI](https://github.com/karanagg166/ExamArena/actions/runs/42489997040) passes, including PostgreSQL/Redis worker and full-stack checks. [Main CI](https://github.com/karanagg166/ExamArena/actions/runs/42491826128) and [current live course journey](https://github.com/karanagg166/ExamArena/actions/runs/42495385199) pass; Vercel production logs confirm uv installation. |
| URL Shortener | [#1](https://github.com/karanagg166/url-shortner/pull/1), pushed/open | Backend Docker build, dependency compatibility and offline FastAPI import pass; Vercel preview passes. |
| Docstribe AI | [#1](https://github.com/karanagg166/docstribe-ai/pull/1), pushed/open | Non-root backend Docker build, uv dependency check and offline FastAPI import pass. Backend Vercel preview is blocked by unchanged configuration declaring both `builds` and `functions`; no successful Vercel backend deployment is claimed. |
| Skylink ESP32 | [#4](https://github.com/Poojan38380/Skylink_ESP32/pull/4), pushed/open | Planned PlatformIO CI example uses uv; documentation-only review and whitespace checks. No workflow was enabled or dispatched. |
| Automated Apply | [#49](https://github.com/karanagg166/automated-apply/pull/49), pushed/open | Runtime image/dependency checks pass; optional browser requirements resolve; 13 migration fixtures pass, one dedicated-PostgreSQL case is skipped. Manual-only workflow policy and extension prerequisite syntax pass. Stacked on the existing feature branch; its acceptance report records the exact implementation SHA and owner-review requirement. |
| ApertureGEO | Local commit `967f9dcbdc2135abbfd124f2d1cb0391e200611a`; publication skipped at the owner’s request | Backend Docker build, dependency check, offline FastAPI import and workflow YAML/installer ordering pass. No remote is configured; the owner asked to skip publication. No new public repository was created. |

Nine other top-level Git repositories had no active direct Python installer to
replace: `test`, `bus-booking-app`, `shop-sizzle`, `Quick-Clinic`, `wishify`,
`portfolio`, `apply-Extenion`, `Vyapar-Hub` and `helm-charts`. They received no empty
commits. `semantic-search` resolves to the Search Sphere checkout and is not a
separate repository. Existing unrelated dirty files, installed releases and private runtime
configuration were preserved. Dirty/owned projects were migrated in isolated
worktrees; this does not claim their primary checkouts have switched branches.

Docker uses the official uv 0.13.7 multi-platform image pinned to digest
`96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490`.
Actions uses `astral-sh/setup-uv` pinned to
`68559e99cc807c1eb7e9238fd43a15bba460eec2` with explicit uv 0.13.7.
Existing dependency specifications, Python versions, application entrypoints and
pnpm caching were preserved. No hosted model inference was needed for migration.

Temporary verification images were removed after validation, and another
6.256 GB of build cache was reclaimed. Docker reports zero build cache afterward;
the four active repair containers and all 83 persistent volumes were retained.
This tooling migration is independent of the still-open Search Sphere feature
rollout and its exact-revision post-deployment acceptance requirements.
