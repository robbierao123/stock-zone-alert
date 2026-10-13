import json
import os
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from zone import (
    get_daily_ohlc_3m,
    detect_zones_from_daily,
    detect_zones_from_weekly,
    convert_daily_to_weekly,
    get_live_price_full,
)

load_dotenv()

DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", 45))
WEEKLY_LIMIT = int(os.getenv("WEEKLY_LIMIT", 250))
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL_DASHBOARD")
DASHBOARD_STATE_FILE = os.getenv("DASHBOARD_STATE_FILE", "dashboard_state.json")
DASHBOARD_VIEW_FILE = os.getenv("DASHBOARD_VIEW_FILE", "dashboard_view.txt")
FMP_API_KEY = os.getenv("FMP_API_KEY")
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", 5))
BREAK_MAX_PCT = float(os.getenv("BREAK_MAX_PCT", 0.3))
HOURLY_EMA_PERIOD = int(os.getenv("HOURLY_EMA_PERIOD", 240))
HOURLY_EMA_PROXIMITY_PCT = float(os.getenv("HOURLY_EMA_PROXIMITY_PCT", 0.5))
FOUR_HOUR_EMA_PERIOD = int(os.getenv("FOUR_HOUR_EMA_PERIOD", 240))
# Shared one-hour history powers both EMA240 timeframes. FMP can return less
# intraday history than requested, so we fetch missing history in small,
# rate-spaced date windows and keep it on disk across restarts.
HOURLY_HISTORY_DAYS = int(os.getenv("HOURLY_HISTORY_DAYS", 300))
HOURLY_HISTORY_CACHE_DIR = os.getenv("HOURLY_HISTORY_CACHE_DIR", "ema240-hourly-history")
HOURLY_BACKFILL_CHUNK_DAYS = max(15, int(os.getenv("HOURLY_BACKFILL_CHUNK_DAYS", 55)))
HOURLY_BACKFILL_TARGET_4H = max(FOUR_HOUR_EMA_PERIOD, int(os.getenv("HOURLY_BACKFILL_TARGET_4H", 400)))
HOURLY_BACKFILL_DELAY_SECONDS = max(1.0, float(os.getenv("HOURLY_BACKFILL_DELAY_SECONDS", 3.0)))
HOURLY_BACKFILL_RETRY_SECONDS = max(60, int(os.getenv("HOURLY_BACKFILL_RETRY_SECONDS", 180)))
# The window redraws every 5s, but quote requests are capped to avoid FMP 429s.
QUOTE_REFRESH_SECONDS = max(5, int(os.getenv("QUOTE_REFRESH_SECONDS", 20)))

# Daily historical cache: built once outside the while loop
PREV_DAY_LEVELS_CACHE: dict[str, dict] = {}

# 5-minute candle-aware cache: one fetch per ticker per 5-minute bucket
FIVE_MIN_CACHE: dict[str, tuple[list[dict], tuple[int, int, int, int, int]]] = {}
FIVE_MIN_CACHE_LOCK = threading.Lock()

# Hourly bars are fetched once per ticker for both 1H and 4H EMAs.
SHARED_HOURLY_BARS_CACHE: dict[str, tuple[list[dict], tuple]] = {}
SHARED_HOURLY_BARS_LOCK = threading.Lock()
SHARED_HOURLY_SYMBOL_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)
HOURLY_EMA_CACHE: dict[str, tuple[float, tuple]] = {}
HOURLY_EMA_CACHE_LOCK = threading.Lock()

# Price polling is cached independently of the 5-second dashboard redraw.
QUOTE_CACHE: dict[str, tuple[float, float]] = {}
QUOTE_CACHE_LOCK = threading.Lock()
# Limit *all* FMP request types to avoid a 60-request simultaneous burst.
FMP_REQUEST_LIMIT = threading.BoundedSemaphore(4)

# 4-hour EMA cache: refresh when a regular-session 4H candle closes.
FOUR_HOUR_EMA_CACHE: dict[str, tuple[float, tuple[int, int, int, int]]] = {}
FOUR_HOUR_EMA_CACHE_LOCK = threading.Lock()

# At most one history-backfill task is active at a time, not 20 concurrent
# workers hammering the FMP API. 4H displays '-' during initial warm-up.
HISTORY_BACKFILL_QUEUE: Queue[str] = Queue()
HISTORY_BACKFILL_QUEUED: set[str] = set()
HISTORY_BACKFILL_LOCK = threading.Lock()
HISTORY_BACKFILL_STARTED = False
HISTORY_BACKFILL_RETRY_AFTER: dict[str, float] = {}


def _ensure_folder(folder_name: str) -> Path:
    folder = Path(folder_name)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _load_json_file(path: str, default):
    file_path = Path(path)
    if not file_path.exists():
        return default
    try:
        with file_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json_file(path: str, payload) -> None:
    file_path = Path(path)
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _safe_error_text(error: Exception) -> str:
    """requests HTTPError URLs include the apikey; avoid leaking it in logs."""
    message = str(error)
    return message.replace(FMP_API_KEY, "[REDACTED_API_KEY]") if FMP_API_KEY else message


def _discord_webhook_wait_url() -> str:
    if not DISCORD_WEBHOOK_URL:
        raise ValueError("DISCORD_WEBHOOK_URL_DASHBOARD is missing in .env")

    if "?" in DISCORD_WEBHOOK_URL:
        return f"{DISCORD_WEBHOOK_URL}&wait=true"
    return f"{DISCORD_WEBHOOK_URL}?wait=true"


