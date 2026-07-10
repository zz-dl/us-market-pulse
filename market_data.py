from __future__ import annotations

import csv
import io
import re
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data" / "prices"
STOOQ_SYMBOLS = {
    "SPY": "spy.us",
    "QQQ": "qqq.us",
}

# 决策时点(北京14:30)的指数期货:标普→ES、纳指→NQ。这是市场对"今晚"的实时下注。
FUTURES_SYMBOLS = {
    "SPY": "ES=F",
    "QQQ": "NQ=F",
}

# Federal Reserve official/tentative decision dates. The release occurs at 2:00 p.m.
# U.S. Eastern time, which is around 02:00/03:00 Beijing time the next day.
FOMC_DECISION_DATES = {
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-10-28",
    "2026-12-09",
    "2027-01-27",
    "2027-03-17",
    "2027-04-28",
    "2027-06-09",
    "2027-07-28",
    "2027-09-15",
    "2027-10-27",
    "2027-12-08",
}

_MACRO_EVENT_PATTERNS = (
    ("FOMC 利率决议", re.compile(r"\b(fomc|federal reserve|fed meeting|fed decision|rate decision)\b", re.I)),
    ("CPI 通胀数据", re.compile(r"\b(cpi|consumer price index|inflation report)\b", re.I)),
    ("美国非农就业报告", re.compile(r"\b(nonfarm|non-farm|payrolls|jobs report|employment report)\b", re.I)),
    ("PCE 通胀数据", re.compile(r"\b(pce|personal consumption expenditures)\b", re.I)),
)
_PENDING_EVENT_PATTERN = re.compile(
    r"\b(ahead of|await|awaiting|before|due|later today|decision today|report today|"
    r"release today|set to release|scheduled|meeting today|focus turns to)\b",
    re.I,
)
_RESOLVED_EVENT_PATTERN = re.compile(
    r"\b(after|released|showed|rose|fell|holds|held|raises|raised|cuts|cut)\b",
    re.I,
)


# 权重股财报观望:纳指/标普前十大权重(高度重合)+ 摩根大通(财报季开锣标志)。
# 单家权重股财报可拉动指数1-2%,公布前期货读数随时作废 → 盘前公布=事件观望,盘后公布=风险提示。
MEGA_CAP_EARNINGS = {
    "NVDA": "英伟达", "MSFT": "微软", "AAPL": "苹果", "AMZN": "亚马逊",
    "GOOGL": "谷歌", "GOOG": "谷歌", "META": "Meta", "AVGO": "博通",
    "TSLA": "特斯拉", "NFLX": "奈飞", "COST": "好市多", "JPM": "摩根大通",
}
_earnings_cache: dict = {}


def fetch_mega_cap_earnings(us_date: str, timeout: float = 8.0) -> list[dict] | None:
    """当日美股财报日历中命中权重股监控表的条目(Nasdaq公共API,需浏览器UA)。
    返回 [{'symbol','cn','time'}];失败返回 None(与"当天无权重股财报"=[] 区分开)。
    进程内按日期缓存,避免每次页面加载都打外部API。"""
    if us_date in _earnings_cache:
        return _earnings_cache[us_date]
    url = f"https://api.nasdaq.com/api/calendar/earnings?date={us_date}"
    try:
        r = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
        })
        rows = ((r.json().get("data") or {}).get("rows")) or []
    except Exception:
        return None
    hits = [
        {"symbol": row["symbol"], "cn": MEGA_CAP_EARNINGS[row["symbol"]],
         "time": row.get("time") or "time-not-supplied"}
        for row in rows if row.get("symbol") in MEGA_CAP_EARNINGS
    ]
    _earnings_cache[us_date] = hits
    return hits


