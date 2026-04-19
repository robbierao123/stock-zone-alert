import os
from datetime import datetime, timedelta

import requests
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


API_KEY = os.getenv("FMP_API_KEY")
BASE_URL = "https://financialmodelingprep.com/stable/historical-chart/1hour"


def fetch_1hour_data(symbol: str, months: int = 4) -> pd.DataFrame:
    if not API_KEY:
        raise ValueError("Missing FMP_API_KEY environment variable")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=30 * months)

    url = (
        f"{BASE_URL}"
        f"?symbol={symbol}"
        f"&from={start_date:%Y-%m-%d}"
        f"&to={end_date:%Y-%m-%d}"
        f"&apikey={API_KEY}"
    )

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, list):
        raise ValueError(f"Unexpected response: {data}")

    if not data:
        raise ValueError("No data returned from FMP")

    df = pd.DataFrame(data)

    required_cols = {"date", "open", "high", "low", "close"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing expected columns: {missing}")

    if "volume" not in df.columns:
        df["volume"] = 0

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    return df


def add_vegas_emas(df: pd.DataFrame) -> pd.DataFrame:
    for ema_len in [144, 169, 288, 338]:
        df[f"EMA{ema_len}"] = df["close"].ewm(span=ema_len, adjust=False).mean()
    return df


def build_time_labels(df: pd.DataFrame, num_labels: int = 8):
    total = len(df)
    if total == 0:
        return [], []

    step = max(total // num_labels, 1)
    positions = list(range(0, total, step))

    if positions[-1] != total - 1:
        positions.append(total - 1)

    labels = []
    for i in positions:
        dt = df.iloc[i]["date"]
        labels.append(dt.strftime("%b %d"))

    return positions, labels


def plot_candles(ax, df):
    up_color = "#19b394"
    down_color = "#ef5350"

    candle_width = 0.72
    wick_width = 1.0

    for i, row in df.iterrows():
        o = row["open"]
        h = row["high"]
        l = row["low"]
        c = row["close"]

        color = up_color if c >= o else down_color

        # wick
        ax.vlines(i, l, h, color=color, linewidth=wick_width, zorder=2)

        # body
        body_low = min(o, c)
        body_height = abs(c - o)

        if body_height < 0.03:
            body_height = 0.03

        rect = Rectangle(
            (i - candle_width / 2, body_low),
            candle_width,
            body_height,
            facecolor=color,
            edgecolor=color,
            linewidth=0.6,
            zorder=3
        )
        ax.add_patch(rect)


def plot_volume(ax, df):
    up_color = "#19b394"
    down_color = "#ef5350"

    colors = [
        up_color if c >= o else down_color
        for o, c in zip(df["open"], df["close"])
    ]

    volume_m = df["volume"] / 1_000_000.0
    ax.bar(range(len(df)), volume_m, color=colors, width=0.72, alpha=0.30)

    ax.set_yticks([])
    ax.set_ylabel("Vol", fontsize=9, color="#777777")


def plot_vegas(symbol: str, df: pd.DataFrame, output_path: str) -> None:
    fig = plt.figure(figsize=(16, 8), facecolor="white")
    gs = fig.add_gridspec(5, 1, hspace=0.0)

    ax_price = fig.add_subplot(gs[:4, 0])
    ax_vol = fig.add_subplot(gs[4, 0], sharex=ax_price)

    # background
    for ax in [ax_price, ax_vol]:
        ax.set_facecolor("white")
        ax.grid(True, color="#e6e6e6", linewidth=0.8, alpha=0.9)
        ax.spines["top"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.spines["right"].set_color("#cccccc")
        ax.spines["bottom"].set_color("#cccccc")
        ax.yaxis.tick_right()
        ax.tick_params(axis="both", labelsize=10, colors="#666666")

    # candles + volume
    plot_candles(ax_price, df)
    plot_volume(ax_vol, df)

    # EMA colors closer to trading chart feel
    ax_price.plot(df.index, df["EMA144"], color="#4A90E2", linewidth=0.9, label="EMA144")
    ax_price.plot(df.index, df["EMA169"], color="#E67E5F", linewidth=0.9, label="EMA169")
    ax_price.plot(df.index, df["EMA288"], color="#6FB7C8", linewidth=0.9, label="EMA288")
    ax_price.plot(df.index, df["EMA338"], color="#6C63FF", linewidth=0.9, label="EMA338")

    # right-side padding
    ax_price.set_xlim(-1, len(df) + 2)

    # tighter y range
    price_min = df["low"].min()
    price_max = df["high"].max()
    pad = (price_max - price_min) * 0.08
    ax_price.set_ylim(price_min - pad, price_max + pad)

    # title
    last = df.iloc[-1]
    ax_price.set_title(
        f"{symbol} 1H  O {last['open']:.2f}  H {last['high']:.2f}  L {last['low']:.2f}  C {last['close']:.2f}",
        fontsize=13,
        pad=12
    )

    # compact legend
    ax_price.legend(
        loc="upper left",
        frameon=False,
        fontsize=10,
        ncol=4,
        handlelength=1.6,
        handletextpad=0.5
    )

    # x labels
    positions, labels = build_time_labels(df, num_labels=8)
    ax_vol.set_xticks(positions)
    ax_vol.set_xticklabels(labels, rotation=0)

    plt.setp(ax_price.get_xticklabels(), visible=False)

    # remove margins that waste space
    ax_price.margins(x=0)
    ax_vol.margins(x=0)

    plt.tight_layout()
    plt.savefig(output_path, dpi=220, bbox_inches="tight")
    plt.close()


def main():
    symbol = "SPY"

    current_file_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(os.path.dirname(current_file_dir), "VEGAS-CHART")
    os.makedirs(output_dir, exist_ok=True)

    output_file = os.path.join(output_dir, f"{symbol}_vegas_1h.png")

    df = fetch_1hour_data(symbol, months=4)
    df = add_vegas_emas(df)
    plot_vegas(symbol, df, output_file)

    print(f"Saved chart to: {output_file}")


if __name__ == "__main__":
    main()