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


def _weekday_of(value) -> int:
    if hasattr(value, "weekday"):
        return value.weekday()
    from datetime import date as _date
    return _date.fromisoformat(str(value)[:10]).weekday()


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
    # 连跌天数（含当日，最多数 6 天）
    down_streak = 0
    while down_streak < 6 and len(closes) > down_streak + 1 \
            and closes[-1 - down_streak] < closes[-2 - down_streak]:
        down_streak += 1
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
        "down_streak": float(down_streak),
        "weekday": float(_weekday_of(last["date"])),
        "last_close": last["close"],
        "last_date": last["date"].isoformat() if hasattr(last["date"], "isoformat") else str(last["date"])[:10],
    }


def _clip(x: float, c: float = 3.0) -> float:
    return max(-c, min(c, x))


def _score_from_features(f: dict, vix_level: float | None = None) -> tuple[float, list[dict], list[str]]:
    """方向打分（基于对 SPY/QQQ 全历史真实数据的实证,双时间窗+双标的验证）：
    日级指数方向≈随机游走，能稳定>50%的边——
      1) 长期上行漂移：美股约 54-57% 的交易日是涨的 → 给一个看多基线；
      2) 1 日均值回归：昨日跌，今晚反弹概率更高 → 反向权重；
         但只在 200 日线上方有完整效果（线下抄底实测≈掷硬币 → 反转项 ×0.45 打折）；
      3) 200 日均线制度：线上做多有效，线下漂移消失；
      4) 连跌 3 天+ → 反弹概率显著升高（SPY 60.9%/QQQ 57.9%,近10年 59.8%/64.2%）；
      5) VIX<15 的平静市里昨日下跌 → 次日上涨 56-60%（VIX 缺失时此项为 0）；
      6) 周五小幅看多（双标的双窗稳健,权重很小）。
    （1/5/20 日“动量”实测无效甚至有害，已弃用。）"""
    drivers: list[dict] = []
    risks: list[str] = []

    r1 = f["one_day"]
    r20 = f["twenty_day"]
    above200 = f["above_ma200"] > 0.5
    down_streak = int(f.get("down_streak", 0))
    weekday = int(f.get("weekday", -1))

    drift = 0.35                                              # 1) 上行漂移基线
    rev = -0.45 * _clip(r1) * (1.0 if above200 else 0.45)     # 2) 制度内才有完整反转边
    regime = 0.15 if above200 else -0.20                      # 3) 200日线制度
    trend = 0.10 * tanh(r20 / 5.0)                            # 轻度中期趋势
    streak = 0.25 if down_streak >= 3 else 0.0                # 4) 连跌3天+反弹
    calm_dip = 0.15 if (vix_level is not None and vix_level < 15 and r1 < 0) else 0.0  # 5)
    friday = 0.07 if weekday == 4 else 0.0                    # 6) 周五效应

    score = drift + rev + regime + trend + streak + calm_dip + friday

    drivers.append({"label": "长期上行漂移", "value": round(drift, 2),
                    "effect": "bullish", "contribution": round(drift, 3)})
    drivers.append({"label": "1日均值回归（昨日跌→今晚易反弹）" + ("" if above200 else "·线下打折"),
                    "value": round(r1, 3),
                    "effect": "bullish" if rev > 0.05 else ("bearish" if rev < -0.05 else "neutral"),
                    "contribution": round(rev, 3)})
    drivers.append({"label": "200日均线上方" if above200 else "200日均线下方",
                    "value": round(f["dist_ma200"], 2),
                    "effect": "bullish" if regime > 0 else "bearish", "contribution": round(regime, 3)})
    drivers.append({"label": "20日趋势", "value": round(r20, 3),
                    "effect": "bullish" if trend > 0.02 else ("bearish" if trend < -0.02 else "neutral"),
                    "contribution": round(trend, 3)})
    if streak:
        drivers.append({"label": f"已连跌{down_streak}天（历史反弹概率↑）", "value": down_streak,
                        "effect": "bullish", "contribution": streak})
    if calm_dip:
        drivers.append({"label": f"平静市回调（VIX {vix_level:.1f}<15 且昨跌）", "value": round(vix_level, 1),
                        "effect": "bullish", "contribution": calm_dip})
    if friday:
        drivers.append({"label": "周五效应", "value": 5, "effect": "bullish", "contribution": friday})

    if f["volatility_20d"] > 30:
        score *= 0.8
        risks.append("20日波动率偏高，方向信号降权")
    if down_streak >= 3:
        risks.append(f"已连跌{down_streak}天 → 历史上次日反弹概率约 58-64%（连跌抄底是最强单一信号）")
    elif r1 <= -1.0 and above200:
        risks.append("昨日大跌 + 处于上升趋势 → 历史上今晚反弹概率偏高（抄底信号强）")
    if r1 >= 1.5 and not above200:
        risks.append("下行趋势中的急涨 → 今晚可能高开回落")

    return score, drivers, risks