def _send_dashboard_message(content: str) -> str:
    response = requests.post(
        _discord_webhook_wait_url(),
        json={"content": content},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    message_id = data.get("id")
    if not message_id:
        raise ValueError(f"Could not get Discord message id: {data}")
    return message_id


def _edit_dashboard_message(message_id: str, content: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        raise ValueError("DISCORD_WEBHOOK_URL_DASHBOARD is missing in .env")

    edit_url = f"{DISCORD_WEBHOOK_URL}/messages/{message_id}"
    response = requests.patch(
        edit_url,
        json={"content": content},
        timeout=20,
    )
    response.raise_for_status()


def _get_or_create_dashboard_message(initial_content: str) -> str:
    state = _load_json_file(DASHBOARD_STATE_FILE, default={})
    message_id = state.get("message_id")

    if message_id:
        try:
            _edit_dashboard_message(message_id, initial_content)
            return message_id
        except Exception:
            pass

    new_message_id = _send_dashboard_message(initial_content)
    _save_json_file(DASHBOARD_STATE_FILE, {"message_id": new_message_id})
    return new_message_id


def _load_zone_file(file_path: Path) -> dict:
    if not file_path.exists():
        return {"zones": []}

    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _get_unique_tickers(tickers: list[str]) -> list[str]:
    seen = set()
    unique = []

    for ticker in tickers:
        ticker_lc = ticker.lower()
        if ticker_lc not in seen:
            seen.add(ticker_lc)
            unique.append(ticker_lc)

    return unique


def build_previous_day_levels_cache(tickers: list[str]) -> dict[str, dict]:
    cache = {}
    ny_today = datetime.now(ZoneInfo("America/New_York")).date()

    for ticker in tickers:
        candles = get_daily_ohlc_3m(ticker, limit=5)

        if not candles:
            raise ValueError(f"No daily candles for {ticker}")

        closed_candles = []

        for candle in candles:
            candle_date = datetime.strptime(candle["date"], "%Y-%m-%d").date()

            # only keep fully closed daily candles
            # if today's daily candle exists intraday, skip it
            if candle_date < ny_today:
                closed_candles.append(candle)

        if not closed_candles:
            raise ValueError(f"No closed daily candles found for {ticker}")

        prev_day = closed_candles[-1]

        cache[ticker.lower()] = {
            "date": prev_day["date"],
            "high": float(prev_day["high"]),
            "low": float(prev_day["low"]),
        }

    return cache


def _get_previous_day_levels(ticker: str) -> dict:
    ticker_lc = ticker.lower()

    if ticker_lc not in PREV_DAY_LEVELS_CACHE:
        raise ValueError(f"Previous day levels not cached for {ticker}")

    return PREV_DAY_LEVELS_CACHE[ticker_lc]


def save_zone_data_for_ticker(
    ticker: str,
    daily_limit: int = DAILY_LIMIT,
    weekly_limit: int = WEEKLY_LIMIT,
    daily_folder: str = "daily-zone-data",
    weekly_folder: str = "weekly-zone-data",
) -> tuple[str, str]:
    ticker = ticker.lower()

    daily_dir = _ensure_folder(daily_folder)
    weekly_dir = _ensure_folder(weekly_folder)

    # One daily historical fetch per ticker
    source_limit = max(daily_limit, weekly_limit)
    all_daily_candles = get_daily_ohlc_3m(ticker, limit=source_limit)

    if len(all_daily_candles) < daily_limit:
        raise ValueError(
            f"Not enough daily candles for {ticker}. "
            f"Needed {daily_limit}, got {len(all_daily_candles)}"
        )

    daily_candles = all_daily_candles[-daily_limit:]
    daily_zones = detect_zones_from_daily(daily_candles)

    daily_payload = {
        "ticker": ticker.upper(),
        "timeframe": "daily",
        "lookback_days": daily_limit,
        "zone_count": len(daily_zones),
        "zones": daily_zones,
    }

    daily_path = daily_dir / f"{ticker}_daily.json"
    with daily_path.open("w", encoding="utf-8") as f:
        json.dump(daily_payload, f, indent=2)

    weekly_candles = convert_daily_to_weekly(all_daily_candles)
    weekly_zones = detect_zones_from_weekly(
        weekly_candles,
        min_touches=2,
        overlap_min_touches=2,
    )

    weekly_payload = {
        "ticker": ticker.upper(),
        "timeframe": "weekly",
        "source_daily_lookback": source_limit,
        "weekly_candle_count": len(weekly_candles),
        "zone_count": len(weekly_zones),
        "zones": weekly_zones,
    }

    weekly_path = weekly_dir / f"{ticker}_weekly.json"
    with weekly_path.open("w", encoding="utf-8") as f:
        json.dump(weekly_payload, f, indent=2)

    return str(daily_path), str(weekly_path)


def save_zone_data_for_tickers(
    tickers: list[str],
    daily_limit: int = DAILY_LIMIT,
    weekly_limit: int = WEEKLY_LIMIT,
    daily_folder: str = "daily-zone-data",
    weekly_folder: str = "weekly-zone-data",
) -> dict[str, dict]:
    results = {}

    for ticker in tickers:
        try:
            daily_path, weekly_path = save_zone_data_for_ticker(
                ticker=ticker,
                daily_limit=daily_limit,
                weekly_limit=weekly_limit,
                daily_folder=daily_folder,
                weekly_folder=weekly_folder,
            )

            results[ticker.upper()] = {
                "daily_json": daily_path,
                "weekly_json": weekly_path,
                "status": "ok",
            }
            print(f"Saved {ticker.upper()} zones")

        except Exception as e:
            results[ticker.upper()] = {
                "status": "error",
                "error": str(e),
            }
            print(f"Error processing {ticker.upper()}: {_safe_error_text(e)}")

    return results


def _fetch_recent_5m_bars(ticker: str, limit: int = 1000) -> list[dict]:
    if not FMP_API_KEY:
        raise ValueError("FMP_API_KEY is missing in .env")

    url = "https://financialmodelingprep.com/stable/historical-chart/5min"
    params = {
        "symbol": ticker,
        "apikey": FMP_API_KEY,
    }

    with FMP_REQUEST_LIMIT:
        response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()

    if not isinstance(data, list) or not data:
        raise ValueError(f"No 5-minute data returned for {ticker}: {data}")

    bars = []
    for row in data:
        dt_value = row.get("date") or row.get("datetime") or ""
        bars.append({
            "date": dt_value,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0)),
        })

    bars.reverse()  # oldest -> newest
    return bars[-limit:]


