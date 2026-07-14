from __future__ import annotations

import asyncio
from pathlib import Path


class GitWatchError(RuntimeError):
    pass


async def read_head_sha(repo_path: str) -> str:
    path = Path(repo_path).expanduser()
    if not path.exists():
        raise GitWatchError(f"Git repo path does not exist: {path}")
    if not (path / ".git").exists() and not path.joinpath(".git").is_file():
        # Allow bare-ish worktrees where .git may be a file; still require something git-like.
        # rev-parse will fail clearly if invalid.
        pass
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(path),
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
