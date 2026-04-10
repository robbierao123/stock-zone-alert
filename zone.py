import requests
from datetime import datetime, timedelta




import requests

API_KEY = "1i7Eb4jjz6vJmKKxO1s4iUytVA6KDI3V"

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


def detect_zones_from_daily(candles: list[dict], tolerance_pct: float = 0.002, min_touches: int = 3):
    """
    Detect support/resistance zones from daily candles.

    - Resistance: high wicks
    - Support: low wicks
    - Merge levels within tolerance
    - Keep zones with >= min_touches
    """

    highs = [{"price": c["high"], "type": "resistance"} for c in candles]
    lows = [{"price": c["low"], "type": "support"} for c in candles]

    def cluster(levels):
        clusters = []

        for lvl in sorted(levels, key=lambda x: x["price"]):
            placed = False

            for c in clusters:
                # use average price of cluster (no mid stored)
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

    resistance = cluster(highs)
    support = cluster(lows)

    return support + resistance




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




data = get_daily_ohlc_3m("AMD", limit=90)

zones = detect_zones_from_daily(data)

for z in zones:
    print(z)

# price = get_live_price_full("AAPL")
# print(price)


