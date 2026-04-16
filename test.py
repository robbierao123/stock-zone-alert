import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# 🔧 IMPORT your actual function
from dashboard import (
    _get_recent_5m_bars_cached,
    _fetch_recent_5m_bars,
    FIVE_MIN_CACHE
)

# 🔥 monkey patch counter
CALL_COUNT = {"count": 0}

def fake_fetch(ticker: str, limit: int = 1000):
    CALL_COUNT["count"] += 1
    print(f"[API CALLED] {ticker} | total calls: {CALL_COUNT['count']}")

    # return fake bars
    return [
        {"date": "2026-01-01 09:30:00", "volume": 100},
        {"date": "2026-01-01 09:35:00", "volume": 200},
        {"date": "2026-01-01 09:40:00", "volume": 300},
    ]


# 🔥 override actual API function
import dashboard
dashboard._fetch_recent_5m_bars = fake_fetch


# 🔧 mock time control
CURRENT_TIME = {"now": datetime(2026, 1, 1, 9, 31, tzinfo=ZoneInfo("America/New_York"))}

def fake_bucket():
    now = CURRENT_TIME["now"]
    return (now.year, now.month, now.day, now.hour, now.minute // 5)

dashboard._current_5m_bucket = fake_bucket


# 🧪 TEST FUNCTION
def run_test():
    ticker = "nvda"

    print("\n--- TEST START ---\n")

    # 1️⃣ First call → should fetch
    print("Step 1: First call (expect API)")
    _get_recent_5m_bars_cached(ticker)

    # 2️⃣ Same bucket → should NOT fetch
    print("\nStep 2: Same bucket (expect NO API)")
    CURRENT_TIME["now"] += timedelta(seconds=30)
    _get_recent_5m_bars_cached(ticker)

    # 3️⃣ Still same bucket → no fetch
    print("\nStep 3: Still same bucket (expect NO API)")
    CURRENT_TIME["now"] += timedelta(seconds=60)
    _get_recent_5m_bars_cached(ticker)

    # 4️⃣ Move to next 5-min bucket → SHOULD fetch
    print("\nStep 4: New bucket (expect API)")
    CURRENT_TIME["now"] = datetime(2026, 1, 1, 9, 35, tzinfo=ZoneInfo("America/New_York"))
    _get_recent_5m_bars_cached(ticker)

    # 5️⃣ Same new bucket → no fetch
    print("\nStep 5: Same new bucket (expect NO API)")
    CURRENT_TIME["now"] += timedelta(seconds=20)
    _get_recent_5m_bars_cached(ticker)

    print("\n--- TEST END ---")
    print(f"\nTotal API calls: {CALL_COUNT['count']}")


if __name__ == "__main__":
    run_test()