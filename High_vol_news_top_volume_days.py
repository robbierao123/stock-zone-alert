import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("FMP_API_KEY")


# -----------------------------
# Existing filter logic
# -----------------------------
def _is_noise_article(title: str, site: str) -> bool:
    t = (title or "").lower().strip()
    s = (site or "").lower().strip()

    noise_sites = {
        "youtube.com",
    }

    if s in noise_sites:
        return True

    noise_patterns = [
        r"\btop\s+\d+\b",
        r"\bbest\b.*\bstock",
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
        r"\bfoldable iphone\b",
        r"\bfoldable phone\b",
        r"\bhinge cringe\b",
        r"\bdelay concerns\b",
        r"\btimeline holds\b",
        r"\bflop or strategic stop\b",
        r"\boptions trade\b",
        r"\bapril fools\b",
        r"\bpodcast\b",
    ]

    return any(re.search(pattern, t) for pattern in noise_patterns)



def _article_score(title: str, site: str) -> int:
    t = (title or "").lower().strip()
    s = (site or "").lower().strip()

    score = 0

    source_scores = {
        "reuters.com": 5,
        "cnbc.com": 4,
        "businesswire.com": 4,
        "techcrunch.com": 3,
        "barrons.com": 3,
        "seekingalpha.com": 2,
        "benzinga.com": 2,
        "feeds.benzinga.com": 2,
        "gurufocus.com": 2,
        "fastcompany.com": 2,
        "proactiveinvestors.com": 2,
        "investopedia.com": 2,
        "defenseworld.net": 1,
    }
    score += source_scores.get(s, 0)

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
        r"\blawsuit\b",
        r"\brecruiting plaintiffs\b",
        r"\bengineers\b",
        r"\bsmartphone\b",
        r"\biphone sales\b",
        r"\bdelivery\b",
        r"\bproduction\b",
        r"\bapproval\b",
        r"\binvestigation\b",
        r"\bcontract\b",
    ]
    for pattern in strong_patterns:
        if re.search(pattern, t):
            score += 3

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
    top_valuable_news: int = 20,
) -> List[Dict[str, Any]]:
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

    kept.sort(key=lambda x: x.get("_score", 0), reverse=True)
    top_items = kept[:top_valuable_news]
    top_items.sort(key=lambda x: x.get("publishedDate") or "", reverse=True)
    return top_items


# -----------------------------
# Market data + news collection
# -----------------------------
def get_last_trading_days_by_volume(
    ticker: str,
    api_key: str,
    lookback_trading_days: int = 30,
    top_volume_days: int = 5,
) -> List[Dict[str, Any]]:
    if not ticker:
        raise ValueError("ticker is required")
    if not api_key:
        raise ValueError("api_key is required")
    if lookback_trading_days <= 0:
        raise ValueError("lookback_trading_days must be > 0")
    if top_volume_days <= 0:
        raise ValueError("top_volume_days must be > 0")

    url = "https://financialmodelingprep.com/stable/historical-price-eod/full"
    params = {
        "symbol": ticker.upper().strip(),
        "apikey": api_key,
    }

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, list) or not data:
        raise ValueError(f"No EOD data returned for {ticker}: {data}")

    recent_rows = data[:lookback_trading_days]
    days = []
    for row in recent_rows:
        days.append(
            {
                "date": row.get("date"),
                "volume": float(row.get("volume") or 0),
                "open": float(row.get("open") or 0),
                "high": float(row.get("high") or 0),
                "low": float(row.get("low") or 0),
                "close": float(row.get("close") or 0),
            }
        )

    top_days = sorted(days, key=lambda x: x["volume"], reverse=True)[:top_volume_days]
    top_days.sort(key=lambda x: x["date"], reverse=True)
    return top_days



