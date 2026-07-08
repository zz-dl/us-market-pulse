# -*- coding: utf-8 -*-
"""每日 14:30 本地数据采集(无推送;用户自己打开网页看)
==================================================
飞书推送已移除(2026-07-08 用户决定)。此脚本保留原推送任务里的两个数据职责,
因为 Render 磁盘每次部署重置,持久数据只能在本地积累:
  1. 溢价采集落库(etf_premium_history)→ 网页端溢价卡口的3日膨胀基线;
  2. 预报快照落库(forecast_snapshots)→ 为实时期货项(权重0.55,从未回测)
     积累实盘验证样本,攒3-6个月后回归检验该系数。
计划任务 USMarketPulsePush(WEEKLY MON-FRI 14:30)经 run_marketpulse_push.bat 调用。
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime

from db_store import store_etf_premium, store_forecast_snapshot
from market_data import fetch_etf_premium

BASE = "https://us-market-pulse.onrender.com"


def _get(path: str, timeout: int = 120) -> dict:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def main() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    quotes = fetch_etf_premium()
    n_prem = store_etf_premium(quotes) if quotes else 0
    prem_txt = "  ".join(
        f"{s}:{q['premium_pct']:.2f}%" for s, q in quotes.items()) or "抓取失败"

    n_snap = 0
    try:
        fc = _get("/api/forecast")
        for f in fc.get("forecasts", []):
            store_forecast_snapshot(f, fc.get("market_context"))
            n_snap += 1
    except Exception as exc:
        print(f"[{now}] forecast snapshot failed: {exc}", file=sys.stderr)

    print(f"[{now}] premium {n_prem} rows ({prem_txt}), snapshots {n_snap} rows")


if __name__ == "__main__":
    main()