def _current_5m_bucket() -> tuple[int, int, int, int, int]:
    now = datetime.now(ZoneInfo("America/New_York"))
    return (now.year, now.month, now.day, now.hour, now.minute // 5)


def _get_recent_5m_bars_cached(ticker: str, limit: int = 1000) -> list[dict]:
    ticker_lc = ticker.lower()
    current_bucket = _current_5m_bucket()

    with FIVE_MIN_CACHE_LOCK:
        cached = FIVE_MIN_CACHE.get(ticker_lc)
        if cached is not None:
            bars, cached_bucket = cached
            if cached_bucket == current_bucket:
                days = set()
                for bar in bars:
                    dt = bar.get("date", "")
                    if " " in dt:
                        days.add(dt.split(" ")[0])

                if len(days) >= 3:
                    return bars
                else:
                    del FIVE_MIN_CACHE[ticker_lc]

    bars = _fetch_recent_5m_bars(ticker_lc, limit=limit)

    days = set()
    for bar in bars:
        dt = bar.get("date", "")
        if " " in dt:
            days.add(dt.split(" ")[0])

    if len(days) >= 3:
        with FIVE_MIN_CACHE_LOCK:
            FIVE_MIN_CACHE[ticker_lc] = (bars, current_bucket)

    return bars


def _get_latest_closed_5m_bar_from_bars(bars: list[dict], ticker: str) -> dict:
    if len(bars) < 2:
        raise ValueError(f"Not enough 5-minute bars for {ticker}")

    # bars[-1] may still be forming
    return bars[-2]


def _get_latest_5m_volume_ratio_from_bars(bars: list[dict], ticker: str) -> dict:
    if len(bars) < 2:
        raise ValueError(f"Not enough 5-minute bars for {ticker}")

    latest_bar = _get_latest_closed_5m_bar_from_bars(bars, ticker)
    latest_dt = latest_bar["date"]

    daily_groups: dict[str, list[dict]] = defaultdict(list)

    # ignore newest possibly-forming bar
    for bar in bars[:-1]:
        dt = bar.get("date", "")
        if not dt or " " not in dt:
            continue

        day = dt.split(" ")[0]
        daily_groups[day].append(bar)

    days = sorted(daily_groups.keys())

    if not days:
        raise ValueError(f"No usable 5-minute bars for {ticker}")

    # use up to last 5 trading days, but do not fail if fewer exist
    days_used = days[-5:]

    days_count = len(days_used)

    # 🚨 NEW FILTER (key change)
    if days_count < 3:
        return None
    
    baseline_bars = []
    for day in days_used:
        baseline_bars.extend(daily_groups[day])

    if not baseline_bars:
        raise ValueError(f"No baseline 5-minute bars found for {ticker}")

    avg_volume = sum(bar["volume"] for bar in baseline_bars) / len(baseline_bars)
    latest_volume = latest_bar["volume"]
    ratio = 0.0 if avg_volume <= 0 else latest_volume / avg_volume

    return {
        "latest_volume": latest_volume,
        "avg_volume": avg_volume,
        "ratio": ratio,
        "bar_time": latest_dt,
        "days_used": days_used,
        "days_count": len(days_used),
    }


def _current_hour_bucket() -> tuple[int, int, int, int, int]:
    """Last completed RTH hour; no hourly refetch overnight or weekends."""
    now = datetime.now(ZoneInfo("America/New_York"))
    minute_of_day = now.hour * 60 + now.minute
    # Earliest 09:30 candle closes at 10:30 NY time. Until then,
    # re-use the last trading session's closing-bar snapshot.
    if now.weekday() >= 5 or minute_of_day < 10 * 60 + 30:
        previous = now.date() - timedelta(days=1)
        while previous.weekday() >= 5:
            previous -= timedelta(days=1)
        return (previous.year, previous.month, previous.day, 16, 0)

    for close_minute in (960, 930, 870, 810, 750, 690, 630):
        if minute_of_day >= close_minute:
            return (now.year, now.month, now.day,
                    close_minute // 60, close_minute % 60)
    raise RuntimeError("Unreachable hourly cache bucket")


def _get_live_price_cached(ticker: str) -> float:
    """Reduce quote requests without changing the dashboard redraw frequency."""
    ticker_lc = ticker.lower()
    now = time.monotonic()
    with QUOTE_CACHE_LOCK:
        cached = QUOTE_CACHE.get(ticker_lc)
        if cached and now - cached[1] < QUOTE_REFRESH_SECONDS:
            return cached[0]

    with FMP_REQUEST_LIMIT:
        price = float(get_live_price_full(ticker_lc))
    with QUOTE_CACHE_LOCK:
        QUOTE_CACHE[ticker_lc] = (price, time.monotonic())
    return price


def _closed_hourly_bars(rows: list[dict], now: datetime) -> list[dict]:
    """Normalize hourly FMP candles; discard anything not fully closed."""
    output = {}
    for row in rows:
        try:
            stamp = str(row.get("date") or row.get("datetime") or "")
            start = _parse_fmp_hourly_bar_time(stamp)
            if start is None:
                continue
            end = start + timedelta(hours=1)
            # Last regular-session hourly bar is a 30-minute candle.
            if start.hour == 15 and start.minute == 30:
                end = start.replace(hour=16, minute=0, second=0)
            if end > now:
                continue
            output[stamp] = {
                "date": stamp,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0)),
            }
        except (ValueError, TypeError, KeyError):
            continue
    return sorted(output.values(), key=lambda bar: bar["date"])


