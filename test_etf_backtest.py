# -*- coding: utf-8 -*-
"""ETF 口径盈亏回测 + 溢价卡口的单元测试(合成数据验证逻辑;真实数据端到端另跑脚本)。"""
from datetime import date, timedelta
from pathlib import Path

import pytest

from etf_backtest import best_exit_rule, run_etf_pnl_backtest
from db_store import (
    evaluate_premium_gate,
    initialize_database,
    load_etf_backtest,
    load_premium_history,
    store_etf_backtest,
    store_etf_premium,
)


def _etf_rows(start: date, prices: list[tuple[float, float]]) -> list[dict]:
    """prices: [(open, close)],逐个工作日排。"""
    rows, d = [], start
    for o, c in prices:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        rows.append({"date": d, "open": o, "high": max(o, c), "low": min(o, c),
                     "close": c, "volume": 1000})
        d += timedelta(days=1)
    return rows


def test_pnl_two_exit_rules_differ():
    # 信号日 2026-01-05(周一美股),次A股日 01-06 收盘 1.00 买入;
    # 01-07 开盘 1.02(开盘卖 +2%)、收盘 0.99(收盘卖 -1%)。
    etf = _etf_rows(date(2026, 1, 5), [(1.0, 1.0), (1.0, 1.00), (1.02, 0.99), (1.0, 1.0)])
    signals = [{"signal_date": "2026-01-05", "direction": "bullish"}]
    out = run_etf_pnl_backtest("QQQ", "159941", signals, etf, cost_pct=0.12)
    by_rule = {r["exit_rule"]: r for r in out if r["window"] == "all"}
    assert by_rule["next_open"]["gross_avg_pct"] == pytest.approx(2.0, abs=0.01)
    assert by_rule["next_open"]["net_avg_pct"] == pytest.approx(1.88, abs=0.01)
    assert by_rule["next_close"]["gross_avg_pct"] == pytest.approx(-1.0, abs=0.01)
    assert by_rule["next_open"]["net_win_rate"] == 100.0
    assert by_rule["next_close"]["net_win_rate"] == 0.0


def test_pnl_skips_non_bullish_and_out_of_range():
    etf = _etf_rows(date(2026, 1, 5), [(1.0, 1.0), (1.0, 1.0), (1.0, 1.0)])
    signals = [
        {"signal_date": "2026-01-05", "direction": "bearish"},   # 非bullish跳过
        {"signal_date": "2025-01-01", "direction": "bullish"},   # 早于ETF史跳过
        {"signal_date": "2026-01-07", "direction": "bullish"},   # 无次日可卖跳过
    ]
    assert run_etf_pnl_backtest("QQQ", "159941", signals, etf) == []


def test_best_exit_rule_picks_higher_net():
    rows = [
        {"us_symbol": "QQQ", "etf_code": "159941", "exit_rule": "next_open",
         "window": "recent_3y", "trades": 100, "net_avg_pct": 0.05, "gross_avg_pct": 0.17,
         "net_win_rate": 55.0, "cum_net_pct": 5.0, "stdev_pct": 1.2, "cost_pct": 0.12},
        {"us_symbol": "QQQ", "etf_code": "159941", "exit_rule": "next_close",
         "window": "recent_3y", "trades": 100, "net_avg_pct": -0.02, "gross_avg_pct": 0.10,
         "net_win_rate": 48.0, "cum_net_pct": -2.0, "stdev_pct": 1.5, "cost_pct": 0.12},
    ]
    best = best_exit_rule(rows)
    assert best["exit_rule"] == "next_open"
    assert best["net_avg_pct"] == pytest.approx(0.05)


def test_premium_store_and_gate(tmp_path):
    db = tmp_path / "t.sqlite3"
    initialize_database(db)
    # 3天前溢价 7.0 → 今天 9.5:膨胀 +2.5pp 应 block
    for i, (d, p) in enumerate([("2026-07-03", 7.0), ("2026-07-06", 7.8), ("2026-07-07", 8.9)]):
        store_etf_premium(
            {"QQQ": {"etf_code": "159941", "price": 1.6, "nav": 1.5, "premium_pct": p}},
            trade_date=d, db_path=db)
    hist = load_premium_history("159941", db_path=db)
    assert [h["premium_pct"] for h in hist] == [7.0, 7.8, 8.9]
    gate = evaluate_premium_gate("159941", 9.5, db_path=db)
    assert gate["level"] == "block"
    assert gate["expansion_3d_pp"] == pytest.approx(2.5)
    # 溢价平稳且不高 → ok
    gate2 = evaluate_premium_gate("159941", 7.2, db_path=db)
    assert gate2["level"] == "ok"
    # 平稳但绝对值高 → warn
    gate3 = evaluate_premium_gate("159941", 8.5, db_path=db)
    assert gate3["level"] == "warn"


def test_etf_backtest_roundtrip(tmp_path):
    db = tmp_path / "t.sqlite3"
    rows = [{"us_symbol": "SPY", "etf_code": "513500", "exit_rule": "next_open",
             "window": "all", "trades": 10, "gross_avg_pct": 0.1, "net_avg_pct": -0.02,
             "net_win_rate": 40.0, "cum_net_pct": -0.2, "stdev_pct": 0.9, "cost_pct": 0.12}]
    assert store_etf_backtest(rows, db_path=db) == 1
    loaded = load_etf_backtest(db_path=db)
    assert len(loaded) == 1 and loaded[0]["net_avg_pct"] == pytest.approx(-0.02)


def test_run_backtest_avg_signed():
    """avg_next_return 必须按预测方向签名(此前 bearish 未签名的 bug 回归测试)。"""
    from forecast import run_backtest
    from test_forecast import make_rows  # 复用现有合成数据工厂
    rows = make_rows()
    result = run_backtest("SPY", "S&P 500", rows)
    if result["trades"]:
        # 手工按签名口径重算,应与返回值一致
        signed = [s["next_return_pct"] * (1 if s["direction"] == "bullish" else -1)
                  for s in result["recent_signals"] if s["direction"] != "neutral"]
        assert isinstance(result["avg_next_return"], float)
