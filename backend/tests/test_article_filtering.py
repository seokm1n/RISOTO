"""기사 필터의 관련성·광고·중복 판정 회귀 테스트."""

import unittest
from types import SimpleNamespace

from app.services.article_filtering import (
    FilterConfig,
    classify_article,
    content_hash,
    normalize_url,
)


def article(title, summary="", url="https://news.example/story", **extra):
    """기사 필터 테스트에 사용할 간단한 기사 객체를 만든다."""
    return SimpleNamespace(
        title=title,
        summary=summary,
        url=url,
        published_at=None,
        **extra,
    )


class ArticleFilteringTests(unittest.TestCase):
    """규칙 기반 및 NLI 보조 기사 필터의 핵심 판정 계약을 검증한다."""

    def setUp(self):
        """각 테스트에서 공유할 기업, 키워드와 규칙 전용 필터 설정을 준비한다."""
        self.company = SimpleNamespace(
            name="Acme Robotics",
            normalized_name="acme robotics",
            ticker="ACME",
        )
        self.keywords = [
            SimpleNamespace(keyword_type="alias", value="Acme Bot"),
            SimpleNamespace(keyword_type="product", value="Robo One"),
            SimpleNamespace(keyword_type="risk", value="recall"),
        ]
        self.config = FilterConfig(ai_enabled=False)

    def classify(self, item, candidates=None):
        """공통 테스트 설정으로 기사와 선택적 중복 후보를 분류한다."""
        return classify_article(
            self.company,
            self.keywords,
            item,
            candidate_articles=candidates or [],
            config=self.config,
        )

    def test_tracking_parameters_do_not_change_normalized_url(self):
        """추적 파라미터와 URL 표기 차이가 정규화 결과에 영향을 주지 않는지 검증한다."""
        left = normalize_url("https://EXAMPLE.com/a/?utm_source=x&b=2&a=1#part")
        right = normalize_url("https://example.com/a?a=1&b=2")
        self.assertEqual(left, right)

    def test_company_mention_is_accepted(self):
        """기업명과 제품명이 명확한 기사가 관련 기사로 승인되는지 검증한다."""
        result = self.classify(article("Acme Robotics launches Robo One"))
        self.assertEqual((result.decision, result.reason), ("accepted", "accepted"))
        self.assertGreaterEqual(result.relevance_score, 0.70)

    def test_unrelated_story_is_rejected(self):
        """기업과 무관한 기사가 관련성 부족으로 거부되는지 검증한다."""
        result = self.classify(article("Weekend weather forecast", "Clear skies expected"))
        self.assertEqual((result.decision, result.reason), ("rejected", "irrelevant"))

    def test_generic_issue_category_page_is_rejected_before_relevance(self):
        """기업명이 요약에 있어도 내용이 바뀌는 이슈 목록 페이지는 기사로 승인하지 않는다."""
        result = self.classify(
            article(
                "인스티즈(instiz) 이슈 카테고리",
                "Acme Robotics 배송 품질 논란",
                url="https://www.instiz.net/pt?srt=3&srd=1",
            )
        )
        self.assertEqual((result.decision, result.reason), ("rejected", "irrelevant"))
        self.assertEqual(
            result.details["page_type_evidence"],
            ["generic_listing_title"],
        )

    def test_article_about_a_product_category_is_not_treated_as_a_listing(self):
        """본문 기사 제목에 일반 명사 카테고리가 있어도 과잉 차단하지 않는다."""
        result = self.classify(
            article("Acme Robotics, 새 제품 카테고리 출시")
        )
        self.assertEqual((result.decision, result.reason), ("accepted", "accepted"))

    def test_obvious_advertising_is_rejected(self):
        """명백한 제휴·쿠폰 광고 문구가 광고로 거부되는지 검증한다."""
        result = self.classify(
            article(
                "Acme Robotics sponsored promotion",
                "Affiliate discount coupon: buy now and request a free consultation",
            )
        )
        self.assertEqual((result.decision, result.reason), ("rejected", "advertisement"))

    def test_exact_url_duplicate_links_to_canonical_raw(self):
        """동일 URL 기사가 중복 처리되고 기존 원문 ID에 연결되는지 검증한다."""
        candidate = article("Acme Robotics launches Robo One", id=41)
        candidate.normalized_url = normalize_url(candidate.url)
        candidate.content_hash = content_hash(candidate.title, candidate.summary)
        result = self.classify(
            article("Acme Robotics launches Robo One"),
            candidates=[candidate],
        )
        self.assertEqual((result.decision, result.reason), ("rejected", "duplicate"))
        self.assertEqual(result.duplicate_of_raw_id, 41)

    def test_similar_content_at_a_different_url_is_preserved(self):
        """내용이 같아도 정규화 URL이 다르면 기사량 집계 대상으로 보존한다."""
        candidate = article(
            "Acme Robotics launches Robo One",
            url="https://publisher-a.example/news/1",
            id=42,
        )
        candidate.normalized_url = normalize_url(candidate.url)
        candidate.content_hash = content_hash(candidate.title, candidate.summary)
        result = self.classify(
            article(
                "Acme Robotics launches Robo One",
                url="https://publisher-b.example/articles/99",
            ),
            candidates=[candidate],
        )
        self.assertEqual((result.decision, result.reason), ("accepted", "accepted"))
        self.assertIsNone(result.duplicate_of_raw_id)

    def test_korean_advertising_signals_are_rejected(self):
        """한국어 광고 신호가 포함된 기사가 광고로 거부되는지 검증한다."""
        korean_company = SimpleNamespace(
            name="가온테크",
            normalized_name="가온테크",
            ticker=None,
        )
        result = classify_article(
            korean_company,
            [],
            article(
                "가온테크 협찬 공동구매 오픈",
                "선착순 할인 쿠폰을 받고 지금 구매하세요",
            ),
            config=self.config,
        )
        self.assertEqual((result.decision, result.reason), ("rejected", "advertisement"))

    def test_klue_nli_can_block_an_ambiguous_company_name(self):
        """동음이의 기업명을 KLUE NLI가 무관 기사로 차단할 수 있는지 검증한다."""
        class FakeNli:
            """모델 다운로드 없이 모호한 기업명 분기를 재현하는 테스트 대역."""

            def score_hypotheses(self, premises, hypotheses):
                """테스트가 기대하는 고정 NLI 확률을 반환한다."""
                return [[0.40, 0.35, 0.25]]

        result = classify_article(
            SimpleNamespace(name="SSG닷컴", normalized_name="ssg닷컴", ticker=None),
            [],
            article("SSG 랜더스, 연장 끝 승리", "프로야구 투수가 호투했다"),
            nli_classifier=FakeNli(),
            config=FilterConfig(
                ai_enabled=True,
                semantic_model_name="unused",
                allow_model_download=False,
            ),
        )
        self.assertEqual((result.decision, result.reason), ("rejected", "irrelevant"))
        self.assertEqual(result.classifier_kind, "hybrid_klue_nli")


if __name__ == "__main__":
    unittest.main()
