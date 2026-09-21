"""Build the zip that goes onto the cPanel account.

    .venv/Scripts/python deploy/make_bundle.py            # -> dist/plm-deploy-<date>.zip

What goes in: the Python package, the built front end (`web/dist`), the Passenger entry point, the
packaging metadata, the tests (so the server can prove itself once), the documentation and these
deployment helpers. What stays out: the virtual environment, `node_modules`, the front-end sources,
the local `data/` directory and git.

The zip unpacks into a single folder `plm/`, so on the server:

    cd ~ && unzip plm-deploy-<date>.zip      # -> /home/<user>/plm
"""
from __future__ import annotations

import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP = "plm"  # the folder name the zip unpacks into

#: (path relative to the repository, recurse?) - order is only for the printed report
INCLUDE: list[tuple[str, bool]] = [
    ("plm", True),
    ("web/dist", True),
    ("web/.htaccess", False),
    ("tests", True),
    ("docs", True),
    ("deploy", True),
    ("passenger_wsgi.py", False),
    ("pyproject.toml", False),
    ("README.md", False),
]

EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git", "node_modules", ".venv"}
EXCLUDE_SUFFIX = {".pyc", ".pyo"}


def wanted(p: Path) -> bool:
    if any(part in EXCLUDE_PARTS for part in p.parts):
        return False
    return p.suffix not in EXCLUDE_SUFFIX


def main() -> int:
    out_dir = ROOT / "dist"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"plm-deploy-{date.today().isoformat()}.zip"

    missing = [rel for rel, _ in INCLUDE if not (ROOT / rel).exists()]
    if missing:
        print("missing (build the front end first? `cd web && npm run build`):", ", ".join(missing), file=sys.stderr)
        return 2

    counts: dict[str, tuple[int, int]] = {}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel, recurse in INCLUDE:
            src = ROOT / rel
            files = [f for f in (src.rglob("*") if recurse else [src]) if f.is_file() and wanted(f)]
            for f in files:
                z.write(f, f"{TOP}/{f.relative_to(ROOT).as_posix()}")
            counts[rel] = (len(files), sum(f.stat().st_size for f in files))

    for rel, (n, size) in counts.items():
        print(f"  {rel:<20} {n:>5} files  {size / 1e6:>7.1f} MB")
    print(f"\n{out}  ->  {out.stat().st_size / 1e6:.1f} MB compressed")
    print(f"upload it to /home/<cpanel-user>/ and run:  cd ~ && unzip -o {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
