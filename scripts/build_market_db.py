from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import UNIVERSE
from db_store import DB_PATH, database_status, sync_symbol_dataset
from market_data import load_cached_history


def main() -> int:
    for symbol, info in UNIVERSE.items():
        rows = load_cached_history(symbol)
        if not rows:
            raise RuntimeError(f"missing cached data for {symbol}")
        result = sync_symbol_dataset(symbol, info["label"], rows, source="local_csv")
        print(f"{symbol}: prices={result['price_rows']} signals={result['signal_rows']}")
    status = database_status()
    print(f"database: {DB_PATH}")
    print(f"tables: {status['tables']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
