"""Shared helpers: credentials, an HTTP client that times every call, and a JSONL logger.

Every request this repo makes goes through `call()`, which records method, path, status, latency,
the Moss/rate-limit headers and a trimmed body to logs/<run>.jsonl, so the write-up can cite real runs.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
PROJECT_ID = os.environ.get("MOSS_PROJECT_ID", "")
PROJECT_KEY = os.environ.get("MOSS_PROJECT_KEY", "")
BASE = "https://inference.moss.dev/v1"
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

KEEP_HEADERS = ("x-ratelimit", "retry-after", "x-moss", "idempotent", "x-request-id", "server-timing")


def auth(project_id: str | None = None, key: str | None = None) -> dict:
    return {"Authorization": f"Bearer {project_id or PROJECT_ID}:{key or PROJECT_KEY}"}


class Run:
    """One experiment run: appends events to logs/<name>.jsonl."""

    def __init__(self, name: str):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = LOGS / f"{name}_{stamp}.jsonl"
        self.client = httpx.Client(timeout=httpx.Timeout(120, connect=20))

    def log(self, **event) -> dict:
        event["ts"] = datetime.now(timezone.utc).isoformat()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        return event

    def call(self, label: str, method: str, path: str, *, headers: dict | None = None, body=None,
             content: bytes | None = None, use_auth: bool = True, raw_auth: dict | None = None) -> dict:
        h = {"Content-Type": "application/json"}
        if use_auth:
            h.update(raw_auth if raw_auth is not None else auth())
        h.update(headers or {})
        url = path if path.startswith("http") else BASE + path
        t0 = time.perf_counter()
        try:
            r = self.client.request(method, url, headers=h, json=body if content is None else None, content=content)
            ms = (time.perf_counter() - t0) * 1000
            try:
                data = r.json()
            except Exception:
                data = r.text[:2000]
            kept = {k: v for k, v in r.headers.items() if k.lower().startswith(KEEP_HEADERS)}
            return self.log(label=label, method=method, path=path, status=r.status_code, ms=round(ms, 1),
                            headers=kept, body=_trim(data), request=_trim(body) if content is None else f"<{len(content)} bytes>")
        except Exception as ex:  # network-level failure is itself a finding
            ms = (time.perf_counter() - t0) * 1000
            return self.log(label=label, method=method, path=path, status=None, ms=round(ms, 1), error=repr(ex)[:500])


def _trim(x, n: int = 4000):
    s = json.dumps(x, ensure_ascii=False, default=str) if not isinstance(x, str) else x
    if len(s) <= n:
        return x
    return s[:n] + f"... <{len(s) - n} more chars>"


def output_text(resp_body) -> str:
    """Pull the text out of an OpenAI Responses-shaped body."""
    if not isinstance(resp_body, dict):
        return ""
    if resp_body.get("output_text"):
        return resp_body["output_text"]
    parts = []
    for item in resp_body.get("output") or []:
        for c in item.get("content") or []:
            if c.get("type") in ("output_text", "text"):
                parts.append(c.get("text", ""))
    return "".join(parts)


def require_creds() -> None:
    if not PROJECT_ID or not PROJECT_KEY or "paste" in PROJECT_ID:
        raise SystemExit("Set MOSS_PROJECT_ID and MOSS_PROJECT_KEY in .env (see .env.example).")
