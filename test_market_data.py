from datetime import date, datetime

from market_data import (
    _latest_vs_prev_close,
    _parse_tencent_klines,
    detect_macro_event_mode,
    latest_bar,
    merge_history_rows,
    parse_stooq_csv,
    rows_to_csv_text,
)


def check(label, cond, detail=""):
    mark = "[PASS]" if cond else "[FAIL]"
    print(f"  {mark} {label}" + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


csv_text = """Date,Open,High,Low,Close,Volume
2026-06-05,612.10,615.20,608.40,614.00,76000000
2026-06-08,613.00,616.00,610.25,611.50,81000000
"""

rows = parse_stooq_csv(csv_text)

check("parses two rows", len(rows) == 2, f"={len(rows)}")
check("dates become date objects", rows[0]["date"] == date(2026, 6, 5), rows[0]["date"])
check("numeric close parsed", rows[1]["close"] == 611.50, rows[1]["close"])
check("latest row is final date", latest_bar(rows)["date"] == date(2026, 6, 8))
round_trip = parse_stooq_csv(rows_to_csv_text(rows))
check("rows serialize back to parseable csv", round_trip[-1]["volume"] == 81000000)

fomc_event = detect_macro_event_mode([], now=datetime(2026, 6, 17, 14, 30))
check("FOMC calendar date enters event mode", fomc_event["active"] is True, fomc_event)
check("FOMC event is pending before Beijing overnight release", fomc_event["status"] == "pending", fomc_event)
check("FOMC event name is explicit", "FOMC" in fomc_event["name"], fomc_event)

headline_event = detect_macro_event_mode([
    {"title": "Stocks steady ahead of CPI inflation report due later today"}
], now=datetime(2026, 7, 14, 14, 30))
check("CPI headline enters event mode", headline_event["active"] is True, headline_event)
check("CPI event name is explicit", "CPI" in headline_event["name"], headline_event)

# 期货涨跌参考收盘必须按日期选(2026-07-10 线上bug:live bar缺失时 closes[-2]
# 取到前天收盘,网页期货涨跌%一直显示昨天全天的行情)。真实结构取自 Yahoo NQ=F。
_DAY = 86400
_T0 = 1782014400  # 2026-07-08 00:00 ET (gmtoffset -14400)
_yahoo_res = {
    "meta": {"regularMarketPrice": 29934.5, "regularMarketTime": _T0 + 2 * _DAY + 9 * 3600,
             "gmtoffset": -14400},
    "timestamp": [_T0, _T0 + _DAY, _T0 + 2 * _DAY],
    "indicators": {"quote": [{"close": [29468.5, 29937.0, 29934.5]}]},
}
px, prev = _latest_vs_prev_close(_yahoo_res)
check("prev close skips today's live bar", prev == 29937.0, prev)

_missing = {**_yahoo_res, "timestamp": _yahoo_res["timestamp"][:2],
            "indicators": {"quote": [{"close": [29468.5, 29937.0]}]}}
px, prev = _latest_vs_prev_close(_missing)
check("prev close correct when live bar missing", prev == 29937.0, prev)

_null_close = {**_yahoo_res, "indicators": {"quote": [{"close": [29468.5, 29937.0, None]}]}}
px, prev = _latest_vs_prev_close(_null_close)
check("prev close correct when live bar close is null", prev == 29937.0, prev)

check("no bars returns None", _latest_vs_prev_close({"meta": {"regularMarketPrice": 1.0}}) is None)

# 腾讯美股日K兜底(2026-07-15 线上bug:Yahoo对已收盘的07-14 bar返回null close,
# 全量历史只到07-13,数据被判stale冻结方向;Stooq兜底又换了JS反爬,双双失效)。
# K线字段顺序为 [日期, 开, 收, 高, 低, 量],真实结构取自 usSPY.AM qfqday。
_tencent_klines = [
    ["2026-07-13", "753.10", "749.17", "754.29", "748.30", "48967332.00"],
    ["2026-07-14", "750.91", "751.83", "753.34", "748.66", "35143138"],
    ["bad-date", "1", "2", "3", "4", "5"],
    ["2026-07-15", "751.83", "751.83", "753.34", "748.66", None],
]
_trows = _parse_tencent_klines(_tencent_klines)
check("tencent rows parsed, bad rows skipped", len(_trows) == 3, len(_trows))
check("tencent field order date/open/close/high/low", _trows[1]["close"] == 751.83, _trows[1])
check("tencent high mapped correctly", _trows[1]["high"] == 753.34, _trows[1])
check("tencent low mapped correctly", _trows[1]["low"] == 748.66, _trows[1])
check("tencent null volume treated as 0", _trows[2]["volume"] == 0, _trows[2])

# 合并规则:已有日期以缓存(Yahoo原始价)为准,腾讯只补缺的日期 —— qfq前复权
# 在除息日前的历史值会整体下移,不能覆盖已有的未复权历史。
_cached = [
    {"date": date(2026, 7, 10), "open": 1, "high": 1, "low": 1, "close": 754.95, "volume": 1},
    {"date": date(2026, 7, 13), "open": 1, "high": 1, "low": 1, "close": 749.17, "volume": 1},
]
_merged = merge_history_rows(_cached, _trows)
check("merge keeps cached value on duplicate date", _merged[1]["close"] == 749.17, _merged[1])
check("merge appends missing dates", _merged[-1]["date"] == date(2026, 7, 15), _merged[-1])
check("merge stays date-sorted", [r["date"] for r in _merged] == sorted(r["date"] for r in _merged))
check("merge row count", len(_merged) == 4, len(_merged))

print("ALL TESTS PASSED")
