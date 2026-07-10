from __future__ import annotations

import os
from datetime import datetime
from math import isfinite

from flask import Flask, jsonify, request, send_from_directory

from datetime import timedelta, timezone as _tz

from daily_runner import (
    BEIJING_TZ,
    create_daily_snapshot,
    run_due_daily_job,
    scheduler_status,
    start_scheduler,
)
from db_store import (
    database_status,
    evaluate_premium_gate,
    load_backtest_from_db,
    load_etf_backtest,
    load_history_from_db,
    store_etf_premium,
    store_forecast_snapshot,
    store_price_rows,
    sync_symbol_dataset,
)
from forecast import build_forecast, run_backtest
from market_data import (
    detect_earnings_event_mode,
    detect_macro_event_mode,
    detect_news_risk_flags,
    ensure_history,
    fetch_etf_premium,
    fetch_futures_change,
    fetch_news_headlines,
    fetch_quote_change,
    fetch_vix_level,
    load_cached_history,
    price_path,
)


UNIVERSE = {
    "QQQ": {"label": "Nasdaq-100", "display": "纳斯达克", "index": "Nasdaq-100"},
    "SPY": {"label": "S&P 500", "display": "标普 500", "index": "S&P 500"},
}

app = Flask(__name__, static_folder="static")
APP_VERSION = "mvp-8-futures-basis"


def clean_json(value):
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, dict):
        return {k: clean_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_json(v) for v in value]
    return value


def beijing_now() -> str:
    return datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


def data_status() -> list[dict]:
    out = []
    db_symbols = {row["symbol"]: row for row in database_status()["symbols"]}
    for symbol, info in UNIVERSE.items():
        rows = load_cached_history(symbol)
        path = price_path(symbol)
        db_row = db_symbols.get(symbol, {})
        out.append({
            "symbol": symbol,
            "label": info["label"],
            "display": info["display"],
            "rows": len(rows),
            "start": rows[0]["date"].isoformat() if rows else None,
            "end": rows[-1]["date"].isoformat() if rows else None,
            "cached": path.exists(),
            "path": str(path),
            "db_rows": db_row.get("rows", 0),
            "db_start": db_row.get("start"),
            "db_end": db_row.get("end"),
            "db_win_rate": db_row.get("win_rate"),
        })
    return out


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/status")
def api_status():
    return jsonify(clean_json({
        "ok": True,
        "app": "USMarketPulse",
        "version": APP_VERSION,
        "beijing_time": beijing_now(),
        "prediction_time": "工作日北京时间 14:30 左右",
        "schedule": scheduler_status(),
        "data": data_status(),
    }))


@app.route("/api/refresh", methods=["POST", "GET"])
def api_refresh():
    results = []
    errors = []
    for symbol in UNIVERSE:
        try:
            rows, meta = ensure_history(symbol, refresh=True)
            db_meta = sync_symbol_dataset(symbol, UNIVERSE[symbol]["label"], rows, source=meta.get("source", "refresh"))
            meta["database"] = {
                "path": db_meta["db_path"],
                "price_rows": db_meta["price_rows"],
                "signal_rows": db_meta["signal_rows"],
            }
            results.append(meta)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
    return jsonify(clean_json({
        "ok": not errors,
        "beijing_time": beijing_now(),
        "results": results,
        "errors": errors,
    }))


def _build_market_context():
    """实时市场环境(展示层):汇率/美元/利率/VIX + 新闻标题 + 风险主题。
    重大宏观事件会单独触发事件模式，不能假定盘前期货已包含尚未公布的结果。"""
    vix_now = fetch_vix_level()
    news = fetch_news_headlines(10)
    ctx = {
        "vix": vix_now,
        "usdcny": fetch_quote_change("CNY=X"),
        "dxy": fetch_quote_change("DX-Y.NYB"),
        "us10y": fetch_quote_change("^TNX"),
        "news": news,
        "note": "已公布冲击参考实时期货；FOMC/CPI/非农/PCE 尚未公布时进入事件模式，暂停收盘方向判断。",
    }
    ctx["risk_flags"] = detect_news_risk_flags(ctx["news"])
    ctx["macro_event"] = detect_macro_event_mode(news, now=datetime.now(BEIJING_TZ))
    # 权重股财报观望:宏观事件优先;无宏观事件时,盘前财报同样触发事件模式
    try:
        earn = detect_earnings_event_mode(now=datetime.now(BEIJING_TZ))
    except Exception:
        earn = {"event_mode": {"active": False, "status": "none"}, "notes": [], "reporters": []}
    ctx["earnings_reporters"] = earn["reporters"]
    ctx["earnings_notes"] = earn["notes"]
    if not ctx["macro_event"].get("active") and earn["event_mode"].get("active"):
        ctx["macro_event"] = earn["event_mode"]
    return ctx, vix_now


