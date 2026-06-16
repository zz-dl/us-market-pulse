from datetime import date, timedelta

from forecast import build_forecast, run_backtest


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
        close *= 1.003 if (i % 20) < 14 else 0.996   # 反复涨跌，制造可回测的样本
        open_px = close * (0.997 if i % 4 == 0 else 1.001)
        rows.append({
            "date": start + timedelta(days=i),
            "open": round(open_px, 2),
            "high": round(close * 1.01, 2),
            "low": round(close * 0.99, 2),
            "close": round(close, 2),
            "volume": 1000000 + i,
        })
    return rows


def make_uptrend_with_last_drop(drop_pct):
    rows = []
    start = date(2025, 1, 1)
    close = 100.0
    for i in range(259):
        close *= 1.0025
        rows.append({
            "date": start + timedelta(days=i),
            "open": round(close * 0.998, 2),
            "high": round(close * 1.01, 2),
            "low": round(close * 0.99, 2),
            "close": round(close, 2),
            "volume": 1000000 + i,
        })
    close = rows[-1]["close"] * (1 + drop_pct / 100)
    rows.append({
        "date": start + timedelta(days=259),
        "open": round(close * 1.002, 2),
        "high": round(close * 1.006, 2),
        "low": round(close * 0.992, 2),
        "close": round(close, 2),
        "volume": 1000000 + 259,
    })
    return rows


rows = make_rows()
forecast = build_forecast("QQQ", "Nasdaq-100", rows)

check("forecast has symbol", forecast["symbol"] == "QQQ")
check("forecast has direction", forecast["direction"] in ("bullish", "bearish", "neutral"))
check("confidence is bounded", 0 <= forecast["confidence"] <= 100, forecast["confidence"])
check("drivers are present", len(forecast["drivers"]) >= 3, forecast["drivers"])
check("invalidation levels are present", len(forecast["invalidation"]) >= 2, forecast["invalidation"])

backtest = run_backtest("QQQ", "Nasdaq-100", rows, min_history=200)
check("backtest produces trades", backtest["trades"] > 20, backtest["trades"])
check("win rate is bounded", 0 <= backtest["win_rate"] <= 100, backtest["win_rate"])
check("recent samples are capped", len(backtest["recent_signals"]) <= 12, len(backtest["recent_signals"]))
check("annual summaries exist", len(backtest["annual"]) >= 1, backtest["annual"])

qqq_gap_down = build_forecast("QQQ", "Nasdaq-100", make_uptrend_with_last_drop(-1.9), live_futures_pct=-0.8)
check("QQQ ETF gap-down pressure overrides mean reversion", qqq_gap_down["direction"] == "bearish", qqq_gap_down)
check("QQQ ETF gap proxy is exposed", qqq_gap_down["etf_gap_proxy_pct"] < -2.0, qqq_gap_down)

spy_gap_down = build_forecast("SPY", "S&P 500", make_uptrend_with_last_drop(-0.6), live_futures_pct=0.28)
check("SPY ETF negative overnight gap is not bullish", spy_gap_down["direction"] == "bearish", spy_gap_down)
check("SPY ETF gap proxy is exposed", spy_gap_down["etf_gap_proxy_pct"] < -0.35, spy_gap_down)

print("ALL TESTS PASSED")
