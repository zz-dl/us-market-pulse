from datetime import date, datetime

from market_data import detect_macro_event_mode, latest_bar, parse_stooq_csv, rows_to_csv_text


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

print("ALL TESTS PASSED")