def _macro_risk_notes(ctx) -> list[str]:
    """数据支持的环境警示(实证:这些日子次日上涨率掉到 50-52%,vs 基线 54-57%)。只提示,不改分。"""
    notes = []
    tnx = ctx.get("us10y") or {}
    dxy = ctx.get("dxy") or {}
    if (tnx.get("chg_pct") or 0) >= 3:
        notes.append(f"美债10Y收益率较昨日收盘 +{tnx['chg_pct']:.1f}% → 历史上利率飙升日次日上涨率仅约50-52%，看多信号打折扣")
    if (tnx.get("chg_pct") or 0) <= -3:
        notes.append(f"美债10Y收益率骤降 {tnx['chg_pct']:.1f}% → 历史上次日偏多（QQQ近10年约64%）")
    if (dxy.get("chg_pct") or 0) >= 0.5:
        notes.append(f"美元指数大涨 +{dxy['chg_pct']:.2f}% → 历史上美元急升日次日偏弱（50-53%）")
    if ctx.get("risk_flags"):
        notes.append("今日新闻含风险主题：" + "、".join(ctx["risk_flags"]) + " → 留意波动放大")
    notes.extend(ctx.get("earnings_notes") or [])   # 盘后权重股财报提示(不暂停方向)
    return notes


def _expected_last_us_session() -> str:
    """最近一个『已收盘』的美股交易日(美东16:00后算当天,否则前一交易日;周末回退)。"""
    et = datetime.now(_tz.utc) - timedelta(hours=5)   # 近似美东(忽略夏令时,偏保守)
    d = et.date()
    if et.hour < 16:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def _last_row_date(rows) -> str:
    last = rows[-1]["date"]
    return last.isoformat() if hasattr(last, "isoformat") else str(last)[:10]


def _apply_data_quality_guard(forecast: dict, expected_last_session: str, actual_last_session: str | None) -> dict:
    fresh = bool(actual_last_session and actual_last_session >= expected_last_session)
    forecast["data_quality"] = {
        "status": "fresh" if fresh else "stale",
        "fresh": fresh,
        "expected_last_session": expected_last_session,
        "actual_last_session": actual_last_session,
        "message": "" if fresh else (
            f"最新美股收盘数据未确认：应到 {expected_last_session}，当前只到 {actual_last_session or '无数据'}。"
        ),
    }
    if fresh:
        return forecast

    forecast["data_quality"]["blocked_direction"] = forecast.get("direction")
    forecast["direction"] = "neutral"
    forecast["score"] = 0.0
    forecast["decision_basis"] = "data_pending"
    forecast.setdefault("drivers", []).append({
        "label": "收盘数据未确认",
        "value": actual_last_session or "none",
        "effect": "neutral",
        "contribution": 0.0,
    })
    forecast.setdefault("risks", []).insert(0, forecast["data_quality"]["message"] + " 暂停买入/卖出方向判断，等待刷新后再看。")
    return forecast


@app.route("/api/forecast")
def api_forecast():
    """轻量路径:打开页面即刷新到最新已收盘数据。
    ⚠️ 不在请求里跑全量回测(全量回测在 Render 免费机要数分钟,曾导致 120s 超时→页面永远加载中);
    回测汇总读 DB 里预存的(模型变更时本地重算后随 commit 上线)。"""
    forecasts = []
    errors = []
    market_context, vix_now = _build_market_context()
    macro_notes = _macro_risk_notes(market_context)
    expected = _expected_last_us_session()
    # QDII 溢价卡口(盈亏最大单一噪音源):实时抓取,失败静默降级(Render 若被拦则不显示)
    premiums = {}
    try:
        premiums = fetch_etf_premium(timeout=5.0)
        if premiums:
            store_etf_premium(premiums)   # 部署周期内积累,供3日膨胀基线
    except Exception:
        premiums = {}
    for symbol, info in UNIVERSE.items():
        try:
            rows = load_history_from_db(symbol)
            meta = {"symbol": symbol, "source": "sqlite_db", "rows": len(rows)}
            if not rows or _last_row_date(rows) < expected:
                # 数据过期(部署重置/隔夜新收盘)→ 只刷价格(秒级),不重算回测
                try:
                    fresh, fmeta = ensure_history(symbol, refresh=True)
                except Exception:
                    fresh, fmeta = None, None
                if fresh and (not rows or _last_row_date(fresh) > _last_row_date(rows)):
                    store_price_rows(symbol, fresh, source=fmeta.get("source", "refresh"))
                    rows, meta = fresh, {**fmeta, "refreshed_to": _last_row_date(fresh)}
            fut = fetch_futures_change(symbol)
            fc = build_forecast(
                symbol,
                info["label"],
                rows,
                live_futures_pct=fut,
                vix_level=vix_now,
                event_mode=market_context.get("macro_event"),
            )
            fc = _apply_data_quality_guard(fc, expected, _last_row_date(rows) if rows else None)
            fc["risks"] = list(fc.get("risks") or []) + macro_notes
            # 溢价卡口:block 时置顶警示(即使模型偏多,今天也别买)
            prem = premiums.get(symbol)
            if prem:
                gate = evaluate_premium_gate(prem["etf_code"], prem["premium_pct"])
                fc["etf_premium"] = {**prem, "gate": gate}
                exp = gate.get("expansion_3d_pp")
                exp_txt = f",3日{exp:+.1f}pp" if exp is not None else ""
                base_txt = (f"场内ETF {prem['etf_code']} 溢价 {prem['premium_pct']:.1f}%"
                            f"(价{prem['price']:.3f}/净值{prem['nav']:.4f}{exp_txt})")
                if gate["level"] == "block":
                    fc["risks"].insert(0, f"🚫 溢价卡口禁买:{base_txt}。{gate['message']}")
                elif gate["level"] == "warn":
                    fc["risks"].insert(0, f"⚠️ {base_txt}。{gate['message']}")
                else:
                    fc["risks"].append(base_txt)
            else:
                fc["etf_premium"] = None
                fc["risks"].append("溢价抓取失败:买场内ETF前务必手动查看实时溢价率(QDII盈亏最大噪音源)")
            try:
                fc["snapshot_id"] = store_forecast_snapshot(fc, market_context)
            except Exception as snap_exc:
                fc["snapshot_error"] = str(snap_exc)
            forecasts.append({
                **fc,
                "display": info["display"],
                "data_meta": meta,
            })
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
    return jsonify(clean_json({
        "ok": not errors,
        "beijing_time": beijing_now(),
        "forecast_window": "预判今晚收盘方向；实时期货仅代表开盘参考，重大数据公布前自动进入事件观望",
        "market_context": market_context,
        "forecasts": forecasts,
        "errors": errors,
        "limits": [
            "免费 MVP 使用 SPY/QQQ 日线代理指数，不等同付费分钟线或期货盘口回测。",
            "FOMC/CPI/非农/PCE 公布前暂停收盘方向判断；实时期货仅表示当时的开盘倾向。",
        ],
    }))


