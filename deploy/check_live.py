"""Prove a deployed server actually works, from outside it.

    python deploy/check_live.py https://plm.example.com

Checks, in order: the API answers and reports its settings; the single-page application and its
built assets are served; the help pages are there; the visitor register is switched on and issues a
cookie; sign-in is enforced if it is supposed to be; and the coordinate-system list (which needs
pyproj and its data files) really loads on that machine.

Exit code 0 when everything passed, 1 otherwise. Only standard library - it runs anywhere.
"""
from __future__ import annotations

import http.cookiejar
import json
import sys
import urllib.error
import urllib.request

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, fn) -> None:
    try:
        RESULTS.append((True, name, fn() or ""))
    except Exception as e:  # noqa: BLE001
        RESULTS.append((False, name, f"{type(e).__name__}: {e}"))


def main(base: str) -> int:
    base = base.rstrip("/")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", "plm-deploy-check")]

    def get(path: str, expect: int = 200):
        try:
            r = opener.open(base + path, timeout=30)
            body, status = r.read(), r.status
        except urllib.error.HTTPError as e:
            body, status = e.read(), e.code
        if status != expect:
            raise AssertionError(f"{path} answered {status}, expected {expect}: {body[:200]!r}")
        return body

    def get_json(path: str, expect: int = 200):
        return json.loads(get(path, expect))

    def health() -> str:
        h = get_json("/api/health")
        assert h["status"] == "ok", h
        return (f"version {h['version']}, auth {'on' if h['auth_enabled'] else 'OFF'}, "
                f"jobs {h['job_mode']}, queue {h['queue']['pending']} pending, "
                f"{h['load']['available_mb']} MB free")

    def spa() -> str:
        page = get("/").decode("utf-8", "replace")
        assert "<div id=\"app\"" in page or "id=app" in page, "index.html is not the application shell"
        assert "/assets/" in page, "the page references no built assets - is PLM_WEB_DIST set?"
        asset = page.split("/assets/")[1].split('"')[0]
        get(f"/assets/{asset}")
        return f"shell and /assets/{asset[:28]}... served"

    def help_pages() -> str:
        for p in ("index", "terrain", "road", "standards", "accounts", "faq"):
            get(f"/help/{p}.html")
        return "6 pages"

    def visitors() -> str:
        st = get_json("/api/visitor/me")
        if not st["tracking"]:
            return "tracking is switched OFF (PLM_VISITOR_TRACKING=0)"
        assert st["visitor"], "no visitor record was created"
        assert any(c.name == "plm_visitor" for c in jar), "no plm_visitor cookie was issued"
        ip = st["visitor"]["ip"]
        priv = ip.startswith(("10.", "127.", "192.168.")) or ip == "unknown"
        note = "  <-- looks like the proxy, not the visitor: check PLM_TRUST_PROXY" if priv else ""
        return (f"seen as {ip}{note}; form "
                f"{'compulsory' if st['intake_required'] else 'offered' if st['intake_enabled'] else 'off'}")

    def auth() -> str:
        s = get_json("/api/auth/status")
        if not s["auth_enabled"]:
            return "SIGN-IN IS OFF - anyone with the URL is an administrator"
        who = get_json("/api/auth/me")
        kind = "guest sandbox" if who.get("guest") else "signed-out"
        return (f"on, {s['users']} account(s), registration "
                f"{'open' if s['open_registration'] else 'closed (invite only)'}, visitors get the {kind}")

    def projects_are_protected() -> str:
        s = get_json("/api/auth/status")
        if not s["auth_enabled"]:
            return "sign-in is off: every project is open to anyone with the URL"
        try:
            n = len(get_json("/api/projects"))
        except AssertionError:
            return "refused without an account (401) - guests are switched off"
        kind = "guest sandbox" if s.get("guest_enabled") else "no account"
        return f"{n} project(s) visible with {kind}; private projects need an account"

    def crs() -> str:
        presets = get_json("/api/crs/presets")
        assert len(presets) > 3, presets
        d = get_json("/api/crs/describe?spec=EPSG:32645")
        assert d["epsg"] == 32645, d
        return f"{len(presets)} presets, EPSG:32645 resolves to {d['name']}"

    print(f"checking {base}\n")
    check("API health", health)
    check("application shell and assets", spa)
    check("help pages", help_pages)
    check("visitor register", visitors)
    check("accounts", auth)
    check("project access", projects_are_protected)
    check("coordinate systems (pyproj data)", crs)

    width = max(len(n) for _, n, _ in RESULTS)
    for ok, name, detail in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    failed = [n for ok, n, _ in RESULTS if not ok]
    print()
    if failed:
        print(f"{len(failed)} check(s) failed: {', '.join(failed)}")
        return 1
    print("all checks passed - the deployment is live and working")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
