# Team Sync MCP Server

A self-hosted synchronization hub for frontend and backend teams using Cursor. It exposes MCP tools for publishing and reading project state, stores everything locally, and serves a small React dashboard for visual inspection.

## What It Provides

- A Python MCP server using the official `mcp` SDK and Streamable HTTP transport at `http://localhost:8080/mcp`.
- Shared project state for API contracts, requirements, component specs, and changelog entries.
- SQLite persistence by default, with an optional JSON-file backend.
- REST endpoints for dashboard access at `/api/state`, `/api/changelog`, and `/api/events`.
- A React, Tailwind CSS, and shadcn-style dashboard served at `http://localhost:8080/dashboard/`.
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

Install and run the Python server:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m sync_mcp
```

Run the dashboard in Vite dev mode:

```bash
cd dashboard
npm install
npm run dev
```

The Vite dev server proxies `/api` to `http://localhost:8080`.

For a production-like local run:

```bash
cd dashboard
npm install
npm run build
cd ..
python -m sync_mcp
```

## Configuration

Copy `.env.example` to `.env` or set environment variables directly.

| Variable | Default | Description |
| --- | --- | --- |
| `SYNC_MCP_PROJECT` | `my-project` | Project name displayed in Cursor and the dashboard. |
| `SYNC_MCP_STORAGE` | `sqlite` | Use `sqlite` or `json`. |
| `SYNC_MCP_DATA_DIR` | `./data` | Directory for persistent state. |
| `SYNC_MCP_HOST` | `0.0.0.0` | Server bind host. |
| `SYNC_MCP_PORT` | `8080` | Server port. |
| `SYNC_MCP_TOKEN` | empty | Optional shared bearer token for MCP and write access. |

## Cursor MCP Configuration

Add the server in Cursor MCP settings. For an open local server:

```json
{
  "mcpServers": {
    "team-sync": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

If `SYNC_MCP_TOKEN` is set:

```json
{
  "mcpServers": {
    "team-sync": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer shared-secret"
      }
    }
  }
}
```

## MCP Tools

### `publish_update(team, type, description, details)`

Records a change and updates the aggregated state.

Example after a backend endpoint change:

```json
{
  "team": "backend",
  "type": "api_added",
  "description": "Add user lookup endpoint",
  "details": {
    "method": "GET",
    "path": "/users/:id",
    "response": {
      "id": "string",
      "name": "string"
    }
  }
}
```

Useful `type` values:

- `api_added`, `api_changed`, `api_removed`
- `requirement_added`, `requirement_changed`, `requirement_closed`
- `component_spec`
- `changelog`
- `other`

### `get_latest_state()`

Returns the current API endpoints, open requirements, component specs, recent changes, and a Cursor-friendly markdown digest.

### `get_changelog(since, team, type, limit)`

Returns recent changes. `since` accepts either an ISO timestamp or a version number.

### `subscribe_to_changes()`

Returns subscription hints. MCP clients that support resource update notifications can subscribe to:

- `sync://state`
- `sync://changelog`

The dashboard uses Server-Sent Events at `/api/events`.

## Dashboard API

```bash
curl http://localhost:8080/api/health
curl http://localhost:8080/api/state
curl "http://localhost:8080/api/changelog?team=backend&type=api_added"
```

Publish through the REST fallback:

```bash
curl -X POST http://localhost:8080/api/updates \
  -H "Content-Type: application/json" \
  -d '{
    "team": "frontend",
    "type": "requirement_added",
    "description": "User payload needs avatar_url",
    "details": { "id": "user-avatar", "title": "Expose avatar_url" }
  }'
```

With token auth:

```bash
curl -X POST http://localhost:8080/api/updates \
  -H "Authorization: Bearer shared-secret" \
  -H "Content-Type: application/json" \
  -d '{"team":"backend","type":"changelog","description":"Deployed staging build"}'
```

## Team Workflow

1. Backend publishes `api_added` or `api_changed` from Cursor after changing an endpoint.
2. Frontend starts a session and calls `get_latest_state()` or the `sync_digest` prompt.
3. Frontend publishes `requirement_added` when they need contract changes.
4. Backend sees the requirement in Cursor or at `http://localhost:8080/dashboard/`.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The tests cover state aggregation, changelog filtering, and token enforcement on write endpoints.
