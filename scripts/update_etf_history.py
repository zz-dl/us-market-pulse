"""
更新 A 股 QDII ETF(159941 纳指广发 / 513500 标普博时)日线进 DB。
来源:东方财富 push2his(⚠️ Render 服务器 IP 被它拦截,此脚本只在本地跑;
DB 是 git 跟踪的,本地更新后随 commit 带到线上)。
用法: python scripts/update_etf_history.py        # 增量(近120根)
      python scripts/update_etf_history.py --full # 全历史重拉
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "us_market_pulse.sqlite3"
ETFS = [("159941", "0.159941"), ("513500", "1.513500")]


def kline(secid: str, lmt: int, retries: int = 3) -> list[tuple]:
    url = ("http://push2his.eastmoney.com/api/qt/stock/kline/get?secid=" + secid +
           "&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56"
           f"&klt=101&fqt=1&end=20500101&lmt={lmt}")
    d = None
    for attempt in range(retries):
        try:
            d = json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15))
            break
        except Exception as exc:
            if attempt == retries - 1:
                raise
            print(f"  {secid} attempt {attempt + 1} failed ({exc}), retrying...")
            time.sleep(2)
    out = []
    for k in (d.get("data") or {}).get("klines") or []:
        p = k.split(",")  # date,open,close,high,low,volume
        out.append((p[0], float(p[1]), float(p[3]), float(p[4]), float(p[2]), int(float(p[5]))))
    return out  # (date, open, high, low, close, volume)


def main() -> None:
    lmt = 4000 if "--full" in sys.argv else 120
    con = sqlite3.connect(DB)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for code, secid in ETFS:
        rows = kline(secid, lmt)
        con.executemany(
            "insert into market_prices(symbol,trade_date,open,high,low,close,volume,source,updated_at) "
            "values(?,?,?,?,?,?,?,?,?) on conflict(symbol,trade_date) do update set "
            "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
            "volume=excluded.volume,updated_at=excluded.updated_at",
            [(code, *r, "eastmoney_daily", now) for r in rows])
        con.commit()
        n = con.execute("select count(*), max(trade_date) from market_prices where symbol=?",
                        (code,)).fetchone()
        print(f"{code}: +{len(rows)} fetched, total {n[0]} rows, latest {n[1]}")


if __name__ == "__main__":
    main()
