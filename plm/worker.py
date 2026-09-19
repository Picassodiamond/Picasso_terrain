"""Background job worker.

    python -m plm.worker            # process pending jobs once (cron: * * * * *)
    python -m plm.worker --loop 5   # poll every 5 seconds (systemd / dev)
"""
from __future__ import annotations

import argparse
import sys
import time

from .api.config import Settings
from .api.jobs import run_pending_jobs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="plm.worker")
    ap.add_argument("--loop", type=float, default=0.0, help="poll interval in seconds (0 = run once)")
    ap.add_argument("--max-jobs", type=int, default=20)
    args = ap.parse_args(argv)
    settings = Settings()
    if args.loop <= 0:
        n = run_pending_jobs(settings, args.max_jobs)
        print(f"processed {n} job(s)")
        return 0
    print(f"worker polling every {args.loop}s (data dir {settings.data_dir})")
    while True:
        try:
            n = run_pending_jobs(settings, args.max_jobs)
            if n:
                print(f"processed {n} job(s)")
        except KeyboardInterrupt:
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"worker error: {e}", file=sys.stderr)
        time.sleep(args.loop)


if __name__ == "__main__":
    sys.exit(main())