def _fetch_hourly_range(ticker: str, from_date: str, to_date: str) -> list[dict]:
    """The ONLY market-data source used by both EMA timeframes: FMP 1hour."""
    if not FMP_API_KEY:
        raise ValueError("FMP_API_KEY is missing in .env")
    params = {
        "symbol": ticker,
        "from": from_date,
        "to": to_date,
        # Match the regular-session and adjusted-price Webull chart settings.
        "extended": "false",
        "nonadjusted": "false",
        "apikey": FMP_API_KEY,
    }
    url = "https://financialmodelingprep.com/stable/historical-chart/1hour"
    with FMP_REQUEST_LIMIT:
        response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError(f"Invalid FMP 1H response for {ticker}: {str(data)[:150]}")
    return _closed_hourly_bars(data, datetime.now(ZoneInfo("America/New_York")))


def _merge_hourly_bars(*groups: list[dict]) -> list[dict]:
    """Deduplicate overlapping FMP pages, using the newest fetched values."""
    merged = {}
    for group in groups:
        for bar in group:
            if bar.get("date"):
                merged[bar["date"]] = bar
    return sorted(merged.values(), key=lambda bar: bar["date"])


def _hourly_history_cache_path(ticker: str) -> Path:
    return Path(HOURLY_HISTORY_CACHE_DIR) / f"{ticker.lower()}_1h.json"


def _read_saved_hourly_bars(ticker: str) -> list[dict]:
    payload = _load_json_file(str(_hourly_history_cache_path(ticker)), {})
    return payload.get("bars", []) if isinstance(payload, dict) else []


def _save_hourly_bars(ticker: str, bars: list[dict]) -> None:
    path = _hourly_history_cache_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement prevents corrupting history on abrupt exit.
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({"ticker": ticker.upper(), "bars": bars}, f)
    os.replace(tmp, path)


def _fetch_hourly_bars(ticker: str) -> list[dict]:
    ny_today = datetime.now(ZoneInfo("America/New_York")).date()
    return _fetch_hourly_range(
        ticker,
        (ny_today - timedelta(days=HOURLY_HISTORY_DAYS)).isoformat(),
        ny_today.isoformat(),
    )


def _get_shared_hourly_bars_cached(ticker: str) -> list[dict]:
    """One recent hourly download per ticker/hour, plus stored older pages."""
    ticker_lc = ticker.lower()
    bucket = _current_hour_bucket()
    with SHARED_HOURLY_BARS_LOCK:
        saved = SHARED_HOURLY_BARS_CACHE.get(ticker_lc)
        if saved and saved[1] == bucket:
            return saved[0]
    with SHARED_HOURLY_SYMBOL_LOCKS[ticker_lc]:
        with SHARED_HOURLY_BARS_LOCK:
            saved = SHARED_HOURLY_BARS_CACHE.get(ticker_lc)
            if saved and saved[1] == bucket:
                return saved[0]
        # Disk history may have been built in a previous process run.
        prior = saved[0] if saved else _read_saved_hourly_bars(ticker_lc)
        try:
            recent = _fetch_hourly_bars(ticker_lc)
        except Exception:
            if not prior:
                raise
            print(f"FMP hourly refresh failed for {ticker_lc.upper()}; using saved history")
            recent = []
        bars = _merge_hourly_bars(prior, recent)
        with SHARED_HOURLY_BARS_LOCK:
            # Background older-history worker may have added bars during fetch.
            updated = SHARED_HOURLY_BARS_CACHE.get(ticker_lc)
            if updated:
                bars = _merge_hourly_bars(updated[0], bars)
            SHARED_HOURLY_BARS_CACHE[ticker_lc] = (bars, bucket)
        if recent:
            _save_hourly_bars(ticker_lc, bars)
        return bars


