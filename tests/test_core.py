"""Tests for deploy-sha-verify."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from deploy_sha_verify import diagnose as diag_mod
from deploy_sha_verify.diagnose import diagnose
from deploy_sha_verify.gitinfo import git_info
from deploy_sha_verify.http import extract_fingerprints, host_headers
from deploy_sha_verify.providers import _parse_cf_deployments


# ── fingerprints ──────────────────────────────────────────────────────────

def test_vite_bundle_detection():
    html = '<html><script src="/assets/index-B8m83k0p.js"></script></html>'
    fp = extract_fingerprints(html)
    assert fp["vite_bundles"] == ["index-B8m83k0p.js"]


def test_next_build_id_detection():
    html = '<script>window.__NEXT_DATA__={"buildId":"abc123","page":"/"}</script>'
    fp = extract_fingerprints(html)
    assert fp["next_build_ids"] == ["abc123"]


def test_next_static_hash():
    html = '<script src="/_next/static/chunks/main-4f2a9c.js"></script>'
    fp = extract_fingerprints(html)
    assert fp["next_static_hashes"] == ["chunks"]


def test_no_fingerprints():
    fp = extract_fingerprints("<html><body>plain</body></html>")
    assert not fp["vite_bundles"]
    assert not fp["next_build_ids"]


# ── gitinfo ───────────────────────────────────────────────────────────────

def test_git_info(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    info = git_info(tmp_path)
    assert info["ok"]
    assert len(info["sha"]) == 40
    assert info["subject"] == "initial"


def test_git_info_not_repo(tmp_path: Path):
    info = git_info(tmp_path)
    assert not info["ok"]
    assert "not a git repo" in info["error"]


# ── providers parsing ─────────────────────────────────────────────────────

def test_parse_cf_deployments():
    raw = json.dumps(
        [
            {
                "id": "d1",
                "url": "https://abc.pages.dev",
                "environment": "production",
                "created_on": "2026-08-01T12:00:00Z",
                "deployment_trigger": {
                    "type": "github",
                    "git_branch": "main",
                    "commit_hash": "abc123def456",
                    "commit_message": "ship it",
                    "commit_dirty": False,
                },
            }
        ]
    )
    parsed = _parse_cf_deployments(raw)
    assert len(parsed) == 1
    assert parsed[0]["commit_hash"] == "abc123def456"
    assert parsed[0]["git_branch"] == "main"


def test_parse_cf_deployments_bad_json():
    assert _parse_cf_deployments("not json") == []


def test_parse_cf_deployments_not_list():
    assert _parse_cf_deployments('{"a": 1}') == []


# ── diagnose ──────────────────────────────────────────────────────────────

def test_diagnose_commit_match():
    git = {"sha": "abc123def4567890"}
    prov = {"ok": True, "provider": "vercel", "git_commit": "abc123def4567890", "state": "READY"}
    d = diagnose(git, {"headers": {}}, {"vite_bundles": []}, prov)
    assert d["verdict"] == "deployed"
    assert d["confidence"] == "high"


def test_diagnose_commit_mismatch():
    git = {"sha": "abc123def4567890"}
    prov = {"ok": True, "provider": "vercel", "git_commit": "deadbeefdeadbeef", "state": "READY"}
    d = diagnose(git, {"headers": {}}, {"vite_bundles": []}, prov)
    assert d["verdict"] == "deploy_mismatch"
    assert d["confidence"] == "high"


def test_diagnose_timestamp_pending():
    git = {"committed_at": "2026-08-03T10:00:00+00:00"}
    prov = {
        "ok": True,
        "provider": "vercel",
        "git_commit": None,
        "created": "2026-08-01T10:00:00+00:00",
    }
    d = diagnose(git, {"headers": {}}, {"vite_bundles": []}, prov)
    assert d["verdict"] == "deploy_pending"


def test_diagnose_timestamp_deployed():
    git = {"committed_at": "2026-08-01T10:00:00+00:00"}
    prov = {
        "ok": True,
        "provider": "vercel",
        "git_commit": None,
        "created": "2026-08-02T10:00:00+00:00",
    }
    d = diagnose(git, {"headers": {}}, {"vite_bundles": []}, prov)
    assert d["verdict"] == "deployed"


def test_diagnose_cache_hit_only():
    git = {"sha": "abc"}
    prov = {"ok": False, "skipped": True, "reason": "no provider"}
    d = diagnose(git, {"headers": {"x-vercel-cache": "HIT", "age": "120"}}, {"vite_bundles": []}, prov)
    assert d["verdict"] == "stale_cache"


def test_diagnose_fingerprint_fallback():
    git = {"sha": "abc"}
    prov = {"ok": False, "skipped": True, "reason": "no provider"}
    d = diagnose(
        git,
        {"headers": {}},
        {"vite_bundles": ["index-abc.js"]},
        prov,
    )
    assert d["verdict"] == "deployed"


def test_stale_verdicts_exit_set():
    assert "deploy_mismatch" in diag_mod.STALE_VERDICTS
    assert "stale_cache" in diag_mod.STALE_VERDICTS
    assert "deployed" not in diag_mod.STALE_VERDICTS


# ── headers ───────────────────────────────────────────────────────────────

def test_host_headers_filters():
    h = host_headers({"x-vercel-id": "iad1::abc", "server": "Vercel", "content-type": "text/html"})
    assert h.get("x-vercel-id") == "iad1::abc"
    assert "content-type" not in h
