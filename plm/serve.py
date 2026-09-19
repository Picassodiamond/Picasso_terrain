"""Run the full application locally (API + built front end) with uvicorn.

    python -m plm.serve                 # http://127.0.0.1:8000
    python -m plm.serve --port 8080 --host 0.0.0.0 --reload
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="plm.serve")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--data-dir", default=None, help="defaults to ./data (PLM_DATA_DIR)")
    ap.add_argument("--auth", choices=["on", "off"], default=None, help="override PLM_AUTH_ENABLED")
    args = ap.parse_args(argv)
    if args.data_dir:
        os.environ["PLM_DATA_DIR"] = args.data_dir
    if args.auth:
        os.environ["PLM_AUTH_ENABLED"] = "1" if args.auth == "on" else "0"
    dist = Path(__file__).resolve().parents[1] / "web" / "dist"
    if not os.environ.get("PLM_WEB_DIST") and dist.exists():
        os.environ["PLM_WEB_DIST"] = str(dist)
    if not dist.exists():
        print("note: web/dist not found - only the API (and /api/docs) will be served. Run `npm run build` in web/.", file=sys.stderr)
    import uvicorn

    print(f"Picasso LandMesh -> http://{args.host}:{args.port}   (API docs: /api/docs)")
    uvicorn.run("plm.api.main:app", host=args.host, port=args.port, reload=args.reload, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
