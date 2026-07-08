"""
美股预判推送(模型 + 实时期货 + 溢价卡口)→ 飞书(Lark)
==================================================
北京时间 14:30 拉取线上 us-market-pulse 的预判(SPY/QQQ 方向 + 实时 ES/NQ 期货),
叠加本地实时抓取的 QDII 溢价卡口,映射到两只 ETF(博时513500 / 广发159941)推送。

本地职责(Render 磁盘每次部署重置,持久数据只能在本地积累):
  1. 溢价采集落库(etf_premium_history)→ 溢价3日膨胀>2pp 禁买卡口;
  2. 预报快照落库(forecast_snapshots)→ 为实时期货项(权重0.55,从未回测)积累实盘验证样本;
  3. 推送依据改用 ETF 口径扣费净收益(唯一和钱包对齐的记分牌),不再引用美股方向命中率。

复用 F:\FeishuBridge\config.json 的飞书应用凭证 + 收件人 open_id。
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime

from db_store import (
    evaluate_premium_gate,
    load_etf_backtest,
    store_etf_premium,
    store_forecast_snapshot,
)
from etf_backtest import ETF_MAPPING, best_exit_rule
from market_data import fetch_etf_premium

BASE = "https://us-market-pulse.onrender.com"
FEISHU_CONFIG = r"F:\FeishuBridge\config.json"

# 标的 → 你的场内基金
ETF = {
    "SPY": ("标普500", "博时513500"),
    "QQQ": ("纳指", "广发159941"),
}
DIR_LABEL = {
    "bullish": "🟢 偏多(可买/持有)",
    "bearish": "🔴 偏空(减仓/别追)",
    "neutral": "⚪ 观望",
}
EXIT_RULE_LABEL = {
    "next_open": "次日开盘卖(只吃隔夜缺口)",
    "next_close": "次日收盘卖",
}


def _get(path: str, timeout: int = 120) -> dict:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def _collect_premiums() -> dict:
    """实时抓溢价 → 落库 → 逐只评估卡口。失败时返回空(推送里如实说明)。"""
    quotes = fetch_etf_premium()
    if quotes:
        store_etf_premium(quotes)
    gates = {}
    for symbol, q in quotes.items():
        gates[symbol] = {
            **q,
            "gate": evaluate_premium_gate(q["etf_code"], q["premium_pct"]),
        }
    return gates


def _etf_basis_lines() -> list[str]:
    """从本地 DB 读 ETF 口径回测,生成推送底部的"真实依据"两行。"""
    rows = load_etf_backtest()
    if not rows:
        return ["⚠️ ETF口径回测数据缺失(先在本地跑 scripts/run_etf_backtest.py)"]
    best = best_exit_rule(rows)
    if not best:
        return []
    lines = [
        f"依据(ETF口径·近3年·扣费0.12%): 最优退出={EXIT_RULE_LABEL.get(best['exit_rule'], best['exit_rule'])}"
        f", 净{best['net_avg_pct']:+.3f}%/笔({best['trades']}笔)",
    ]
    per = {r["us_symbol"]: r for r in rows
           if r["exit_rule"] == best["exit_rule"] and r["window"].startswith("recent")}
    detail = "  ".join(
        f"{ETF[s][1]}: 净{per[s]['net_avg_pct']:+.3f}%/笔·净胜率{per[s]['net_win_rate']:.0f}%"
        for s in ("SPY", "QQQ") if s in per
    )
    if detail:
        lines.append("   " + detail)
    return lines


def build_message() -> str:
    fc = _get("/api/forecast")
    forecasts = {f["symbol"]: f for f in fc.get("forecasts", [])}
    premiums = _collect_premiums()

    # 本地持久化快照:期货项(0.55)的实盘验证样本只能在这里积累(Render 磁盘会被部署重置)
    for f in forecasts.values():
        try:
            store_forecast_snapshot(f, fc.get("market_context"))
        except Exception as exc:
            print(f"snapshot store failed: {exc}", file=sys.stderr)

    now = datetime.now().strftime("%m-%d %H:%M")
    L = ["📈 美股预判(模型+实时期货+溢价卡口)", f"北京时间 {now} · 513500/159941 买卖依据", ""]

    for sym in ("SPY", "QQQ"):
        f = forecasts.get(sym)
        idx_name, etf_name = ETF[sym]
        L.append(f"〔{idx_name} → {etf_name}〕")
        if not f:
            L.append("  (预判获取失败)")
            continue
        event = f.get("event_mode") or {}
        d = DIR_LABEL.get(f.get("direction"), f.get("direction", "—"))
        fut = f.get("live_futures_pct")
        fut_name = "ES" if sym == "SPY" else "NQ"

        prem = premiums.get(sym)
        gate = (prem or {}).get("gate") or {}
        blocked = gate.get("level") == "block"

        # 方向行:溢价卡口 block 时,即使模型偏多也降级为"今天别买"
        if blocked and f.get("direction") == "bullish":
            L.append(f"  🚫 模型偏多,但溢价卡口禁买 → 今天别买")
        else:
            L.append(f"  {d}  信号强度{f.get('signal_strength', f.get('confidence', '—'))}")

        # 溢价行(QDII 盈亏最大单一噪音源,必须每天看)
        if prem:
            exp = gate.get("expansion_3d_pp")
            exp_txt = f", 3日{exp:+.1f}pp" if exp is not None else ""
            icon = {"block": "🚫", "warn": "⚠️", "ok": "·"}.get(gate.get("level"), "·")
            L.append(f"  {icon} 溢价 {prem['premium_pct']:.1f}%(价{prem['price']:.3f}/净值{prem['nav']:.4f}{exp_txt})")
            if gate.get("message"):
                L.append(f"    {gate['message']}")
        else:
            L.append("  ⚠️ 溢价抓取失败,买前务必手动看溢价率")

        if fut is not None:
            L.append(f"  开盘参考: 实时{fut_name}期货 {fut:+.2f}%(不代表收盘)")
        if event.get("active"):
            raw = DIR_LABEL.get(f.get("model_direction"), f.get("model_direction", "—"))
            L.append(f"  ⚠️ {event.get('name')}待公布: 基础模型{raw},最终收盘方向暂停判断")
        risks = f.get("risks") or []
        if risks:
            L.append(f"  · {risks[0]}")

    L.append("")
    L.extend(_etf_basis_lines())
    L.append("⚠️ 方向边很薄(净~0.05%/笔),溢价单日波动±1-2pp可数倍吞掉它;溢价卡口优先级最高。")
    L.append("   FOMC/CPI/非农/PCE 公布前进入事件观望。不构成投资建议。")
    return "\n".join(L)


def push_lark(text: str) -> bool:
    with open(FEISHU_CONFIG, encoding="utf-8") as fp:
        cfg = json.load(fp)
    recipients = cfg.get("allowed_open_ids", [])
    if not recipients:
        print("config 无 allowed_open_ids", file=sys.stderr)
        return False
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
    client = (lark.Client.builder().app_id(cfg["app_id"]).app_secret(cfg["app_secret"])
              .domain(cfg.get("domain", "https://open.feishu.cn")).build())
    ok = True
    for oid in recipients:
        req = (CreateMessageRequest.builder().receive_id_type("open_id")
               .request_body(CreateMessageRequestBody.builder().receive_id(oid)
                             .msg_type("text").content(lark.JSON.marshal({"text": text})).build())
               .build())
        resp = client.im.v1.message.create(req)
        if not resp.success():
            print(f"push {oid} failed: {resp.code} {resp.msg}", file=sys.stderr)
            ok = False
    return ok


def main() -> None:
    msg = build_message()
    print(msg)
    if "--dry" in sys.argv:
        return
    print(f"\n[push: {'OK' if push_lark(msg) else 'FAILED'}]", file=sys.stderr)


if __name__ == "__main__":
    main()
