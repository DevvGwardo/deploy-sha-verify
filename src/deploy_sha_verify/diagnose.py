"""Verdict logic: local HEAD vs live deployment signals."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

FIXES = {
    "deployed": "Prod likely has the latest build. If UI still looks old: hard refresh or incognito.",
    "stale_cache": "Hard refresh, clear site data, or fetch the asset with Cache-Control: no-cache.",
    "deploy_pending": "Wait for the build to finish, or trigger a deploy (vercel deploy --prod / wrangler pages deploy).",
    "deploy_mismatch": "Check the repo links the right project; redeploy from the branch you actually pushed.",
    "not_deployed": "No live deployment found for this project.",
    "unknown": "Inspect the provider dashboard; confirm the domain maps to the expected project.",
}

STALE_VERDICTS = {"deploy_mismatch", "deploy_pending", "stale_cache", "not_deployed"}


def parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def diagnose(
    git: dict[str, Any],
    resp: dict[str, Any],
    fingerprints: dict[str, Any],
    provider: dict[str, Any],
) -> dict[str, Any]:
    signals: list[str] = []
    verdict = "unknown"
    confidence = "low"

    headers = resp.get("headers") or {}
    cache = headers.get("x-vercel-cache", "")
    age = headers.get("age", "")
    cf_cache = headers.get("cf-cache-status", "")

    if cache.upper() == "HIT" and age:
        signals.append(f"CDN cache HIT (age={age}s)")
    if cf_cache:
        signals.append(f"cf-cache-status={cf_cache}")

    prov_ok = bool(provider.get("ok"))

    # 1. Strongest: provider-reported git commit vs local HEAD
    if prov_ok and provider.get("git_commit"):
        remote_sha = str(provider["git_commit"])
        local_sha = str(git.get("sha", ""))
        if local_sha.startswith(remote_sha) or remote_sha.startswith(local_sha):
            verdict = "deployed"
            confidence = "high"
            signals.append(
                f"{provider['provider']} prod commit matches HEAD ({remote_sha[:12]})"
            )
        else:
            verdict = "deploy_mismatch"
            confidence = "high"
            signals.append(
                f"{provider['provider']} prod commit {remote_sha[:12]} != local HEAD {local_sha[:12]}"
            )

    # 2. Timestamp heuristic when no commit info
    deploy_created = parse_iso(str(provider.get("created") or ""))
    commit_time = parse_iso(str(git.get("committed_at") or ""))
    if verdict == "unknown" and deploy_created and commit_time:
        if commit_time.tzinfo is None:
            commit_time = commit_time.replace(tzinfo=timezone.utc)
        if deploy_created < commit_time:
            verdict = "deploy_pending"
            confidence = "medium"
            signals.append("latest READY deploy is older than local HEAD commit time")
        else:
            verdict = "deployed"
            confidence = "medium"
            signals.append("latest deploy is newer than local HEAD commit time")

    # 3. Provider state signals
    state = str(provider.get("state") or "").upper()
    if verdict == "unknown" and state:
        if state in {"READY", "READY_STATE", "RUNNING", "LIVE", "PRODUCTION", "READY"}:
            verdict = "deployed"
            confidence = "medium"
            signals.append(f"provider reports state={state}")
        elif state in {"BUILDING", "QUEUED", "PENDING", "INITIALIZING", "ERROR", "CANCELED"}:
            verdict = "deploy_pending"
            confidence = "medium"
            signals.append(f"provider deployment state={state}")

    # 4. Weakest: build fingerprints in prod HTML
    if verdict == "unknown":
        if fingerprints.get("vite_bundles") or fingerprints.get("next_build_ids"):
            verdict = "deployed"
            confidence = "low"
            signals.append("prod HTML has build fingerprints (manual compare needed)")
        elif cache.upper() == "HIT" or cf_cache.upper() == "HIT":
            verdict = "stale_cache"
            confidence = "low"
            signals.append("only signal is CDN HIT — try hard refresh")

    # Provider failed but page is up: fall back to fingerprint-only judgment
    if not prov_ok and verdict == "unknown":
        if fingerprints.get("vite_bundles") or fingerprints.get("next_build_ids"):
            verdict = "deployed"
            confidence = "low"
            signals.append("provider check unavailable; fingerprints only")

    return {
        "verdict": verdict,
        "confidence": confidence,
        "signals": signals,
        "fix": FIXES.get(verdict, FIXES["unknown"]),
    }
