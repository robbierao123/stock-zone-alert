import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("FMP_API_KEY")


def _is_noise_article(title: str, site: str) -> bool:
    """
    Return True if the article looks like low-value / noisy content.
    """
    t = (title or "").lower().strip()
    s = (site or "").lower().strip()

    noise_sites = {
        "youtube.com",
    }

    if s in noise_sites:
        return True

    noise_patterns = [
        r"\btop\s+\d+\b",                     # Top 3, Top 5, etc.
        r"\bbest\b.*\bstock",                # best stock / best AI stock
        r"\bshould you buy\b",
        r"\breason to buy\b",
        r"\bstocks to buy\b",
        r"\bai stocks to hold\b",
        r"\bi love\b",
        r"\bi don't like\b",
        r"\bprediction:\b",
        r"\bhonest report card\b",
        r"\btrade strategy\b",
        r"\bforecasts?\b",
        r"\bworth the wait\b",
        r"\beveryone'?s wrong\b",
        r"\bgoes higher\b",
        r"\bmag 7\b",
        r"\bappleverse\b",
        r"\bperfect companies underperform\b",
        r"\bcash-generating machines\b",
        r"\bdividend yields\b",
        r"\bdcf analysis\b",
        r"\bintrinsic value\b",
        r"\bchallenging environment for malls\b",
        r"\bfoldable iphone\b",              # remove foldable speculation
        r"\bfoldable phone\b",
        r"\bhinge cringe\b",
        r"\bdelay concerns\b",
        r"\btimeline holds\b",
        r"\bflop or strategic stop\b",
        r"\boptions trade\b",
        r"\bapril fools\b",
    ]

    return any(re.search(pattern, t) for pattern in noise_patterns)


def _article_score(title: str, site: str) -> int:
    """
    Higher score = more valuable.
    """
    t = (title or "").lower().strip()
    s = (site or "").lower().strip()

    score = 0

    # Source weighting
    high_value_sites = {
        "reuters.com": 5,
        "cnbc.com": 4,
        "businesswire.com": 4,
        "techcrunch.com": 3,
        "seekingalpha.com": 2,
        "benzinga.com": 2,
        "feeds.benzinga.com": 2,
        "gurufocus.com": 2,
        "fastcompany.com": 2,
        "proactiveinvestors.com": 2,
        "barrons.com": 3,
        "investopedia.com": 2,
    }
    score += high_value_sites.get(s, 0)

    # High-value themes
    strong_patterns = [
        r"\bearnings\b",
        r"\bmiss\b",
        r"\bbeat\b",
        r"\bguidance\b",
        r"\bsales\b",
        r"\bshipments\b",
        r"\bstore closure\b",
        r"\bclosing stores\b",
        r"\bshutter\b",
        r"\bunionized\b",
        r"\bunion busting\b",
        r"\bsmartphone shipments\b",
        r"\bleads global smartphone shipments\b",
        r"\bmemory costs\b",
        r"\bweaker iphone sales\b",
        r"\bbullish call\b",
        r"\binsider trading\b",
        r"\bposition\b",
        r"\bpurchased\b",
        r"\bbuys\b",
        r"\bincreases position\b",
        r"\breduces position\b",
        r"\bstake\b",
        r"\bfines\b",
        r"\badministration\b",
        r"\beu\b.*\bbig tech\b",
    ]
    for pattern in strong_patterns:
        if re.search(pattern, t):
            score += 3

    # Medium-value themes
    medium_patterns = [
        r"\bai\b",
        r"\binfrastructure\b",
        r"\blocal ai\b",
        r"\bcybersecurity\b",
        r"\banthropic\b",
        r"\bwedbush\b",
    ]
    for pattern in medium_patterns:
        if re.search(pattern, t):
            score += 1

    return score


def _filter_and_rank_news(
    news_items: List[Dict[str, Any]],
    top_valuable_news: int = 10,
) -> List[Dict[str, Any]]:
    """
    Remove noisy articles, rank remaining by score, and keep top N.
    """
    kept = []
    seen_titles = set()

    for item in news_items:
        title = (item.get("title") or "").strip()
        site = (item.get("site") or "").strip()

        if not title:
            continue

        normalized_title = re.sub(r"\s+", " ", title.lower())
        if normalized_title in seen_titles:
            continue
        seen_titles.add(normalized_title)

        if _is_noise_article(title, site):
            continue

        score = _article_score(title, site)
        if score <= 0:
            continue

        enriched = dict(item)
        enriched["_score"] = score
        kept.append(enriched)

    kept.sort(
        key=lambda x: (
            x.get("_score", 0),
            x.get("publishedDate") or "",
        ),
        reverse=True,
    )

    return kept[:top_valuable_news]


