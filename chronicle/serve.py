"""Start the API server: python -m chronicle.serve --db ... --port 8000"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chronicle.serve", description="Run the Chronicle API.")
    parser.add_argument("--db", required=True, type=Path, help="SQLite file used by the API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    # The app reads its database path from the environment at import time.
    os.environ["CHRONICLE_DB"] = str(args.db)
    # Client addresses come from chronicle.auth.client_ip, which trusts only CHRONICLE_TRUSTED_PROXIES.
    uvicorn.run("chronicle.api:app", host=args.host, port=args.port, proxy_headers=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
