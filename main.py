import json
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
import time
from zone import (
    get_daily_ohlc_3m,
    detect_zones_from_daily,
    detect_zones_from_weekly,
    convert_daily_to_weekly,
    get_latest_closed_5m_price,
)

from zone_drawer import (
    save_daily_zones_chart,
    save_weekly_zones_chart,
)

from SendAlert import send_message_with_image
import os
from dotenv import load_dotenv
load_dotenv()

DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", 45))
WEEKLY_LIMIT = int(os.getenv("WEEKLY_LIMIT", 250))


def _ensure_folder(folder_name: str) -> Path:
    folder = Path(folder_name)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_zone_data_for_ticker(
    ticker: str,
    daily_limit: int = DAILY_LIMIT,
    weekly_limit: int = WEEKLY_LIMIT,
    daily_folder: str = "daily-zone-data",
    weekly_folder: str = "weekly-zone-data",
) -> tuple[str, str]:
    """
    Generate daily + weekly zone data for one ticker and save as JSON.

    Returns:
        (daily_json_path, weekly_json_path)
    """
    ticker = ticker.lower()

    daily_dir = _ensure_folder(daily_folder)
    weekly_dir = _ensure_folder(weekly_folder)

    # Daily zones
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

    # Weekly zones
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
    """
    Generate daily + weekly zone JSON files for a list of tickers.
    """
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

            print(f"Saved {ticker.upper()} daily zones -> {daily_path}")
            print(f"Saved {ticker.upper()} weekly zones -> {weekly_path}")

        except Exception as e:
            results[ticker.upper()] = {
                "status": "error",
                "error": str(e),
            }
            print(f"Error processing {ticker.upper()}: {e}")

    return results


def clear_zone_data(
    daily_folder: str = "daily-zone-data",
    weekly_folder: str = "weekly-zone-data"
) -> None:
    """
    Delete all files inside daily and weekly zone data folders.
    Keeps the folders themselves.
    """
    for folder_name in [daily_folder, weekly_folder]:
        folder = Path(folder_name)

        if not folder.exists():
            print(f"{folder_name} does not exist, skipping...")
            continue

        for file in folder.iterdir():
            try:
                if file.is_file():
                    file.unlink()
                elif file.is_dir():
                    import shutil
                    shutil.rmtree(file)
            except Exception as e:
                print(f"Failed to delete {file}: {e}")

        print(f"Cleared folder: {folder_name}")


def _load_zone_file(file_path: Path) -> dict:
    if not file_path.exists():
        return {"zones": []}

    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _check_ticker_zones_worker(
    ticker: str,
    daily_folder: str = "daily-zone-data",
    weekly_folder: str = "weekly-zone-data",
) -> dict:
    """
    Worker process:
    - gets latest closed 5m price
    - reads saved daily/weekly zone JSON
    - returns matching zones
    """
    ticker_lc = ticker.lower()
    price = get_latest_closed_5m_price(ticker_lc)

    daily_path = Path(daily_folder) / f"{ticker_lc}_daily.json"
    weekly_path = Path(weekly_folder) / f"{ticker_lc}_weekly.json"

    daily_data = _load_zone_file(daily_path)
    weekly_data = _load_zone_file(weekly_path)

    matches = []

    for zone in daily_data.get("zones", []):
        if zone["low"] <= price <= zone["high"]:
            matches.append({
                "timeframe": "daily",
                "zone": zone,
            })

    for zone in weekly_data.get("zones", []):
        if zone["low"] <= price <= zone["high"]:
            matches.append({
                "timeframe": "weekly",
                "zone": zone,
            })

    return {
        "ticker": ticker.upper(),
        "price": price,
        "matches": matches,
    }


def _send_zone_hit_alert(ticker: str, price: float, timeframe: str, zone: dict) -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    if timeframe == "daily":
        image_path = save_daily_zones_chart(ticker, limit= DAILY_LIMIT)
        title = "📊 Daily Zone Hit"
    else:
        image_path = save_weekly_zones_chart(ticker, limit=WEEKLY_LIMIT)
        title = "📈 Weekly Zone Hit"

    message = (
        f"{title}\n"
        f"Time: {now_str}\n"
        f"Ticker: {ticker.upper()}\n"
        f"5m Closed Price: {price:.2f}\n"
        f"Zone Range: {zone['low']} - {zone['high']}\n"
        f"Zone Type: {zone['type']}\n"
        f"Touches: {zone['touches']}"
    )

    send_message_with_image(message, image_path)
    print(f"Sent {timeframe} alert for {ticker.upper()} -> {zone['low']} - {zone['high']}")


def monitor_tickers_and_alert(
    tickers: list[str],
    max_workers: int | None = None,
    daily_folder: str = "daily-zone-data",
    weekly_folder: str = "weekly-zone-data",
) -> list[dict]:
    """
    One monitoring pass:
    - multiprocess all tickers
    - compare latest closed 5m price vs saved zones
    - send Discord alert with image on hit
    """
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

                if result["matches"]:
                    for match in result["matches"]:
                        _send_zone_hit_alert(
                            ticker=result["ticker"],
                            price=result["price"],
                            timeframe=match["timeframe"],
                            zone=match["zone"],
                        )
                else:
                    print(f"No zone hit for {ticker.upper()} at price {result['price']:.2f}")

            except Exception as e:
                print(f"Error monitoring {ticker.upper()}: {e}")

    return results




if __name__ == "__main__":
    tickers = [ "tsla", "mu",
     "aapl", "amzn", "amd", "avgo", "asml",
    "googl", "intc", "meta", "msft", "nvda",
    "orcl", "pltr", "rddt", "sndk", "stx","intc"
    ,"nflx","mstr","hood","coin","pltr","baba","uso","xom","xle"]

    # run once at start (build zone JSON)
    print("Generating zone data...")
    save_zone_data_for_tickers(
        tickers=tickers,
        daily_limit= DAILY_LIMIT,
        weekly_limit=WEEKLY_LIMIT,
    )
    print(f"DAILY_LIMIT={DAILY_LIMIT}, WEEKLY_LIMIT={WEEKLY_LIMIT}")
    print("Start monitoring every 5 minutes...\n")

    while True:
        try:
            print(f"Running check at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            monitor_tickers_and_alert(
                tickers=tickers,
                max_workers=len(tickers),
            )

        except Exception as e:
            print("Error in main loop:", e)

        # wait 5 minutes (300 seconds)
        time.sleep(300)