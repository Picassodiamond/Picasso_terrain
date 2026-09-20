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
    return 0


if __name__ == "__main__":
    sys.exit(main())
