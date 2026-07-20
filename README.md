# Team Sync MCP Server

A self-hosted synchronization hub for frontend and backend teams using Cursor. It exposes MCP tools for publishing and reading project state across **multiple projects**, stores everything locally, and serves a React dashboard for visual inspection.

## What It Provides

- A Python MCP server using the official `mcp` SDK and Streamable HTTP transport at `http://localhost:8080/mcp`.
- Multi-project shared state for API contracts, requirements, component specs, and changelog entries.
- Cursor-driven **subproject onboarding**: after connecting MCP, the agent reviews the open workspace and bulk-publishes what other teams need.
- SQLite persistence by default, with an optional JSON-file backend.
- REST endpoints at `/api/projects...` plus live SSE at `/api/events`.
- A React, Tailwind CSS, and shadcn-style dashboard at `http://localhost:8080/dashboard/`.
- Optional shared bearer token for MCP calls and write endpoints.
- Local username/password login, API keys for Cursor/CI, and project RBAC (owner/editor/viewer).

## Quick Start With Docker

```bash
docker compose up --build
```

Open the dashboard:

```text
http://localhost:8080/dashboard/
```

The SQLite database is stored in `./data` through the compose volume.

## Local Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m sync_mcp
```

Dashboard with hot reload:

```bash
cd dashboard
npm install
npm run dev
```

Production-like local UI:

```bash
cd dashboard && npm install && npm run build && cd ..
python -m sync_mcp
```

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `SYNC_MCP_PROJECT` | `my-project` | Name used only when migrating a legacy single-project database. |
| `SYNC_MCP_STORAGE` | `sqlite` | Use `sqlite` or `json`. |
| `SYNC_MCP_DATA_DIR` | `./data` | Directory for persistent state. |
| `SYNC_MCP_HOST` | `0.0.0.0` | Server bind host. |
| `SYNC_MCP_PORT` | `8080` | Server port. |
| `SYNC_MCP_SECRET` | empty | JWT signing secret (auto-generated for the process if empty; set in production). |
| `SYNC_MCP_ADMIN_USERNAME` | empty | Bootstrap admin username when no users exist. |
| `SYNC_MCP_ADMIN_PASSWORD` | empty | Bootstrap admin password when no users exist. |
| `SYNC_MCP_TOKEN` | empty | Deprecated; migrated once into an admin API key if present and no keys exist. |
| `SYNC_MCP_HTTP_PROXY` | empty | HTTP proxy for outbound **public** web fetches (OpenAPI URLs on the internet). Falls back to `HTTP_PROXY`. |
| `SYNC_MCP_HTTPS_PROXY` | empty | HTTPS proxy for outbound public web fetches. Falls back to `HTTPS_PROXY`, then `SYNC_MCP_HTTP_PROXY` / `HTTP_PROXY`. |

**HTTP proxy (internet only):** OpenAPI auto-sync and `import_openapi` URL fetches use a proxy only for non-local hosts. Private LAN (`192.168.x.x`), loopback (`127.0.0.1`, `localhost`), and link-local addresses always connect directly — so a corporate proxy does not break LAN OpenAPI URLs like `http://192.168.17.29:8001/openapi.json`. `sync_agent` status posts to the hub use the same rules. Cursor SDK cloud traffic is separate; set `HTTPS_PROXY` on the agent process if Cursor itself needs a proxy.

Existing single-project databases are auto-migrated into one project (slug derived from `SYNC_MCP_PROJECT`) on startup.

## Authentication and RBAC

1. Set `SYNC_MCP_ADMIN_USERNAME` / `SYNC_MCP_ADMIN_PASSWORD` and start the hub — the first admin is created if the DB has no users.
2. Open the dashboard and **sign in**.
3. Under **API keys**, create a key (copy it once). Admins can manage users under **Admin**.
4. Project roles: **owner** (edit/delete/members), **editor** (sync/publish), **viewer** (read). Hub **admin** can manage all projects and users.

| Action | Hub admin | Owner | Editor | Viewer |
| --- | --- | --- | --- | --- |
| Read project | all | yes | yes | yes |
| Create project | yes | yes* | no | no |
| Edit / delete project | yes | yes | no | no |
| Sync / publish | yes | yes | yes | no |
| Manage members | yes | yes | no | no |
| Hub settings / users | yes | no | no | no |

\*Any signed-in hub member can create a project and becomes its owner.

## Cursor MCP Configuration

Use an **API key** (not the dashboard JWT) plus **Project** and **Team** headers (preferred):

