"""HTTP helpers for probing production URLs."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Any

VITE_BUNDLE_RE = re.compile(r"index-[A-Za-z0-9_-]+\.js")
NEXT_BUILD_ID_RE = re.compile(r'"buildId"\s*:\s*"([^"]+)"')
NEXT_STATIC_RE = re.compile(r"/_next/static/([^/]+)/")
SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="([^"]+)"')
CF_PAGES_RE = re.compile(r"__cf_chl|cloudflarepages", re.IGNORECASE)


def http_request(url: str, method: str = "GET", timeout: float = 15.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "User-Agent": "deploy-sha-verify/0.1",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            body = resp.read() if method == "GET" else b""
            return {
                "ok": True,
                "status": resp.status,
                "headers": headers,
                "body": body.decode("utf-8", errors="replace"),
                "final_url": resp.geturl(),
            }
    except urllib.error.HTTPError as exc:
        headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {
            "ok": True,
            "status": exc.code,
            "headers": headers,
            "body": body,
            "final_url": exc.geturl(),
        }
    except Exception as exc:  # noqa: BLE001 — diagnostic CLI
        return {"ok": False, "error": str(exc)}


def extract_fingerprints(html: str) -> dict[str, Any]:
    """Pull build fingerprints out of an HTML document."""
    vite = sorted(set(VITE_BUNDLE_RE.findall(html)))
    build_ids = NEXT_BUILD_ID_RE.findall(html)
    static_chunks = sorted(set(NEXT_STATIC_RE.findall(html)))
    script_srcs = SCRIPT_SRC_RE.findall(html)

    return {
        "vite_bundles": vite[:5],
        "next_build_ids": build_ids[:3],
        "next_static_hashes": static_chunks[:3],
        "script_srcs": script_srcs[:8],
    }


def host_headers(headers: dict[str, str]) -> dict[str, str]:
    """Pick the interesting CDN/provider headers out of a response."""
    picked = {}
    for key in (
        "x-vercel-id",
        "x-vercel-cache",
        "x-vercel-error",
        "age",
        "cf-ray",
        "cf-cache-status",
        "server",
        "etag",
        "x-railway-",
        "x-render-origin-server",
    ):
        val = headers.get(key)
        if val:
            picked[key] = val
    return picked
