import pandas as pd
import os
import mplfinance as mpf
from pathlib import Path
from dotenv import load_dotenv
from zone import (
    get_daily_ohlc_3m,
    detect_zones_from_daily,
    detect_zones_from_weekly,
    convert_daily_to_weekly,
)


def _get_zone_color(zone_type: str) -> str:
    if zone_type == "resistance":
        return "#e57373"
    if zone_type == "support":
        return "#81c784"
    if zone_type in ["support&resust", "support&resistance"]:
        return "#ffb74d"
    return "blue"


def _draw_chart_from_candles(
    candles: list[dict],
    zones: list[dict],
    title: str,
    save_path: str | None = None
):
    df = pd.DataFrame(candles)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)

    fill_between = []
    for z in zones:
        zone_type = z["type"]

        if zone_type == "resistance":
            color = "#e74c3c"
            alpha = 0.20
        elif zone_type == "support":
            color = "#2ecc71"
            alpha = 0.20
        elif zone_type in ["support&resust", "support&resistance"]:
            color = "#f39c12"
            alpha = 0.24
        else:
            color = "#95a5a6"
            alpha = 0.18

        fill_between.append(
            dict(
                y1=z["low"],
                y2=z["high"],
                color=color,
                alpha=alpha
            )
        )

    mc = mpf.make_marketcolors(
        up="#26a69a",
        down="#ef5350",
        edge="inherit",
        wick="inherit",
        volume="inherit"
    )

    s = mpf.make_mpf_style(
        marketcolors=mc,
        gridstyle="",
        facecolor="white",
        figcolor="white",
        edgecolor="white",
        rc={
            "axes.spines.left": False,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "axes.spines.bottom": False,
        }
    )

    kwargs = dict(
        type="candle",
        style=s,
        volume=True,
        title=title,
        figsize=(14, 8),
    )

    if fill_between:
        kwargs["fill_between"] = fill_between

    if save_path:
        kwargs["savefig"] = save_path

    mpf.plot(df, **kwargs)


def draw_daily_zones_chart(
    ticker: str,
    limit: int = 90,
    save_path: str | None = None
):
    daily_candles = get_daily_ohlc_3m(ticker, limit=limit)
    daily_zones = detect_zones_from_daily(daily_candles)

    _draw_chart_from_candles(
        candles=daily_candles,
        zones=daily_zones,
        title=f"{ticker} Daily Zones",
        save_path=save_path
    )


def draw_weekly_zones_chart(
    ticker: str,
    limit: int = 250,
    save_path: str | None = None
):
    daily_candles = get_daily_ohlc_3m(ticker, limit=limit)
    weekly_candles = convert_daily_to_weekly(daily_candles)

    weekly_zones = detect_zones_from_weekly(
        weekly_candles,
        tolerance_pct=0.0075,
        min_touches=2,
        overlap_min_touches=2
    )

    _draw_chart_from_candles(
        candles=weekly_candles,
        zones=weekly_zones,
        title=f"{ticker} Weekly Zones",
        save_path=save_path
    )


def _get_chart_folder() -> Path:
    chart_dir = Path("chart")
    chart_dir.mkdir(parents=True, exist_ok=True)
    return chart_dir


def save_daily_zones_chart(ticker: str, limit: int = 90) -> str:
    chart_dir = _get_chart_folder()
    save_path = chart_dir / f"{ticker.lower()}_daily.png"

    draw_daily_zones_chart(
        ticker=ticker,
        limit=limit,
        save_path=str(save_path)
    )

    return str(save_path)


def save_weekly_zones_chart(ticker: str, limit: int = 250) -> str:
    chart_dir = _get_chart_folder()
    save_path = chart_dir / f"{ticker.lower()}_weekly.png"

    draw_weekly_zones_chart(
        ticker=ticker,
        limit=limit,
        save_path=str(save_path)
    )

    return str(save_path)


if __name__ == "__main__":
    tickers = [ "tsla", "mu",
     "aapl", "amzn", "amd", "avgo", "asml",
    "googl", "intc", "meta", "msft", "nvda",
    "orcl", "pltr", "rddt", "sndk", "stx","intc"
    ,"nflx","mstr","hood","coin","pltr","baba","uso","xom","xle","hood"]

    for ticker in tickers:
        try:
            daily_path = save_daily_zones_chart(ticker, limit=45)
            print(f"Saved daily chart: {daily_path}")

            weekly_path = save_weekly_zones_chart(ticker, limit=250)
            print(f"Saved weekly chart: {weekly_path}")

        except Exception as e:
            print(f"Error drawing charts for {ticker.upper()}: {e}")