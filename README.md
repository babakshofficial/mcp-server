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
| `SYNC_MCP_TOKEN` | empty | Optional shared bearer token for MCP and write access. |

Existing single-project databases are auto-migrated into one project (slug derived from `SYNC_MCP_PROJECT`) on startup.

## Cursor MCP Configuration

Connect with **Authorization** (when `SYNC_MCP_TOKEN` is set) and a **Project** header of the form `<project_name>-<project_type>`:

```json
{
  "mcpServers": {
    "team-sync": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN",
        "Project": "adra-backend"
      }
    }
  }
}
```

`Project: adra-backend` scopes tools to project `adra` and team `backend` (type is the last `-` segment: `backend` | `frontend` | `other`).

When `SYNC_MCP_TOKEN` is set, both Bearer and `Project` are required on `/mcp`. In open/dev mode (empty token), `Project` is optional — without it, tools still need an explicit `project_id` argument.

If you previously saw `POST ... Not Found` / SSE `404`, restart Team Sync — an older bug mounted FastMCP at `/mcp/mcp`. The endpoint is now `/mcp`.

Also make sure the hub process is running (`python -m sync_mcp` or `docker compose up`) before enabling the MCP server in Cursor. Early `ERR_CONNECTION_REFUSED` means nothing was listening on port 8080.

## Automatic OpenAPI sync

Backend teams do **not** need to republish manually after each code change.

1. In the dashboard, set the project **OpenAPI URL** (e.g. `http://192.168.17.29:8001/openapi.json`) and enable auto-sync.
2. Choose **sync mode**:
   - **Every N seconds** (`interval`): fetch OpenAPI on each hub poll tick (default).
   - **After each commit** (`on_commit`): on each tick, cheaply read local `git rev-parse HEAD`; only fetch OpenAPI when the SHA changes. Requires a **Git repo path** visible to the hub host.
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
| `register_project(name, description?)` | Create a project |
| `onboard_subproject(project_id?, team?)` | Return checklist + instructions |
| `import_snapshot(project_id?, team?, ...)` | Bulk import after review |
| `import_openapi(project_id?, openapi_url?, openapi_json?, team?)` | Import FastAPI/OpenAPI routes |
| `publish_update(project_id?, team?, type, description, details)` | Publish one change |
| `get_latest_state(project_id?)` | Full state + markdown digest |
| `get_changelog(project_id?, since, ...)` | Filtered changelog |
| `subscribe_to_changes(project_id?)` | Resource subscription hints |

With a `Project` header, `project_id` / `team` can be omitted on tools that support them.

Resources: `sync://projects`, `sync://projects/{id}/state`, `sync://projects/{id}/changelog`.

## Dashboard

- Overview lists every project with API/requirement counts and FE/BE onboard status.
- Open a project (`?project=<id>`) for detailed state + activity feed.
- Create projects from the UI (optional bearer token field stored in `localStorage`).
- Live updates via SSE.

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
