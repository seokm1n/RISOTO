"""외부 뉴스 수집기의 공급자별 요청 규격을 검증한다."""

from datetime import date
import unittest
from unittest.mock import MagicMock, patch

from app.services.news_collectors import NaverNewsCollector


class NaverNewsCollectorTests(unittest.TestCase):
    """NAVER API HUB 요청 규격의 회귀를 방지한다."""

    def test_uses_api_hub_endpoint_and_headers(self) -> None:
        """신규 주소와 API Gateway 인증 헤더를 사용한다."""
        response = MagicMock()
        response.json.return_value = {"items": []}

        with patch("app.services.news_collectors.httpx.Client") as client_class:
            client = client_class.return_value.__enter__.return_value
            client.get.return_value = response

            collector = NaverNewsCollector("client-id", "client-secret")
            self.assertEqual(collector.search("네이버", date(2026, 1, 1)), [])

        client.get.assert_called_once_with(
            "https://naverapihub.apigw.ntruss.com/search/v1/news",
            headers={
                "X-NCP-APIGW-API-KEY-ID": "client-id",
                "X-NCP-APIGW-API-KEY": "client-secret",
            },
            params={"query": "네이버", "display": 100, "start": 1, "sort": "date", "format": "json"},
        )
        response.raise_for_status.assert_called_once_with()
