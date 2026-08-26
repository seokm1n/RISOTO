"""외부 뉴스·검색·댓글 API 응답을 공통 기사 형식으로 수집한다."""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx


# 제공자 응답의 HTML을 제거하고 URL 중복을 줄이는 공통 정규화 규칙이다.
TAG_RE = re.compile(r"<[^>]+>")
TRACKING_PARAMETERS = {"fbclid", "gclid", "ref", "source"}


@dataclass(slots=True)
class CollectedArticle:
    """서로 다른 외부 제공자의 결과를 파이프라인 공통 형식으로 표현한다."""

    source: str
    title: str
    summary: str | None
    url: str
    original_url: str | None
    published_at: datetime | None
    raw_payload: dict
    matched_keyword: str


def clean_html(value: str | None) -> str:
    """HTML 태그와 엔티티를 제거하고 공백을 정리한다."""
    if not value:
        return ""
    return " ".join(unescape(TAG_RE.sub("", value)).split())


def canonicalize_url(value: str) -> str:
    """추적용 쿼리와 프래그먼트를 제거해 기사 URL을 표준화한다."""
    parts = urlsplit(value.strip())
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    ]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def parse_datetime(value: str | None) -> datetime | None:
    """RFC 이메일 날짜 또는 ISO 날짜 문자열을 datetime으로 안전하게 변환한다."""
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
    """네이버 개발자센터(openapi.naver.com) 뉴스 검색 결과를 수집한다."""

    source = "naver_api_hub"
    endpoint = "https://openapi.naver.com/v1/search/news.json"

    def __init__(self, client_id: str, client_secret: str) -> None:
        """네이버 검색 API 인증 헤더를 구성한다."""
        self.headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        }

    def search(self, query: str, start_date: date) -> list[CollectedArticle]:
        """네이버 뉴스 검색 결과를 시작일 이후의 표준 기사 객체로 변환한다."""
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
    """Tavily 뉴스 검색 API에서 과거 및 최신 기사를 수집한다."""

    source = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, api_key: str) -> None:
        """Tavily 검색에 사용할 API 키를 저장한다."""
        self.api_key = api_key

    def search(self, query: str, start_date: date) -> list[CollectedArticle]:
        """Tavily 뉴스 검색 결과를 표준 기사 객체로 변환한다."""
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


class KakaoDaumSearchCollector:
    """카카오 다음 웹 검색에서 기사 후보를 수집한다."""

    source = "kakao_daum"
    endpoint = "https://dapi.kakao.com/v2/search/web"

    def __init__(self, rest_api_key: str) -> None:
        """카카오 웹 검색 API 인증 헤더를 구성한다."""
        self.headers = {"Authorization": f"KakaoAK {rest_api_key}"}

    def search(self, query: str, start_date: date) -> list[CollectedArticle]:
        """다음 웹 검색 결과 중 시작일 이후 항목을 표준 기사 객체로 변환한다."""
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                self.endpoint,
                headers=self.headers,
                params={
                    "query": query,
                    "sort": "recency",
                    "page": 1,
                    "size": 50,
                },
            )
            response.raise_for_status()
            payload = response.json()

        articles: list[CollectedArticle] = []
        for item in payload.get("documents", []):
            selected_url = item.get("url")
            if not selected_url:
                continue
            published_at = parse_datetime(item.get("datetime"))
            if published_at and published_at.date() < start_date:
                continue
            articles.append(
                CollectedArticle(
                    source=self.source,
                    title=clean_html(item.get("title")),
                    summary=clean_html(item.get("contents")) or None,
                    url=canonicalize_url(selected_url),
                    original_url=selected_url,
                    published_at=published_at,
                    raw_payload=item,
                    matched_keyword=query,
                )
            )
        return articles


class YouTubeCommentCollector:
    """관련 YouTube 영상의 공개 최상위 댓글을 여론 기사 형태로 수집한다."""

    source = "youtube_comment"
    search_endpoint = "https://www.googleapis.com/youtube/v3/search"
    comments_endpoint = "https://www.googleapis.com/youtube/v3/commentThreads"

    def __init__(self, api_key: str, max_videos: int = 5, comments_per_video: int = 20) -> None:
        """YouTube API 키와 영상·댓글 수집 한도를 설정한다."""
        self.api_key = api_key
        self.max_videos = max_videos
        self.comments_per_video = comments_per_video

    @staticmethod
    def _error_details(response: httpx.Response) -> tuple[str, set[str]]:
        """YouTube 오류 응답에서 안전한 메시지와 원인 코드를 추출한다."""
        try:
            error = response.json().get("error", {})
        except ValueError:
            return "알 수 없는 오류", set()
        reasons = {
            item.get("reason", "")
            for item in error.get("errors", [])
            if item.get("reason")
        }
        return clean_html(error.get("message")) or "알 수 없는 오류", reasons

    @classmethod
    def _raise_safe_error(cls, response: httpx.Response) -> None:
        """YouTube 응답의 민감 정보를 제외한 설명으로 예외를 발생시킨다."""
        message, _ = cls._error_details(response)
        raise ValueError(f"YouTube API 오류 ({response.status_code}): {message}")

    def search(self, query: str, start_date: date) -> list[CollectedArticle]:
        """관련 최신 영상을 검색하고 공개 최상위 댓글을 표준 기사 형태로 수집한다."""
        published_after = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                self.search_endpoint,
                params={
                    "key": self.api_key,
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "order": "date",
                    "maxResults": self.max_videos,
                    "publishedAfter": published_after.isoformat().replace("+00:00", "Z"),
                    "regionCode": "KR",
                    "relevanceLanguage": "ko",
                },
            )
            if response.is_error:
                self._raise_safe_error(response)
            videos = response.json().get("items", [])

            comments: list[CollectedArticle] = []
            for video in videos:
                video_id = video.get("id", {}).get("videoId")
                if not video_id:
                    continue
                comment_response = client.get(
                    self.comments_endpoint,
                    params={
                        "key": self.api_key,
                        "part": "snippet",
                        "videoId": video_id,
                        "order": "time",
                        "textFormat": "plainText",
                        "maxResults": self.comments_per_video,
                    },
                )
                if comment_response.is_error:
                    _, reasons = self._error_details(comment_response)
                    if reasons & {"commentsDisabled", "videoNotFound"}:
                        continue
                    self._raise_safe_error(comment_response)

                video_title = clean_html(video.get("snippet", {}).get("title"))
                for thread in comment_response.json().get("items", []):
                    top_comment = thread.get("snippet", {}).get("topLevelComment", {})
                    comment_id = top_comment.get("id")
                    snippet = top_comment.get("snippet", {})
                    body = clean_html(snippet.get("textOriginal"))
                    published_at = parse_datetime(snippet.get("publishedAt"))
                    if not comment_id or not body:
                        continue
                    if published_at and published_at.date() < start_date:
                        continue
                    comment_url = f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"
                    comments.append(
                        CollectedArticle(
                            source=self.source,
                            title=video_title or f"YouTube 영상 {video_id}",
                            summary=body,
                            url=comment_url,
                            original_url=f"https://www.youtube.com/watch?v={video_id}",
                            published_at=published_at,
                            raw_payload={"video": video, "comment_thread": thread},
                            matched_keyword=query,
                        )
                    )
        return comments
