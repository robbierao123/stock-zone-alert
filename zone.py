import requests
from datetime import datetime, timedelta
import os
import requests
from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv("FMP_API_KEY")
TOP_PICK = int(os.getenv("TOP_PICK", 5))
def get_daily_ohlc_3m(ticker: str, limit: int = 90) -> list[dict]:
    url = "https://financialmodelingprep.com/stable/historical-price-eod/full"
    params = {
        "symbol": ticker,
        "apikey": API_KEY
    }

    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    if not isinstance(data, list) or not data:
        raise ValueError(f"No data returned for {ticker}: {data}")

    rows = data[:limit]
    rows.reverse()

    result = []
    for row in rows:
        result.append({
            "date": row["date"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0))
        })

    return result


def detect_zones_from_daily(
    candles: list[dict],
    tolerance_pct: float = 0.002,
    min_touches: int = 2,
    overlap_min_touches: int = 2,
    pivot_left: int = 3,
    pivot_right: int = 3,
    top_pick: int = TOP_PICK
):
    highs = [{"price": c["high"], "type": "resistance"} for c in candles]
    lows = [{"price": c["low"], "type": "support"} for c in candles]

    def cluster(levels):
        clusters = []

        for lvl in sorted(levels, key=lambda x: x["price"]):
            placed = False

            for c in clusters:
                avg_price = sum(c["prices"]) / len(c["prices"])

                if abs(lvl["price"] - avg_price) / avg_price <= tolerance_pct:
                    c["prices"].append(lvl["price"])
                    c["touches"] += 1
                    placed = True
                    break

            if not placed:
                clusters.append({
                    "type": lvl["type"],
                    "prices": [lvl["price"]],
                    "touches": 1
                })

        zones = []
        for c in clusters:
            if c["touches"] >= min_touches:
                zones.append({
                    "type": c["type"],
                    "low": round(min(c["prices"]), 2),
                    "high": round(max(c["prices"]), 2),
                    "touches": c["touches"]
                })

        return zones

    def build_support_resist_zones():
        swing_high_wicks = []
        swing_low_wicks = []

        for i in range(pivot_left, len(candles) - pivot_right):
            current = candles[i]
            current_high = current["high"]
            current_low = current["low"]

            is_swing_high = all(
                current_high > candles[j]["high"]
                for j in range(i - pivot_left, i + pivot_right + 1)
                if j != i
            )

            is_swing_low = all(
                current_low < candles[j]["low"]
                for j in range(i - pivot_left, i + pivot_right + 1)
                if j != i
            )

            if is_swing_high:
                body_top = max(current["open"], current["close"])
                wick_low = body_top
                wick_high = current["high"]

                if wick_high > wick_low:
                    swing_high_wicks.append({
                        "low": float(wick_low),
                        "high": float(wick_high),
                        "date": current["date"]
                    })

            if is_swing_low:
                body_bottom = min(current["open"], current["close"])
                wick_low = current["low"]
                wick_high = body_bottom

                if wick_high > wick_low:
                    swing_low_wicks.append({
                        "low": float(wick_low),
                        "high": float(wick_high),
                        "date": current["date"]
                    })

        overlap_levels = []

        for high_wick in swing_high_wicks:
            for low_wick in swing_low_wicks:
                overlap_low = max(high_wick["low"], low_wick["low"])
                overlap_high = min(high_wick["high"], low_wick["high"])

                if overlap_low <= overlap_high:
                    overlap_levels.append({
                        "price": (overlap_low + overlap_high) / 2,
                        "range_low": overlap_low,
                        "range_high": overlap_high,
                        "type": "support&resust"
                    })

        clusters = []

        for lvl in sorted(overlap_levels, key=lambda x: x["price"]):
            placed = False

            for c in clusters:
                avg_price = sum(c["prices"]) / len(c["prices"])

                if abs(lvl["price"] - avg_price) / avg_price <= tolerance_pct:
                    c["prices"].append(lvl["price"])
                    c["range_lows"].append(lvl["range_low"])
                    c["range_highs"].append(lvl["range_high"])
                    c["touches"] += 1
                    placed = True
                    break

            if not placed:
                clusters.append({
                    "type": "support&resust",
                    "prices": [lvl["price"]],
                    "range_lows": [lvl["range_low"]],
                    "range_highs": [lvl["range_high"]],
                    "touches": 1
                })

        zones = []
        for c in clusters:
            if c["touches"] >= overlap_min_touches:
                zones.append({
                    "type": c["type"],
                    "low": round(min(c["range_lows"]), 2),
                    "high": round(max(c["range_highs"]), 2),
                    "touches": c["touches"]
                })

        return zones

    resistance = cluster(highs)
    support = cluster(lows)
    _support_resist = build_support_resist_zones()  # calculated but excluded

    normal_zones = support + resistance

    # keep only top_pick non-overlap zones by touches
    normal_zones = sorted(
        normal_zones,
        key=lambda z: (z["touches"], -(z["high"] - z["low"])),
        reverse=True
    )[:top_pick]

    return normal_zones

