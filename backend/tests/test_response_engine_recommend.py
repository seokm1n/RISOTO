"""동종기업 추천 생성(response_engine.recommend)과 서비스 연결의 검증.

픽스처는 독립 작업본에서 검수를 마친 실데이터 골든이다(tests/data/):
  - 부정적_파급: alert_peer_downside(2025-12 쿠팡 유출 실제 멘션) -> example_output_peer_downside
    (영향 판단 산출) -> rec_downside(추천 산출, 담당자 검수 완료)
  - 반사이익: example_output_peer(쿠팡 정산 이슈) -> rec_run3
  - 관점 교체: alert_peer_view_musinsa / _11st(위 downside 알림의 우리 기업만 교체,
    프로덕션 페이로드 모양으로 정렬) -> example_output_peer_noimpact(영향 판단 산출, 실측 대기)
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
from app.services.response_engine.impact import IMPACT_CHANNELS
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

    def test_all_impact_channels_render(self):
        """impact가 낼 수 있는 6개 채널이 모두 프롬프트에 실리는가.

        골든 2건이 다루는 채널은 4개뿐이라 공급망_협력사_공유·투자자_주가_동조는
        한 번도 지나간 적이 없다. 채널은 권고를 매다는 축(검증 규칙 1)이라 조립
        단계에서 빠지면 그 경로의 권고가 통째로 생기지 않는다.
        """
        for channel in IMPACT_CHANNELS:
            prompt = recommend.build_user_prompt(_peer(impact_channels=[channel]))
            self.assertIn("[영향 경로", prompt, channel)
            self.assertIn(channel, prompt, channel)

    def test_no_channel_switches_to_observation_only(self):
        """경로가 하나도 없으면 권고 대신 관찰만 제안하도록 지시가 바뀌는가.

        impact가 채널을 못 찾은 채 proceed를 냈을 때의 안전망이다. 권고는 채널에
        매달려야 하므로(규칙 1), 매달 곳이 없으면 만들지 말라고 해야 한다.
        """
        prompt = recommend.build_user_prompt(_peer(impact_channels=[]))
        self.assertIn("식별된 경로 없음", prompt)
        self.assertIn("관찰만 제안", prompt)


class RecommendChannelRuleTests(unittest.TestCase):
    """미검증 채널까지 포함해 규칙 1(채널 앵커)이 6종 전부에서 작동하는가."""

    def test_rule1_accepts_every_channel(self):
        """앞 단계가 식별한 채널이면 어느 것이든 통과해야 한다."""
        for channel in IMPACT_CHANNELS:
            rec = _clean_rec()
            rec["recommendations"][0]["channel"] = channel
            peer = _peer(impact_channels=[channel])
            self.assertEqual(recommend.verify_recommendation(rec, peer), [], channel)

    def test_rule1_rejects_every_unlisted_channel(self):
        """앞 단계가 식별하지 않은 채널이면 어느 것이든 걸러야 한다.

        규칙 1을 채널 하나(투자자_주가_동조)로만 확인해 왔는데, 다른 채널에서도
        같은 판정이 나오는지는 확인된 적이 없었다.
        """
        for channel in IMPACT_CHANNELS:
            others = [c for c in IMPACT_CHANNELS if c != channel]
            rec = _clean_rec()
            rec["recommendations"][0]["channel"] = channel
            violations = recommend.verify_recommendation(
                rec, _peer(impact_channels=others))
            self.assertTrue(
                any("영향 경로에 없는 채널" in v and channel in v for v in violations),
                f"{channel}이 경로 밖인데 걸리지 않음: {violations}",
            )


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


class PeerImpactViewFixtureTests(unittest.TestCase):
    """관점 교체 실험의 통제 조건과 영향_없음 골든(Phase 1-3·1-4).

    영향 판단은 동종 경로의 비용 통제 지점이다 - `proceed`가 False면 사례 검색도 추천
    생성도 부르지 않는다. 그런데 "같은 업계라는 이유만으로 영향 있음이 되지는 않는가",
    즉 **게이트에 판별력이 있는가**는 지금까지 스텁으로만 확인했다(PeerContentGateTests).

    실측 설계: 2025-12 쿠팡 유출 알림 하나를 고정하고 **우리 기업만** 갈아끼운다. 공유 DB
    실측(2026-08-31) 결과 프로덕션이 우리 기업에 대해 넘기는 것은 이름과 업종뿐인데, 등록된
    이커머스 기업(쿠팡·11번가·SSG·무신사·마켓컬리·에이블리·올리브영)의 업종이 전부 '유통'
    한 값이라 **두 arm의 프롬프트 차이는 회사 이름 하나**로 좁혀진다. 판정이 갈리면 원인이
    그것뿐이라는 뜻이다.

    아래 테스트들이 그 '고정'을 코드로 잠근다 - 누가 한쪽 픽스처만 손대면 대조 실험이
    조용히 깨지고, 그때는 판정이 갈려도 원인을 알 수 없게 된다.
    """

    VIEWS = ("alert_peer_view_musinsa.json", "alert_peer_view_11st.json")
    # 우리 기업이 누구냐에 따라 달라져야 하는 필드. 이것들 말고 달라지면 통제 조건 위반이다.
    OUR_COMPANY_FIELDS = {"alert_id", "my_company", "main_company_industry", "_provenance"}
    # 프로덕션 _payload_from_event가 채우지 않는 필드. 픽스처가 들고 있으면 실제로는
    # 존재하지 않는 입력으로 실험하게 된다.
    NOT_IN_PRODUCTION = ("main_services", "main_company_services")

    def test_arms_differ_only_in_our_company(self):
        a, b = (_load(name) for name in self.VIEWS)
        self.assertEqual(set(a), set(b), "두 arm의 키 구성이 다르다")
        for key in set(a) - self.OUR_COMPANY_FIELDS:
            self.assertEqual(a[key], b[key],
                             f"{key}가 arm마다 다름 - 우리 기업 외 변인이 섞였다")
        self.assertNotEqual(a["my_company"], b["my_company"])

    def test_arms_keep_the_real_data_alert_body(self):
        """알림 본체가 검수 완료된 실데이터 골든에서 온 그대로인가.

        본체가 손을 타면 이 실험의 근거가 '원경님 실데이터'에서 '누군가 고친 값'으로
        바뀐다. 출처 사슬을 코드로 붙들어 둔다.
        """
        base = _load("alert_peer_downside.json")
        for name in self.VIEWS:
            view = _load(name)
            self.assertEqual(view["mentions"], base["mentions"], name)
            for key in ("company_name", "window_start", "window_end", "mention_count",
                        "negative_ratio", "daily_series", "source_mix", "attribution"):
                self.assertEqual(view[key], base[key], f"{name}: {key}")

    def test_arms_match_production_payload_shape(self):
        """프로덕션이 실제로 만드는 페이로드 모양인가 (공유 DB 실측 기준).

        업종은 industries.name 한 값이고(계층 없음, 이커머스는 전부 '유통'), services
        계열은 _payload_from_event가 아예 채우지 않으며, 역할 값은 'main'|'competitor'다.
        손으로 쓴 기업 소개를 넣으면 게이트의 판별력이 아니라 그 문구의 설득력을 재게 된다.
        """
        for name in self.VIEWS:
            view = _load(name)
            for field in self.NOT_IN_PRODUCTION:
                self.assertNotIn(field, view,
                                 f"{name}: {field}는 프로덕션이 채우지 않는 필드다")
            self.assertEqual(view["company_role"], "competitor", name)
            for field in ("industry", "main_company_industry"):
                self.assertIn(view[field], ("유통", "IT·플랫폼"), f"{name}: {field}")

    def test_arms_record_measurement_basis(self):
        """비율 수치는 산정 기준과 함께 있어야 한다.

        같은 '90%'도 분자를 본문만 볼지 제목까지 볼지에 따라 달라진다(작업 로그에 75%와
        90%가 함께 남은 이유). 수치만 있고 기준이 없으면 나중에 재현이 안 된다.
        """
        for name in self.VIEWS:
            ratio = _load(name)["_provenance"]["peer_mention_ratio"]
            self.assertIn("basis", ratio)
            self.assertIn("window", ratio)
            self.assertGreater(ratio["negative"], 0,
                               "모집단이 0이면 비율 자체가 성립하지 않는다")
            self.assertAlmostEqual(ratio["ratio"],
                                   ratio["mentions_peer"] / ratio["negative"], places=3)

    def test_arms_span_the_connection_gap(self):
        """두 arm이 실제로 대비되는가.

        무신사(22%)와 11번가(90%)를 고른 이유가 '연결 강도의 양 끝'이기 때문이다. 픽스처를
        갈아끼우다 비슷한 두 회사가 되면 판정이 같게 나와도 그것이 '게이트가 못 가른다'인지
        '애초에 가를 것이 없었다'인지 구분할 수 없다.
        """
        ratios = {}
        for name in self.VIEWS:
            view = _load(name)
            ratios[view["my_company"]] = view["_provenance"]["peer_mention_ratio"]["ratio"]
        self.assertGreaterEqual(max(ratios.values()) - min(ratios.values()), 0.5, ratios)

    def test_noimpact_golden(self):
        """영향_없음 실측 골든이 게이트 계약을 지키는가.

        골든이 아직 없으면 건너뛴다 - 이 산출에는 LLM 호출이 필요한데 2026-08-31 현재
        OpenAI 크레딧이 소진돼 있다. 파일이 놓이는 순간 이 테스트가 살아난다.
        """
        path = DATA_DIR / "example_output_peer_noimpact.json"
        if not path.exists():
            self.skipTest(
                "영향_없음 골든 미생성(크레딧 대기). 생성 방법: cd backend && "
                "python -m scripts.run_peer_impact "
                "tests/data/alert_peer_view_musinsa.json --out-dir tests/data "
                "-> 판정이 영향_없음인 산출을 example_output_peer_noimpact.json으로 저장"
            )
        peer = json.loads(path.read_text(encoding="utf-8"))
        # 게이트 계약: 영향_없음이면 경로도 관찰 대상도 없고 다음 단계로 넘어가지 않는다.
        self.assertEqual(peer["impact_direction"], "영향_없음")
        self.assertFalse(peer["proceed"])
        self.assertEqual(peer["impact_channels"], [])
        self.assertEqual(peer["impact_level"], "없음")
        self.assertEqual(peer["watch_points"], [])
        self.assertEqual(peer["status"], "영향없음_종료")
        self.assertEqual(peer["cases"], [])
        # 어느 모델이 낸 판정인지 남아 있어야 나중에 산출끼리 비교가 된다.
        self.assertTrue(peer["versions"]["model"], "versions.model이 비어 있다")
        # 픽스처 코드 가드(test_fixture_codes_match_engine과 같은 취지).
        self.assertIn(peer["risk_type"], CODES)
        self.assertEqual(peer["risk_type_label"], get_type(peer["risk_type"]).label)


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
        except Exception as exc:  # pydantic ValidationError, 드라이버 미설치 등
            # 원인을 DATABASE_URL로 단정하지 않는다 - 실제로는 DB 드라이버(psycopg) 미설치로
            # 걸리는 경우가 있고, 메시지가 원인을 가리키면 엉뚱한 곳을 고치게 된다.
            raise unittest.SkipTest(f"service를 불러올 수 없어 건너뜁니다: {exc}")
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
