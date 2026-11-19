"""`python -m app` — start the local AutoContent AI server (API + dashboard + workers + scheduler)."""
from __future__ import annotations

import uvicorn

from app.core.config import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port, log_level="info", proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", timeout_graceful_shutdown=25)


if __name__ == "__main__":
    main()
