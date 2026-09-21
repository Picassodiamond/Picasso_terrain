"""Administration CLI.

    python -m plm.admin create-user admin --role admin --org "SSTN"
    python -m plm.admin set-password admin
    python -m plm.admin list-users
    python -m plm.admin list-projects
"""
from __future__ import annotations

import argparse
import getpass
import sys

from .api.config import Settings
from .api.db import AppDB


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="plm.admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user")
    cu.add_argument("username")
    cu.add_argument("--password", help="omit to be prompted")
    cu.add_argument("--role", choices=["viewer", "editor", "admin"], default="editor")
    cu.add_argument("--org", default="")
    cu.add_argument("--name", default="", help="full name")
    cu.add_argument("--email", default="")
    sp = sub.add_parser("set-password")
    sp.add_argument("username")
    sp.add_argument("--password")
    sub.add_parser("list-users")
    sub.add_parser("list-projects")
    bk = sub.add_parser("backup", help="consistent copy of the app database and all project files into a zip")
    bk.add_argument("--out", default=None, help="directory for the backup zip (default: <data>/backups)")
    fc = sub.add_parser("flatten-constraints",
                        help="drop the levels from constraint lines: they are plan geometry and the engine "
                             "interpolates a vertex level from the survey surface")
    fc.add_argument("--project", default=None, help="one project id (default: every project)")
    fc.add_argument("--kinds", default="boundary,void",
                    help="which kinds to flatten (default boundary,void). A breakline imported with surveyed "
                         "levels shapes the surface, so it is left alone unless you name it.")
    fc.add_argument("--dry-run", action="store_true", help="report what would change and write nothing")
    args = ap.parse_args(argv)

    settings = Settings()
    db = AppDB(settings.app_db_path)
    if args.cmd == "create-user":
        pw = args.password or getpass.getpass("Password: ")
        if len(pw) < 8:
            print("password must be at least 8 characters", file=sys.stderr)
            return 2
        if db.get_user_by_name(args.username):
            print("user exists", file=sys.stderr)
            return 2
        u = db.create_user(args.username, pw, args.role, args.org, args.name, args.email)
        print(f"created {u['username']} ({u['role']})")
    elif args.cmd == "set-password":
        pw = args.password or getpass.getpass("New password: ")
        print("ok" if db.set_password(args.username, pw) else "user not found")
    elif args.cmd == "list-users":
        for u in db.list_users():
            print(f"{u['username']:20s} {u['role']:8s} {u['organisation']:20s} {u['created']}")
    elif args.cmd == "list-projects":
        for p in db.list_projects():
            print(f"{p['id']}  {p['name']:30s} {p['crs']:10s} {p.get('status', 'active'):9s} {p['updated']}")
    elif args.cmd == "backup":
        from pathlib import Path

        from .api.archive import backup

        out = backup(settings, Path(args.out) if args.out else None)
        print(f"backup written: {out} ({out.stat().st_size / 1e6:.1f} MB)")
    elif args.cmd == "flatten-constraints":
        return _flatten_constraints(settings, db, args)
    return 0


def _flatten_constraints(settings, db, args) -> int:
    """Set the Z of every vertex of the named constraint kinds to zero.

    Constraint lines are plan geometry. The engine reads a constraint vertex whose Z is 0 as
    "interpolate me from the survey surface", which is what a boundary or a void has always needed;
    carrying a stale level on them only invites the two to disagree. A breakline is different - one
    imported with surveyed levels does shape the surface - so breaklines are only touched when they
    are named in --kinds.
    """
    import numpy as np

    from .api.gpkg import ProjectStore

    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()}
    unknown = kinds - {"boundary", "void", "feature", "contour"}
    if unknown:
        print(f"unknown kind(s): {', '.join(sorted(unknown))} (use boundary, void, feature, contour)", file=sys.stderr)
        return 2
    projects = [db.get_project(args.project)] if args.project else db.list_projects()
    if args.project and projects[0] is None:
        print(f"no project {args.project}", file=sys.stderr)
        return 2

    total_lines = total_verts = 0
    for pr in projects:
        path = settings.project_gpkg(pr["id"])
        if not path.exists():
            continue
        store = ProjectStore.open(path)
        changed = []
        for ln in store.lines():
            if ln["kind"] not in kinds:
                continue
            c = np.asarray(ln["coords"], dtype=float)
            if c.shape[1] < 3 or not np.any(np.abs(c[:, 2]) > 1e-9):
                continue
            n = int(np.count_nonzero(np.abs(c[:, 2]) > 1e-9))
            changed.append((ln["fid"], ln["kind"], len(c), n))
            if not args.dry_run:
                store.update_line(ln["fid"], coords=np.column_stack([c[:, 0], c[:, 1], np.zeros(len(c))]))
        if changed:
            print(f"{pr['id']}  {pr['name']}")
            for fid, kind, npts, nz in changed:
                print(f"    line {fid:>4}  {kind:<9} {npts:>5} vertices, {nz} carried a level")
            total_lines += len(changed)
            total_verts += sum(x[3] for x in changed)
    verb = "would flatten" if args.dry_run else "flattened"
    print(f"{verb} {total_lines} line(s), {total_verts} vertices; kinds: {', '.join(sorted(kinds))}")
    if total_lines and not args.dry_run:
        print("the stored TIN runs are unchanged - build the TIN again where you want the new geometry used")
    return 0


if __name__ == "__main__":
    sys.exit(main())
