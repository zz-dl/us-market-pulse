# US Market Pulse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a mobile-first Flask app that predicts Nasdaq-100 and S&P 500 direction around Beijing 14:30 and validates the signal with real historical U.S. market data.

**Architecture:** A small Flask backend serves a phone-first dashboard and JSON APIs. Historical daily OHLCV data is downloaded from Stooq into local CSV files, then reused for forecast and backtest. The first version uses `QQQ` and `SPY` as tradeable proxies for Nasdaq-100 and S&P 500.

**Tech Stack:** Python 3.11, Flask, requests, standard-library CSV/statistics, HTML/CSS/JavaScript, local file cache.

---

### Task 1: Project Skeleton

**Files:**
- Create: `F:/USMarketPulse/requirements.txt`
- Create: `F:/USMarketPulse/Procfile`
- Create: `F:/USMarketPulse/render.yaml`
- Create: `F:/USMarketPulse/.gitignore`

- [ ] Create a Flask-ready project with `flask`, `requests`, and `gunicorn`.
- [ ] Add deployment entrypoints compatible with Render.

### Task 2: Historical Data Layer

**Files:**
- Create: `F:/USMarketPulse/market_data.py`
- Test: `F:/USMarketPulse/test_market_data.py`

- [ ] Implement `download_symbol_history(symbol)` using Stooq CSV URLs such as `https://stooq.com/q/d/l/?s=spy.us&i=d`.
- [ ] Save real historical rows to `data/prices/{symbol}.csv`.
- [ ] Parse rows into typed dictionaries with date, open, high, low, close, volume.
- [ ] Add tests for parsing, return calculation, and stale-cache behavior.

### Task 3: Forecast And Backtest Engine

**Files:**
- Create: `F:/USMarketPulse/forecast.py`
- Test: `F:/USMarketPulse/test_forecast.py`

- [ ] Build explainable features from daily bars: 1-day momentum, 5-day momentum, 20-day trend, 20-day volatility, range position, and overnight gap proxy.
- [ ] Score direction into bullish, bearish, or neutral with confidence.
- [ ] Backtest the same score over history without lookahead: signal from day `t` predicts day `t+1` close return.
- [ ] Return win rate, average next-day return, signal counts, recent trades, and annual summaries.

### Task 4: Flask API

**Files:**
- Create: `F:/USMarketPulse/app.py`

- [ ] Serve `/` from `static/index.html`.
- [ ] Add `/api/status`, `/api/refresh`, `/api/forecast`, `/api/backtest`, and `/api/data`.
- [ ] Ensure JSON never contains NaN or Infinity.
- [ ] Use `QQQ` for Nasdaq-100 proxy and `SPY` for S&P 500 proxy.

### Task 5: Mobile UI

**Files:**
- Create: `F:/USMarketPulse/static/index.html`

- [ ] Implement the approved dark mobile dashboard concept.
- [ ] Include tabs: Forecast, Backtest, Data, Strategy.
- [ ] Add refresh, run forecast, and run backtest controls.
- [ ] Render confidence arcs, driver rows, invalidation conditions, backtest metrics, data status, and strategy notes.

### Task 6: Verification

**Commands:**
- `python test_market_data.py`
- `python test_forecast.py`
- `python app.py` then call `/api/refresh`, `/api/forecast`, `/api/backtest`

- [ ] Run tests.
- [ ] Download real historical data.
- [ ] Start local server and verify mobile page loads.
- [ ] Verify APIs return usable JSON.