```json
{
  "mcpServers": {
    "team-sync": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer sk_YOUR_API_KEY",
        "Project": "adra",
        "Team": "backend"
      }
    }
  }
}
```

Also accepted: `Project: adra/backend`, or legacy `Project: adra-backend`. Team slugs are lowercase (`frontend`, `backend`, `mobile`, `qa`, …). Bearer + Project (with Team, slash form, or legacy suffix) are required on `/mcp`. The API key’s user must be a member of that project (or hub admin).

If you previously saw `POST ... Not Found` / SSE `404`, restart Team Sync — an older bug mounted FastMCP at `/mcp/mcp`. The endpoint is now `/mcp`.

Also make sure the hub process is running (`python -m sync_mcp` or `docker compose up`) before enabling the MCP server in Cursor. Early `ERR_CONNECTION_REFUSED` means nothing was listening on port 8080.

## CI / REST import (no Cursor SDK)

When the Cursor SDK is unavailable (e.g. region blocks), push contracts with the hub REST API:

```bash
pip install -e .
# Backend OpenAPI (URL must be reachable from the machine running the CLI)
sync-mcp-import openapi --hub http://192.168.17.29:8080 \
  --api-key sk_... --project adra --url http://192.168.17.29:8001/openapi.json

# Frontend snapshot JSON (supports --replace to prune stale components)
sync-mcp-import snapshot --hub http://192.168.17.29:8080 \
  --api-key sk_... --project adra --team frontend --file snapshot.json --replace

# Trigger on_commit OpenAPI sync
sync-mcp-import commit --hub http://192.168.17.29:8080 --api-key sk_... --project adra --sha abc123
```

Example `snapshot.json`:

```json
{
  "team": "frontend",
  "components": [{"name": "UserTable", "spec": "props: rows[]"}],
  "requirements": [{"id": "tax", "title": "Need tax field"}],
  "artifacts": [{"kind": "env_var", "key": "VITE_API_URL", "title": "API base"}],
  "replace": true
}
```

## Automatic OpenAPI sync

Backend teams do **not** need to republish manually after each code change.

1. In the dashboard, set the project **OpenAPI URL** to an address the **hub process** can reach (e.g. `http://192.168.17.29:8001/openapi.json`) and enable auto-sync. Do **not** use `localhost` / `127.0.0.1` when the hub runs in Docker — that points at the container itself. Use the host LAN IP, `host.docker.internal` (Docker Desktop), or `http://172.17.0.1:<port>` (Linux bridge gateway) depending on where the backend listens.
2. Choose **sync mode**:
   - **Every N seconds** (`interval`): fetch OpenAPI on each hub poll tick (default).
   - **After each commit** (`on_commit`): on each tick, cheaply read local `git rev-parse HEAD`; only fetch OpenAPI when the SHA changes. Requires a **Git repo path** visible to the hub process. With Docker, `docker-compose.yml` bind-mounts `${SYNC_MCP_GIT_HOST_PATH:-../AD2}` → `/repos/AD2`; set the project **Git repo path** to `/repos/AD2` (not a host path like `/home/.../AD2`).
3. In **Auto-sync settings**, set the hub poll / check cadence (default **30 seconds**, min 5).
4. Use **Sync now** on the project page to force an immediate OpenAPI refresh.

Remote/CI can trigger the same sync path without local git on the hub:

```bash
curl -X POST http://localhost:8080/api/projects/adra/hooks/commit \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"commit_sha":"abc123"}'
```

`import_openapi` from Cursor also saves the `openapi_url` and turns on auto-sync for that project.

## Team-local sync agents (autonomous crawl)

Each subproject team runs an agent **on a machine that has their checkout**. The agent uses the Cursor SDK to read the codebase and push updates to the hub over MCP. The hub does **not** need those repos mounted.

```bash
# On the frontend team's machine
pip install -e ".[agent]"
export CURSOR_API_KEY=...                    # from Cursor dashboard
export SYNC_AGENT_HUB_URL=http://192.168.17.29:8080/mcp
export SYNC_AGENT_API_KEY=sk_...             # hub API key (editor+ on project)
export SYNC_AGENT_PROJECT=adra-frontend
export SYNC_AGENT_CWD=/path/to/frontend-repo
export SYNC_AGENT_MODE=on_commit             # or: schedule | once
# export SYNC_AGENT_INTERVAL_SECONDS=300     # for schedule / poll cadence
python -m sync_agent
# or: sync-mcp-agent
```

