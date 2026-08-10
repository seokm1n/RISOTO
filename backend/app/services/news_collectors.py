from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from html import unescape
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx


TAG_RE = re.compile(r"<[^>]+>")
TRACKING_PARAMETERS = {"fbclid", "gclid", "ref", "source"}


@dataclass(slots=True)
class CollectedArticle:
    source: str
    title: str
    summary: str | None
    url: str
    original_url: str | None
    published_at: datetime | None
    raw_payload: dict
    matched_keyword: str


def clean_html(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(unescape(TAG_RE.sub("", value)).split())


def canonicalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    ]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed


class NaverNewsCollector:
    source = "naver_api_hub"
    endpoint = "https://naverapihub.apigw.ntruss.com/search/v1/news"

    def __init__(self, client_id: str, client_secret: str) -> None:
        self.headers = {
            "X-NCP-APIGW-API-KEY-ID": client_id,
            "X-NCP-APIGW-API-KEY": client_secret,
        }

    def search(self, query: str, start_date: date) -> list[CollectedArticle]:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                self.endpoint,
                headers=self.headers,
                params={"query": query, "display": 100, "start": 1, "sort": "date", "format": "json"},
            )
            response.raise_for_status()
            payload = response.json()

        articles: list[CollectedArticle] = []
        for item in payload.get("items", []):
            published_at = parse_datetime(item.get("pubDate"))
            if published_at and published_at.date() < start_date:
                continue
            selected_url = item.get("originallink") or item.get("link")
            if not selected_url:
                continue
            articles.append(
                CollectedArticle(
                    source=self.source,
                    title=clean_html(item.get("title")),
                    summary=clean_html(item.get("description")) or None,
                    url=canonicalize_url(selected_url),
                    original_url=item.get("originallink"),
                    published_at=published_at,
                    raw_payload=item,
                    matched_keyword=query,
                )
            )
        return articles


class TavilyNewsCollector:
    source = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, start_date: date) -> list[CollectedArticle]:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "query": query,
                    "topic": "news",
                    "search_depth": "basic",
                    "max_results": 10,
                    "include_answer": False,
                    "include_raw_content": False,
                    "start_date": start_date.isoformat(),
                },
            )
            response.raise_for_status()
            payload = response.json()

        articles: list[CollectedArticle] = []
        for item in payload.get("results", []):
            selected_url = item.get("url")
            if not selected_url:
                continue
            articles.append(
                CollectedArticle(
                    source=self.source,
                    title=clean_html(item.get("title")),
                    summary=clean_html(item.get("content")) or None,
                    url=canonicalize_url(selected_url),
                    original_url=selected_url,
                    published_at=parse_datetime(item.get("published_date")),
                    raw_payload=item,
                    matched_keyword=query,
                )
            )
        return articles
