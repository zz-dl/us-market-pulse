# -*- coding: utf-8 -*-
"""本地全量回测(模型变更后手动跑,结果落 DB 随 commit 上线,绝不进请求路径)。
1. 重算 SPY/QQQ 信号(sync_symbol_dataset);
2. ETF 口径真实盈亏回测(两种退出规则 × 全历史/近3年)落 etf_backtest_summary;
3. 打印对比,给出最优退出规则。
先跑 scripts/update_etf_history.py 刷新 159941/513500 日线。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db_store import (
    load_history_from_db,
    load_signal_rows,
    store_etf_backtest,
    sync_symbol_dataset,
)
from etf_backtest import ETF_MAPPING, best_exit_rule, run_etf_pnl_backtest
from market_data import ensure_history

LABELS = {"QQQ": "Nasdaq-100", "SPY": "S&P 500"}


def main() -> None:
    all_rows = []
    for us_symbol, etf_code in ETF_MAPPING.items():
        rows, meta = ensure_history(us_symbol, refresh=True)
        sync = sync_symbol_dataset(us_symbol, LABELS[us_symbol], rows, source=meta.get("source", "refresh"))
        print(f"{us_symbol}: 信号 {sync['signal_rows']} 行 (美股口径 win_rate={sync['backtest_summary']['win_rate']}%, "
              f"avg_signed={sync['backtest_summary']['avg_next_return']}%)")

        signals = load_signal_rows(us_symbol)
        etf_rows = load_history_from_db(etf_code)
        if not etf_rows:
            print(f"  !! {etf_code} 无历史数据,先跑 scripts/update_etf_history.py")
            continue
        summary = run_etf_pnl_backtest(us_symbol, etf_code, signals, etf_rows)
        all_rows.extend(summary)
        for r in summary:
            print(f"  {etf_code} {r['exit_rule']:<10} {r['window']:<9} N={r['trades']:<5} "
                  f"毛{r['gross_avg_pct']:+.3f}% 净{r['net_avg_pct']:+.3f}%/笔 "
                  f"净胜率{r['net_win_rate']:.1f}% 累计净{r['cum_net_pct']:+.1f}%")

    n = store_etf_backtest(all_rows)
    print(f"\n已落库 etf_backtest_summary {n} 行")
    best = best_exit_rule(all_rows)
    if best:
        print(f"最优退出规则(近3年双标的加权): {best['exit_rule']} 净{best['net_avg_pct']:+.4f}%/笔 "
              f"(备选: {best['alternatives']})")


if __name__ == "__main__":
    main()