Backend example: set `SYNC_AGENT_PROJECT=adra-backend`, optionally `SYNC_AGENT_OPENAPI_URL=http://localhost:8001/openapi.json`.

| Mode | Behavior |
| --- | --- |
| `once` | Single crawl then exit |
| `schedule` | Crawl every `SYNC_AGENT_INTERVAL_SECONDS` |
| `on_commit` | Watch local `git HEAD`; crawl when SHA changes |

After each run the agent reports status to `POST /api/projects/{id}/agent-status` (shown on the project dashboard). If that returns **404**, the hub process is an older build — rebuild/redeploy (e.g. `docker compose up --build`) so the new route is present. Status reporting is best-effort and does not block crawls.

**Windows:** the sync Cursor SDK bridge uses `select()` on a pipe and fails with `WinError 10038`. `sync_agent` automatically uses the async bridge on Windows. Prefer running the agent from the **team repo checkout** (`SYNC_AGENT_CWD`), not only from this hub repo. WSL also works if you prefer a Linux environment.

## First-Connect Workflow (Cursor-driven onboarding)

MCP connection alone does **not** auto-run an agent. After wiring the server, ask Cursor once (or add a project rule) to onboard:

1. `list_projects` / `register_project("acme-app")` → get `project_id` (e.g. `acme-app`).
2. Call `onboard_subproject(project_id, team="backend")` or use the `onboard_subproject_prompt` prompt.
3. **FastAPI backend (preferred):** start your API, then call `import_openapi(project_id, openapi_url="http://localhost:8000/openapi.json")`.
4. Otherwise Cursor reviews the workspace and calls `import_snapshot`.
5. The other team opens the same `project_id` via `get_latest_state` or the dashboard.

### Suggested Cursor rule

```text
When this repo is first opened with Team Sync MCP available:
1. list_projects / register_project for this product
2. run onboard_subproject for this team's role (backend|frontend)
3. If backend FastAPI: import_openapi from /openapi.json
4. Else explore the workspace and import_snapshot when ready
```

### Example: FastAPI OpenAPI import

```text
Register/list project acme-app, then import_openapi with
openapi_url=http://localhost:8000/openapi.json
```

## MCP Tools

| Tool | Purpose |
| --- | --- |
| `list_projects` | List hub projects |
| `register_project(name, description?, template?, teams?)` | Create a project (`web` / `mobile` / `monorepo` / `blank` templates) |
| `onboard_subproject(project_id?, team?)` | Return checklist + instructions |
| `import_snapshot(..., replace?)` | Bulk import; `replace=true` prunes missing team-owned items |
| `import_artifacts(...)` | Upsert typed artifacts (env vars, flags, events, …) |
| `acknowledge_change(change_id, status, ...)` | Mark a change `ack` / `blocked` / `needs_version` |
| `get_artifacts(project_id?, kind?)` | List artifacts |
| `import_openapi(project_id?, openapi_url?, openapi_json?, team?)` | Import FastAPI/OpenAPI routes |
| `publish_update(project_id?, team?, type, description, details)` | Publish one change |
| `get_latest_state(project_id?)` | Full state + markdown digest |
| `get_changelog(project_id?, since, ...)` | Filtered changelog |
| `subscribe_to_changes(project_id?)` | Resource subscription hints |

With a `Project` header, `project_id` / `team` can be omitted on tools that support them.

Resources: `sync://projects`, `sync://projects/{id}/state`, `sync://projects/{id}/changelog`.

## Dashboard

- Sign in with username/password; JWT is stored in `localStorage`.
- Overview lists projects you can access; create projects (you become owner).
- Open a project for state + activity; owners can edit/delete/sync and manage members.
- **API keys** page for Cursor/CI; **Admin** (hub admins) manages users.
- Live updates via SSE (`/api/events?access_token=...`).

## REST examples

```bash
curl http://localhost:8080/api/health
curl http://localhost:8080/api/projects

curl -X POST http://localhost:8080/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name":"Acme App","description":"Main product"}'

curl http://localhost:8080/api/projects/acme-app/state

curl -X POST http://localhost:8080/api/projects/acme-app/snapshot \
  -H "Content-Type: application/json" \
  -d '{
    "team":"backend",
    "api":[{"method":"GET","path":"/users/:id","description":"User lookup"}],
    "notes":"Initial scan"
  }'
```

Legacy `/api/state` and `/api/changelog` still work but require `?project_id=...`.

## Tests

```bash
pip install -e ".[dev]"
pytest
```