def convert_daily_to_weekly(daily_candles: list[dict]) -> list[dict]:
    """
    Convert daily OHLCV candles into weekly OHLCV candles.
    Assumes daily_candles are sorted oldest -> newest.
    """

    weekly = []
    current_week = None

    for candle in daily_candles:
        dt = datetime.strptime(candle["date"], "%Y-%m-%d")
        year, week_num, _ = dt.isocalendar()
        week_key = (year, week_num)

        if current_week is None or current_week["week_key"] != week_key:
            if current_week is not None:
                weekly.append({
                    "date": current_week["date"],
                    "open": current_week["open"],
                    "high": current_week["high"],
                    "low": current_week["low"],
                    "close": current_week["close"],
                    "volume": current_week["volume"]
                })

            current_week = {
                "week_key": week_key,
                "date": candle["date"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle["volume"]
            }
        else:
            current_week["high"] = max(current_week["high"], candle["high"])
            current_week["low"] = min(current_week["low"], candle["low"])
            current_week["close"] = candle["close"]
            current_week["volume"] += candle["volume"]

    if current_week is not None:
        weekly.append({
            "date": current_week["date"],
            "open": current_week["open"],
            "high": current_week["high"],
            "low": current_week["low"],
            "close": current_week["close"],
            "volume": current_week["volume"]
        })

    return weekly
def detect_zones_from_weekly(
    candles: list[dict],
    tolerance_pct: float = 0.002,
    min_touches: int = 3,
    overlap_min_touches: int = 2,
    pivot_left: int = 3,
    pivot_right: int = 3
):
    """
    Same logic as detect_zones_from_daily(), but for weekly candles.
    """

    return detect_zones_from_daily(
        candles=candles,
        tolerance_pct=tolerance_pct,
        min_touches=min_touches,
        overlap_min_touches=overlap_min_touches,
        pivot_left=pivot_left,
        pivot_right=pivot_right
    )
def get_live_price_full(ticker: str) -> float:
    """
    Get best available price (regular + aftermarket)
    """

    # try aftermarket first
    url = "https://financialmodelingprep.com/stable/aftermarket-quote"
    params = {
        "symbol": ticker,
        "apikey": API_KEY
    }

    # r = requests.get(url, params=params, timeout=10)
    # r.raise_for_status()

    # data = r.json()

    # if data and isinstance(data, list):
    #     price = data[0].get("price")

    #     if price:
    #         return float(price)

    # fallback to regular quote
    url = "https://financialmodelingprep.com/stable/quote"
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()

    data = r.json()

    if not data:
        raise ValueError("No price data")

    return float(data[0]["price"])



def get_latest_closed_5m_price(ticker: str) -> float:
    """
    Return the latest closed 5-minute candle close price for a ticker.
    """

    url = "https://financialmodelingprep.com/stable/historical-chart/5min"
    params = {
        "symbol": ticker,
        "apikey": API_KEY
    }

    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    if not isinstance(data, list) or not data:
        raise ValueError(f"No 5-minute data returned for {ticker}: {data}")

    latest_bar = data[0]

    if "close" not in latest_bar:
        raise ValueError(f"Missing close price in latest 5-minute bar for {ticker}: {latest_bar}")

    return float(latest_bar["close"])



if __name__ == "__main__":

    price = get_latest_closed_5m_price("googl")
    print(price)
    # data = get_daily_ohlc_3m("tsla", limit=20)
    # zones = detect_zones_from_daily(data)

    # for z in zones:
    #     print(z)

    # daily_data = get_daily_ohlc_3m("AMD", limit=90)
    # weekly_data = convert_daily_to_weekly(daily_data)

    # weekly_zones = detect_zones_from_weekly(
    #     weekly_data,
    #     tolerance_pct=0.0075,
    #     min_touches=2,
    #     overlap_min_touches=2
    # )
