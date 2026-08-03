"""Local git HEAD introspection."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def run_git(repo: Path, *args: str) -> tuple[str | None, str | None]:
    """Run git in `repo`. Returns (stdout, error)."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "git failed").strip()
        return None, err
    return (proc.stdout or "").strip(), None


def git_info(repo: Path) -> dict[str, Any]:
    """Return local HEAD info: sha, short sha, subject, committed_at, branch."""
    if not (repo / ".git").exists():
        return {"ok": False, "error": f"not a git repo: {repo}"}

    sha, err = run_git(repo, "rev-parse", "HEAD")
    if err:
        return {"ok": False, "error": err}

    short, _ = run_git(repo, "rev-parse", "--short", "HEAD")
    subject, _ = run_git(repo, "log", "-1", "--format=%s")
    committed_at, _ = run_git(repo, "log", "-1", "--format=%cI")
    branch, _ = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")

    return {
        "ok": True,
        "sha": sha,
        "short_sha": short or sha[:7],
        "subject": subject or "",
        "committed_at": committed_at or "",
        "branch": branch or "HEAD",
    }
