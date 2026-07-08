from datetime import date, timedelta
from tempfile import TemporaryDirectory
from pathlib import Path

from db_store import (
    database_status,
    initialize_database,
    load_backtest_from_db,
    load_history_from_db,
    store_forecast_snapshot,
    sync_symbol_dataset,
)


def check(label, cond, detail=""):
    mark = "[PASS]" if cond else "[FAIL]"
    print(f"  {mark} {label}" + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def make_rows():
    rows = []
    start = date(2025, 1, 1)
    close = 100.0
    for i in range(260):
        close *= 1.004 if i % 3 else 0.997
        rows.append({
            "date": start + timedelta(days=i),
            "open": round(close * 0.998, 2),
            "high": round(close * 1.01, 2),
            "low": round(close * 0.99, 2),
            "close": round(close, 2),
            "volume": 1000000 + i,
        })
    return rows


with TemporaryDirectory() as temp:
    db_path = Path(temp) / "market.sqlite3"
    initialize_database(db_path)
    rows = make_rows()
    result = sync_symbol_dataset("QQQ", "Nasdaq-100", rows, db_path=db_path, source="test")
    loaded = load_history_from_db("QQQ", db_path=db_path)
    backtest = load_backtest_from_db("QQQ", "Nasdaq-100", db_path=db_path)
    status = database_status(db_path)

    check("writes all price rows", result["price_rows"] == len(rows), result)
    check("reads rows from db", len(loaded) == len(rows), len(loaded))
    check("dates round trip as date objects", loaded[0]["date"] == rows[0]["date"], loaded[0]["date"])
    check("summary is written", result["backtest_summary"]["trades"] > 0, result["backtest_summary"])
    check("backtest can be read from db", backtest["trades"] == result["backtest_summary"]["trades"], backtest)
    check("recent db signals are capped", len(backtest["recent_signals"]) <= 12, len(backtest["recent_signals"]))
    check("signals are written", result["signal_rows"] > 0, result["signal_rows"])
    check("status includes market_prices", status["tables"]["market_prices"] == len(rows), status)
    check("status includes summary table", status["tables"]["backtest_summary"] == 1, status)
    check("status includes persisted signal table", status["tables"]["backtest_signals"] == result["signal_rows"], status)
    check("status includes symbol range", status["symbols"][0]["start"] == rows[0]["date"].isoformat(), status["symbols"])

    snapshot_id = store_forecast_snapshot({
        "symbol": "QQQ",
        "as_of": rows[-1]["date"].isoformat(),
        "direction": "neutral",
        "model_direction": "bullish",
        "score": 0.0,
        "model_score": 0.7,
        "live_futures_pct": -1.2,
        "opening_bias": "bearish",
        "data_quality": {
            "status": "fresh",
            "expected_last_session": rows[-1]["date"].isoformat(),
            "actual_last_session": rows[-1]["date"].isoformat(),
        },
        "realtime_guard": {"active": True},
        "drivers": [],
        "risks": [],
    }, {"risk_flags": ["test"]}, db_path=db_path)
    status = database_status(db_path)
    check("forecast snapshot is written", snapshot_id > 0, snapshot_id)
    check("status includes forecast snapshot table", status["tables"]["forecast_snapshots"] == 1, status)

print("ALL TESTS PASSED")
