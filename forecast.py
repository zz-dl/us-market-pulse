from __future__ import annotations

from collections import defaultdict
from math import sqrt, tanh
from statistics import mean, pstdev


def pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a / b - 1.0) * 100.0


def _slice(rows: list[dict], end: int) -> list[dict]:
    return rows[: end + 1]


def _features(rows: list[dict]) -> dict:
    if len(rows) < 60:
        raise ValueError("at least 60 bars are required")
    last = rows[-1]
    closes = [r["close"] for r in rows]
    recent_returns = [pct(closes[i], closes[i - 1]) for i in range(len(closes) - 19, len(closes))]
    high_20 = max(r["high"] for r in rows[-20:])
    low_20 = min(r["low"] for r in rows[-20:])
    range_pos = 50.0 if high_20 == low_20 else (last["close"] - low_20) / (high_20 - low_20) * 100.0
    n_long = min(200, len(closes))
    ma50 = mean(closes[-50:])
    ma_long = mean(closes[-n_long:])
    return {
        "one_day": pct(closes[-1], closes[-2]),
        "five_day": pct(closes[-1], closes[-6]),
        "twenty_day": pct(closes[-1], closes[-21]),
        "volatility_20d": pstdev(recent_returns) * sqrt(252),
        "range_pos_20d": range_pos,
        "gap_proxy": pct(last["open"], rows[-2]["close"]),
        "above_ma50": 1.0 if last["close"] > ma50 else 0.0,
        "above_ma200": 1.0 if last["close"] > ma_long else 0.0,
        "dist_ma200": pct(last["close"], ma_long),
        "last_close": last["close"],
        "last_date": last["date"].isoformat() if hasattr(last["date"], "isoformat") else str(last["date"])[:10],
    }


def _clip(x: float, c: float = 3.0) -> float:
    return max(-c, min(c, x))


def _score_from_features(f: dict) -> tuple[float, list[dict], list[str]]:
    """方向打分（基于对 SPY/QQQ 全历史真实数据的实证）：
    日级指数方向≈随机游走，能稳定>50%的只有三条边——
      1) 长期上行漂移：美股约 54-57% 的交易日是涨的 → 给一个看多基线；
      2) 1 日均值回归：昨日跌，今晚反弹概率更高（昨日跌≤2%时次日上涨约 57-60%）→ 对昨日涨跌取“反向”权重；
      3) 200 日均线区间制度：线上做多有效，线下漂移消失 → 制度过滤。
    （1/5/20 日“动量”实测无效甚至有害，已弃用。）"""
    drivers: list[dict] = []
    risks: list[str] = []

    r1 = f["one_day"]
    r20 = f["twenty_day"]
    above200 = f["above_ma200"] > 0.5

    drift = 0.35                              # 1) 上行漂移基线
    rev = -0.45 * _clip(r1)                   # 2) 1日均值回归（昨日跌→看多）
    regime = 0.15 if above200 else -0.20      # 3) 200日线制度
    trend = 0.10 * tanh(r20 / 5.0)            # 轻度中期趋势

    score = drift + rev + regime + trend

    drivers.append({"label": "长期上行漂移", "value": round(drift, 2),
                    "effect": "bullish", "contribution": round(drift, 3)})
    drivers.append({"label": "1日均值回归（昨日跌→今晚易反弹）", "value": round(r1, 3),
                    "effect": "bullish" if rev > 0.05 else ("bearish" if rev < -0.05 else "neutral"),
                    "contribution": round(rev, 3)})
    drivers.append({"label": "200日均线上方" if above200 else "200日均线下方",
                    "value": round(f["dist_ma200"], 2),
                    "effect": "bullish" if regime > 0 else "bearish", "contribution": round(regime, 3)})
    drivers.append({"label": "20日趋势", "value": round(r20, 3),
                    "effect": "bullish" if trend > 0.02 else ("bearish" if trend < -0.02 else "neutral"),
                    "contribution": round(trend, 3)})

    if f["volatility_20d"] > 30:
        score *= 0.8
        risks.append("20日波动率偏高，方向信号降权")
    if r1 <= -1.0 and above200:
        risks.append("昨日大跌 + 处于上升趋势 → 历史上今晚反弹概率偏高（抄底信号最强）")
    if r1 >= 1.5 and not above200:
        risks.append("下行趋势中的急涨 → 今晚可能高开回落")

    return score, drivers, risks