def detect_earnings_event_mode(now: datetime | None = None,
                               reporters: list[dict] | None = None) -> dict:
    """今夜美股时段是否有权重股财报。reporters 可注入(测试用),否则实时抓取。
    盘前(time-pre-market)/时间未定 → 公布落在今夜收盘前 → event_mode(暂停收盘方向判断);
    盘后(time-after-hours) → 公布在今夜收盘后,不暂停,只给风险提示(盘中博弈波动+次日跳空)。
    返回 {'event_mode': dict, 'notes': [str], 'reporters': [dict]}。"""
    now = now or datetime.now()
    inactive = {"event_mode": _inactive_event_mode(), "notes": [], "reporters": []}
    # 今夜美股时段的日历日≈北京时间减6小时所在日期(美东凌晨~傍晚都映射到当日)
    us_date = (now - timedelta(hours=6)).date()
    if us_date.weekday() >= 5:
        return inactive
    reporters = reporters if reporters is not None else fetch_mega_cap_earnings(us_date.isoformat())
    if not reporters:
        return inactive

    pre = [r for r in reporters if r["time"] != "time-after-hours"]
    post = [r for r in reporters if r["time"] == "time-after-hours"]
    notes = []
    if post:
        names = "、".join(f"{r['cn']}({r['symbol']})" for r in post)
        notes.append(
            f"今夜盘后财报:{names} → 公布在收盘之后,不直接决定今晚收盘,"
            "但盘中随博弈波动放大,且次日大概率跳空"
        )
    event_mode = _inactive_event_mode()
    if pre:
        names = "、".join(f"{r['cn']}({r['symbol']})" for r in pre)
        undecided = any(r["time"] == "time-not-supplied" for r in pre)
        event_mode = {
            "active": True,
            "status": "pending",
            "name": f"权重股财报:{names}",
            "reason": ("单家权重股财报可拉动指数1-2%,公布前期货读数不能代表最终收盘方向。"
                       + ("(公布时间未定,保守按盘前处理)" if undecided else "")),
            "release_time_beijing": "今晚约20:30(美股盘前)" if not undecided else "今晚至次日凌晨",
            "source": "earnings_calendar",
        }
    return {"event_mode": event_mode, "notes": notes, "reporters": reporters}


def _inactive_event_mode() -> dict:
    return {
        "active": False,
        "status": "none",
        "name": "",
        "reason": "",
        "release_time_beijing": "",
        "source": "",
    }


def detect_macro_event_mode(headlines: list[dict], now: datetime | None = None) -> dict:
    """Detect a known high-impact U.S. macro release that is still pending.

    `now` is expected to be Beijing local time when supplied by the app. FOMC
    dates use the official calendar; CPI/jobs/PCE use explicit pending-language
    headlines so ordinary retrospective news does not freeze the forecast.
    """
    now = now or datetime.now()
    day_text = now.date().isoformat()
    if day_text in FOMC_DECISION_DATES:
        release_deadline = datetime.combine(now.date() + timedelta(days=1), dt_time(5, 0))
        if now.tzinfo is not None:
            release_deadline = release_deadline.replace(tzinfo=now.tzinfo)
        if now < release_deadline:
            return {
                "active": True,
                "status": "pending",
                "name": "FOMC 利率决议",
                "reason": "美联储声明、点阵图或发布会尚未公布，盘前期货不能代表最终收盘方向。",
                "release_time_beijing": "次日约 02:00-04:00",
                "source": "official_calendar",
            }

    for item in headlines or []:
        title = str(item.get("title") or "").strip()
        if not title or _RESOLVED_EVENT_PATTERN.search(title):
            continue
        if not _PENDING_EVENT_PATTERN.search(title):
            continue
        for name, pattern in _MACRO_EVENT_PATTERNS:
            if pattern.search(title):
                return {
                    "active": True,
                    "status": "pending",
                    "name": name,
                    "reason": f"新闻显示重大数据仍待公布：{title}",
                    "release_time_beijing": "今晚/次日凌晨",
                    "source": "news_headline",
                }
    return _inactive_event_mode()


