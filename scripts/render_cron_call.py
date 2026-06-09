from __future__ import annotations

import os
import sys

import requests


DEFAULT_BASE_URL = "https://us-market-pulse.onrender.com"
ACTION_PATHS = {
    "daily-run": "/api/actions/daily-run",
}


def build_url(action: str, base_url: str | None = None) -> str:
    if action not in ACTION_PATHS:
        valid = ", ".join(sorted(ACTION_PATHS))
        raise ValueError(f"unknown action {action!r}; expected one of: {valid}")
    base = (base_url or os.environ.get("US_MARKET_PULSE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    return f"{base}{ACTION_PATHS[action]}"


def call_action(action: str, base_url: str | None = None, post=requests.post) -> tuple[int, str]:
    response = None
    try:
        response = post(
            build_url(action, base_url),
            headers={"Content-Type": "application/json"},
            timeout=120,
        )
        body = response.text
        response.raise_for_status()
        return 0, body
    except Exception as exc:
        body = getattr(response, "text", "") if response is not None else ""
        return 1, body or str(exc)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) != 1:
        valid = ", ".join(sorted(ACTION_PATHS))
        print(f"usage: python scripts/render_cron_call.py <{valid}>", file=sys.stderr)
        return 2
    code, body = call_action(argv[0])
    if body:
        print(body)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