def build_forecast(symbol: str, label: str, rows: list[dict],
                   live_futures_pct: float | None = None) -> dict:
    rows = sorted(rows, key=lambda r: r["date"])
    f = _features(rows)
    score, drivers, risks = _score_from_features(f)

    # 实时期货修正(仅实盘 14:30 调用时传入;回测不传 → None → 不影响回测口径)。
    # 2:30pm 的 ES/NQ 期货是市场对"今晚"的实时下注,是日线数据看不到的最强信号。
    futures_used = None
    if live_futures_pct is not None:
        contribution = round(0.55 * _clip(live_futures_pct, 2.5), 3)
        score += contribution
        futures_used = round(live_futures_pct, 3)
        drivers.append({
            "label": "实时期货(决策时点)",
            "value": round(live_futures_pct, 3),
            "effect": "bullish" if contribution > 0.05 else ("bearish" if contribution < -0.05 else "neutral"),
            "contribution": contribution,
        })
        if abs(live_futures_pct) >= 0.4:
            fut_name = "ES(标普)" if symbol.upper() == "SPY" else "NQ(纳指)"
            risks.append(
                f"实时 {fut_name} 期货 {live_futures_pct:+.2f}% → 今晚开盘方向偏"
                f"{'多' if live_futures_pct > 0 else '空'}（回测口径不含此项,实盘参考）"
            )

    # 非对称阈值:做多门槛低（上行漂移是真实的），做空门槛高（实证里做空≈掷硬币，
    # 只有分数很负时偏空才勉强不亏）。对应你的用法:bullish=买/持有，bearish=减仓/别追，neutral=观望。
    if score > 0.30:
        direction = "bullish"
    elif score < -0.50:
        direction = "bearish"
    else:
        direction = "neutral"
    confidence = min(82, max(35, round(48 + abs(score) * 12, 1)))
    close = f["last_close"]
    invalidation = [
        f"若盘前/期货跌破参考收盘 {close * 0.992:.2f}，多头判断失效",
        f"若盘前/期货突破参考收盘 {close * 1.008:.2f}，空头判断失效",
    ]
    return {
        "symbol": symbol,
        "label": label,
        "proxy_note": f"{symbol} is used as the tradeable proxy for {label}",
        "as_of": f["last_date"],
        "direction": direction,
        "score": round(score, 3),
        "confidence": confidence,
        "live_futures_pct": futures_used,
        "last_close": round(close, 3),
        "features": {k: round(v, 4) if isinstance(v, float) else v for k, v in f.items()},
        "drivers": sorted(drivers, key=lambda d: abs(d["contribution"]), reverse=True),
        "risks": risks,
        "invalidation": invalidation,
    }


def run_backtest(symbol: str, label: str, rows: list[dict], min_history: int = 200) -> dict:
    rows = sorted(rows, key=lambda r: r["date"])
    samples = []
    actionable = []
    annual = defaultdict(lambda: {"trades": 0, "wins": 0, "returns": []})
    for i in range(min_history, len(rows) - 1):
        forecast = build_forecast(symbol, label, _slice(rows, i))
        ret = pct(rows[i + 1]["close"], rows[i]["close"])
        if forecast["direction"] == "neutral":
            predicted_sign = 0
            win = None
        else:
            predicted_sign = 1 if forecast["direction"] == "bullish" else -1
            win = ret * predicted_sign > 0
        sample = {
            "date": rows[i]["date"].isoformat() if hasattr(rows[i]["date"], "isoformat") else str(rows[i]["date"])[:10],
            "next_date": rows[i + 1]["date"].isoformat() if hasattr(rows[i + 1]["date"], "isoformat") else str(rows[i + 1]["date"])[:10],
            "direction": forecast["direction"],
            "confidence": forecast["confidence"],
            "next_return_pct": round(ret, 3),
            "win": bool(win) if win is not None else None,
        }
        samples.append(sample)
        if predicted_sign:
            actionable.append(sample)
            year = sample["next_date"][:4]
            annual[year]["trades"] += 1
            annual[year]["wins"] += 1 if win else 0
            annual[year]["returns"].append(ret * predicted_sign)

    wins = sum(1 for s in actionable if s["win"])
    returns = [s["next_return_pct"] for s in actionable]
    annual_rows = []
    for year, data in sorted(annual.items()):
        annual_rows.append({
            "year": year,
            "trades": data["trades"],
            "win_rate": round(data["wins"] / data["trades"] * 100, 2) if data["trades"] else 0,
            "avg_signal_return": round(mean(data["returns"]), 4) if data["returns"] else 0,
        })

    return {
        "symbol": symbol,
        "label": label,
        "observations": len(samples),
        "trades": len(actionable),
        "win_rate": round(wins / len(actionable) * 100, 2) if actionable else 0,
        "avg_next_return": round(mean(returns), 4) if returns else 0,
        "bullish_count": sum(1 for s in samples if s["direction"] == "bullish"),
        "bearish_count": sum(1 for s in samples if s["direction"] == "bearish"),
        "neutral_count": sum(1 for s in samples if s["direction"] == "neutral"),
        "recent_signals": samples[-12:],
        "annual": annual_rows[-12:],
    }