def get_stock_news_for_date(
    ticker: str,
    date_str: str,
    api_key: str,
    news_limit: int = 250,
) -> List[Dict[str, Any]]:
    url = "https://financialmodelingprep.com/stable/news/stock"
    params = {
        "symbols": ticker.upper().strip(),
        "from": date_str,
        "to": date_str,
        "page": 0,
        "limit": min(news_limit, 250),
        "apikey": api_key,
    }

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, list):
        return []

    normalized = []
    for item in data:
        normalized.append(
            {
                "publishedDate": item.get("publishedDate"),
                "title": item.get("title"),
                "site": item.get("site") or item.get("publisher"),
                "publisher": item.get("publisher") or item.get("site"),
                "url": item.get("url"),
                "text": item.get("text"),
                "symbol": item.get("symbol"),
            }
        )
    return normalized



def news_pdf_top_volume_days(
    ticker: str,
    api_key: str,
    lookback_trading_days: int = 30,
    top_volume_days: int = 5,
    news_limit: int = 250,
    top_valuable_news_per_day: int = 20,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    1) Look at the last `lookback_trading_days` trading days.
    2) Pick the `top_volume_days` highest-volume days.
    3) Fetch ticker news for each of those exact days.
    4) Apply the existing filter/ranking logic to each day's news.
    5) Save grouped results to a PDF.

    Returns:
        (grouped_results, pdf_path)
    """
    if not ticker:
        raise ValueError("ticker is required")
    if not api_key:
        raise ValueError("api_key is required")

    ticker = ticker.upper().strip()
    volume_days = get_last_trading_days_by_volume(
        ticker=ticker,
        api_key=api_key,
        lookback_trading_days=lookback_trading_days,
        top_volume_days=top_volume_days,
    )

    grouped_results: List[Dict[str, Any]] = []

    for rank, day in enumerate(volume_days, start=1):
        trading_date = day["date"]
        raw_news = get_stock_news_for_date(
            ticker=ticker,
            date_str=trading_date,
            api_key=api_key,
            news_limit=news_limit,
        )

        filtered_news = _filter_and_rank_news(
            news_items=raw_news,
            top_valuable_news=top_valuable_news_per_day,
        )

        grouped_results.append(
            {
                "rank": rank,
                "date": trading_date,
                "volume": day["volume"],
                "open": day["open"],
                "high": day["high"],
                "low": day["low"],
                "close": day["close"],
                "raw_news_count": len(raw_news),
                "filtered_news_count": len(filtered_news),
                "news": filtered_news,
            }
        )

    base_dir = os.path.dirname(os.path.abspath(__file__))
    news_dir = os.path.join(base_dir, "NEWS")
    os.makedirs(news_dir, exist_ok=True)

    title = f"{ticker}_top_volume_days_news"
    pdf_path = os.path.join(news_dir, f"{title}.pdf")

    _render_top_volume_days_news_to_pdf(
        grouped_results=grouped_results,
        title=title,
        pdf_path=pdf_path,
        ticker=ticker,
        lookback_trading_days=lookback_trading_days,
    )

    print(f"Saved PDF to: {pdf_path}")
    return grouped_results, pdf_path


# -----------------------------
# PDF rendering
# -----------------------------
def _render_top_volume_days_news_to_pdf(
    grouped_results: List[Dict[str, Any]],
    title: str,
    pdf_path: str,
    ticker: str,
    lookback_trading_days: int,
) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(pdf_path, pagesize=letter)
    page_width, page_height = letter

    left_margin = 0.6 * inch
    right_margin = 0.6 * inch
    top_margin = 0.6 * inch
    bottom_margin = 0.6 * inch
    usable_width = page_width - left_margin - right_margin

    line_height = 13

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
        font_name: str = "Helvetica",
        font_size: int = 10,
        extra_gap_after: int = 0,
    ) -> float:
        c.setFont(font_name, font_size)
        words = (text or "").split()
        if not words:
            return y_pos - line_height - extra_gap_after

        line = words[0]
        for word in words[1:]:
            candidate = f"{line} {word}"
            if c.stringWidth(candidate, font_name, font_size) <= usable_width:
                line = candidate
            else:
                c.drawString(x, y_pos, line)
                y_pos -= line_height
                line = word

        c.drawString(x, y_pos, line)
        y_pos -= line_height + extra_gap_after
        return y_pos

    total_raw = sum(group["raw_news_count"] for group in grouped_results)
    total_filtered = sum(group["filtered_news_count"] for group in grouped_results)

    y = page_height - top_margin
    c.setFont("Helvetica-Bold", 16)
    c.drawString(left_margin, y, title)
    y -= 24

    c.setFont("Helvetica", 10)
    c.drawString(left_margin, y, f"Ticker: {ticker}")
    y -= 14
    c.drawString(left_margin, y, f"Lookback trading days: {lookback_trading_days}")
    y -= 14
    c.drawString(left_margin, y, f"Selected high-volume days: {len(grouped_results)}")
    y -= 14
    c.drawString(left_margin, y, f"Total raw news fetched: {total_raw}    Total filtered kept: {total_filtered}")
    y -= 22

    if not grouped_results:
        c.drawString(left_margin, y, "No volume days found.")
        c.save()
        return

    for group in grouped_results:
        y = ensure_space(y, 90)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(
            left_margin,
            y,
            f"Volume Rank #{group['rank']} - {group['date']} - Volume: {int(group['volume']):,}",
        )
        y -= 16

        c.setFont("Helvetica", 10)
        c.drawString(
            left_margin,
            y,
            f"OHLC: O {group['open']:.2f}   H {group['high']:.2f}   L {group['low']:.2f}   C {group['close']:.2f}",
        )
        y -= 14
        c.drawString(
            left_margin,
            y,
            f"Raw news fetched: {group['raw_news_count']}   Filtered kept: {group['filtered_news_count']}",
        )
        y -= 16

        if not group["news"]:
            y = draw_wrapped_text(
                text="No valuable news found for this high-volume day.",
                x=left_margin,
                y_pos=y,
                font_name="Helvetica-Oblique",
                font_size=10,
                extra_gap_after=8,
            )
            continue

        for idx, article in enumerate(group["news"], start=1):
            y = ensure_space(y, 90)
            title_line = (
                f"{idx}. {article.get('title') or '(No title)'} "
                f"[{article.get('site') or 'Unknown'}] "
                f"Score:{article.get('_score', 0)}"
            )
            y = draw_wrapped_text(
                text=title_line,
                x=left_margin,
                y_pos=y,
                font_name="Helvetica-Bold",
                font_size=10,
            )

            published = article.get("publishedDate") or ""
            if published:
                y = draw_wrapped_text(
                    text=f"Published: {published}",
                    x=left_margin,
                    y_pos=y,
                    font_name="Helvetica",
                    font_size=9,
                )

            summary = (article.get("text") or "").strip()
            if summary:
                y = draw_wrapped_text(
                    text=f"Summary: {summary}",
                    x=left_margin,
                    y_pos=y,
                    font_name="Helvetica",
                    font_size=9,
                )

            url = (article.get("url") or "").strip()
            if url:
                y = draw_wrapped_text(
                    text=f"URL: {url}",
                    x=left_margin,
                    y_pos=y,
                    font_name="Helvetica",
                    font_size=8,
                    extra_gap_after=6,
                )
            else:
                y -= 6

        y -= 4

    c.save()


if __name__ == "__main__":
    try:
        print("STARTING...")
        results, pdf_file = news_pdf_top_volume_days(
            ticker="TSLA",
            api_key=API_KEY,
            lookback_trading_days=30,
            top_volume_days=5,
            news_limit=250,
            top_valuable_news_per_day=20,
        )
        print(f"DONE: {pdf_file}")
    except Exception as e:
        print("ERROR:", e)
