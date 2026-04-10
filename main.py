from zone_drawer import save_daily_zones_chart, save_weekly_zones_chart
from SendAlert import send_ticker_charts
from zone import *
from pathlib import Path
import json

def run_for_ticker(ticker: str, daily_limit: int = 90, weekly_limit: int = 250) -> None:
    # 1. generate and save images
    daily_path = save_daily_zones_chart(ticker, limit=daily_limit)
    weekly_path = save_weekly_zones_chart(ticker, limit=weekly_limit)

    print(f"Saved daily chart: {daily_path}")
    print(f"Saved weekly chart: {weekly_path}")

    # 2. send to discord
    send_ticker_charts(
        ticker=ticker,
        extra_message="Latest zones generated"
    )

import json


from zone import (
    get_daily_ohlc_3m,
    detect_zones_from_daily,
    detect_zones_from_weekly,
    convert_daily_to_weekly,
)


def _ensure_folder(folder_name: str) -> Path:
    folder = Path(folder_name)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_zone_data_for_ticker(
    ticker: str,
    daily_limit: int = 90,
    weekly_limit: int = 250,
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

    # Daily
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

    # Weekly
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
    daily_limit: int = 90,
    weekly_limit: int = 250,
    daily_folder: str = "daily-zone-data",
    weekly_folder: str = "weekly-zone-data",
) -> dict[str, dict]:
    """
    Generate daily + weekly zone JSON files for a list of tickers.

    Returns:
        {
            "SPY": {
                "daily_json": "...",
                "weekly_json": "...",
                "status": "ok"
            },
            "TSLA": {
                "status": "error",
                "error": "..."
            }
        }
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

from pathlib import Path


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
                    # optional: delete subfolders too
                    import shutil
                    shutil.rmtree(file)

            except Exception as e:
                print(f"Failed to delete {file}: {e}")

        print(f"Cleared folder: {folder_name}")



if __name__ == "__main__":
    # ticker = "qqq"
    # run_for_ticker(ticker, daily_limit=45, weekly_limit=250)

    # price = get_latest_closed_5m_price("tsla")
    # print(price)

    # tickers = ["spy", "qqq", "tsla", "mu"]

    # results = save_zone_data_for_tickers(
    #         tickers=tickers,
    #         daily_limit=45,
    #         weekly_limit=250,
    #     )

    # print(json.dumps(results, indent=2))
    clear_zone_data()