def news_pdf_on_top_volume_days(
    ticker: str,
    days: int,
    top: int,
    api_key: str,
    news_limit_per_day: int = 50,
    top_valuable_news: int = 10,
) -> List[Dict[str, Any]]:
    """
    Get news for the top highest-volume trading days in the last `days`,
    compute relative volume vs the fixed average daily volume over that same
    lookback window, filter out noisy articles, keep only valuable news,
    and save the result as a PDF under NEWS/.

    Args:
        ticker: Stock ticker, e.g. "AMZN"
        days: Calendar days to look back
        top: Number of highest-volume trading days to include
        api_key: FMP API key
        news_limit_per_day: Max news items to fetch per selected day
        top_valuable_news: Max valuable news items to keep per day

    Returns:
        A list of dicts sorted by date descending (recent -> older).
    """
    if days <= 0:
        raise ValueError("days must be > 0")
    if top <= 0:
        raise ValueError("top must be > 0")
    if top_valuable_news <= 0:
        raise ValueError("top_valuable_news must be > 0")
    if not api_key:
        raise ValueError("api_key is required")

    ticker = ticker.upper().strip()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    news_dir = os.path.join(base_dir, "NEWS")
    os.makedirs(news_dir, exist_ok=True)

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days)

    price_url = "https://financialmodelingprep.com/stable/historical-price-eod/full"
    price_params = {
        "symbol": ticker,
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "apikey": api_key,
    }

    price_resp = requests.get(price_url, params=price_params, timeout=30)
    price_resp.raise_for_status()
    price_data = price_resp.json()

    if not isinstance(price_data, list) or not price_data:
        raise ValueError(f"No price data returned for {ticker}: {price_data}")

    rows: List[Dict[str, Any]] = []
    for row in price_data:
        if "date" not in row or "volume" not in row:
            continue

        try:
            row_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
        except ValueError:
            continue

        open_price = row.get("open")
        close_price = row.get("close")
        volume = row.get("volume", 0) or 0

        rows.append(
            {
                "date": row["date"],
                "date_obj": row_date,
                "open": open_price,
                "high": row.get("high"),
                "low": row.get("low"),
                "close": close_price,
                "volume": volume,
                "color": (
                    "green"
                    if open_price is not None and close_price is not None and close_price >= open_price
                    else "red"
                ),
            }
        )

    if not rows:
        raise ValueError(f"No usable price rows found for {ticker}")

    candidate_days = [r for r in rows if start_date <= r["date_obj"] <= end_date]
    if not candidate_days:
        raise ValueError(f"No trading days found for {ticker} in the last {days} days")

    fixed_avg_volume = sum(r["volume"] for r in candidate_days) / len(candidate_days)
    top_volume_days = sorted(candidate_days, key=lambda x: x["volume"], reverse=True)[:top]

    news_url = "https://financialmodelingprep.com/stable/news/stock"
    results: List[Dict[str, Any]] = []

    for day_row in top_volume_days:
        day_start = f"{day_row['date']} 00:00:00"
        day_end = f"{day_row['date']} 23:59:59"

        news_params = {
            "symbols": ticker,
            "from": day_start,
            "to": day_end,
            "limit": news_limit_per_day,
            "apikey": api_key,
        }

        news_resp = requests.get(news_url, params=news_params, timeout=30)
        news_resp.raise_for_status()
        news_data = news_resp.json()

        if not isinstance(news_data, list):
            news_data = []

        filtered_news = _filter_and_rank_news(
            news_items=[
                {
                    "publishedDate": item.get("publishedDate"),
                    "title": item.get("title"),
                    "site": item.get("site"),
                    "url": item.get("url"),
                }
                for item in news_data
            ],
            top_valuable_news=top_valuable_news,
        )

        rel_volume = (day_row["volume"] / fixed_avg_volume) if fixed_avg_volume > 0 else None

        results.append(
            {
                "ticker": ticker,
                "date": day_row["date"],
                "date_obj": day_row["date_obj"],
                "volume": day_row["volume"],
                "avg_volume": round(fixed_avg_volume, 2),
                "rel_volume": round(rel_volume, 2) if rel_volume is not None else None,
                "rel_volume_text": f"{round(rel_volume, 2)}x" if rel_volume is not None else "N/A",
                "open": day_row["open"],
                "high": day_row["high"],
                "low": day_row["low"],
                "close": day_row["close"],
                "color": day_row["color"],
                "news_count": len(filtered_news),
                "news": filtered_news,
            }
        )

    results.sort(key=lambda x: x["date_obj"], reverse=True)

    title = f"{ticker}_top{top}_days_monthly_news"
    pdf_path = os.path.join(news_dir, f"{title}.pdf")

    _render_news_results_to_pdf(results, title, pdf_path)

    print(f"Saved PDF to: {pdf_path}")
    return results