def _schedule_history_backfill(ticker: str) -> None:
    """Queue missing history in the background; don't stall live dashboard."""
    global HISTORY_BACKFILL_STARTED
    ticker = ticker.lower()
    with HISTORY_BACKFILL_LOCK:
        if ticker in HISTORY_BACKFILL_QUEUED:
            return
        if time.monotonic() < HISTORY_BACKFILL_RETRY_AFTER.get(ticker, 0):
            return
        HISTORY_BACKFILL_QUEUED.add(ticker)
        HISTORY_BACKFILL_QUEUE.put(ticker)
        if not HISTORY_BACKFILL_STARTED:
            threading.Thread(
                target=_history_backfill_worker,
                name="EMA240-history-backfill",
                daemon=True,
            ).start()
            HISTORY_BACKFILL_STARTED = True


def _history_backfill_worker() -> None:
    """Backfill OLD 1H candles one request at a time, only for active tickers."""
    while True:
        ticker = HISTORY_BACKFILL_QUEUE.get()
        try:
            _backfill_one_ticker(ticker)
            with SHARED_HOURLY_BARS_LOCK:
                current = SHARED_HOURLY_BARS_CACHE.get(ticker)
            if current:
                complete_count = len(_aggregate_regular_session_4h(
                    current[0], datetime.now(ZoneInfo("America/New_York"))
                ))
                if complete_count < HOURLY_BACKFILL_TARGET_4H:
                    # The provider may not have older data on this plan.
                    # Avoid re-downloading the same ranges every five seconds.
                    HISTORY_BACKFILL_RETRY_AFTER[ticker] = time.monotonic() + 3600
        except Exception as exc:
            HISTORY_BACKFILL_RETRY_AFTER[ticker] = (
                time.monotonic() + HOURLY_BACKFILL_RETRY_SECONDS
            )
            # Never print the FMP URL here: it contains the user's API key.
            print(f"EMA history warm-up deferred for {ticker.upper()}: {type(exc).__name__}; will retry later")
        finally:
            with HISTORY_BACKFILL_LOCK:
                HISTORY_BACKFILL_QUEUED.discard(ticker)
            HISTORY_BACKFILL_QUEUE.task_done()


def _backfill_one_ticker(ticker: str) -> None:
    now = datetime.now(ZoneInfo("America/New_York"))
    bars = _get_shared_hourly_bars_cached(ticker)
    count = len(_aggregate_regular_session_4h(bars, now))
    print(f"EMA history {ticker.upper()}: {count} closed 4H candles; target {HOURLY_BACKFILL_TARGET_4H}")
    if not bars:
        return
    # Limit historical requests per symbol: use small older slices rather
    # than a single huge FMP response that gets capped/truncated.
    for _ in range(12):
        if count >= HOURLY_BACKFILL_TARGET_4H:
            return
        oldest = _parse_fmp_hourly_bar_time(bars[0]["date"])
        if oldest is None:
            return
        end_day = oldest.date() - timedelta(days=1)
        start_day = end_day - timedelta(days=HOURLY_BACKFILL_CHUNK_DAYS - 1)
        time.sleep(HOURLY_BACKFILL_DELAY_SECONDS)
        older = _fetch_hourly_range(ticker, start_day.isoformat(), end_day.isoformat())
        if not older:
            print(f"EMA history {ticker.upper()}: no older FMP data in {start_day}..{end_day}")
            return
        new_bars = _merge_hourly_bars(older, bars)
        if len(new_bars) <= len(bars):
            print(f"EMA history {ticker.upper()}: older FMP page added no bars")
            return
        bars = new_bars
        count = len(_aggregate_regular_session_4h(bars, now))
        with SHARED_HOURLY_SYMBOL_LOCKS[ticker]:
            with SHARED_HOURLY_BARS_LOCK:
                saved = SHARED_HOURLY_BARS_CACHE.get(ticker)
                if saved:
                    bars = _merge_hourly_bars(bars, saved[0])
                    SHARED_HOURLY_BARS_CACHE[ticker] = (bars, saved[1])
            _save_hourly_bars(ticker, bars)
        with HOURLY_EMA_CACHE_LOCK:
            HOURLY_EMA_CACHE.pop(ticker, None)
        with FOUR_HOUR_EMA_CACHE_LOCK:
            FOUR_HOUR_EMA_CACHE.pop(ticker, None)
        print(f"EMA history {ticker.upper()}: now {count} closed 4H candles (saved to disk)")
    if count < FOUR_HOUR_EMA_PERIOD:
        print(f"EMA history {ticker.upper()}: fewer than {FOUR_HOUR_EMA_PERIOD} 4H bars after backfill")


def _calculate_ema(closes: list[float], period: int) -> float:
    if len(closes) < period:
        raise ValueError(f"Need at least {period} closes, got {len(closes)}")

    ema = sum(closes[:period]) / period
    multiplier = 2.0 / (period + 1.0)
    for close in closes[period:]:
        ema = (close - ema) * multiplier + ema
    return ema


def _get_hourly_ema_240_cached(ticker: str) -> float:
    ticker_lc = ticker.lower()
    current_bucket = _current_hour_bucket()

    with HOURLY_EMA_CACHE_LOCK:
        cached = HOURLY_EMA_CACHE.get(ticker_lc)
        if cached is not None:
            ema_value, cached_bucket = cached
            if cached_bucket == current_bucket:
                return ema_value

    bars = _get_shared_hourly_bars_cached(ticker_lc)
    closes = [float(bar["close"]) for bar in bars]
    ema_value = _calculate_ema(closes, HOURLY_EMA_PERIOD)

    with HOURLY_EMA_CACHE_LOCK:
        HOURLY_EMA_CACHE[ticker_lc] = (ema_value, current_bucket)

    return ema_value


