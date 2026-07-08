from __future__ import annotations

import sqlite3
import json
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

from forecast import build_forecast, pct, run_backtest


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "us_market_pulse.sqlite3"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def initialize_database(db_path: Path = DB_PATH) -> None:
    with closing(connect(db_path)) as con:
        con.executescript(
            """
            create table if not exists market_prices (
                symbol text not null,
                trade_date text not null,
                open real not null,
                high real not null,
                low real not null,
                close real not null,
                volume integer not null,
                source text not null default 'unknown',
                updated_at text not null,
                primary key (symbol, trade_date)
            );

            create table if not exists backtest_summary (
                symbol text primary key,
                label text not null,
                observations integer not null,
                trades integer not null,
                win_rate real not null,
                avg_next_return real not null,
                bullish_count integer not null,
                bearish_count integer not null,
                neutral_count integer not null,
                generated_at text not null
            );

            create table if not exists backtest_annual (
                symbol text not null,
                year text not null,
                trades integer not null,
                win_rate real not null,
                avg_signal_return real not null,
                generated_at text not null,
                primary key (symbol, year)
            );

            create table if not exists backtest_signals (
                symbol text not null,
                signal_date text not null,
                next_date text not null,
                direction text not null,
                confidence real not null,
                next_return_pct real not null,
                win integer,
                generated_at text not null,
                primary key (symbol, signal_date)
            );

            create index if not exists idx_market_prices_symbol_date
                on market_prices(symbol, trade_date);
            create index if not exists idx_backtest_signals_symbol_date
                on backtest_signals(symbol, signal_date);

            create table if not exists forecast_snapshots (
                id integer primary key autoincrement,
                captured_at text not null,
                symbol text not null,
                as_of text,
                expected_last_session text,
                actual_last_session text,
                data_quality_status text,
                direction text not null,
                model_direction text,
                score real,
                model_score real,
                live_futures_pct real,
                opening_bias text,
                realtime_guard_active integer not null default 0,
                drivers_json text not null,
                risks_json text not null,
                market_context_json text not null
            );

            create index if not exists idx_forecast_snapshots_symbol_time
                on forecast_snapshots(symbol, captured_at);

            -- QDII ETF 溢价历史(本地 14:30 推送任务采集;溢价是 QDII 盈亏最大单一噪音源)
            create table if not exists etf_premium_history (
                etf_code text not null,
                trade_date text not null,
                captured_at text not null,
                price real not null,
                nav real not null,
                premium_pct real not null,
                primary key (etf_code, trade_date)
            );

            -- ETF 口径真实盈亏回测(信号→按用户实际操作交易ETF,扣双边成本;唯一和钱包对齐的记分牌)
            create table if not exists etf_backtest_summary (
                us_symbol text not null,
                etf_code text not null,
                exit_rule text not null,
                window text not null,
                trades integer not null,
                gross_avg_pct real not null,
                net_avg_pct real not null,
                net_win_rate real not null,
                cum_net_pct real not null,
                stdev_pct real not null,
                cost_pct real not null,
                generated_at text not null,
                primary key (us_symbol, etf_code, exit_rule, window)
            );
            """
        )
        con.commit()


