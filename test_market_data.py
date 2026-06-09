from datetime import date

from market_data import parse_stooq_csv, latest_bar, rows_to_csv_text


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

print("ALL TESTS PASSED")
