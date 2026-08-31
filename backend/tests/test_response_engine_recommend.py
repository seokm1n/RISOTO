"""동종기업 추천 생성(response_engine.recommend)과 서비스 연결의 검증.

픽스처는 독립 작업본에서 검수를 마친 실데이터 골든이다(tests/data/):
  - 부정적_파급: alert_peer_downside(2025-12 쿠팡 유출 실제 멘션) -> example_output_peer_downside
    (영향 판단 산출) -> rec_downside(추천 산출, 담당자 검수 완료)
  - 반사이익: example_output_peer(쿠팡 정산 이슈) -> rec_run3
골든 비교는 바이트 동일성이 아니라 **규칙 준수 불변식**으로 한다 - 법령 소스가
KoreanRegulationMapper로 바뀌며 프롬프트가 달라지는 것은 의도된 개선이기 때문이다.

DB가 필요한 것은 서비스 연결 테스트뿐이며, DATABASE_URL이 없으면 그 클래스만
건너뛴다(test_llm_labeling.py의 관례).
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.response_engine import recommend
from app.services.response_engine.risk_types import CODES, get as get_type
from app.services.response_engine.schema import AlertPayload

DATA_DIR = Path(__file__).parent / "data"

# 규칙 5의 근거가 된 실제 불량 headline(작업본 rec.json의 초기 프롬프트 산출).
# 해명은 source_event의 몫인데 headline을 차지해 행동 요약을 밀어냈다 - 회귀 가드.
_BAD_HEADLINE = "11번가 자체 사고 아님: 쿠팡 판매자 정산 지연·수수료 불만 확산에 따른 대응 필요"


def _load(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def _clean_rec(**overrides) -> dict:
    """모든 규칙을 통과하는 최소 추천. 각 테스트가 한 조각만 바꿔 위반을 만든다."""
    rec = {
        "source_event": "동종 기업에서 정산 지연 사안이 보도되었고, 우리 회사 자체 사고는 아닙니다.",
        "headline": "정산 일정과 안내 문구를 즉시 점검하세요.",
        "recommendations": [
            {
                "channel": "규제_조사_확대",
                "action": "정산 관련 대외 문의에 대비해 내부 현황을 확인한다.",
                "rationale": "업계 점검이 확대될 수 있습니다.",
                "verify_first": True,
                "owner_hint": "재무팀에 확인 요청",
                "timeframe": "즉시",
            }
        ],
        "avoid": ["내부 확인 전에 안전하다고 단정하지 말 것"],
        "realert_condition": "규제기관이 업계 실태점검을 공식 발표하면 재경보합니다.",
        "limitations": "내부 검토용이며 법률 자문이 아닙니다.",
        "cited_case_ids": ["C-1"],
    }
    rec.update(overrides)
    return rec


def _peer(**overrides) -> dict:
    peer = {
        "company_name": "쿠팡",
        "main_company_name": "11번가",
        "risk_type": "R11",
        "risk_type_label": "정산·거래조건",
        "impact_direction": "부정적_파급",
        "impact_level": "중간",
        "impact_channels": ["규제_조사_확대", "동일_취약점_보유"],
        "reason": "업계 전반 점검으로 번질 수 있습니다.",
        "watch_points": ["규제기관 발표 여부"],
        "confidence": 0.9,
        "needs_review": False,
        "missing_input_fields": [],
        "cases": [{"case_id": "C-1", "title": "과거 정산 지연 사례",
                   "summary_what": "정산 지연으로 판매자 피해가 발생했다.",
                   "lesson": "일정 공개가 우선이다.", "provenance": "web_search"}],
        "regulations": [],
    }
    peer.update(overrides)
    return peer


class RecommendVerifyRuleTests(unittest.TestCase):
    """규칙 1~6 각각의 양성(잡음)·음성(통과) 케이스."""

    def test_clean_passes(self):
        self.assertEqual(recommend.verify_recommendation(_clean_rec(), _peer()), [])

    def test_rule1_channel_outside_impact(self):
        rec = _clean_rec()
        rec["recommendations"][0]["channel"] = "투자자_주가_동조"
        violations = recommend.verify_recommendation(rec, _peer())
        self.assertTrue(any("영향 경로에 없는 채널" in v for v in violations))

    def test_rule2_unknown_case_id(self):
        rec = _clean_rec(cited_case_ids=["없는사례"])
        violations = recommend.verify_recommendation(rec, _peer())
        self.assertTrue(any("존재하지 않는 사례" in v for v in violations))

    def test_rule3_caps(self):
        one = _clean_rec()["recommendations"][0]
        rec = _clean_rec(recommendations=[dict(one) for _ in range(5)])
        violations = recommend.verify_recommendation(rec, _peer())
        self.assertTrue(any("최대 4개" in v for v in violations))

    def test_rule3_extension_cases_uncited(self):
        rec = _clean_rec(cited_case_ids=[])
        violations = recommend.verify_recommendation(rec, _peer())
        self.assertTrue(any("인용 0건" in v for v in violations))
        # 사례가 아예 없으면 빈 인용이 정상이다.
        self.assertEqual(
            recommend.verify_recommendation(rec, _peer(cases=[])), [])

    def test_rule4_upside_requires_avoid(self):
        rec = _clean_rec(avoid=[])
        peer = _peer(impact_direction="반사이익")
        violations = recommend.verify_recommendation(rec, peer)
        self.assertTrue(any("금지 항목이 비어" in v for v in violations))

    def test_rule5_headline_disclaimer(self):
        violations = recommend.verify_recommendation(
            _clean_rec(headline=_BAD_HEADLINE), _peer())
        self.assertTrue(any("headline에 '우리 사고 아님'" in v for v in violations))
        # 해명은 source_event에 있어야 정상이므로 거기서는 잡지 않는다.
        self.assertEqual(recommend.verify_recommendation(_clean_rec(), _peer()), [])

    def test_rule6_internal_token_leaks(self):
        rec = _clean_rec()
        rec["recommendations"][0]["action"] = "규제_조사_확대에 대비해 negative_ratio 원인을 정리한다"
        violations = recommend.verify_recommendation(rec, _peer())
        self.assertTrue(any("내부 표기" in v for v in violations))
        self.assertTrue(any("내부 필드명" in v for v in violations))
        url_rec = _clean_rec(limitations="자세한 내용은 https://example.com 참고")
        self.assertTrue(any("생 URL" in v
                            for v in recommend.verify_recommendation(url_rec, _peer())))


class RecommendPromptAssemblyTests(unittest.TestCase):
    def test_direction_rule_switch(self):
        up = recommend.build_system_prompt(_peer(impact_direction="반사이익"))
        down = recommend.build_system_prompt(_peer(impact_direction="부정적_파급"))
        self.assertIn("반사이익", up)
        self.assertIn("경쟁사 사고를 직접 언급하는 마케팅", up)
        self.assertIn("부정적 파급", down)

    def test_case_rule_switch(self):
        with_cases = recommend.build_system_prompt(_peer())
        without = recommend.build_system_prompt(_peer(cases=[]))
        self.assertIn("cited_case_ids에 넣으세요", with_cases)
        self.assertIn("과거 사례를 인용하지 마세요", without)

    def test_regulation_block_channel_gate(self):
        regs = [{"law_name": "개인정보보호법 시행령", "article": "제39조",
                 "requirement": "유출 통지·신고"}]
        gated = recommend.build_user_prompt(
            _peer(risk_type="R04", regulations=regs))
        self.assertIn("[참고 법령", gated)
        self.assertIn("제39조", gated)
        # 법령이 있어도 조건 채널이 없으면 블록이 들어가지 않는다.
        no_channel = recommend.build_user_prompt(
            _peer(risk_type="R04", regulations=regs,
                  impact_channels=["고객_유입_기회"]))
        self.assertNotIn("[참고 법령", no_channel)
        # R11(정산)도 같은 경로를 탄다 - v0.2에서 새로 열린 유형.
        r11 = recommend.build_user_prompt(
            _peer(regulations=[{"law_name": "대규모유통업법", "article": "제8조",
                                "requirement": "상품판매대금 정산 기한"}]))
        self.assertIn("대규모유통업법", r11)

    def test_missing_fields_humanized(self):
        prompt = recommend.build_user_prompt(
            _peer(missing_input_fields=["spread_stage", "days_since_last_alert"]))
        self.assertIn("확산 단계", prompt)
        self.assertNotIn("spread_stage", prompt)


class RecommendGoldenFixtureTests(unittest.TestCase):
    """검수 완료된 실데이터 골든이 6규칙을 계속 통과하는가(회귀 기준)."""

    def test_downside_golden(self):
        peer = _load("example_output_peer_downside.json")
        rec = _load("rec_downside.json")["recommendation"]
        self.assertEqual(recommend.verify_recommendation(rec, peer), [])

    def test_upside_golden(self):
        peer = _load("example_output_peer.json")
        rec = _load("rec_run3.json")["recommendation"]
        self.assertEqual(recommend.verify_recommendation(rec, peer), [])

    def test_fixture_codes_match_engine(self):
        """픽스처의 유형 코드·사례 ID가 엔진이 실제로 낼 수 있는 값인가.

        골든은 독립 작업본 산출이라 옛 체계(T코드)를 달고 있었다. 그대로 두면
        KoreanRegulationMapper.lookup이 조용히 빈 목록을 반환해 [참고 법령] 블록이
        통째로 빠진다 - 실제로 한 번 겪은 함정이라 코드로 잠근다(규칙 5·6과 같은
        회귀 가드). 사례 ID 형식은 case_search가 만드는 f"WEB-{유형코드}-{순번}"이다.
        """
        for peer_name, rec_name in (
            ("example_output_peer_downside.json", "rec_downside.json"),
            ("example_output_peer.json", "rec_run3.json"),
        ):
            peer = _load(peer_name)
            code = peer["risk_type"]
            self.assertIn(code, CODES, peer_name)
            self.assertEqual(peer["risk_type_label"], get_type(code).label, peer_name)

            available = set()
            for case in peer.get("cases", []):
                self.assertTrue(
                    case["case_id"].startswith(f"WEB-{code}-"),
                    f"{peer_name}: {case['case_id']}는 유형 {code}의 사례 ID 형식이 아님",
                )
                available.add(case["case_id"])

            # 골든 출력의 인용이 입력 사례 안에 있는가(규칙 2와 같은 불변식).
            # 코드를 재매핑할 때 한쪽만 고치면 여기서 걸린다.
            cited = set(_load(rec_name)["recommendation"]["cited_case_ids"])
            self.assertTrue(cited <= available, f"{rec_name}: {cited - available}")


class RecommendCallPlumbingTests(unittest.TestCase):
    """structured_call로의 배선 - 프롬프트 조립과 피드백 병합이 실제 호출에 닿는가."""

    @patch("app.services.response_engine.recommend.structured_call")
    def test_recommend_passes_prompts_and_schema(self, call):
        call.return_value = ({"ok": True}, {"input_tokens": 10, "output_tokens": 5, "calls": 1})
        rec, usage = recommend.recommend(_peer())
        self.assertEqual(rec, {"ok": True})
        self.assertEqual(usage["calls"], 1)
        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs["schema_name"], "peer_recommendation")
        self.assertIn("영향 경로", kwargs["user"])

    @patch("app.services.response_engine.recommend.structured_call")
    def test_feedback_appends_violations_and_previous(self, call):
        call.return_value = ({}, {"input_tokens": 0, "output_tokens": 0, "calls": 1})
        previous = _clean_rec()
        recommend.regenerate_with_feedback(_peer(), previous, ["위반 A"])
        user = call.call_args.kwargs["user"]
        self.assertIn("위반 A", user)
        self.assertIn("직전 추천방안", user)
        self.assertIn(previous["headline"], user)


class PeerContentGateTests(unittest.TestCase):
    """service._build_peer_content의 게이트·조립. DATABASE_URL 없으면 건너뛴다."""

    @classmethod
    def setUpClass(cls):
        try:
            from app.services.response_engine import service
        except Exception as exc:  # pydantic ValidationError 등 - DB 설정 없는 환경
            raise unittest.SkipTest(f"DATABASE_URL 환경이 없어 service를 불러올 수 없습니다: {exc}")
        cls.service = service

    def _payload(self):
        return AlertPayload.from_dict({
            "company_name": "쿠팡",
            "main_company_name": "11번가",
            "alert_id": "RE-1",
            "mentions": [{"mention_id": "m_1", "text": "유출 관련 반응",
                          "url": "https://news.example/1"}],
        })

    def _analysis(self, proceed: bool) -> dict:
        return {
            "risk_type": "R04", "impact_direction": "부정적_파급" if proceed else "영향_없음",
            "impact_level": "중간" if proceed else "없음",
            "impact_channels": ["규제_조사_확대"] if proceed else [],
            "reason": "이유", "watch_points": [], "confidence": 0.9,
            "needs_review": False, "keyword_hint": {},
            "proceed": proceed,
            "usage": {"input_tokens": 7, "output_tokens": 3, "calls": 1},
        }

    def test_no_impact_skips_cases_and_generation(self):
        svc = self.service
        with patch.object(svc.impact, "analyze", return_value=self._analysis(False)), \
             patch.object(svc, "TeamCaseRetriever") as retriever, \
             patch.object(svc.recommend, "recommend") as gen:
            content, urls, _ = svc._build_peer_content(None, self._payload())
        retriever.assert_not_called()
        gen.assert_not_called()
        self.assertEqual(content["status"], "영향없음_종료")
        self.assertIsNone(content["recommendation"])
        self.assertEqual(urls, set())
        self.assertEqual(content["usage"]["calls"], 1)  # 영향 판단 1회만

    def test_proceed_assembles_content_and_urls(self):
        svc = self.service
        case = SimpleNamespace(
            case_id="C-1", title="사례", summary_what="무슨 일", lesson="교훈",
            provenance="web_search", source_urls=["https://case.example/a"])
        retriever = MagicMock()
        retriever.search.return_value = [case]
        retriever.last_usage = {"input_tokens": 2, "output_tokens": 1, "calls": 1}
        rec = _clean_rec(
            recommendations=[dict(_clean_rec()["recommendations"][0],
                                  channel="규제_조사_확대")])
        with patch.object(svc.impact, "analyze", return_value=self._analysis(True)), \
             patch.object(svc, "TeamCaseRetriever", return_value=retriever), \
             patch.object(svc, "KoreanRegulationMapper") as mapper, \
             patch.object(svc.recommend, "recommend",
                          return_value=(rec, {"input_tokens": 5, "output_tokens": 4, "calls": 1})):
            mapper.return_value.lookup.return_value = []
            content, urls, _ = svc._build_peer_content(None, self._payload())
        self.assertEqual(content["status"], "생성완료")
        self.assertEqual(content["content_kind"], "peer_recommendation")
        self.assertTrue(content["verification"]["passed"])
        self.assertIn("https://case.example/a", urls)
        self.assertIn("https://news.example/1", urls)
        self.assertEqual(content["usage"]["calls"], 3)  # 판단 1 + 사례 1 + 생성 1


if __name__ == "__main__":
    unittest.main()