def _latest_vs_prev_close(res: dict) -> tuple[float, float] | None:
    """从 Yahoo chart 结果取 (最新价, 上一交易日收盘)。
    上一收盘必须按日期选,不能取 closes[-2]:当天的 live bar 在部分节点缺失或 close 为
    null 时,closes[-2] 会取到前天收盘,涨跌%就变成"昨天全天的行情"且整天不动。"""
    meta = res.get("meta") or {}
    px = meta.get("regularMarketPrice")
    rmt = meta.get("regularMarketTime")
    gmtoff = meta.get("gmtoffset") or 0
    ts = res.get("timestamp") or []
    closes = (res.get("indicators", {}).get("quote", [{}])[0].get("close")) or []
    bars = [(t, c) for t, c in zip(ts, closes) if c is not None]
    if px is None or not bars:
        return None
    if rmt:
        tz = timezone(timedelta(seconds=gmtoff))
        cur_day = datetime.fromtimestamp(rmt, tz=tz).date()
        prev = [c for t, c in bars if datetime.fromtimestamp(t, tz=tz).date() < cur_day]
        if prev:
            return float(px), float(prev[-1])
    if len(bars) >= 2:
        return float(px), float(bars[-2][1])
    return None


def fetch_quote_change(symbol: str, timeout: float = 8.0) -> dict | None:
    """{'price','chg_pct'}:最新价 vs 上一交易日收盘(按日期选)。失败 None。"""
    import urllib.parse
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol) + "?range=7d&interval=1d")
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        res = r.json()["chart"]["result"][0]
        pair = _latest_vs_prev_close(res)
        if not pair or not pair[1]:
            return None
        px, prev_close = pair
        return {"price": px, "chg_pct": (px / prev_close - 1.0) * 100.0}
    except Exception:
        return None


_NEWS_RISK_KEYWORDS = {
    "地缘冲突": ["iran", "israel", "war", "strike", "attack", "missile", "military", "conflict", "nuclear"],
    "美联储/利率": ["fed ", "federal reserve", "powell", "rate cut", "rate hike", "fomc", "inflation"],
    "贸易/关税": ["tariff", "trade war", "sanction", "export control"],
    "油价冲击": ["oil price", "crude", "opec"],
}


def fetch_news_headlines(limit: int = 6, timeout: float = 10.0) -> list[dict]:
    """美股相关最新新闻标题(Google News RSS,仅标题+链接+时间)。失败返回 []。"""
    import xml.etree.ElementTree as ET
    url = ("https://news.google.com/rss/search?q=US%20stock%20market%20OR%20Nasdaq%20OR%20"
           "%22S%26P%20500%22%20when:1d&hl=en-US&gl=US&ceid=US:en")
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        root = ET.fromstring(r.content)
        items = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            items.append({
                "title": title,
                "link": (item.findtext("link") or "").strip(),
                "published": (item.findtext("pubDate") or "").strip(),
            })
            if len(items) >= limit:
                break
        return items
    except Exception:
        return []


def detect_news_risk_flags(headlines: list[dict]) -> list[str]:
    """从新闻标题里识别风险主题(关键词级,只做提示,不进打分)。"""
    text = " ".join(h.get("title", "").lower() for h in headlines)
    return [flag for flag, kws in _NEWS_RISK_KEYWORDS.items() if any(k in text for k in kws)]


def fetch_vix_level(timeout: float = 8.0) -> float | None:
    """实时 VIX 水平(决策时点)。失败返回 None(模型里 VIX 项自动为 0,不影响其它项)。"""
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?range=5d&interval=1d"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        meta = r.json()["chart"]["result"][0]["meta"]
        px = meta.get("regularMarketPrice")
        return float(px) if px else None
    except Exception:
        return None


