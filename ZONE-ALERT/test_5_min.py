import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from dashboard import (
    _get_recent_5m_bars_cached,
    _get_latest_5m_volume_ratio_from_bars,
)


TICKERS = [
        "tsla", "mu", "aapl", "amzn", "amd", "avgo", "asml",
        "googl", "intc", "meta", "msft", "nvda", "orcl",
        "pltr", "nflx", "mstr", "hood", "coin", "hood",
        "QBTS", "BABA"
    ]


# ---------- helpers ----------

def get_distinct_days(bars):
    groups = defaultdict(list)

    for bar in bars:
        dt = bar.get("date", "")
        if " " not in dt:
            continue
        day = dt.split(" ")[0]
        groups[day].append(bar)

    return sorted(groups.keys())


def check_ticker(ticker):
    try:
        bars = _get_recent_5m_bars_cached(ticker, limit=1000)
        days = get_distinct_days(bars)

        vol = _get_latest_5m_volume_ratio_from_bars(bars, ticker)

        if vol is None:
            return {
                "ticker": ticker,
                "status": "FILTERED",
                "received_days": len(days),
            }

        return {
            "ticker": ticker,
            "status": "OK",
            "received_days": len(days),
            "used_days": vol.get("days_count", 0),
            "ratio": vol.get("ratio", 0),
        }

    except Exception as e:
        return {
            "ticker": ticker,
            "status": "ERROR",
            "error": str(e),
        }


# ---------- test modes ----------

def run_parallel():
    print("\n=== PARALLEL TEST ===\n")

    results = []

    with ThreadPoolExecutor(max_workers=len(TICKERS)) as executor:
        futures = [executor.submit(check_ticker, t) for t in TICKERS]

        for f in as_completed(futures):
            res = f.result()
            results.append(res)

            print_result(res)

    return results


def run_sequential():
    print("\n=== SEQUENTIAL TEST ===\n")

    results = []

    for ticker in TICKERS:
        res = check_ticker(ticker)
        results.append(res)

        print_result(res)

        # small delay to simulate clean spacing
        time.sleep(0.2)

    return results


# ---------- output ----------

def print_result(res):
    if res["status"] == "OK":
        print(
            f"OK       | {res['ticker'].upper():<5} | "
            f"days={res['received_days']} | "
            f"used={res['used_days']} | "
            f"ratio={res['ratio']:.2f}"
        )

    elif res["status"] == "FILTERED":
        print(
            f"FILTERED | {res['ticker'].upper():<5} | "
            f"days={res['received_days']}"
        )

    else:
        print(
            f"ERROR    | {res['ticker'].upper():<5} | "
            f"{res['error']}"
        )


# ---------- main ----------

if __name__ == "__main__":
    parallel_results = run_parallel()

    print("\nSleeping before sequential test...\n")
    time.sleep(3)

    sequential_results = run_sequential()

    print("\n=== DONE ===")