def _get_hourly_ema_proximity(ticker: str, live_price: float) -> dict | None:
    ema_value = _get_hourly_ema_240_cached(ticker)
    if ema_value <= 0:
        return None

    distance_pct = ((float(live_price) - ema_value) / ema_value) * 100.0
    return {
        "ema": ema_value,
        "distance_pct": distance_pct,
        "position": "ABOVE" if distance_pct >= 0 else "BELOW",
        "within_range": abs(distance_pct) <= HOURLY_EMA_PROXIMITY_PCT,
    }


def _current_four_hour_bucket() -> tuple[int, int, int, int]:
    """NY session-based cache bucket: before 13:30, 13:30-16:00, after 16:00.

    Regular-session 4-hour candles are based on 09:30-13:30 and the
    remaining 13:30-16:00 session. Using the closing boundaries avoids
    unnecessary API requests on each 5-second dashboard refresh.
    """
    now = datetime.now(ZoneInfo("America/New_York"))
    minute_of_day = now.hour * 60 + now.minute
    phase = 0 if minute_of_day < 13 * 60 + 30 else (1 if minute_of_day < 16 * 60 else 2)
    return (now.year, now.month, now.day, phase)


def _parse_fmp_hourly_bar_time(value: str) -> datetime | None:
    """FMP hourly timestamps represent candle starts in US Eastern time."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(value), fmt).replace(
                tzinfo=ZoneInfo("America/New_York")
            )
        except ValueError:
            continue
    return None


def _aggregate_regular_session_4h(hourly_bars: list[dict], now: datetime) -> list[dict]:
    """Create 4H chart candles from 1H bars, based on US stock sessions.

    A regular US stock session is 09:30-16:00 ET. The corresponding 4H
    chart contains the 09:30-13:30 block (four 1H bars) and a shorter
    13:30-16:00 block (three 1H bars, final 1H bar is just 30 minutes).
    Do not combine candles across different trading dates, include overnight
    bars, or allow a partially formed 4H candle into the EMA.
    """
    session_buckets = defaultdict(dict)
    tz = ZoneInfo("America/New_York")
    for bar in hourly_bars:
        start = _parse_fmp_hourly_bar_time(bar.get("date", ""))
        if start is None:
            continue
        minute = start.hour * 60 + start.minute
        if minute < 9 * 60 + 30 or minute >= 16 * 60:
            continue
        session = 0 if minute < 13 * 60 + 30 else 1
        session_buckets[(start.date(), session)][minute] = bar

    expected = (
        (9 * 60 + 30, 10 * 60 + 30, 11 * 60 + 30, 12 * 60 + 30),
        (13 * 60 + 30, 14 * 60 + 30, 15 * 60 + 30),
    )
    four_hour_bars = []
    for (trading_date, session), time_map in sorted(session_buckets.items()):
        # Skip incomplete/gappy blocks rather than treating partial data as
        # the last 4H closing price. This also avoids using the live 4H bar.
        if not all(minute in time_map for minute in expected[session]):
            continue
        close_hour, close_minute = ((13, 30) if session == 0 else (16, 0))
        candle_end = datetime.combine(trading_date, datetime.min.time(), tzinfo=tz)
        candle_end = candle_end.replace(hour=close_hour, minute=close_minute)
        if candle_end > now:
            continue
        final_hourly = time_map[expected[session][-1]]
        four_hour_bars.append({
            "date": f"{trading_date.isoformat()} {'09:30' if session == 0 else '13:30'}:00",
            "close": float(final_hourly["close"]),
        })
    return four_hour_bars


def _fetch_four_hour_bars(ticker: str) -> list[dict]:
    """Build CLOSED regular-session 4H candles from FMP's 1H candles."""
    now = datetime.now(ZoneInfo("America/New_York"))
    history = _get_shared_hourly_bars_cached(ticker)
    bars = _aggregate_regular_session_4h(history, now)
    if len(bars) < FOUR_HOUR_EMA_PERIOD:
        raise ValueError(
            f"Only {len(bars)} valid 4H bars for {ticker}; "
            f"need at least {FOUR_HOUR_EMA_PERIOD} (historical warm-up pending)."
        )
    return bars


def _get_four_hour_ema_240_cached(ticker: str) -> float:
    ticker_lc = ticker.lower()
    current_bucket = _current_four_hour_bucket()

    with FOUR_HOUR_EMA_CACHE_LOCK:
        cached = FOUR_HOUR_EMA_CACHE.get(ticker_lc)
        if cached is not None:
            ema_value, cached_bucket = cached
            if cached_bucket == current_bucket:
                return ema_value

    bars = _fetch_four_hour_bars(ticker_lc)
    closes = [float(bar["close"]) for bar in bars]
    ema_value = _calculate_ema(closes, FOUR_HOUR_EMA_PERIOD)

    with FOUR_HOUR_EMA_CACHE_LOCK:
        FOUR_HOUR_EMA_CACHE[ticker_lc] = (ema_value, current_bucket)

    return ema_value