def fetch_vix_history_rows(days_range: str = "1mo", timeout: float = 10.0) -> list[tuple]:
    """近段 VIX 日线 [(date_str, close)],供每日把新 VIX 追加进 DB(保持回测数据不陈旧)。"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?range={days_range}&interval=1d"
    out: list[tuple] = []
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        res = r.json()["chart"]["result"][0]
        ts = res.get("timestamp") or []
        closes = res["indicators"]["quote"][0]["close"]
        for i, t in enumerate(ts):
            c = closes[i]
            if c is None:
                continue
            out.append((datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"), float(c)))
    except Exception:
        pass
    return out


def fetch_futures_change(symbol: str, timeout: float = 8.0) -> float | None:
    """返回该标的对应指数期货相对上一交易日结算的涨跌%(实时,如北京14:30)。
    SPY→ES=F, QQQ→NQ=F。失败返回 None。涨跌 = 最新价 / 上一交易日收盘(按日期选)。"""
    import urllib.parse
    fsym = FUTURES_SYMBOLS.get(symbol.upper())
    if not fsym:
        return None
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(fsym) + "?range=7d&interval=1d")
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        res = r.json()["chart"]["result"][0]
        pair = _latest_vs_prev_close(res)
        if not pair or not pair[1]:
            return None
        px, prev_close = pair
        return (px / prev_close - 1.0) * 100.0
    except Exception:
        return None


# 你的两只场内 QDII ETF(腾讯行情代码)。溢价是 QDII 盈亏的最大单一噪音源:
# 单日溢价变化 ±1-2pp,远大于模型的方向边(~0.05-0.17%/笔),必须监控。
ETF_PREMIUM_CODES = {
    "QQQ": "sz159941",   # 广发纳指ETF
    "SPY": "sh513500",   # 博时标普500ETF
}


def _safe_float(value) -> float | None:
    try:
        v = float(str(value).strip())
        return v if v == v else None  # NaN guard
    except (TypeError, ValueError):
        return None


def fetch_etf_premium(codes: dict | None = None, timeout: float = 8.0) -> dict:
    """实时抓取场内 ETF 的价格/IOPV净值/溢价率(腾讯行情)。
    字段下标经真实返回验证(2026-07-08):parts[3]=价格, parts[77]=溢价率%, parts[78]=实时IOPV。
    双重校验:parts[77] 与 price/parts[78]-1 一致才采信。失败的标的不出现在结果里。"""
    codes = codes or ETF_PREMIUM_CODES
    query = ",".join(codes.values())
    out: dict = {}
    try:
        r = requests.get(f"http://qt.gtimg.cn/q={query}", timeout=timeout,
                         headers={"User-Agent": "Mozilla/5.0"})
        text = r.content.decode("gbk", "replace")
    except Exception:
        return out
    quotes = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        tcode = line.split("=")[0].replace("v_", "")
        parts = line.split("~")
        if len(parts) < 80:
            continue
        price = _safe_float(parts[3])
        premium = _safe_float(parts[77])
        nav = _safe_float(parts[78])
        if not price or not nav or premium is None:
            continue
        implied = (price / nav - 1.0) * 100.0
        if abs(implied - premium) > 1.0:   # 字段漂移防护:两口径对不上就不采信
            continue
        quotes[tcode] = {
            "name": parts[1],
            "price": price,
            "nav": nav,
            "premium_pct": round(premium, 2),
        }
    for symbol, tcode in codes.items():
        if tcode in quotes:
            out[symbol] = {**quotes[tcode], "etf_code": tcode.lstrip("shz")}
    return out


def _num(value: str) -> float:
    return float(str(value).strip())


def _int(value: str) -> int:
    text = str(value).strip()
    return int(float(text)) if text else 0


def parse_stooq_csv(text: str) -> list[dict]:
    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(text.strip()))
    for raw in reader:
        if not raw or raw.get("Date") in ("Date", None):
            continue
        try:
            rows.append({
                "date": datetime.strptime(raw["Date"], "%Y-%m-%d").date(),
                "open": _num(raw["Open"]),
                "high": _num(raw["High"]),
                "low": _num(raw["Low"]),
                "close": _num(raw["Close"]),
                "volume": _int(raw.get("Volume", "0")),
            })
        except Exception:
            continue
    rows.sort(key=lambda r: r["date"])
    return rows


def rows_to_csv_text(rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=["Date", "Open", "High", "Low", "Close", "Volume"], lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            "Date": row["date"].isoformat() if isinstance(row["date"], date) else str(row["date"])[:10],
            "Open": row["open"],
            "High": row["high"],
            "Low": row["low"],
            "Close": row["close"],
            "Volume": row.get("volume", 0),
        })
    return out.getvalue()


def latest_bar(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("no rows")
    return sorted(rows, key=lambda r: r["date"])[-1]


def price_path(symbol: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{symbol.upper()}.csv"


def load_cached_history(symbol: str) -> list[dict]:
    path = price_path(symbol)
    if not path.exists():
        return []
    return parse_stooq_csv(path.read_text(encoding="utf-8"))


def download_symbol_history(symbol: str, timeout: int = 20) -> dict:
    symbol = symbol.upper()
    try:
        return download_symbol_history_yahoo(symbol, timeout=timeout)
    except Exception:
        return download_symbol_history_stooq(symbol, timeout=timeout)


def download_symbol_history_yahoo(symbol: str, timeout: int = 20) -> dict:
    period2 = int(time.time())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1=0&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
    )
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    payload = response.json()
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        raise RuntimeError(f"Yahoo returned no chart result for {symbol}")
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    rows = []
    for i, ts in enumerate(timestamps):
        try:
            open_px = quote.get("open", [])[i]
            high = quote.get("high", [])[i]
            low = quote.get("low", [])[i]
            close = quote.get("close", [])[i]
            volume = quote.get("volume", [])[i] or 0
            if None in (open_px, high, low, close):
                continue
            rows.append({
                "date": datetime.fromtimestamp(ts, timezone.utc).date(),
                "open": float(open_px),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": int(volume),
            })
        except Exception:
            continue
    rows.sort(key=lambda r: r["date"])
    if len(rows) < 100:
        raise RuntimeError(f"Yahoo returned too few rows for {symbol}")
    path = price_path(symbol)
    path.write_text(rows_to_csv_text(rows), encoding="utf-8")
    return {
        "symbol": symbol,
        "source": "yahoo_chart",
        "url": url,
        "rows": len(rows),
        "start": rows[0]["date"].isoformat(),
        "end": rows[-1]["date"].isoformat(),
        "saved_to": str(path),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def download_symbol_history_stooq(symbol: str, timeout: int = 20) -> dict:
    symbol = symbol.upper()
    stooq_symbol = STOOQ_SYMBOLS.get(symbol, f"{symbol.lower()}.us")
    url = f"https://stooq.com/q/d/l/?s={stooq_symbol}&i=d"
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "USMarketPulse/1.0"})
    response.raise_for_status()
    rows = parse_stooq_csv(response.text)
    if len(rows) < 100:
        raise RuntimeError(f"Stooq returned too few rows for {symbol}")
    path = price_path(symbol)
    path.write_text(rows_to_csv_text(rows), encoding="utf-8")
    return {
        "symbol": symbol,
        "source": "stooq",
        "url": url,
        "rows": len(rows),
        "start": rows[0]["date"].isoformat(),
        "end": rows[-1]["date"].isoformat(),
        "saved_to": str(path),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def ensure_history(symbol: str, refresh: bool = False) -> tuple[list[dict], dict]:
    if refresh:
        meta = download_symbol_history(symbol)
        return load_cached_history(symbol), meta
    rows = load_cached_history(symbol)
    if rows:
        return rows, {
            "symbol": symbol.upper(),
            "source": "cache",
            "rows": len(rows),
            "start": rows[0]["date"].isoformat(),
            "end": rows[-1]["date"].isoformat(),
            "saved_to": str(price_path(symbol)),
        }
    meta = download_symbol_history(symbol)
    return load_cached_history(symbol), meta
