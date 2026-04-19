from dashboard import (
    build_previous_day_levels_cache,
    PREV_DAY_LEVELS_CACHE,
    _get_recent_5m_bars_cached,
    _get_latest_closed_5m_bar_from_bars,
    _get_latest_5m_volume_ratio_from_bars,
    _find_recent_break,
    BREAK_MAX_PCT,
)
from zone import get_live_price_full


def run_test() -> None:
    ticker = "msft"

    # build prev-day cache for MSFT only
    PREV_DAY_LEVELS_CACHE.clear()
    PREV_DAY_LEVELS_CACHE.update(build_previous_day_levels_cache([ticker]))

    prev_day = PREV_DAY_LEVELS_CACHE[ticker]
    prev_high = float(prev_day["high"])
    prev_low = float(prev_day["low"])

    live_price = get_live_price_full(ticker)
    bars = _get_recent_5m_bars_cached(ticker, limit=1000)
    latest_closed_bar = _get_latest_closed_5m_bar_from_bars(bars, ticker)
    volume_ratio = _get_latest_5m_volume_ratio_from_bars(bars, ticker)
    recent_break = _find_recent_break(ticker, live_price, bars)

    pct = BREAK_MAX_PCT / 100.0
    upper_limit = prev_high * (1 + pct)
    lower_limit = prev_low * (1 - pct)

    print("\n=== MSFT BREAK TEST ===")
    print(f"Ticker: {ticker.upper()}")
    print(f"Prev Day Date : {prev_day['date']}")
    print(f"Prev Day High : {prev_high:.2f}")
    print(f"Prev Day Low  : {prev_low:.2f}")
    print(f"Live Price    : {live_price:.2f}")
    print(f"Upper Limit   : {upper_limit:.2f}")
    print(f"Lower Limit   : {lower_limit:.2f}")
    print()

    print("Latest closed 5m bar:")
    print(f"  Date   : {latest_closed_bar['date']}")
    print(f"  Open   : {latest_closed_bar['open']:.2f}")
    print(f"  High   : {latest_closed_bar['high']:.2f}")
    print(f"  Low    : {latest_closed_bar['low']:.2f}")
    print(f"  Close  : {latest_closed_bar['close']:.2f}")
    print(f"  Volume : {latest_closed_bar['volume']:.0f}")
    print()

    print("Volume ratio:")
    print(f"  Ratio      : {volume_ratio['ratio']:.2f}")
    print(f"  Latest Vol : {volume_ratio['latest_volume']:.0f}")
    print(f"  Avg Vol    : {volume_ratio['avg_volume']:.0f}")
    print(f"  Bar Time   : {volume_ratio['bar_time']}")
    print(f"  Days Used  : {', '.join(volume_ratio['days_used'])}")
    print()

    broke_high = latest_closed_bar["high"] > prev_high
    broke_low = latest_closed_bar["low"] < prev_low
    still_near_high = live_price <= upper_limit
    still_near_low = live_price >= lower_limit

    print("Direct comparisons:")
    print(f"  Live > Prev High?         : {live_price > prev_high}")
    print(f"  Live < Prev Low?          : {live_price < prev_low}")
    print(f"  5m High > Prev High?      : {broke_high}")
    print(f"  5m Low < Prev Low?        : {broke_low}")
    print(f"  Live <= Upper Limit?      : {still_near_high}")
    print(f"  Live >= Lower Limit?      : {still_near_low}")
    print()

    print("Recent break result:")
    print(recent_break if recent_break else "None")
    print("========================\n")


if __name__ == "__main__":
    run_test()