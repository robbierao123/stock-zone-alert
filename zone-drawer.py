import pandas as pd
import mplfinance as mpf

from zone import (
    get_daily_ohlc_3m,
    detect_zones_from_daily,
    detect_zones_from_weekly,
    convert_daily_to_weekly,
)


def _get_zone_color(zone_type: str) -> str:
    if zone_type == "resistance":
        return "#e57373"   # softer red but visible
    if zone_type == "support":
        return "#81c784"   # soft green but visible
    if zone_type in ["support&resust", "support&resistance"]:
        return "#ffb74d"   # orange highlight
    return "blue"


def _draw_chart_from_candles(candles: list[dict], zones: list[dict], title: str, save_path: str | None = None):
    df = pd.DataFrame(candles)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)

    fill_between = []
    for z in zones:
        zone_type = z["type"]

        # 🔥 stronger colors (not pastel)
        if zone_type == "resistance":
            color = "#e74c3c"   # strong red
            alpha = 0.25
        elif zone_type == "support":
            color = "#2ecc71"   # strong green
            alpha = 0.25
        elif zone_type in ["support&resust", "support&resistance"]:
            color = "#f39c12"   # orange = important overlap
            alpha = 0.30
        else:
            color = "#95a5a6"
            alpha = 0.20

        fill_between.append(
            dict(
                y1=z["low"],
                y2=z["high"],
                color=color,
                alpha=alpha
            )
        )
    # softer colors (closer to Yahoo)
    mc = mpf.make_marketcolors(
        up="#26a69a",        # soft teal green
        down="#ef5350",      # soft red
        edge="inherit",
        wick="inherit",
        volume="inherit"
    )

    s = mpf.make_mpf_style(
        marketcolors=mc,
        gridstyle="",        # no grid
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


def draw_daily_zones_chart(ticker: str, limit: int = 90, save_path: str | None = None):
    daily_candles = get_daily_ohlc_3m(ticker, limit=limit)
    daily_zones = detect_zones_from_daily(daily_candles)

    _draw_chart_from_candles(
        candles=daily_candles,
        zones=daily_zones,
        title=f"{ticker} Daily Zones",
        save_path=save_path
    )


def draw_weekly_zones_chart(ticker: str, limit: int = 250, save_path: str | None = None):
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


if __name__ == "__main__":
    ticker = "uso"

    # Daily chart
    draw_daily_zones_chart(ticker, limit=45)

    # Weekly chart
    draw_weekly_zones_chart(ticker, limit=250)