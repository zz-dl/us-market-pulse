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

qqq_rebound = build_forecast("QQQ", "Nasdaq-100", make_uptrend_with_last_drop(-1.9), live_futures_pct=1.61)
check("QQQ futures rebound drives tonight US forecast", qqq_rebound["direction"] == "bullish", qqq_rebound)
check("QQQ ETF pressure remains separately exposed", qqq_rebound["etf_gap_proxy_pct"] < -0.35, qqq_rebound)
check("QQQ ETF pressure does not set primary decision basis", qqq_rebound["decision_basis"] == "us_next_session", qqq_rebound)
check("QQQ ETF pressure has its own signal", qqq_rebound["etf_signal"]["direction"] == "bearish", qqq_rebound)

spy_rebound = build_forecast("SPY", "S&P 500", make_uptrend_with_last_drop(-0.6), live_futures_pct=1.16)
check("SPY futures rebound drives tonight US forecast", spy_rebound["direction"] == "bullish", spy_rebound)
check("SPY ETF pressure can be neutral separately", spy_rebound["etf_signal"]["direction"] == "neutral", spy_rebound)

event_mode = {
    "active": True,
    "status": "pending",
    "name": "FOMC 利率决议",
    "reason": "重大政策结果尚未公布",
    "release_time_beijing": "次日约 02:00",
}
fomc_qqq = build_forecast(
    "QQQ",
    "Nasdaq-100",
    make_uptrend_with_last_drop(-1.9),
    live_futures_pct=1.61,
    event_mode=event_mode,
)
check("pending macro event forces neutral close forecast", fomc_qqq["direction"] == "neutral", fomc_qqq)
check("pending macro event keeps raw model direction", fomc_qqq["model_direction"] == "bullish", fomc_qqq)
check("pending macro event keeps opening futures bias separate", fomc_qqq["opening_bias"] == "bullish", fomc_qqq)
check("pending macro event is exposed", fomc_qqq["event_mode"]["active"] is True, fomc_qqq)
check("confidence is renamed as signal strength", fomc_qqq["signal_strength"] == fomc_qqq["confidence"], fomc_qqq)

print("ALL TESTS PASSED")
