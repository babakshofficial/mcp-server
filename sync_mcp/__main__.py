from __future__ import annotations

import uvicorn

from sync_mcp.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "sync_mcp.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
