from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class GitWatchError(RuntimeError):
    pass


def read_head_sha(repo_path: str | Path) -> str:
    path = Path(repo_path).expanduser().resolve()
    if not path.exists():
        raise GitWatchError(f"Git repo path does not exist: {path}")
    process = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or "").strip() or f"exit {process.returncode}"
        raise GitWatchError(f"git rev-parse failed for {path}: {detail}")
    sha = process.stdout.strip()
    if not sha:
        raise GitWatchError(f"Empty HEAD SHA for {path}")
    return sha


def sleep_interval(seconds: int) -> None:
    time.sleep(max(5, seconds))
