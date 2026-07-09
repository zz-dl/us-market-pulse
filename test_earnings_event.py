# -*- coding: utf-8 -*-
"""权重股财报事件观望的单元测试(reporters 注入,不依赖网络)。"""
from datetime import datetime

from market_data import detect_earnings_event_mode


def _wed_afternoon():
    return datetime(2026, 7, 29, 14, 30)   # 周三北京14:30 → 今夜美股=周三时段


def test_pre_market_triggers_event_mode():
    out = detect_earnings_event_mode(now=_wed_afternoon(), reporters=[
        {"symbol": "JPM", "cn": "摩根大通", "time": "time-pre-market"},
    ])
    assert out["event_mode"]["active"] is True
    assert out["event_mode"]["status"] == "pending"
    assert "摩根大通" in out["event_mode"]["name"]
    assert out["notes"] == []


def test_after_hours_only_notes_no_event():
    out = detect_earnings_event_mode(now=_wed_afternoon(), reporters=[
        {"symbol": "NVDA", "cn": "英伟达", "time": "time-after-hours"},
    ])
    assert out["event_mode"]["active"] is False
    assert len(out["notes"]) == 1 and "英伟达" in out["notes"][0]


def test_not_supplied_treated_as_pending_conservatively():
    out = detect_earnings_event_mode(now=_wed_afternoon(), reporters=[
        {"symbol": "MSFT", "cn": "微软", "time": "time-not-supplied"},
        {"symbol": "META", "cn": "Meta", "time": "time-not-supplied"},
    ])
    assert out["event_mode"]["active"] is True
    assert "时间未定" in out["event_mode"]["reason"]
    assert "微软" in out["event_mode"]["name"] and "Meta" in out["event_mode"]["name"]


def test_mixed_pre_and_post():
    out = detect_earnings_event_mode(now=_wed_afternoon(), reporters=[
        {"symbol": "JPM", "cn": "摩根大通", "time": "time-pre-market"},
        {"symbol": "NVDA", "cn": "英伟达", "time": "time-after-hours"},
    ])
    assert out["event_mode"]["active"] is True          # 盘前那家触发观望
    assert "摩根大通" in out["event_mode"]["name"]
    assert "英伟达" not in out["event_mode"]["name"]     # 盘后那家不进事件名
    assert len(out["notes"]) == 1                        # 但有盘后提示


def test_no_reporters_inactive():
    out = detect_earnings_event_mode(now=_wed_afternoon(), reporters=[])
    assert out["event_mode"]["active"] is False and out["notes"] == []


def test_weekend_inactive():
    # 周六北京14:30 → 今夜无美股时段
    out = detect_earnings_event_mode(now=datetime(2026, 8, 1, 14, 30), reporters=[
        {"symbol": "NVDA", "cn": "英伟达", "time": "time-pre-market"},
    ])
    assert out["event_mode"]["active"] is False
