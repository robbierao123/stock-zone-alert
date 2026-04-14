
import json
import os
import time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

import requests
from dotenv import load_dotenv

from zone import (
    get_daily_ohlc_3m,
    detect_zones_from_daily,
    detect_zones_from_weekly,
    convert_daily_to_weekly,
    get_latest_closed_5m_price,
)

load_dotenv()

DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", 45))
WEEKLY_LIMIT = int(os.getenv("WEEKLY_LIMIT", 250))
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL_ZONE")
DASHBOARD_STATE_FILE = os.getenv("DASHBOARD_STATE_FILE", "dashboard_state.json")


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
        raise ValueError("DISCORD_WEBHOOK_URL is missing in .env")

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
        raise ValueError("DISCORD_WEBHOOK_URL is missing in .env")

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

    daily_candles = get_daily_ohlc_3m(ticker, limit=daily_limit)
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

    weekly_source_daily = get_daily_ohlc_3m(ticker, limit=weekly_limit)
    weekly_candles = convert_daily_to_weekly(weekly_source_daily)
    weekly_zones = detect_zones_from_weekly(
        weekly_candles,
        tolerance_pct=0.0075,
        min_touches=2,
        overlap_min_touches=2,
    )

    weekly_payload = {
        "ticker": ticker.upper(),
        "timeframe": "weekly",
        "source_daily_lookback": weekly_limit,
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


def _check_ticker_zones_worker(
    ticker: str,
    daily_folder: str = "daily-zone-data",
    weekly_folder: str = "weekly-zone-data",
) -> dict:
    ticker_lc = ticker.lower()
    price = get_latest_closed_5m_price(ticker_lc)

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

    return {
        "ticker": ticker.upper(),
        "price": price,
        "hits": hits,
    }


def _hit_text_for_timeframe(hit_list: list[dict], timeframe: str) -> str:
    """
    Return only HIT text for the requested timeframe.
    If no hit in that timeframe, return '-'
    """
    for hit in hit_list:
        if hit["timeframe"] == timeframe:
            zone = hit["zone"]
            return f"HIT {zone['type']} {zone['low']}-{zone['high']}"
    return "-"


def _build_dashboard_content(results: list[dict]) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # only keep tickers that currently hit at least one zone
    hit_results = [r for r in results if r["hits"]]

    lines = []
    lines.append("```")
    lines.append("LIVE ZONE HITS")
    lines.append(f"Updated: {now_str}")
    lines.append("")
    lines.append("Ticker  Price      Daily                          Weekly")
    lines.append("------  ---------  -----------------------------  -----------------------------")

    if not hit_results:
        lines.append("None    -          No active hits                 -")
    else:
        for result in sorted(hit_results, key=lambda x: x["ticker"]):
            ticker = f"{result['ticker']:<6}"
            price = f"{result['price']:<9.2f}"

            daily_text = _hit_text_for_timeframe(result["hits"], "daily")[:29]
            weekly_text = _hit_text_for_timeframe(result["hits"], "weekly")[:29]

            lines.append(f"{ticker}  {price}  {daily_text:<29}  {weekly_text:<29}")

    lines.append("```")
    return "\n".join(lines)


def update_dashboard_message(results: list[dict]) -> None:
    content = _build_dashboard_content(results)
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

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _check_ticker_zones_worker,
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
                print(f"{ticker.upper()} checked at {result['price']:.2f}")
            except Exception as e:
                print(f"Error monitoring {ticker.upper()}: {e}")

    update_dashboard_message(results)
    return results


if __name__ == "__main__":
    tickers = [
        "tsla", "mu", "aapl", "amzn", "amd", "avgo", "asml",
        "googl", "intc", "meta", "msft", "nvda", "orcl",
        "pltr", "rddt", "sndk", "stx", "nflx", "mstr",
        "hood", "coin", "baba", "uso", "xom", "xle"
    ]

    print("Generating zone data...")
    save_zone_data_for_tickers(
        tickers=tickers,
        daily_limit=DAILY_LIMIT,
        weekly_limit=WEEKLY_LIMIT,
    )

    print("Starting hit-only dashboard...\n")

    while True:
        try:
            print(f"Running check at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            monitor_tickers_and_update_dashboard(
                tickers=tickers,
                max_workers=len(tickers),
            )
        except Exception as e:
            print("Error in main loop:", e)

        time.sleep(60)

