# Project-scoped Supabase MCP

The repository's `.codex/config.toml` and `.agents/mcp_config.json` point only to
Search Sphere project `jyzjgoniuwexvipbbpwa`. They contain an endpoint, not credentials.
No global MCP configuration is changed by these files.

In a trusted Codex checkout, authenticate using:

```sh
codex mcp login supabase-search-sphere
codex mcp get supabase-search-sphere
```

AGY loads workspace servers from `.agents/mcp_config.json`; reopen this project and
use its `/mcp` manager to authenticate. The installed AGY 1.4.12 `mcp list` command
lists global servers only, so that command alone has not verified workspace loading.
See [AGY's configuration documentation](https://antigravity.google/docs/mcp) and
[Codex project configuration](https://learn.chatgpt.com/docs/config-file/config-basic).

Use an OAuth account that can access the target project. A publishable key or
server Storage secret does not grant Supabase Management API access. The supplied
server-only settings are stored in ignored `.env`; both modern `SUPABASE_SECRET_KEY`
and legacy `SUPABASE_SERVICE_ROLE_KEY` work for private Storage. They must never be
sent to a browser or committed. `SUPABASE_JWKS_URL` does not change the application's
existing JWT authentication contract.

The current OAuth registration supports database and analytics access but rejects
the documented `rest:read` / `rest:write` configuration scopes. Migration 011 therefore
restricts the effective Data API to an empty schema through its documented PostgreSQL
configuration. Backend SQL, Auth and Storage do not use that API surface. A future
Data API consumer requires an explicit access review and a new migration; do not
restore `public` as an exposed schema to work around an application failure.
