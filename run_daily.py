from __future__ import annotations

import os

import requests

from app import UNIVERSE
from daily_runner import create_daily_snapshot


def main() -> None:
    url = os.environ.get("US_MARKET_PULSE_URL")
    key = os.environ.get("RUN_KEY")
    if url:
        headers = {"X-Run-Key": key} if key else {}
        response = requests.post(f"{url.rstrip('/')}/api/daily/run", headers=headers, timeout=120)
        response.raise_for_status()
        print(response.text)
        return
    payload = create_daily_snapshot(UNIVERSE, refresh=True)
    print(f"daily run ok={payload['ok']} date={payload['run_date']}")


if __name__ == "__main__":
    main()
