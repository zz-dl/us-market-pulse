from __future__ import annotations

import os
from datetime import datetime
from math import isfinite

from flask import Flask, jsonify, request, send_from_directory

from daily_runner import (
    BEIJING_TZ,
    create_daily_snapshot,
    run_due_daily_job,
    scheduler_status,
    start_scheduler,
)
from forecast import build_forecast, run_backtest
from market_data import ensure_history, load_cached_history, price_path


UNIVERSE = {
    "QQQ": {"label": "Nasdaq-100", "display": "纳斯达克", "index": "Nasdaq-100"},
    "SPY": {"label": "S&P 500", "display": "标普 500", "index": "S&P 500"},
}

app = Flask(__name__, static_folder="static")
APP_VERSION = "mvp-2-render-cron-daily-1430"


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
    for symbol, info in UNIVERSE.items():
        rows = load_cached_history(symbol)
        path = price_path(symbol)
        out.append({
            "symbol": symbol,
            "label": info["label"],
            "display": info["display"],
            "rows": len(rows),
            "start": rows[0]["date"].isoformat() if rows else None,
            "end": rows[-1]["date"].isoformat() if rows else None,
            "cached": path.exists(),
            "path": str(path),
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
            _, meta = ensure_history(symbol, refresh=True)
            results.append(meta)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
    return jsonify(clean_json({
        "ok": not errors,
        "beijing_time": beijing_now(),
        "results": results,
        "errors": errors,
    }))


@app.route("/api/forecast")
def api_forecast():
    run_due_daily_job(UNIVERSE, refresh=True)
    forecasts = []
    errors = []
    for symbol, info in UNIVERSE.items():
        try:
            rows, meta = ensure_history(symbol, refresh=False)
            forecasts.append({
                **build_forecast(symbol, info["label"], rows),
                "display": info["display"],
                "data_meta": meta,
            })
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
    return jsonify(clean_json({
        "ok": not errors,
        "beijing_time": beijing_now(),
        "forecast_window": "北京时间 14:30 使用上一美股交易日收盘后的真实历史数据做第一版判断",
        "forecasts": forecasts,
        "errors": errors,
        "limits": [
            "免费 MVP 使用 SPY/QQQ 日线代理指数，不等同付费分钟线或期货盘口回测。",
            "北京时间 20:30 后的美国宏观数据可能推翻 14:30 版判断。",
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
            rows, meta = ensure_history(symbol, refresh=False)
            backtests.append({
                **run_backtest(symbol, info["label"], rows),
                "display": info["display"],
                "data_meta": meta,
            })
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
    return jsonify(clean_json({
        "ok": not errors,
        "beijing_time": beijing_now(),
        "method": "信号由第 t 日收盘前已知的日线特征生成，验证第 t+1 日收盘涨跌，不偷看未来。",
        "backtests": backtests,
        "errors": errors,
    }))


@app.route("/api/data")
def api_data():
    return jsonify(clean_json({
        "ok": True,
        "source": "Yahoo Chart API daily data, with Stooq daily CSV fallback",
        "symbols": data_status(),
        "beijing_time": beijing_now(),
    }))


if __name__ == "__main__":
    scheduler_enabled = os.environ.get("US_MARKET_PULSE_SCHEDULER", "1").lower() not in {"0", "false", "no"}
    start_scheduler(UNIVERSE, enabled=scheduler_enabled)
    port = int(os.environ.get("PORT", "5080"))
    app.run(host="0.0.0.0", port=port, debug=False)
