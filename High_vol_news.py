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
    """
    Higher score = more valuable.
    """
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


def news_pdf_filtered_30_days(
    ticker: str,
    api_key: str,
    days: int = 30,
    news_limit: int = 250,
    top_valuable_news: int = 20,
) -> List[Dict[str, Any]]:
    """
    Fetch stock news for the last `days`, filter/rank it, keep the top valuable items,
    and save to a PDF under NEWS/.

    Args:
        ticker: Stock ticker, e.g. "AAPL"
        api_key: FMP API key
        days: Lookback window in calendar days
        news_limit: Max raw news records to request
        top_valuable_news: Number of filtered news items to keep

    Returns:
        List of filtered and ranked news items.
    """
    if not ticker:
        raise ValueError("ticker is required")
    if not api_key:
        raise ValueError("api_key is required")
    if days <= 0:
        raise ValueError("days must be > 0")
    if news_limit <= 0:
        raise ValueError("news_limit must be > 0")
    if top_valuable_news <= 0:
        raise ValueError("top_valuable_news must be > 0")

    ticker = ticker.upper().strip()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    news_dir = os.path.join(base_dir, "NEWS")
    os.makedirs(news_dir, exist_ok=True)

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days)

    news_url = "https://financialmodelingprep.com/stable/news/stock"
    news_params = {
        "symbols": ticker,
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "limit": min(news_limit, 250),
        "page": 0,
        "apikey": api_key,
    }

    news_resp = requests.get(news_url, params=news_params, timeout=30)
    news_resp.raise_for_status()
    news_data = news_resp.json()

    if not isinstance(news_data, list):
        news_data = []

    normalized_news = [
        {
            "publishedDate": item.get("publishedDate"),
            "title": item.get("title"),
            "site": item.get("site"),
            "url": item.get("url"),
        }
        for item in news_data
    ]

    filtered_news = _filter_and_rank_news(
        news_items=normalized_news,
        top_valuable_news=top_valuable_news,
    )

    title = f"{ticker}_filtered_{days}_days_news"
    pdf_path = os.path.join(news_dir, f"{title}.pdf")

    _render_filtered_news_to_pdf(
        news_items=filtered_news,
        title=title,
        pdf_path=pdf_path,
        ticker=ticker,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        raw_count=len(normalized_news),
    )

    print(f"Saved PDF to: {pdf_path}")
    return filtered_news


def _render_filtered_news_to_pdf(
    news_items: List[Dict[str, Any]],
    title: str,
    pdf_path: str,
    ticker: str,
    start_date: str,
    end_date: str,
    raw_count: int,
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

    title_font = "Helvetica-Bold"
    section_font = "Helvetica-Bold"
    meta_font = "Helvetica"
    body_font = "Helvetica"

    title_size = 16
    section_size = 12
    meta_size = 10
    body_size = 10

    line_height = 13
    news_spacing = 14

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
    y -= 24

    c.setFont(meta_font, meta_size)
    c.drawString(left_margin, y, f"Ticker: {ticker}")
    y -= 14
    c.drawString(left_margin, y, f"Window: {start_date} to {end_date}")
    y -= 14
    c.drawString(left_margin, y, f"RawNewsFetched: {raw_count}    ValuableNewsKept: {len(news_items)}")
    y -= 22

    c.setFont(section_font, section_size)
    c.drawString(left_margin, y, "Top filtered news")
    y -= 18

    if not news_items:
        c.setFont(body_font, body_size)
        c.drawString(left_margin, y, "No valuable news found.")
        c.save()
        return

    for idx, article in enumerate(news_items, start=1):
        site = article.get("site") or "Unknown"
        headline = article.get("title") or "(No title)"
        published = (article.get("publishedDate") or "")[:16]
        score = article.get("_score", 0)

        line = f"{idx}. {headline}"
        if published:
            line += f" ({published})"
        line += f" [{site}]"
        line += f"  Score:{score}"

        y = ensure_space(y, 32)
        y = draw_wrapped_text(
            text=line,
            x=left_margin,
            y_pos=y,
            font_name=body_font,
            font_size=body_size,
            max_width=usable_width,
        )
        y -= news_spacing

    c.save()


if __name__ == "__main__":
    try:
        print("STARTING...")
        results = news_pdf_filtered_30_days(
            ticker="TSLA",
            api_key=API_KEY,
            days=30,
            news_limit=250,
            top_valuable_news=20,
        )
        print("DONE")
    except Exception as e:
        print("ERROR:", e)