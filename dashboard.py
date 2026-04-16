import json
import os
import time
import threading
from pathlib import Path
from datetime import datetime
from collections import defaultdict
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
BREAK_MAX_PCT = float(os.getenv("BREAK_MAX_PCT", 0.5))

# Daily historical cache: built once outside the while loop
PREV_DAY_LEVELS_CACHE: dict[str, dict] = {}

# 5-minute candle-aware cache: one fetch per ticker per 5-minute bucket
FIVE_MIN_CACHE: dict[str, tuple[list[dict], tuple[int, int, int, int, int]]] = {}
FIVE_MIN_CACHE_LOCK = threading.Lock()


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

    for ticker in tickers:
        candles = get_daily_ohlc_3m(ticker, limit=3)

        if len(candles) < 2:
            raise ValueError(f"Not enough daily candles for {ticker}")

        prev_day = candles[-2]
        cache[ticker] = {
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
        tolerance_pct=0.0075,
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
            print(f"Error processing {ticker.upper()}: {e}")

    return results


def _fetch_recent_5m_bars(ticker: str, limit: int = 1000) -> list[dict]:
    if not FMP_API_KEY:
        raise ValueError("FMP_API_KEY is missing in .env")

    url = "https://financialmodelingprep.com/stable/historical-chart/5min"
    params = {
        "symbol": ticker,
        "apikey": FMP_API_KEY,
    }

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
                return bars

    bars = _fetch_recent_5m_bars(ticker_lc, limit=limit)

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

    # Ignore newest possibly-forming bar
    for bar in bars[:-1]:
        dt = bar.get("date", "")
        if not dt or " " not in dt:
            continue

        day = dt.split(" ")[0]
        daily_groups[day].append(bar)

    days = sorted(daily_groups.keys())

    if len(days) < 5:
        raise ValueError(
            f"Need at least 5 trading days of 5-minute bars for {ticker}, got {len(days)}"
        )

    last_5_days = days[-5:]

    baseline_bars = []
    for day in last_5_days:
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
        "days_used": last_5_days,
    }


def _find_recent_break(ticker: str, live_price: float) -> dict | None:
    prev_day = _get_previous_day_levels(ticker)

    prev_high = float(prev_day["high"])
    prev_low = float(prev_day["low"])

    pct = BREAK_MAX_PCT / 100.0  # 0.05 -> 0.0005

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
    price = get_live_price_full(ticker_lc)

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
        print(f"Break check skipped for {ticker.upper()}: {e}")

    volume_ratio = None
    try:
        bars = _get_recent_5m_bars_cached(ticker_lc, limit=1000)
        volume_ratio = _get_latest_5m_volume_ratio_from_bars(bars, ticker_lc)
    except Exception as e:
        print(f"Volume ratio skipped for {ticker.upper()}: {e}")

    return {
        "ticker": ticker.upper(),
        "price": price,
        "hits": hits,
        "break": recent_break,
        "volume_ratio": volume_ratio,
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


def _build_dashboard_content(results: list[dict]) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    active_results = [
        r for r in results
        if r["hits"] or r.get("break")
    ]

    lines = []
    lines.append("```")
    lines.append("LIVE ZONE HITS")
    lines.append(f"Last Updated: {now_str}".center(92))
    lines.append("")
    lines.append("Ticker  Price      Vol     Daily                         Weekly")
    lines.append("------  ---------  ------  -----------------------------  -----------------------------")

    if not active_results:
        lines.append("None    -          -       No active hits                 -")
    else:
        for result in sorted(active_results, key=lambda x: x["ticker"]):
            ticker = f"{result['ticker']:<6}"
            price = f"{result['price']:<9.2f}"
            vol_text = _volume_ratio_text(result.get("volume_ratio"))[:6]
            daily_text = _daily_text(result)[:29]
            weekly_text = _zone_text_for_timeframe(result["hits"], "weekly")[:29]

            lines.append(
                f"{ticker}  {price}  {vol_text:<6}  {daily_text:<29}  {weekly_text:<29}"
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

                print(" | ".join(parts))
            except Exception as e:
                print(f"Error monitoring {ticker.upper()}: {e}")

    update_dashboard_message(results)
    return results


if __name__ == "__main__":
    tickers = [
        "tsla", "mu", "aapl", "amzn", "amd", "avgo", "asml",
        "googl", "intc", "meta", "msft", "nvda", "orcl",
        "pltr", "nflx", "mstr", "hood", "coin", "hood",
        "QBTS", "BABA"
    ]

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
            print("Error in main loop:", e)

        time.sleep(CHECK_INTERVAL_SECONDS)