def _date_text(value) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def upsert_vix_rows(rows: list[tuple], db_path: Path = DB_PATH) -> int:
    """把 [(date_str, close)] 追加/更新进 market_prices(symbol='^VIX')。"""
    if not rows:
        return 0
    initialize_database(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(connect(db_path)) as con:
        con.executemany(
            """
            insert into market_prices (symbol, trade_date, open, high, low, close, volume, source, updated_at)
            values ('^VIX', ?, ?, ?, ?, ?, 0, 'yahoo_daily', ?)
            on conflict(symbol, trade_date) do update set
                close = excluded.close, updated_at = excluded.updated_at
            """,
            [(d, c, c, c, c, now) for d, c in rows],
        )
        con.commit()
    return len(rows)


def load_vix_series(db_path: Path = DB_PATH) -> dict:
    """{YYYY-MM-DD: VIX收盘}。VIX 日线已入库(symbol='^VIX'),供回测的平静市回调项用。"""
    initialize_database(db_path)
    with closing(connect(db_path)) as con:
        return {
            row["trade_date"]: row["close"]
            for row in con.execute(
                "select trade_date, close from market_prices where symbol = '^VIX'"
            ).fetchall()
        }


def load_history_from_db(symbol: str, db_path: Path = DB_PATH) -> list[dict]:
    initialize_database(db_path)
    with closing(connect(db_path)) as con:
        rows = con.execute(
            """
            select trade_date, open, high, low, close, volume
            from market_prices
            where symbol = ?
            order by trade_date
            """,
            (symbol.upper(),),
        ).fetchall()
    return [
        {
            "date": _parse_date(row["trade_date"]),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        }
        for row in rows
    ]


def load_backtest_from_db(symbol: str, label: str, db_path: Path = DB_PATH) -> dict | None:
    initialize_database(db_path)
    symbol = symbol.upper()
    with closing(connect(db_path)) as con:
        summary = con.execute(
            """
            select *
            from backtest_summary
            where symbol = ?
            """,
            (symbol,),
        ).fetchone()
        if summary is None:
            return None
        annual = [
            {
                "year": row["year"],
                "trades": row["trades"],
                "win_rate": row["win_rate"],
                "avg_signal_return": row["avg_signal_return"],
            }
            for row in con.execute(
                """
                select year, trades, win_rate, avg_signal_return
                from backtest_annual
                where symbol = ?
                order by year
                """,
                (symbol,),
            ).fetchall()
        ]
        recent_desc = con.execute(
            """
            select signal_date, next_date, direction, confidence, next_return_pct, win
            from backtest_signals
            where symbol = ?
            order by signal_date desc
            limit 12
            """,
            (symbol,),
        ).fetchall()
        # 各方向历史命中率（直接从已存的全部信号统计，给"这类信号几率多大"用）
        direction_stats = {}
        for row in con.execute(
            """
            select direction,
                   count(*) as n,
                   sum(case when win = 1 then 1 else 0 end) as w
            from backtest_signals
            where symbol = ? and direction in ('bullish', 'bearish')
            group by direction
            """,
            (symbol,),
        ).fetchall():
            n = row["n"] or 0
            direction_stats[row["direction"]] = {
                "trades": n,
                "win_rate": round((row["w"] or 0) / n * 100, 1) if n else None,
            }
    recent_signals = [
        {
            "date": row["signal_date"],
            "next_date": row["next_date"],
            "direction": row["direction"],
            "confidence": row["confidence"],
            "next_return_pct": row["next_return_pct"],
            "win": None if row["win"] is None else bool(row["win"]),
        }
        for row in reversed(recent_desc)
    ]
    return {
        "symbol": symbol,
        "label": label,
        "observations": summary["observations"],
        "trades": summary["trades"],
        "win_rate": summary["win_rate"],
        "avg_next_return": summary["avg_next_return"],
        "bullish_count": summary["bullish_count"],
        "bearish_count": summary["bearish_count"],
        "neutral_count": summary["neutral_count"],
        "direction_stats": direction_stats,
        "recent_signals": recent_signals,
        "annual": annual[-12:],
    }


def _all_backtest_signals(symbol: str, label: str, rows: list[dict], min_history: int = 200,
                          vix_series: dict | None = None) -> list[dict]:
    rows = sorted(rows, key=lambda r: r["date"])
    signals = []
    for i in range(min_history, len(rows) - 1):
        vix = (vix_series or {}).get(_date_text(rows[i]["date"]))
        forecast = build_forecast(symbol, label, rows[: i + 1], vix_level=vix)
        next_return = pct(rows[i + 1]["close"], rows[i]["close"])
        if forecast["direction"] == "neutral":
            win = None
        else:
            predicted_sign = 1 if forecast["direction"] == "bullish" else -1
            win = 1 if next_return * predicted_sign > 0 else 0
        signals.append({
            "signal_date": _date_text(rows[i]["date"]),
            "next_date": _date_text(rows[i + 1]["date"]),
            "direction": forecast["direction"],
            "confidence": forecast["confidence"],
            "next_return_pct": round(next_return, 3),
            "win": win,
        })
    return signals


def store_price_rows(symbol: str, rows: list[dict], db_path: Path = DB_PATH,
                     source: str = "refresh") -> int:
    """只写价格行,不重算回测(轻量,供请求路径/每日任务用)。"""
    if not rows:
        return 0
    initialize_database(db_path)
    symbol = symbol.upper()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(connect(db_path)) as con:
        con.executemany(
            """
            insert into market_prices (symbol, trade_date, open, high, low, close, volume, source, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(symbol, trade_date) do update set
                open = excluded.open, high = excluded.high, low = excluded.low,
                close = excluded.close, volume = excluded.volume,
                source = excluded.source, updated_at = excluded.updated_at
            """,
            [
                (symbol, _date_text(r["date"]), r["open"], r["high"], r["low"],
                 r["close"], int(r.get("volume", 0)), source, now)
                for r in rows
            ],
        )
        con.commit()
    return len(rows)


def store_forecast_snapshot(
    forecast: dict,
    market_context: dict | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Persist one lightweight intraday forecast observation for later review."""
    initialize_database(db_path)
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data_quality = forecast.get("data_quality") or {}
    realtime_guard = forecast.get("realtime_guard") or {}
    with closing(connect(db_path)) as con:
        cur = con.execute(
            """
            insert into forecast_snapshots (
                captured_at, symbol, as_of, expected_last_session, actual_last_session,
                data_quality_status, direction, model_direction, score, model_score,
                live_futures_pct, opening_bias, realtime_guard_active,
                drivers_json, risks_json, market_context_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                captured_at,
                forecast.get("symbol"),
                forecast.get("as_of"),
                data_quality.get("expected_last_session"),
                data_quality.get("actual_last_session"),
                data_quality.get("status"),
                forecast.get("direction"),
                forecast.get("model_direction"),
                forecast.get("score"),
                forecast.get("model_score"),
                forecast.get("live_futures_pct"),
                forecast.get("opening_bias"),
                1 if realtime_guard.get("active") else 0,
                json.dumps(forecast.get("drivers") or [], ensure_ascii=False),
                json.dumps(forecast.get("risks") or [], ensure_ascii=False),
                json.dumps(market_context or {}, ensure_ascii=False),
            ),
        )
        con.commit()
        return int(cur.lastrowid)


def store_etf_premium(quotes: dict, trade_date: str | None = None, db_path: Path = DB_PATH) -> int:
    """把 fetch_etf_premium 的结果按交易日落库(同日重复采集时覆盖,保留最新一次)。"""
    if not quotes:
        return 0
    initialize_database(db_path)
    trade_date = trade_date or date.today().isoformat()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(connect(db_path)) as con:
        con.executemany(
            """
            insert into etf_premium_history (etf_code, trade_date, captured_at, price, nav, premium_pct)
            values (?, ?, ?, ?, ?, ?)
            on conflict(etf_code, trade_date) do update set
                captured_at = excluded.captured_at, price = excluded.price,
                nav = excluded.nav, premium_pct = excluded.premium_pct
            """,
            [
                (q["etf_code"], trade_date, now, q["price"], q["nav"], q["premium_pct"])
                for q in quotes.values()
            ],
        )
        con.commit()
    return len(quotes)


def load_premium_history(etf_code: str, limit: int = 10, db_path: Path = DB_PATH) -> list[dict]:
    """最近 N 个交易日的溢价记录,按日期升序。"""
    initialize_database(db_path)
    with closing(connect(db_path)) as con:
        rows = con.execute(
            """
            select trade_date, price, nav, premium_pct from etf_premium_history
            where etf_code = ? order by trade_date desc limit ?
            """,
            (etf_code, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def evaluate_premium_gate(etf_code: str, current_premium: float,
                          db_path: Path = DB_PATH) -> dict:
    """溢价卡口:近3个交易日溢价膨胀 >2pp → block(禁买);
    溢价绝对值 >8% → warn。数据不足时只按绝对值判断。
    实证依据(2026-07-08):159941 溢价单日波动可达 ±2pp,远大于模型方向边。"""
    history = load_premium_history(etf_code, limit=5, db_path=db_path)
    expansion = None
    if len(history) >= 3:
        expansion = round(current_premium - history[-3]["premium_pct"], 2)
    level, notes = "ok", []
    if expansion is not None and expansion > 2.0:
        level = "block"
        notes.append(f"溢价3日膨胀 +{expansion:.1f}pp(>{history[-3]['premium_pct']:.1f}%→{current_premium:.1f}%),泡沫加厚期,禁买")
    if current_premium > 8.0:
        level = "block" if level == "block" else "warn"
        notes.append(f"溢价 {current_premium:.1f}% 处历史高位,买入≈赌泡沫,溢价回落可吞掉数倍于方向边的收益")
    return {
        "etf_code": etf_code,
        "premium_pct": current_premium,
        "expansion_3d_pp": expansion,
        "level": level,
        "message": ";".join(notes),
    }


def store_etf_backtest(rows: list[dict], db_path: Path = DB_PATH) -> int:
    if not rows:
        return 0
    initialize_database(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(connect(db_path)) as con:
        con.executemany(
            """
            insert into etf_backtest_summary (
                us_symbol, etf_code, exit_rule, window, trades, gross_avg_pct,
                net_avg_pct, net_win_rate, cum_net_pct, stdev_pct, cost_pct, generated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(us_symbol, etf_code, exit_rule, window) do update set
                trades = excluded.trades, gross_avg_pct = excluded.gross_avg_pct,
                net_avg_pct = excluded.net_avg_pct, net_win_rate = excluded.net_win_rate,
                cum_net_pct = excluded.cum_net_pct, stdev_pct = excluded.stdev_pct,
                cost_pct = excluded.cost_pct, generated_at = excluded.generated_at
            """,
            [
                (r["us_symbol"], r["etf_code"], r["exit_rule"], r["window"], r["trades"],
                 r["gross_avg_pct"], r["net_avg_pct"], r["net_win_rate"], r["cum_net_pct"],
                 r["stdev_pct"], r["cost_pct"], now)
                for r in rows
            ],
        )
        con.commit()
    return len(rows)


def load_etf_backtest(db_path: Path = DB_PATH) -> list[dict]:
    initialize_database(db_path)
    with closing(connect(db_path)) as con:
        rows = con.execute(
            "select * from etf_backtest_summary order by us_symbol, exit_rule, window"
        ).fetchall()
    return [dict(r) for r in rows]


def load_signal_rows(symbol: str, db_path: Path = DB_PATH) -> list[dict]:
    """回测信号(供 ETF 口径盈亏对账用)。"""
    initialize_database(db_path)
    with closing(connect(db_path)) as con:
        rows = con.execute(
            """
            select signal_date, next_date, direction, next_return_pct
            from backtest_signals where symbol = ? order by signal_date
            """,
            (symbol.upper(),),
        ).fetchall()
    return [dict(r) for r in rows]


def sync_symbol_dataset(
    symbol: str,
    label: str,
    rows: list[dict],
    db_path: Path = DB_PATH,
    source: str = "cache",
) -> dict:
    initialize_database(db_path)
    symbol = symbol.upper()
    rows = sorted(rows, key=lambda r: r["date"])
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    vix_series = load_vix_series(db_path)
    summary = run_backtest(symbol, label, rows, vix_series=vix_series)
    signals = _all_backtest_signals(symbol, label, rows, vix_series=vix_series)

    with closing(connect(db_path)) as con:
        con.executemany(
            """
            insert into market_prices (
                symbol, trade_date, open, high, low, close, volume, source, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(symbol, trade_date) do update set
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            [
                (
                    symbol,
                    _date_text(row["date"]),
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    int(row.get("volume", 0)),
                    source,
                    generated_at,
                )
                for row in rows
            ],
        )
        con.commit()

        con.execute(
            """
            insert into backtest_summary (
                symbol, label, observations, trades, win_rate, avg_next_return,
                bullish_count, bearish_count, neutral_count, generated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(symbol) do update set
                label = excluded.label,
                observations = excluded.observations,
                trades = excluded.trades,
                win_rate = excluded.win_rate,
                avg_next_return = excluded.avg_next_return,
                bullish_count = excluded.bullish_count,
                bearish_count = excluded.bearish_count,
                neutral_count = excluded.neutral_count,
                generated_at = excluded.generated_at
            """,
            (
                symbol,
                label,
                summary["observations"],
                summary["trades"],
                summary["win_rate"],
                summary["avg_next_return"],
                summary["bullish_count"],
                summary["bearish_count"],
                summary["neutral_count"],
                generated_at,
            ),
        )

        con.execute("delete from backtest_annual where symbol = ?", (symbol,))
        con.executemany(
            """
            insert into backtest_annual (
                symbol, year, trades, win_rate, avg_signal_return, generated_at
            )
            values (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    symbol,
                    row["year"],
                    row["trades"],
                    row["win_rate"],
                    row["avg_signal_return"],
                    generated_at,
                )
                for row in summary["annual"]
            ],
        )

        con.execute("delete from backtest_signals where symbol = ?", (symbol,))
        con.executemany(
            """
            insert into backtest_signals (
                symbol, signal_date, next_date, direction, confidence,
                next_return_pct, win, generated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    symbol,
                    row["signal_date"],
                    row["next_date"],
                    row["direction"],
                    row["confidence"],
                    row["next_return_pct"],
                    row["win"],
                    generated_at,
                )
                for row in signals
            ],
        )
        con.commit()

    return {
        "symbol": symbol,
        "db_path": str(db_path),
        "price_rows": len(rows),
        "signal_rows": len(signals),
        "backtest_summary": summary,
        "generated_at": generated_at,
    }


def database_status(db_path: Path = DB_PATH) -> dict:
    initialize_database(db_path)
    with closing(connect(db_path)) as con:
        tables = {}
        for table in ("market_prices", "backtest_summary", "backtest_annual", "backtest_signals", "forecast_snapshots"):
            tables[table] = con.execute(f"select count(*) from {table}").fetchone()[0]
        symbols = [
            dict(row)
            for row in con.execute(
                """
                select
                    p.symbol,
                    count(*) as rows,
                    min(p.trade_date) as start,
                    max(p.trade_date) as end,
                    s.trades,
                    s.win_rate
                from market_prices p
                left join backtest_summary s on s.symbol = p.symbol
                group by p.symbol
                order by p.symbol
                """
            ).fetchall()
        ]
    return {
        "path": str(db_path),
        "exists": db_path.exists(),
        "tables": tables,
        "symbols": symbols,
    }
