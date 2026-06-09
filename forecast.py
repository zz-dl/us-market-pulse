from __future__ import annotations

from collections import defaultdict
from math import sqrt
from statistics import mean, pstdev


def pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a / b - 1.0) * 100.0


def _slice(rows: list[dict], end: int) -> list[dict]:
    return rows[: end + 1]


def _features(rows: list[dict]) -> dict:
    if len(rows) < 30:
        raise ValueError("at least 30 bars are required")
    last = rows[-1]
    closes = [r["close"] for r in rows]
    recent_returns = [pct(closes[i], closes[i - 1]) for i in range(len(closes) - 19, len(closes))]
    high_20 = max(r["high"] for r in rows[-20:])
    low_20 = min(r["low"] for r in rows[-20:])
    range_pos = 50.0 if high_20 == low_20 else (last["close"] - low_20) / (high_20 - low_20) * 100.0
    return {
        "one_day": pct(closes[-1], closes[-2]),
        "five_day": pct(closes[-1], closes[-6]),
        "twenty_day": pct(closes[-1], closes[-21]),
        "volatility_20d": pstdev(recent_returns) * sqrt(252),
        "range_pos_20d": range_pos,
        "gap_proxy": pct(last["open"], rows[-2]["close"]),
        "last_close": last["close"],
        "last_date": last["date"].isoformat() if hasattr(last["date"], "isoformat") else str(last["date"])[:10],
    }


def _score_from_features(f: dict) -> tuple[float, list[dict], list[str]]:
    score = 0.0
    drivers: list[dict] = []
    risks: list[str] = []

    def add(label: str, value: float, weight: float, bullish_when_positive: bool = True):
        nonlocal score
        direction = 1 if (value >= 0) == bullish_when_positive else -1
        magnitude = min(abs(value), 3.0) / 3.0
        contribution = direction * weight * magnitude
        score += contribution
        drivers.append({
            "label": label,
            "value": round(value, 3),
            "effect": "bullish" if contribution > 0.05 else ("bearish" if contribution < -0.05 else "neutral"),
            "contribution": round(contribution, 3),
        })

    add("1日动量", f["one_day"], 0.8)
    add("5日动量", f["five_day"], 1.0)
    add("20日趋势", f["twenty_day"], 1.1)
    add("隔夜缺口代理", f["gap_proxy"], 0.5)

    if f["range_pos_20d"] > 75:
        score += 0.35
        drivers.append({"label": "20日区间位置", "value": round(f["range_pos_20d"], 1), "effect": "bullish", "contribution": 0.35})
    elif f["range_pos_20d"] < 25:
        score -= 0.35
        drivers.append({"label": "20日区间位置", "value": round(f["range_pos_20d"], 1), "effect": "bearish", "contribution": -0.35})
    else:
        drivers.append({"label": "20日区间位置", "value": round(f["range_pos_20d"], 1), "effect": "neutral", "contribution": 0.0})

    if f["volatility_20d"] > 28:
        score *= 0.72
        risks.append("20日波动率偏高，方向信号降权")
    if abs(f["one_day"]) > 2.5:
        risks.append("上一交易日大幅波动，今晚容易高开低走或低开反抽")

    return score, drivers, risks


def build_forecast(symbol: str, label: str, rows: list[dict]) -> dict:
    rows = sorted(rows, key=lambda r: r["date"])
    f = _features(rows)
    score, drivers, risks = _score_from_features(f)
    if score > 0.55:
        direction = "bullish"
    elif score < -0.55:
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
        "last_close": round(close, 3),
        "features": {k: round(v, 4) if isinstance(v, float) else v for k, v in f.items()},
        "drivers": sorted(drivers, key=lambda d: abs(d["contribution"]), reverse=True),
        "risks": risks,
        "invalidation": invalidation,
    }


def run_backtest(symbol: str, label: str, rows: list[dict], min_history: int = 60) -> dict:
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