def _run_key_allowed() -> bool:
    configured = os.environ.get("RUN_KEY")
    if not configured:
        return True
    supplied = request.headers.get("X-Run-Key") or request.args.get("key")
    return supplied == configured


@app.route("/api/daily/latest")
def api_daily_latest():
    return jsonify(clean_json({
        "ok": True,
        "beijing_time": beijing_now(),
        "schedule": scheduler_status(),
        "latest": run_due_daily_job(UNIVERSE, refresh=True),
    }))


@app.route("/api/daily/run", methods=["POST", "GET"])
@app.route("/api/actions/daily-run", methods=["POST", "GET"])
def api_daily_run():
    if not _run_key_allowed():
        return jsonify({"ok": False, "error": "invalid run key"}), 403
    payload = create_daily_snapshot(UNIVERSE, refresh=True)
    return jsonify(clean_json(payload))


@app.route("/api/version")
def api_version():
    return jsonify({
        "ok": True,
        "app": "USMarketPulse",
        "version": APP_VERSION,
        "beijing_time": beijing_now(),
    })


@app.route("/api/backtest")
def api_backtest():
    backtests = []
    errors = []
    for symbol, info in UNIVERSE.items():
        try:
            backtest = load_backtest_from_db(symbol, info["label"])
            rows = load_history_from_db(symbol)
            if backtest and rows:
                meta = {"symbol": symbol, "source": "sqlite_db", "rows": len(rows)}
            else:
                rows, meta = ensure_history(symbol, refresh=False)
                sync_symbol_dataset(symbol, info["label"], rows, source=meta.get("source", "cache"))
                backtest = load_backtest_from_db(symbol, info["label"]) or run_backtest(symbol, info["label"], rows)
            backtests.append({
                **backtest,
                "display": info["display"],
                "data_meta": meta,
            })
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
    etf_backtests = []
    try:
        etf_backtests = load_etf_backtest()
    except Exception:
        pass
    return jsonify(clean_json({
        "ok": not errors,
        "beijing_time": beijing_now(),
        "method": "信号由第 t 日收盘前已知的日线特征生成，验证第 t+1 日收盘涨跌，不偷看未来。",
        "backtests": backtests,
        "etf_backtests": etf_backtests,
        "etf_method": "ETF口径:信号日后首个A股日收盘买入,按退出规则卖出,扣双边成本0.12%。唯一和实际盈亏对齐的记分牌。",
        "errors": errors,
    }))


@app.route("/api/data")
def api_data():
    return jsonify(clean_json({
        "ok": True,
        "source": "Yahoo Chart API daily data, with Stooq daily CSV fallback",
        "database": database_status(),
        "symbols": data_status(),
        "beijing_time": beijing_now(),
    }))


@app.route("/api/db")
def api_db():
    return jsonify(clean_json({
        "ok": True,
        "beijing_time": beijing_now(),
        "database": database_status(),
    }))


if __name__ == "__main__":
    scheduler_enabled = os.environ.get("US_MARKET_PULSE_SCHEDULER", "1").lower() not in {"0", "false", "no"}
    start_scheduler(UNIVERSE, enabled=scheduler_enabled)
    port = int(os.environ.get("PORT", "5080"))
    app.run(host="0.0.0.0", port=port, debug=False)
