"""메인 기업 대응방안 생성을 DB 없이 끝까지 돌려보는 단독 실행 스크립트.

    cd backend
    python -m scripts.run_main_response tests/data/alert_peer_downside.json --dry-run
    python -m scripts.run_main_response tests/data/alert_peer_downside.json --out-dir out/

`run_peer_impact.py`/`run_peer_recommend.py`의 메인 경로 짝이다. 저쪽이 동종 기업
경로(영향 판단 -> 추천)를 다룬다면 이쪽은 메인 기업 경로(유형 세분화 -> 등급 ->
근거 수집 -> 시나리오 생성 -> 검증)를 통째로 돈다.

**왜 필요한가**: 동종 경로는 PR #23에서 실측했지만 메인 경로는 유닛테스트와 스텁만
거쳤다. 라우터를 v3로 바꾸는 순간 위기 이벤트마다 이 경로가 자동으로 도는데, 그때
처음 실패를 보면 곤란하다. 전환 전에 한 번은 진짜 모델로 끝까지 돌려봐야 한다.

**DB 없이 도는 이유**: `_build_content`가 db를 쓰는 곳은 `TeamCaseRetriever` 하나뿐이고,
이 클래스는 db=None이면 검수 사례 조회를 건너뛴다(지금 case_records가 0행이라 실제
동작도 같다). event는 탐지 유형 점수를 읽는 용도라 가벼운 스텁으로 대체한다.

**--dry-run은 비용을 미리 잰다**: 조립된 프롬프트의 글자 수를 세어 호출 횟수와 함께
보여준다. 라우터 전환 시 자동 생성 비용을 추산하는 근거가 된다. LLM은 부르지 않는다.

**설정**: `--dry-run`은 OPENAI_API_KEY 없이 돈다(Settings가 DATABASE_URL 형식은
요구하므로 접속하지 않는 더미라도 있어야 한다). 실제 호출에는 키가 필요하다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _load_env() -> None:
    """저장소 루트의 .env를 읽어 온다. 컨테이너 밖 실행용."""
    env = Path("../.env")
    if not env.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env)


class _EventStub:
    """service._detection_scores가 읽는 필드만 갖춘 대역.

    실제 RiskEvent를 흉내내지 않는다 - 이 경로가 event에서 읽는 것은 유형 점수뿐이다.
    필드를 더 늘리면 진짜 모델과 어긋났을 때 알아채기 어려워지므로 최소로 둔다.
    """

    def __init__(self, primary_type: str, scores: dict | None = None) -> None:
        self.primary_type = primary_type
        self.risk_type_scores = scores


def _payload_from_file(path: Path):
    from app.services.response_engine.schema import AlertPayload

    raw = json.loads(path.read_text(encoding="utf-8"))
    # 동종 픽스처를 그대로 재사용할 수 있게 한다. 메인 경로는 company_role이 main이어야
    # 하고 main_company_* 필드는 쓰지 않는다.
    raw = dict(raw)
    raw["company_role"] = "main"
    return AlertPayload.from_dict(raw)


def _estimate(payload, code: str) -> dict:
    """LLM을 부르지 않고 프롬프트 크기와 호출 횟수를 센다."""
    from app.services.response_engine import evidence, generate, tier
    from app.services.response_engine.case_search import TeamCaseRetriever
    from app.services.response_engine.rag import RagPrincipleProvider
    from app.services.response_engine.retrieval import KoreanRegulationMapper

    decision = tier.decide(code, payload)
    stances = (
        generate.DEFAULT_STANCES if decision.tier == "T3_긴급" else generate.DEFAULT_STANCES[:2]
    )
    ev = evidence.build(
        payload, code,
        case_retriever=TeamCaseRetriever(company_name=payload.company_name, db=None),
        regulation_mapper=KoreanRegulationMapper(),
    )
    provider = RagPrincipleProvider()
    per_stance = []
    for stance in stances:
        system = generate.build_system_prompt(code, payload, ev, provider=provider, stance=stance)
        user = generate.build_user_prompt(payload, ev)
        per_stance.append({"stance": stance, "system_chars": len(system), "user_chars": len(user)})

    total_chars = sum(s["system_chars"] + s["user_chars"] for s in per_stance)
    return {
        "risk_type": code,
        "tier": decision.tier,
        "stances": per_stance,
        "scenario_calls": len(stances),
        "prompt_chars_total": total_chars,
        # 한국어는 대략 1.4~2.2자/토큰 구간이다. 상·하한을 같이 내야 과소평가하지 않는다.
        "input_tokens_est": [round(total_chars / 2.2), round(total_chars / 1.4)],
        "note": (
            "시나리오 생성만 센 값이다. 사례 검색 1회(LLM), 중복 제거 임베딩, 검증 실패 시 "
            "시나리오당 재생성 1회가 추가될 수 있다."
        ),
    }


def run(path: Path, dry_run: bool, out_dir: Path | None, primary_type: str) -> dict:
    from app.services.response_engine import classify, service

    payload = _payload_from_file(path)
    event = _EventStub(primary_type)

    cls = classify.refine(payload, service._detection_scores(event), allow_llm=not dry_run)
    code = cls["risk_type"]

    if dry_run:
        est = _estimate(payload, code)
        est["classify"] = cls
        return est

    content, allowed_urls, model_name = service._build_content(
        None, payload, event, service.MAIN_RESPONSE, None
    )
    result = {
        "source_fixture": path.name,
        "model_name": model_name,
        "allowed_urls": sorted(allowed_urls),
        "content": content,
        "_provenance": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runner": "scripts/run_main_response.py",
            "db": None,
            "note": "DB 없이 실행 - 검수 사례 조회를 건너뛴다(case_records가 0행이라 실동작과 같다).",
        },
    }
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"main_response_{path.stem}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장 -> {out}")
    return result


def _summarize(result: dict) -> None:
    content = result.get("content")
    if content is None:  # dry-run
        print(f"유형 {result['risk_type']} / 등급 {result['tier']}")
        for s in result["stances"]:
            print(f"  {s['stance']:<14} system {s['system_chars']:>6,}자  user {s['user_chars']:>5,}자")
        lo, hi = result["input_tokens_est"]
        print(f"  시나리오 호출 {result['scenario_calls']}회 / 입력 {result['prompt_chars_total']:,}자"
              f" ≈ {lo:,}~{hi:,} 토큰")
        print(f"  * {result['note']}")
        return

    print(f"상태 {content['status']} / 유형 {content['risk_type_label']} / 등급 {content['tier']}")
    print(f"모델 {result['model_name']}")
    u = content.get("usage", {})
    print(f"사용량 입력 {u.get('input_tokens',0):,} / 출력 {u.get('output_tokens',0):,} / 호출 {u.get('calls',0)}회")
    for i, sc in enumerate(content.get("scenarios", []), 1):
        v = sc.get("verification", {})
        mark = "통과" if v.get("passed") else f"위반 {len(v.get('violations') or [])}건"
        print(f"  안 {i}: {sc.get('stance','?'):<14} 검증 {mark}")
    print(f"근거 {len(content.get('evidence', []))}건 / 사례 {len(content.get('precedents', []))}건"
          f" / 법령 {len(content.get('regulations', []))}건")


def parse_args():
    p = argparse.ArgumentParser(description="메인 기업 대응방안 생성 단독 실행")
    p.add_argument("payload", type=Path, help="알림 페이로드 JSON")
    p.add_argument("--dry-run", action="store_true", help="LLM 없이 프롬프트 크기·호출 횟수만 계산")
    p.add_argument("--out-dir", type=Path, default=None, help="산출 JSON 저장 위치")
    p.add_argument("--primary-type", default="reputation_consumer",
                   help="상단 탐지 유형(8개 중 하나). 기본값 reputation_consumer")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    _load_env()
    if not args.payload.exists():
        print(f"페이로드를 찾을 수 없습니다: {args.payload}", file=sys.stderr)
        return 1
    if not args.dry_run and not os.environ.get("OPENAI_API_KEY"):
        print("실제 호출에는 OPENAI_API_KEY가 필요합니다. --dry-run을 쓰세요.", file=sys.stderr)
        return 1
    result = run(args.payload, args.dry_run, args.out_dir, args.primary_type)
    _summarize(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
