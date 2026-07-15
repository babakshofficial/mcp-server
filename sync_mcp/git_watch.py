from __future__ import annotations

import asyncio
from pathlib import Path


class GitWatchError(RuntimeError):
    pass


def _normalize_repo_path(repo_path: str) -> Path:
    path = Path(repo_path).expanduser()
    # Dashboard users sometimes paste .../repo/.git; git -C wants the work tree.
    if path.name == ".git":
        path = path.parent
    return path


async def read_head_sha(repo_path: str) -> str:
    path = _normalize_repo_path(repo_path)
    if not path.exists():
        raise GitWatchError(f"Git repo path does not exist: {path}")
    # Bind-mounted host checkouts often have a different UID than the container
    # process; mark the path safe for this one-shot read only.
    resolved = str(path.resolve())
    process = await asyncio.create_subprocess_exec(
        "git",
        "-c",
        f"safe.directory={resolved}",
        "-C",
        resolved,
        "rev-parse",
        "HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        detail = (stderr or stdout).decode().strip() or f"exit {process.returncode}"
        raise GitWatchError(f"git rev-parse failed for {path}: {detail}")
    sha = stdout.decode().strip()
    if not sha:
        raise GitWatchError(f"Empty HEAD SHA for {path}")
    return sha
