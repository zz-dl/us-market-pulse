from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from daily_runner import BEIJING_TZ, should_run_daily_job


def check(label, cond, detail=""):
    mark = "[PASS]" if cond else "[FAIL]"
    print(f"  {mark} {label}" + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


with TemporaryDirectory() as temp:
    run_dir = Path(temp)
    before = datetime(2026, 6, 9, 14, 29, tzinfo=BEIJING_TZ)
    due = datetime(2026, 6, 9, 14, 30, tzinfo=BEIJING_TZ)
    weekend = datetime(2026, 6, 13, 15, 0, tzinfo=BEIJING_TZ)
    utc_due = due.astimezone(timezone.utc)

    check("does not run before target time", not should_run_daily_job(before, run_dir))
    check("runs at target time on a weekday", should_run_daily_job(due, run_dir))
    check("timezone conversion uses Beijing time", should_run_daily_job(utc_due, run_dir))
    check("does not run on weekends", not should_run_daily_job(weekend, run_dir))

    (run_dir / "2026-06-09.json").write_text('{"run_at_beijing":"2026-06-09T14:24:00+08:00"}', encoding="utf-8")
    check("reruns at target if an early manual run exists", should_run_daily_job(due + timedelta(minutes=1), run_dir))

    (run_dir / "2026-06-09.json").write_text('{"run_at_beijing":"2026-06-09T14:30:00+08:00"}', encoding="utf-8")
    check("does not run twice for same Beijing date", not should_run_daily_job(due + timedelta(minutes=1), run_dir))

print("ALL TESTS PASSED")
