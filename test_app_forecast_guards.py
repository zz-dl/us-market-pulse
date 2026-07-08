from app import _apply_data_quality_guard


def check(label, cond, detail=""):
    mark = "[PASS]" if cond else "[FAIL]"
    print(f"  {mark} {label}" + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


fresh = {
    "direction": "bullish",
    "score": 0.8,
    "decision_basis": "us_next_session",
    "drivers": [],
    "risks": [],
}
fresh = _apply_data_quality_guard(fresh, expected_last_session="2026-07-06", actual_last_session="2026-07-06")
check("fresh data keeps actionable direction", fresh["direction"] == "bullish", fresh)
check("fresh data quality is explicit", fresh["data_quality"]["status"] == "fresh", fresh)

stale = {
    "direction": "bullish",
    "score": 0.8,
    "decision_basis": "us_next_session",
    "drivers": [],
    "risks": [],
}
stale = _apply_data_quality_guard(stale, expected_last_session="2026-07-06", actual_last_session="2026-07-02")
check("stale close data blocks buy signal", stale["direction"] == "neutral", stale)
check("stale close data resets final score", stale["score"] == 0.0, stale)
check("stale close data changes decision basis", stale["decision_basis"] == "data_pending", stale)
check("stale close data quality records expected and actual dates", stale["data_quality"]["expected_last_session"] == "2026-07-06" and stale["data_quality"]["actual_last_session"] == "2026-07-02", stale)
check("stale close data adds a visible driver", any(d["label"] == "收盘数据未确认" for d in stale["drivers"]), stale)

print("ALL TESTS PASSED")
