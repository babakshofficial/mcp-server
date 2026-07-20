"""CI-friendly REST importer (no Cursor SDK).

Examples:
  sync-mcp-import openapi --hub http://192.168.17.29:8080 \\
    --api-key sk_... --project adra --url http://192.168.17.29:8001/openapi.json

  sync-mcp-import snapshot --hub http://192.168.17.29:8080 \\
    --api-key sk_... --project adra --team frontend --file snapshot.json --replace
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urljoin

import httpx


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def cmd_openapi(args: argparse.Namespace) -> int:
    url = urljoin(args.hub.rstrip("/") + "/", f"api/projects/{args.project}/openapi")
    body: dict = {"team": args.team}
    if args.url:
        body["openapi_url"] = args.url
    if args.file:
        body["openapi_json"] = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if not args.url and not args.file:
        print("Provide --url and/or --file", file=sys.stderr)
        return 2
    response = httpx.post(url, headers=_headers(args.api_key), json=body, timeout=60.0)
    print(response.status_code, response.text[:2000])
    return 0 if response.status_code < 400 else 1


def cmd_snapshot(args: argparse.Namespace) -> int:
    url = urljoin(args.hub.rstrip("/") + "/", f"api/projects/{args.project}/snapshot")
    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if "team" not in payload:
        payload["team"] = args.team
    if args.replace:
        payload["replace"] = True
    response = httpx.post(url, headers=_headers(args.api_key), json=payload, timeout=60.0)
    print(response.status_code, response.text[:2000])
    return 0 if response.status_code < 400 else 1


def cmd_commit(args: argparse.Namespace) -> int:
    url = urljoin(args.hub.rstrip("/") + "/", f"api/projects/{args.project}/hooks/commit")
    body = {"commit_sha": args.sha} if args.sha else {}
    response = httpx.post(url, headers=_headers(args.api_key), json=body, timeout=60.0)
    print(response.status_code, response.text[:2000])
    return 0 if response.status_code < 400 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sync-mcp-import", description=__doc__)
    parser.add_argument("--hub", required=True, help="Hub REST base, e.g. http://192.168.17.29:8080")
    parser.add_argument("--api-key", required=True, help="Hub API key (sk_...)")
    parser.add_argument("--project", required=True, help="Project id/slug")
    sub = parser.add_subparsers(dest="command", required=True)

    openapi = sub.add_parser("openapi", help="Import OpenAPI via REST")
    openapi.add_argument("--team", default="backend")
    openapi.add_argument("--url", default="", help="Reachable OpenAPI URL (LAN IP, not 0.0.0.0)")
    openapi.add_argument("--file", default="", help="Local openapi.json path")
    openapi.set_defaults(func=cmd_openapi)

    snapshot = sub.add_parser("snapshot", help="POST a snapshot JSON file")
    snapshot.add_argument("--team", default="frontend")
    snapshot.add_argument("--file", required=True, help="Snapshot JSON path")
    snapshot.add_argument("--replace", action="store_true", help="Prune missing team-owned items")
    snapshot.set_defaults(func=cmd_snapshot)

    commit = sub.add_parser("commit", help="Trigger on_commit OpenAPI sync webhook")
    commit.add_argument("--sha", default="", help="Optional commit SHA")
    commit.set_defaults(func=cmd_commit)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