def _get_four_hour_ema_proximity(ticker: str, live_price: float) -> dict | None:
    ema_value = _get_four_hour_ema_240_cached(ticker)
    if ema_value <= 0:
        return None

    distance_pct = ((float(live_price) - ema_value) / ema_value) * 100.0
    return {
        "ema": ema_value,
        "distance_pct": distance_pct,
        "position": "ABOVE" if distance_pct >= 0 else "BELOW",
    }


def _find_recent_break(ticker: str, live_price: float) -> dict | None:
    prev_day = _get_previous_day_levels(ticker)

    prev_high = float(prev_day["high"])
    prev_low = float(prev_day["low"])

    pct = BREAK_MAX_PCT / 100.0  # 0.05 -> 0.005

    high_lower = prev_high * (1 - pct)
    high_upper = prev_high * (1 + pct)

    low_lower = prev_low * (1 - pct)
    low_upper = prev_low * (1 + pct)

    if high_lower <= live_price <= high_upper:
        return {
            "type": "NEAR HIGH",
            "level": round(prev_high, 2),
            "trigger_price": round(live_price, 2),
            "trigger_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    if low_lower <= live_price <= low_upper:
        return {
            "type": "NEAR LOW",
            "level": round(prev_low, 2),
            "trigger_price": round(live_price, 2),
            "trigger_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    return None



def _check_ticker_worker(
    ticker: str,
    daily_folder: str = "daily-zone-data",
    weekly_folder: str = "weekly-zone-data",
) -> dict:
    ticker_lc = ticker.lower()
    price = _get_live_price_cached(ticker_lc)

    daily_path = Path(daily_folder) / f"{ticker_lc}_daily.json"
    weekly_path = Path(weekly_folder) / f"{ticker_lc}_weekly.json"

    daily_data = _load_zone_file(daily_path)
    weekly_data = _load_zone_file(weekly_path)

    hits = []

    for zone in daily_data.get("zones", []):
        if zone["low"] <= price <= zone["high"]:
            hits.append({
                "timeframe": "daily",
                "zone": zone,
            })

    for zone in weekly_data.get("zones", []):
        if zone["low"] <= price <= zone["high"]:
            hits.append({
                "timeframe": "weekly",
                "zone": zone,
            })

    recent_break = None
    try:
        recent_break = _find_recent_break(ticker_lc, price)
    except Exception as e:
        print(f"Break check skipped for {ticker.upper()}: {_safe_error_text(e)}")

    volume_ratio = None
    try:
        bars = _get_recent_5m_bars_cached(ticker_lc, limit=1000)
        volume_ratio = _get_latest_5m_volume_ratio_from_bars(bars, ticker_lc)
    except Exception as e:
        print(f"5m data / volume ratio skipped for {ticker.upper()}: {_safe_error_text(e)}")

    hourly_ema_240 = None
    try:
        hourly_ema_240 = _get_hourly_ema_proximity(ticker_lc, price)
    except Exception as e:
        print(f"Hourly EMA 240 skipped for {ticker.upper()}: {_safe_error_text(e)}")

    four_hour_ema_240 = None
    try:
        four_hour_ema_240 = _get_four_hour_ema_proximity(ticker_lc, price)
    except ValueError as e:
        # Only fetch older history for actual displayed rows; it is queued
        # in a single background worker to avoid the previous FMP 429 storm.
        if hits or recent_break:
            _schedule_history_backfill(ticker_lc)
        # Avoid repeating the same warning for all 20 symbols every 5s.
    except Exception as e:
        print(f"4-hour EMA 240 skipped for {ticker.upper()}: {type(e).__name__}")

    if (hits or recent_break) and four_hour_ema_240 is not None:
        with SHARED_HOURLY_BARS_LOCK:
            shared = SHARED_HOURLY_BARS_CACHE.get(ticker_lc)
        if shared and len(_aggregate_regular_session_4h(
            shared[0], datetime.now(ZoneInfo("America/New_York"))
        )) < HOURLY_BACKFILL_TARGET_4H:
            _schedule_history_backfill(ticker_lc)

    return {
        "ticker": ticker.upper(),
        "price": price,
        "hits": hits,
        "break": recent_break,
        "volume_ratio": volume_ratio,
        "hourly_ema_240": hourly_ema_240,
        "four_hour_ema_240": four_hour_ema_240,
    }


def _zone_text_for_timeframe(hit_list: list[dict], timeframe: str) -> str:
    for hit in hit_list:
        if hit["timeframe"] == timeframe:
            zone = hit["zone"]
            return f"HIT {zone['type']} {zone['low']}-{zone['high']}"
    return "-"


def _break_text(break_info: dict | None) -> str:
    if not break_info:
        return "-"
    return f"{break_info['type']} {break_info['level']}"


def _volume_ratio_text(volume_info: dict | None) -> str:
    if not volume_info:
        return "-"
    return f"{volume_info['ratio']:.2f}"


def _daily_text(result: dict) -> str:
    break_text = _break_text(result.get("break"))
    zone_text = _zone_text_for_timeframe(result["hits"], "daily")

    if break_text != "-" and zone_text != "-":
        return f"{break_text}; {zone_text}"
    if break_text != "-":
        return break_text
    if zone_text != "-":
        return zone_text
    return "-"


def _ema_value_and_distance_text(ema_info: dict | None) -> str:
    """Show the actual EMA240 level and live-price distance, or '-' if unavailable."""
    if not ema_info:
        return "-"
    return f"{ema_info['ema']:.2f} ({ema_info['distance_pct']:+.2f}%)"


def _build_dashboard_content(results: list[dict]) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    active_results = [
        r for r in results
        if r["hits"] or r.get("break")
    ]

    lines = []
    lines.append("```")
    lines.append("LIVE ZONE HITS")
    lines.append(f"Last Updated: {now_str}".center(112))
    lines.append("")
    # EMA columns show both the computed EMA price level and the signed distance.
    lines.append(f"{'Ticker':<6}  {'Price':<9}  {'Vol':<6}  {'Daily':<29}  {'Weekly':<28}  {'EMA240(1H)':<20}  {'EMA240(4H)':<20}")
    lines.append(f"{'-' * 6}  {'-' * 9}  {'-' * 6}  {'-' * 29}  {'-' * 28}  {'-' * 20}  {'-' * 20}")

    if not active_results:
        lines.append(f"{'None':<6}  {'-':<9}  {'-':<6}  {'No active hits':<29}  {'-':<28}  {'-':<20}  {'-':<20}")
    else:
        for result in sorted(active_results, key=lambda x: x["ticker"]):
            ticker = f"{result['ticker']:<6}"
            price = f"{result['price']:<9.2f}"
            vol_text = _volume_ratio_text(result.get("volume_ratio"))[:6]
            daily_text = _daily_text(result)[:29]
            weekly_text = _zone_text_for_timeframe(result["hits"], "weekly")[:28]
            ema_text = _ema_value_and_distance_text(result.get("hourly_ema_240"))
            four_hour_ema_text = _ema_value_and_distance_text(result.get("four_hour_ema_240"))

            lines.append(
                f"{ticker}  {price}  {vol_text:<6}  {daily_text:<29}  {weekly_text:<28}  {ema_text:<20}  {four_hour_ema_text:<20}"
            )

    lines.append("```")
    return "\n".join(lines)


def _content_for_local_view(content: str) -> str:
    lines = content.splitlines()

    if lines and lines[0].strip() == "```":
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    return "\n".join(lines).strip() + "\n"


def _save_dashboard_view(content: str) -> None:
    clean_content = _content_for_local_view(content)
    with open(DASHBOARD_VIEW_FILE, "w", encoding="utf-8") as f:
        f.write(clean_content)


def update_dashboard_message(results: list[dict]) -> None:
    content = _build_dashboard_content(results)
    _save_dashboard_view(content)
    message_id = _get_or_create_dashboard_message(content)
    _edit_dashboard_message(message_id, content)
    print(f"Dashboard updated: message_id={message_id}")


def monitor_tickers_and_update_dashboard(
    tickers: list[str],
    max_workers: int | None = None,
    daily_folder: str = "daily-zone-data",
    weekly_folder: str = "weekly-zone-data",
) -> list[dict]:
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _check_ticker_worker,
                ticker,
                daily_folder,
                weekly_folder,
            ): ticker
            for ticker in tickers
        }

        for future in as_completed(futures):
            ticker = futures[future]
            try:
                result = future.result()
                results.append(result)

                parts = [f"{ticker.upper()} checked at {result['price']:.2f}"]

                if result.get("break"):
                    parts.append(
                        f"{result['break']['type']} {result['break']['level']:.2f}"
                    )

                if result.get("volume_ratio"):
                    parts.append(
                        f"vol {result['volume_ratio']['ratio']:.2f}"
                    )

                if result["hits"]:
                    parts.append("zone hit")

                # Include the actual 1H/4H EMA240 levels in every ticker update.
                parts.append(f"EMA240(1H) {_ema_value_and_distance_text(result.get('hourly_ema_240'))}")
                parts.append(f"EMA240(4H) {_ema_value_and_distance_text(result.get('four_hour_ema_240'))}")

                print(" | ".join(parts))
            except Exception as e:
                print(f"Error monitoring {ticker.upper()}: {_safe_error_text(e)}")

    # If a temporary FMP 429 makes every ticker fail, leave the previous
    # dashboard visible instead of replacing it with "Tickers Shown: 0".
    if results:
        update_dashboard_message(results)
    else:
        print("No quotes available (possibly FMP 429); keeping last dashboard snapshot.")
    return results


if __name__ == "__main__":
    tickers = ["tsla", "mu",
     "aapl", "amzn", "amd", "avgo",
    "googl", "intc", "meta", "msft", "nvda",
    "orcl", "pltr", "nflx","mstr","hood","coin","baba","spy","qqq"]

    unique_tickers = _get_unique_tickers(tickers)

    print("Building previous-day cache...")
    PREV_DAY_LEVELS_CACHE = build_previous_day_levels_cache(unique_tickers)

    print("Generating zone data...")
    save_zone_data_for_tickers(
        tickers=unique_tickers,
        daily_limit=DAILY_LIMIT,
        weekly_limit=WEEKLY_LIMIT,
    )

    print("Starting combined zone + previous-day break dashboard...\n")

    while True:
        try:
            print(f"Running check at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            monitor_tickers_and_update_dashboard(
                tickers=unique_tickers,
                max_workers=len(unique_tickers),
            )
        except Exception as e:
            print("Error in main loop:", _safe_error_text(e))

        time.sleep(CHECK_INTERVAL_SECONDS)