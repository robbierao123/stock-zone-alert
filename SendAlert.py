import requests
from pathlib import Path
from dotenv import load_dotenv
import os

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


def send_message(message: str) -> None:
    response = requests.post(
        WEBHOOK_URL,
        json={"content": message},
        timeout=10,
    )
    response.raise_for_status()
    print("Sent message")


def send_message_with_image(message: str, file_path: str) -> None:
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Image not found: {file_path}")

    with path.open("rb") as f:
        response = requests.post(
            WEBHOOK_URL,
            data={"content": message},
            files={"file": (path.name, f, "image/png")},
            timeout=20,
        )

    response.raise_for_status()
    print(f"Sent image: {path.name}")


def send_ticker_charts(ticker: str, extra_message: str = "") -> None:
    ticker = ticker.lower()

    daily_path = Path("chart") / f"{ticker}_daily.png"
    weekly_path = Path("chart") / f"{ticker}_weekly.png"

    if not daily_path.exists() and not weekly_path.exists():
        raise FileNotFoundError(
            f"No chart images found for {ticker}. Expected {daily_path} and/or {weekly_path}"
        )

    if daily_path.exists():
        msg = f"📊 {ticker.upper()} Daily Chart"
        if extra_message:
            msg += f"\n{extra_message}"
        send_message_with_image(msg, str(daily_path))

    if weekly_path.exists():
        msg = f"📈 {ticker.upper()} Weekly Chart"
        if extra_message:
            msg += f"\n{extra_message}"
        send_message_with_image(msg, str(weekly_path))


if __name__ == "__main__":
    # simple test message
    send_message("🚀 小助手第2图")

    # send one saved image
    # send_message_with_image("📊 SPY Daily Chart", "chart/spy_daily.png")

    # send both daily + weekly if they exist
    send_ticker_charts("spy", extra_message="Zone charts ready")