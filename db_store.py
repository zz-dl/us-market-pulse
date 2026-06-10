from __future__ import annotations

import sqlite3
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
            """
        )
        con.commit()


def _date_text(value) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


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


def _all_backtest_signals(symbol: str, label: str, rows: list[dict], min_history: int = 200) -> list[dict]:
    rows = sorted(rows, key=lambda r: r["date"])
    signals = []
    for i in range(min_history, len(rows) - 1):
        forecast = build_forecast(symbol, label, rows[: i + 1])
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
    summary = run_backtest(symbol, label, rows)
    signals = _all_backtest_signals(symbol, label, rows)

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
        for table in ("market_prices", "backtest_summary", "backtest_annual", "backtest_signals"):
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