def _render_news_results_to_pdf(results: List[Dict[str, Any]], title: str, pdf_path: str) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    if not results:
        raise ValueError("No results to render")

    c = canvas.Canvas(pdf_path, pagesize=letter)
    page_width, page_height = letter

    left_margin = 0.6 * inch
    right_margin = 0.6 * inch
    top_margin = 0.6 * inch
    bottom_margin = 0.6 * inch
    usable_width = page_width - left_margin - right_margin

    title_font = "Helvetica-Bold"
    section_font = "Helvetica-Bold"
    meta_font = "Helvetica"
    body_font = "Helvetica"

    title_size = 16
    day_title_size = 14
    meta_size = 10
    body_size = 10

    line_height = 13
    news_spacing = 20
    section_spacing = 16

    def new_page() -> float:
        c.showPage()
        return page_height - top_margin

    def ensure_space(y_pos: float, needed: float) -> float:
        if y_pos - needed < bottom_margin:
            return new_page()
        return y_pos

    def draw_wrapped_text(
        text: str,
        x: float,
        y_pos: float,
        font_name: str,
        font_size: int,
        max_width: float,
    ) -> float:
        c.setFont(font_name, font_size)

        words = text.split()
        if not words:
            return y_pos - line_height

        line = words[0]
        for word in words[1:]:
            candidate = f"{line} {word}"
            if c.stringWidth(candidate, font_name, font_size) <= max_width:
                line = candidate
            else:
                c.drawString(x, y_pos, line)
                y_pos -= line_height
                line = word

        c.drawString(x, y_pos, line)
        y_pos -= line_height
        return y_pos

    y = page_height - top_margin
    c.setFont(title_font, title_size)
    c.drawString(left_margin, y, title)
    y -= 26

    for idx, item in enumerate(results, start=1):
        y = ensure_space(y, 52)

        date_header = f"{idx}. {item['date']}"
        c.setFont(section_font, day_title_size)
        c.drawString(left_margin, y, date_header)
        y -= 18

        meta_line = (
            f"Volume: {item['volume']:,}    "
            f"AvgVol: {int(item['avg_volume']):,}    "
            f"RelVol: {item['rel_volume_text']}    "
            f"Color: {item['color'].upper()}    "
            f"ValuableNews: {item['news_count']}"
        )
        c.setFont(meta_font, meta_size)
        c.drawString(left_margin, y, meta_line)
        y -= 18

        news_items = item.get("news", [])
        if not news_items:
            y = ensure_space(y, 20)
            c.setFont(body_font, body_size)
            c.drawString(left_margin + 10, y, "No valuable news found.")
            y -= section_spacing
            continue

        for news_idx, article in enumerate(news_items, start=1):
            site = article.get("site") or "Unknown"
            headline = article.get("title") or "(No title)"
            published = (article.get("publishedDate") or "")[:16]

            line = f"{news_idx}. {headline}"
            if published:
                line += f" ({published})"
            line += f" [{site}]"

            y = ensure_space(y, 32)
            y = draw_wrapped_text(
                text=line,
                x=left_margin + 10,
                y_pos=y,
                font_name=body_font,
                font_size=body_size,
                max_width=usable_width - 10,
            )
            y -= news_spacing

        y -= section_spacing

    c.save()


if __name__ == "__main__":
    try:
        print("STARTING...")
        results = news_pdf_on_top_volume_days(
            ticker="AAPL",
            days=30,
            top=5,
            api_key=API_KEY,
            news_limit_per_day=50,
            top_valuable_news=10,
        )
        print("DONE")
    except Exception as e:
        print("ERROR:", e)