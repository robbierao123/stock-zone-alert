from zone_drawer import save_daily_zones_chart, save_weekly_zones_chart
from SendAlert import send_ticker_charts


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


if __name__ == "__main__":
    ticker = "spy"
    run_for_ticker(ticker, daily_limit=45, weekly_limit=250)