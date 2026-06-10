from __future__ import annotations

import json
import time
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Callable

from db_store import sync_symbol_dataset, upsert_vix_rows
from forecast import build_forecast, run_backtest
from market_data import ensure_history, fetch_futures_change, fetch_vix_history_rows, fetch_vix_level


ROOT = Path(__file__).resolve().parent
RUN_DIR = ROOT / "data" / "daily_runs"
LATEST_PATH = RUN_DIR / "latest.json"
BEIJING_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
TARGET_TIME = clock_time(14, 30)
RUN_LOCK = Lock()
_scheduler_started = False


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def run_date_for(now: datetime | None = None) -> str:
    now = now.astimezone(BEIJING_TZ) if now else beijing_now()
    return now.date().isoformat()


def run_path_for(run_date: str) -> Path:
    return RUN_DIR / f"{run_date}.json"


def latest_run() -> dict | None:
    if not LATEST_PATH.exists():
        return None
    try:
        return json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def should_run_daily_job(now: datetime | None = None, run_dir: Path = RUN_DIR) -> bool:
    now = now.astimezone(BEIJING_TZ) if now else beijing_now()
    if now.weekday() >= 5:
        return False
    if now.time() < TARGET_TIME:
        return False
    path = run_dir / f"{now.date().isoformat()}.json"
    if not path.exists():
        return True
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        run_at_text = payload.get("run_at_beijing")
        if not run_at_text:
            return False
        run_at = datetime.fromisoformat(run_at_text).astimezone(BEIJING_TZ)
        return run_at.date() == now.date() and run_at.time() < TARGET_TIME
    except Exception:
        return False


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def create_daily_snapshot(universe: dict, refresh: bool = True, now: datetime | None = None) -> dict:
    with RUN_LOCK:
        run_at = now.astimezone(BEIJING_TZ) if now else beijing_now()
        forecasts = []
        backtests = []
        data = []
        errors = []

        # 先把近一个月 VIX 日线补进 DB(回测的平静市回调项需要;失败不阻塞)
        try:
            upsert_vix_rows(fetch_vix_history_rows("1mo"))
        except Exception:
            pass
        vix_now = fetch_vix_level()

        for symbol, info in universe.items():
            try:
                rows, meta = ensure_history(symbol, refresh=refresh)
                sync_symbol_dataset(symbol, info["label"], rows, source=meta.get("source", "daily_run"))
                data.append(meta)
                fut = fetch_futures_change(symbol)
                forecasts.append({
                    **build_forecast(symbol, info["label"], rows, live_futures_pct=fut, vix_level=vix_now),
                    "display": info["display"],
                    "data_meta": meta,
                })
                backtests.append({
                    **run_backtest(symbol, info["label"], rows),
                    "display": info["display"],
                    "data_meta": meta,
                })
            except Exception as exc:
                errors.append({"symbol": symbol, "error": str(exc)})

        payload = {
            "ok": not errors,
            "run_date": run_at.date().isoformat(),
            "run_at_beijing": run_at.isoformat(timespec="seconds"),
            "scheduled_for": "14:30 Asia/Shanghai",
            "data_source": "Yahoo Chart API primary, Stooq fallback",
            "forecasts": forecasts,
            "backtests": backtests,
            "data": data,
            "errors": errors,
        }
        _write_json(run_path_for(payload["run_date"]), payload)
        _write_json(LATEST_PATH, payload)
        return payload


def run_due_daily_job(universe: dict, refresh: bool = True, now: datetime | None = None) -> dict | None:
    if should_run_daily_job(now):
        return create_daily_snapshot(universe, refresh=refresh, now=now)
    return latest_run()


def scheduler_status() -> dict:
    latest = latest_run()
    return {
        "timezone": "Asia/Shanghai",
        "target_time": "14:30",
        "weekdays_only": True,
        "latest_run_date": latest.get("run_date") if latest else None,
        "latest_run_at_beijing": latest.get("run_at_beijing") if latest else None,
        "latest_ok": latest.get("ok") if latest else None,
        "storage": str(RUN_DIR),
    }


def scheduler_loop(universe: dict, sleep_seconds: int = 60) -> None:
    while True:
        try:
            run_due_daily_job(universe, refresh=True)
        except Exception as exc:
            RUN_DIR.mkdir(parents=True, exist_ok=True)
            (RUN_DIR / "last_error.txt").write_text(
                f"{beijing_now().isoformat(timespec='seconds')} {exc}",
                encoding="utf-8",
            )
        time.sleep(sleep_seconds)


def start_scheduler(universe: dict, enabled: bool = True, thread_factory: Callable[..., Thread] = Thread) -> bool:
    global _scheduler_started
    if not enabled or _scheduler_started:
        return False
    _scheduler_started = True
    thread = thread_factory(target=scheduler_loop, args=(universe,), daemon=True, name="us-market-pulse-daily")
    thread.start()
    return True