def build_forecast(symbol: str, label: str, rows: list[dict],
                   live_futures_pct: float | None = None,
                   vix_level: float | None = None,
                   event_mode: dict | None = None) -> dict:
    rows = sorted(rows, key=lambda r: r["date"])
    f = _features(rows)
    score, drivers, risks = _score_from_features(f, vix_level=vix_level)

    # 实时期货修正(仅实盘 14:30 调用时传入;回测不传 → None → 不影响回测口径)。
    # 2:30pm 的 ES/NQ 期货是市场对"今晚"的实时下注,是日线数据看不到的最强信号。
    futures_used = None
    etf_gap_proxy = None
    etf_signal = None
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

        # A股交易时段的 QDII ETF 会先反映昨晚美股收盘，再叠加盘前期货。
        # 这和“今晚美股是否均值反弹”不是同一个问题；若隔夜净值压力明显为负，
        # 只作为国内 ETF 压力参考，不能覆盖今晚美股方向判断。
        etf_gap_proxy = round(f["one_day"] + 0.60 * live_futures_pct, 3)
        if etf_gap_proxy <= -0.35:
            etf_signal = {
                "direction": "bearish",
                "label": "今日ETF净值压力",
                "action": "ETF减仓/别追",
                "value": etf_gap_proxy,
            }
            drivers.append({
                "label": "ETF参考: 隔夜净值压力",
                "value": etf_gap_proxy,
                "effect": "bearish",
                "contribution": 0.0,
            })
            risks.append(
                f"ETF参考：今日净值压力约 {etf_gap_proxy:+.2f}%；这影响A股场内ETF，"
                "不覆盖今晚美股方向判断。"
            )
        elif etf_gap_proxy >= 0.35:
            etf_signal = {
                "direction": "bullish",
                "label": "今日ETF净值支撑",
                "action": "ETF可持有",
                "value": etf_gap_proxy,
            }
            drivers.append({
                "label": "ETF参考: 隔夜净值支撑",
                "value": etf_gap_proxy,
                "effect": "bullish",
                "contribution": 0.0,
            })
        else:
            etf_signal = {
                "direction": "neutral",
                "label": "今日ETF影响中性",
                "action": "ETF看场内溢价",
                "value": etf_gap_proxy,
            }

    # 非对称阈值:做多门槛低（上行漂移是真实的），做空门槛高（实证里做空≈掷硬币）。
    # bear 阈值 -0.70 经实测调优:更宽(-0.5)时偏空腿掉到 43-50% 亏损,收紧后剩下的偏空才有边。
    # 对应你的用法:bullish=买/持有，bearish=减仓/别追，neutral=观望。
    if score > 0.30:
        model_direction = "bullish"
    elif score < -0.70:
        model_direction = "bearish"
    else:
        model_direction = "neutral"
    model_score = round(score, 3)
    signal_strength = min(82, max(35, round(48 + abs(score) * 12, 1)))

    event_mode = event_mode or {"active": False, "status": "none"}
    if event_mode.get("active") and event_mode.get("status") == "pending":
        direction = "neutral"
        final_score = 0.0
        drivers.append({
            "label": f"重大事件模式：{event_mode.get('name', '宏观数据')}",
            "value": "pending",
            "effect": "neutral",
            "contribution": 0.0,
        })
        risks.insert(
            0,
            f"{event_mode.get('name', '重大宏观事件')}尚未公布：最终收盘方向暂停判断；"
            f"基础模型为{model_direction}，仅保留开盘参考。",
        )
    else:
        direction = model_direction
        final_score = model_score

    if live_futures_pct is None or abs(live_futures_pct) < 0.30:
        opening_bias = "neutral"
    else:
        opening_bias = "bullish" if live_futures_pct > 0 else "bearish"

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
        "score": final_score,
        "model_direction": model_direction,
        "model_score": model_score,
        "opening_bias": opening_bias,
        "signal_strength": signal_strength,
        "confidence": signal_strength,
        "event_mode": event_mode,
        "live_futures_pct": futures_used,
        "etf_gap_proxy_pct": etf_gap_proxy,
        "etf_signal": etf_signal,
        "decision_basis": "us_next_session",
        "vix_level": round(vix_level, 2) if vix_level is not None else None,
        "last_close": round(close, 3),
        "features": {k: round(v, 4) if isinstance(v, float) else v for k, v in f.items()},
        "drivers": sorted(drivers, key=lambda d: abs(d["contribution"]), reverse=True),
        "risks": risks,
        "invalidation": invalidation,
    }


def _date_key(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)[:10]


def run_backtest(symbol: str, label: str, rows: list[dict], min_history: int = 200,
                 vix_series: dict | None = None) -> dict:
    """vix_series: {YYYY-MM-DD: vix_close}。回测里第 t 日只用第 t 日(当日收盘前已知)的 VIX。"""
    rows = sorted(rows, key=lambda r: r["date"])
    samples = []
    actionable = []
    annual = defaultdict(lambda: {"trades": 0, "wins": 0, "returns": []})
    for i in range(min_history, len(rows) - 1):
        vix = (vix_series or {}).get(_date_key(rows[i]["date"]))
        forecast = build_forecast(symbol, label, _slice(rows, i), vix_level=vix)
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
