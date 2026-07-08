"""ETF 口径真实盈亏回测 —— 唯一和钱包对齐的记分牌
====================================================
背景(2026-07-08 实证):原回测的 54-57% 赢率是"美股方向命中率",但实际交易的是
A股 QDII ETF(159941/513500),两者之间隔着溢价波动/汇率/开盘缺口噪音。
对账结果:美股真涨的日子里,ETF 交易扣费后赚钱的只有 67-68%;
全历史 ETF 口径扣费后为负,近3年 QQQ 侧勉强 +0.05%/笔。

交易还原(用户实际操作):
  信号日 t(美股日,预测 t+1 晚)之后的第一个 A 股交易日,以收盘价买入(≈14:30 决策价);
  退出规则两种口径对比:next_open = 次日开盘卖(只吃隔夜缺口),
                        next_close = 次日收盘卖(多持一个 A 股交易时段)。
成本:双边 0.12%(佣金 ~0.01%×2 + 买卖价差 ~0.06% + 冲击,保守口径)。
"""

from __future__ import annotations

from statistics import mean, stdev

ROUND_TRIP_COST_PCT = 0.12
RECENT_CUTOFF_YEARS = 3

# 美股信号标的 → 实际交易的 A 股 ETF
ETF_MAPPING = {
    "QQQ": "159941",   # 广发纳指ETF
    "SPY": "513500",   # 博时标普500ETF
}


def _stats(rets: list[float], cost: float) -> dict | None:
    if not rets:
        return None
    n = len(rets)
    gross = mean(rets)
    net = gross - cost
    return {
        "trades": n,
        "gross_avg_pct": round(gross, 4),
        "net_avg_pct": round(net, 4),
        "net_win_rate": round(sum(1 for r in rets if r > cost) / n * 100, 2),
        "cum_net_pct": round(net * n, 2),
        "stdev_pct": round(stdev(rets), 3) if n > 1 else 0.0,
        "cost_pct": cost,
    }


def run_etf_pnl_backtest(us_symbol: str, etf_code: str,
                         signals: list[dict], etf_rows: list[dict],
                         cost_pct: float = ROUND_TRIP_COST_PCT) -> list[dict]:
    """signals: [{signal_date, direction, ...}](来自 backtest_signals,只用 bullish);
    etf_rows: [{date, open, close, ...}] A股ETF日线(date 为 date 对象或 ISO 字符串)。
    返回按 (exit_rule, window) 展开的汇总行,供 store_etf_backtest 落库。"""
    prices = sorted(
        ({"d": r["date"].isoformat() if hasattr(r["date"], "isoformat") else str(r["date"])[:10],
          "open": r["open"], "close": r["close"]} for r in etf_rows),
        key=lambda x: x["d"],
    )
    dates = [p["d"] for p in prices]
    if not dates:
        return []
    recent_cutoff = f"{int(dates[-1][:4]) - RECENT_CUTOFF_YEARS}{dates[-1][4:10]}"

    def first_idx_after(d: str) -> int | None:
        lo, hi = 0, len(dates)
        while lo < hi:
            m = (lo + hi) // 2
            if dates[m] <= d:
                lo = m + 1
            else:
                hi = m
        return lo if lo < len(dates) - 1 else None  # 退出还需要下一交易日

    trades: dict[str, list[tuple[str, float]]] = {"next_open": [], "next_close": []}
    for sig in signals:
        if sig.get("direction") != "bullish":
            continue
        sig_date = str(sig["signal_date"])[:10]
        if sig_date < dates[0]:
            continue
        i = first_idx_after(sig_date)
        if i is None:
            continue
        entry = prices[i]["close"]
        if not entry:
            continue
        for rule, exit_px in (("next_open", prices[i + 1]["open"]),
                              ("next_close", prices[i + 1]["close"])):
            if exit_px:
                trades[rule].append((sig_date, (exit_px / entry - 1.0) * 100.0))

    out = []
    for rule, pairs in trades.items():
        for window, rets in (
            ("all", [r for _, r in pairs]),
            (f"recent_{RECENT_CUTOFF_YEARS}y", [r for d, r in pairs if d >= recent_cutoff]),
        ):
            s = _stats(rets, cost_pct)
            if s:
                out.append({"us_symbol": us_symbol.upper(), "etf_code": etf_code,
                            "exit_rule": rule, "window": window, **s})
    return out


def best_exit_rule(summary_rows: list[dict]) -> dict | None:
    """在近3年窗口上比较两种退出规则的净收益/笔(双标的合计加权),返回胜者及其数据。"""
    recent = [r for r in summary_rows if r["window"].startswith("recent")]
    if not recent:
        return None
    agg: dict[str, dict] = {}
    for r in recent:
        a = agg.setdefault(r["exit_rule"], {"net_sum": 0.0, "trades": 0, "rows": []})
        a["net_sum"] += r["net_avg_pct"] * r["trades"]
        a["trades"] += r["trades"]
        a["rows"].append(r)
    scored = {
        rule: {"net_avg_pct": round(a["net_sum"] / a["trades"], 4), "trades": a["trades"], "rows": a["rows"]}
        for rule, a in agg.items() if a["trades"]
    }
    if not scored:
        return None
    winner = max(scored, key=lambda k: scored[k]["net_avg_pct"])
    return {"exit_rule": winner, **scored[winner],
            "alternatives": {k: v["net_avg_pct"] for k, v in scored.items()}}
