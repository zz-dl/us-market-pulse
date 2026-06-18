"""
美股预判推送(模型 + 实时期货)→ 飞书(Lark)
==================================================
北京时间 14:30 拉取线上 us-market-pulse 的预判(SPY/QQQ 方向 + 实时 ES/NQ 期货),
映射到你的两只 ETF(博时标普500 513500 / 广发纳指 159941),推送到 GraceClaude。

复用 F:\FeishuBridge\config.json 的飞书应用凭证 + 收件人 open_id。
定位:辅助判断,不是算命;回测命中率≈54%,重大宏观数据公布前暂停收盘方向判断。
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime

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


def _get(path: str, timeout: int = 120) -> dict:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def build_message() -> str:
    fc = _get("/api/forecast")
    forecasts = {f["symbol"]: f for f in fc.get("forecasts", [])}
    # 回测命中率(best-effort)
    win = {}
    try:
        bt = _get("/api/backtest", timeout=60)
        win = {b["symbol"]: b.get("win_rate") for b in bt.get("backtests", [])}
    except Exception:
        pass

    now = datetime.now().strftime("%m-%d %H:%M")
    L = ["📈 美股预判(模型+实时期货)", f"北京时间 {now} · 决定 513500/159941 今晚买卖的依据", ""]

    for sym in ("SPY", "QQQ"):
        f = forecasts.get(sym)
        idx_name, etf_name = ETF[sym]
        L.append(f"〔{idx_name} → {etf_name}〕")
        if not f:
            L.append("  (预判获取失败)")
            continue
        event = f.get("event_mode") or {}
        d = DIR_LABEL.get(f.get("direction"), f.get("direction", "—"))
        strength = f.get("signal_strength", f.get("confidence"))
        fut = f.get("live_futures_pct")
        fut_name = "ES" if sym == "SPY" else "NQ"
        wr = win.get(sym)
        line = f"  {d}"
        if strength is not None:
            line += f"  信号强度{strength}"
        if wr is not None:
            line += f"  ·回测{wr}%"
        L.append(line)
        if fut is not None:
            L.append(f"  开盘参考：实时{fut_name}期货 {fut:+.2f}%（不代表收盘）")
        if event.get("active"):
            raw = DIR_LABEL.get(f.get("model_direction"), f.get("model_direction", "—"))
            L.append(f"  ⚠️ {event.get('name')}待公布：基础模型{raw}，最终收盘方向暂停判断")
        # 最关键的一条提示(抄底信号 / 期货方向 / 风险)
        risks = f.get("risks") or []
        if risks:
            L.append(f"  · {risks[0]}")

    L.append("")
    L.append("⚠️ 回测命中率≈54%(仅日线模型);实时期货只作开盘参考。")
    L.append("   FOMC/CPI/非农/PCE 公布前进入事件观望，不给强方向。不构成投资建议。")
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
