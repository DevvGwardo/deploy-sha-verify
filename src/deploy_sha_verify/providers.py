"""Deployment-provider adapters.

Each provider implements `latest_deployment(repo, url)` and returns a dict
with: ok, provider, and either deployment fields (id, url, state, created,
git_commit) or an error/skipped reason.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable


def _run_cli(cmd: list[str], cwd: Path | None, timeout: int = 30) -> tuple[str | None, str | None]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "command failed").strip()
        return None, err
    return (proc.stdout or "").strip(), None


# ── Vercel ────────────────────────────────────────────────────────────────

def vercel_latest(repo: Path) -> dict[str, Any]:
    project_file = repo / ".vercel" / "project.json"
    if not project_file.is_file():
        return {"ok": False, "skipped": True, "reason": "no .vercel/project.json", "provider": "vercel"}

    out, err = _run_cli(["vercel", "ls", "--prod", "-F", "json", "-y"], cwd=repo)
    if err:
        return {"ok": False, "error": err, "provider": "vercel"}
    if not out:
        return {"ok": False, "error": "vercel ls returned empty output", "provider": "vercel"}

    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"vercel ls json parse failed: {exc}", "provider": "vercel"}

    deployments = data.get("deployments") if isinstance(data, dict) else data
    if not isinstance(deployments, list):
        deployments = [data] if isinstance(data, dict) else []

    ready = [
        d
        for d in deployments
        if isinstance(d, dict)
        and str(d.get("state", d.get("readyState", ""))).upper() in {"READY", "READY_STATE"}
    ]
    latest = ready[0] if ready else (deployments[0] if deployments else None)
    if not latest:
        return {"ok": False, "error": "no deployments found", "provider": "vercel"}

    meta = latest.get("meta") or {}
    git_commit = (
        meta.get("githubCommitSha")
        or meta.get("gitlabCommitSha")
        or meta.get("gitCommitSha")
        or meta.get("gitDirty")
    )
    if isinstance(git_commit, str) and git_commit.lower() == "1":
        git_commit = None

    return {
        "ok": True,
        "provider": "vercel",
        "deployment_id": latest.get("uid") or latest.get("id"),
        "url": latest.get("url"),
        "state": latest.get("state") or latest.get("readyState"),
        "created": latest.get("created") or latest.get("createdAt"),
        "git_commit": git_commit,
        "inspector_url": latest.get("inspectorUrl"),
    }


# ── Cloudflare Pages ──────────────────────────────────────────────────────

def _parse_cf_deployments(raw: str) -> list[dict[str, Any]]:
    """Parse `wrangler pages deployment list --json` output."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for d in data:
        if not isinstance(d, dict):
            continue
        trigger = d.get("deployment_trigger") or {}
        out.append(
            {
                "id": d.get("id"),
                "url": d.get("url"),
                "environment": d.get("environment"),
                "created": d.get("created_on") or d.get("created_on"),
                "trigger_type": trigger.get("type"),
                "git_branch": trigger.get("git_branch"),
                "commit_hash": trigger.get("commit_hash"),
                "commit_message": trigger.get("commit_message"),
                "commit_dirty": trigger.get("commit_dirty"),
            }
        )
    return out


def _parse_cf_project_name(raw: str) -> str | None:
    m = re.search(r'project_name\s*=\s*"?([^"\s]+)"?', raw)
    return m.group(1) if m else None


def cloudflare_pages_latest(repo: Path) -> dict[str, Any]:
    cfg = repo / "wrangler.toml"
    project = None
    if cfg.is_file():
        project = _parse_cf_project_name(cfg.read_text(errors="replace"))

    out, err = _run_cli(
        ["wrangler", "pages", "deployment", "list", "--json"]
        + (["--project-name", project] if project else []),
        cwd=repo,
        timeout=60,
    )
    if err:
        return {"ok": False, "error": err, "provider": "cloudflare-pages"}
    if not out:
        return {"ok": False, "error": "wrangler returned empty output", "provider": "cloudflare-pages"}

    deployments = _parse_cf_deployments(out)
    if not deployments:
        return {"ok": False, "error": "no deployments found", "provider": "cloudflare-pages"}

    latest = deployments[0]
    return {
        "ok": True,
        "provider": "cloudflare-pages",
        "deployment_id": latest["id"],
        "url": latest["url"],
        "state": latest["environment"] or "ready",
        "created": latest["created"],
        "git_commit": latest["commit_hash"],
        "git_branch": latest["git_branch"],
        "commit_message": latest["commit_message"],
    }


# ── Railway (best effort) ─────────────────────────────────────────────────

def railway_latest(repo: Path) -> dict[str, Any]:
    out, err = _run_cli(["railway", "status"], cwd=repo, timeout=30)
    if err:
        return {"ok": False, "error": err, "provider": "railway"}
    if not out:
        return {"ok": False, "error": "railway status returned empty output", "provider": "railway"}

    deployment_id = None
    m = re.search(r"Deployment ID:\s*(\S+)", out)
    if m:
        deployment_id = m.group(1)

    return {
        "ok": True,
        "provider": "railway",
        "deployment_id": deployment_id,
        "url": None,
        "state": "running" if deployment_id else "unknown",
        "created": None,
        "git_commit": None,
    }


# ── Registry ──────────────────────────────────────────────────────────────

PROVIDERS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "vercel": vercel_latest,
    "cloudflare-pages": cloudflare_pages_latest,
    "railway": railway_latest,
}


def detect_provider(repo: Path) -> str | None:
    """Guess which provider a repo is linked to, best first."""
    if (repo / ".vercel" / "project.json").is_file():
        return "vercel"
    if (repo / "wrangler.toml").is_file():
        return "cloudflare-pages"
    if (repo / "railway.json").is_file() or (repo / ".railway").exists():
        return "railway"
    return None
