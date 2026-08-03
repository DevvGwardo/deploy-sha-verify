"""dsv — deploy-sha-verify CLI.

Did your deploy actually ship? Compare local git HEAD against the live
deployment on Vercel, Cloudflare Pages, or Railway.

Exit codes:
  0  prod looks fresh — deploy landed
  1  stale signals — CDN cache, deploy older than HEAD, or commit mismatch
  2  setup error — bad URL, not a git repo, network failure, missing CLI
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from . import __version__
from .diagnose import STALE_VERDICTS, diagnose
from .gitinfo import git_info
from .http import extract_fingerprints, host_headers, http_request
from .providers import PROVIDERS, detect_provider


def _check(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    url = args.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    git = git_info(repo)
    if not git.get("ok"):
        _emit({"ok": False, "error": git.get("error")}, args.json, exit_code=2)
        return 2

    head_resp = http_request(url, method="HEAD")
    if not head_resp.get("ok"):
        _emit({"ok": False, "error": head_resp.get("error")}, args.json, exit_code=2)
        return 2

    body_resp = http_request(url, method="GET")
    html = body_resp.get("body") or ""
    fingerprints = extract_fingerprints(html)

    provider_name = args.provider or detect_provider(repo)
    provider: dict[str, Any] = {
        "provider": provider_name or "none",
        "ok": False,
        "skipped": True,
        "reason": "no provider detected (pass --provider vercel|cloudflare-pages|railway)",
    }
    if provider_name:
        fn = PROVIDERS.get(provider_name)
        if fn is None:
            _emit(
                {"ok": False, "error": f"unknown provider: {provider_name}"},
                args.json,
                exit_code=2,
            )
            return 2
        provider = fn(repo)

    diagnosis = diagnose(git, head_resp, fingerprints, provider)

    report = {
        "ok": True,
        "version": __version__,
        "url": url,
        "final_url": body_resp.get("final_url") or head_resp.get("final_url"),
        "http_status": head_resp.get("status"),
        "git": git,
        "headers": host_headers(head_resp.get("headers") or {}),
        "fingerprints": fingerprints,
        "provider": provider,
        "diagnosis": diagnosis,
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human(report)

    return 1 if diagnosis.get("verdict") in STALE_VERDICTS else 0


def _ls(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    provider_name = args.provider or detect_provider(repo)
    if not provider_name:
        print("error: no provider detected. pass --provider vercel|cloudflare-pages|railway", file=sys.stderr)
        return 2
    fn = PROVIDERS.get(provider_name)
    if fn is None:
        print(f"error: unknown provider: {provider_name}", file=sys.stderr)
        return 2

    latest = fn(repo)
    if args.json:
        print(json.dumps(latest, indent=2))
    else:
        if not latest.get("ok"):
            print(f"{provider_name}: {latest.get('error', 'failed')}")
            return 1
        print(f"provider:    {latest['provider']}")
        if latest.get("deployment_id"):
            print(f"deployment:  {latest['deployment_id']}")
        if latest.get("url"):
            print(f"url:         {latest['url']}")
        if latest.get("state"):
            print(f"state:       {latest['state']}")
        if latest.get("created"):
            print(f"created:     {latest['created']}")
        if latest.get("git_commit"):
            print(f"git commit:  {str(latest['git_commit'])[:12]}")
        if latest.get("git_branch"):
            print(f"git branch:  {latest['git_branch']}")
    return 0


def print_human(report: dict[str, Any]) -> None:
    git = report["git"]
    diag = report["diagnosis"]
    prov = report.get("provider") or {}
    headers = report.get("headers") or {}

    print(f"DEPLOY SHA VERIFY — {report['url']}")
    print(f"  local HEAD: {git.get('short_sha')} ({git.get('branch')}) — {git.get('subject')}")
    print(f"  committed:  {git.get('committed_at') or 'unknown'}")
    print(f"  final URL:  {report.get('final_url') or report['url']}")
    print(f"  HTTP:       {report.get('http_status')}")
    if headers.get("x-vercel-id"):
        print(f"  x-vercel-id: {headers['x-vercel-id']}")
    if headers.get("x-vercel-cache"):
        print(f"  x-vercel-cache: {headers['x-vercel-cache']}")
    if headers.get("cf-ray"):
        print(f"  cf-ray:     {headers['cf-ray']}")

    fp = report.get("fingerprints") or {}
    if fp.get("vite_bundles"):
        print(f"  vite bundle: {fp['vite_bundles'][0]}")
    if fp.get("next_build_ids"):
        print(f"  next buildId: {fp['next_build_ids'][0]}")

    if prov.get("ok"):
        print(f"  provider:   {prov.get('provider')} state={prov.get('state')} created={prov.get('created')}")
        if prov.get("git_commit"):
            print(f"  prod commit:{str(prov['git_commit'])[:12]}")
    elif prov.get("skipped"):
        print(f"  provider:   skipped ({prov.get('reason')})")
    elif prov.get("error"):
        print(f"  provider:   {prov.get('error')}")

    print(f"\n  verdict: {diag['verdict']} ({diag['confidence']} confidence)")
    for sig in diag.get("signals") or []:
        print(f"    - {sig}")
    print(f"  fix: {diag['fix']}")


def _emit(payload: dict[str, Any], as_json: bool, exit_code: int) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"error: {payload.get('error', 'failed')}", file=sys.stderr)
    sys.exit(exit_code)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dsv",
        description="Did your deploy actually ship? Compare local git HEAD against the live deployment.",
    )
    parser.add_argument("--version", action="version", version=f"dsv {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Compare local HEAD against the live URL")
    p_check.add_argument("--url", required=True, help="Production URL to verify")
    p_check.add_argument("--repo", default=".", help="Git repo path (default: cwd)")
    p_check.add_argument("--provider", choices=sorted(PROVIDERS), help="Override provider detection")
    p_check.add_argument("--json", action="store_true", help="Emit JSON report")
    p_check.set_defaults(fn=_check)

    p_ls = sub.add_parser("ls", help="Show the latest deployment for the repo")
    p_ls.add_argument("--repo", default=".", help="Git repo path (default: cwd)")
    p_ls.add_argument("--provider", choices=sorted(PROVIDERS), help="Override provider detection")
    p_ls.add_argument("--json", action="store_true", help="Emit JSON")
    p_ls.set_defaults(fn=_ls)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
