"""동종기업 추천을 DB 없이 한 번 돌려보는 단독 실행 스크립트.

    cd backend
    python -m scripts.run_peer_recommend tests/data/example_output_peer_downside.json --dry-run
    python -m scripts.run_peer_recommend tests/data/example_output_peer_downside.json --out rec.json

`service._build_peer_content`의 **뒷부분**(법령 주입 → 추천 생성 → 검증 → 재생성)만 떼어
낸 것이다. 앞부분인 영향 판단(`impact.analyze`)은 이미 끝난 결과를 파일로 받으므로 LLM
호출이 추천 1회(+검증 실패 시 재생성 1회)뿐이고, DB 세션도 필요 없다.

**왜 필요한가**: 엔진으로 이식한 뒤로는 추천 한 번을 확인하려고 파이썬 한 줄짜리를 매번
새로 써야 했다. 골든 점검·프롬프트 튜닝·시연에 계속 쓰이는 동작이라 스크립트로 굳힌다.

**service와 중복되는 부분을 최소로 둔 이유**: `service`를 직접 임포트하면 sqlalchemy와
DB 설정이 딸려와 "DB 없이 돌린다"는 목적이 깨진다. 그래서 법령 주입과 재생성 정책만
아래에 옮겨 적고, 옮긴 지점에 주석으로 원본을 가리켜 둔다. 그쪽이 바뀌면 여기도 같이
고쳐야 한다.

**입력 파일**: `impact.analyze`가 낸 결과(= tests/data/example_output_peer_*.json 모양).
`risk_type`·`impact_direction`·`impact_channels`·`cases` 등이 이미 채워져 있어야 한다.

**설정**: 프롬프트만 보는 `--dry-run`은 아무 설정 없이 돌아간다. 실제 생성은
`app.config.Settings`가 `DATABASE_URL`을 요구하므로(DB에 접속하지는 않는다) 값이
형식만 맞으면 된다. `OPENAI_API_KEY`도 필요하다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app.services.response_engine import recommend
from app.services.response_engine.recommend_schema import TIMEFRAMES
from app.services.response_engine.retrieval import KoreanRegulationMapper

# service.MAX_FEEDBACK_RETRIES와 같은 값. 메인 경로의 시나리오별 1회 재생성과 같은 정책이다.
MAX_FEEDBACK_RETRIES = 1


def inject_regulations(peer: dict) -> int:
    """유형에 맞는 시행 중인 법령을 peer에 실어 준다. 반환: 실린 건수.

    `service._build_peer_content`의 법령 주입부를 그대로 옮긴 것이다. 두 가지가 그쪽
    결정이라 함께 지킨다.
      - `include_upcoming=False`: 동종 경로의 [참고 법령]은 한 줄짜리 목록이라 메인
        경로처럼 [시행 예정] 블록으로 구분할 수 없다. 그래서 조회 시점에 거른다.
      - 적용 요건(applicability_note)을 요건 문장에 합친다: 원사업자 해당 여부 등은
        회사마다 달라 사람이 확인해야 하는 값인데, 프롬프트 렌더러가 law_name·article·
        requirement 세 키만 읽으므로 여기서 합쳐야 단서가 살아남는다.
    """
    regs = KoreanRegulationMapper(include_upcoming=False).lookup(str(peer.get("risk_type")))
    peer["regulations"] = [
        {
            "law_name": r.law_name,
            "article": r.article,
            "requirement": (
                r.requirement
                + (f" (※ 적용 요건 확인 필요: {r.applicability_note})" if r.applicability_note else "")
            ),
        }
        for r in regs
    ]
    return len(peer["regulations"])


def _timeframe_key(item: dict) -> int:
    """TIMEFRAMES에 없는 값은 맨 뒤로. 같은 시점끼리는 모델이 낸 순서를 유지한다."""
    tf = item.get("timeframe")
    return TIMEFRAMES.index(tf) if tf in TIMEFRAMES else len(TIMEFRAMES)


def print_report(rec: dict, peer: dict) -> None:
    """담당자가 보는 순서대로 출력한다 - 상황 -> 행동 -> 경로별 권고 -> 금지 -> 재경보.

    권고는 앞 단계가 식별한 영향 경로 순으로 묶고, 경로 안에서는 시점 순으로 낸다.
    정렬하지 않으면 목록 위쪽이 '먼저 할 일'로 읽히는데 실제로는 모델이 낸 순서일 뿐이다.
    경로 밖 권고(검증 규칙 1 위반)는 버리지 않고 맨 아래 '경로 불명'으로 모은다 -
    검증 실패 건도 사람이 보고 판단해야 하므로 출력에서 사라지면 안 된다.
    """
    print("\n" + "=" * 72)
    print(f"[{peer.get('company_name')} · {peer.get('risk_type_label')}] "
          f"{peer.get('impact_direction')} / 노출 {peer.get('impact_level')} "
          f"/ 확신도 {peer.get('confidence')}")
    print("-" * 72)
    print(rec.get("source_event", ""))
    print("=" * 72)
    print(rec.get("headline", ""))

    items = rec.get("recommendations") or []
    channels = peer.get("impact_channels") or []
    groups = [(c, [r for r in items if r.get("channel") == c]) for c in channels]
    orphans = [r for r in items if r.get("channel") not in channels]
    if orphans:
        groups.append(("경로 불명", orphans))

    n = 0
    for name, group in groups:
        if not group:
            # 앞 단계가 식별한 경로인데 권고가 없는 것은 그 자체로 봐야 할 신호다.
            print(f"\n[권고 · {name}] (권고 없음)")
            continue
        print(f"\n[권고 · {name}]")
        for item in sorted(group, key=_timeframe_key):
            n += 1
            flag = " ※확인 후 대외 답변" if item.get("verify_first") else ""
            print(f"  {n}. ({item.get('timeframe')}) {item.get('action')}{flag}")
            if name == "경로 불명":
                print(f"     경로: {item.get('channel')} ※영향 경로에 없음")
            print(f"     담당: {item.get('owner_hint')}")
            print(f"     이유: {item.get('rationale')}")

    if rec.get("avoid"):
        print("\n[하지 말 것]")
        for a in rec["avoid"]:
            print(f"  - {a}")

    print(f"\n[재경보 조건]\n  {rec.get('realert_condition')}")
    print(f"\n[한계]\n  {rec.get('limitations')}")

    cited = rec.get("cited_case_ids") or []
    if cited:
        by_id = {c.get("case_id"): c for c in (peer.get("cases") or [])}
        print("\n[인용 사례]")
        for cid in cited:
            case = by_id.get(cid, {})
            tag = " ※미검수" if case.get("provenance") == "web_search" else ""
            print(f"  {case.get('title', cid)}{tag}")
            for url in case.get("urls") or case.get("source_urls") or []:
                print(f"    {url}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="동종기업 추천 단독 실행")
    parser.add_argument("peer", help="impact.analyze 결과 JSON 경로")
    parser.add_argument("--dry-run", action="store_true",
                        help="조립된 프롬프트만 출력하고 끝낸다 (LLM 호출 없음)")
    parser.add_argument("--out", help="결과 JSON 저장 경로")
    args = parser.parse_args()

    peer = json.loads(Path(args.peer).read_text(encoding="utf-8"))

    # 앞 단계가 여기까지 오지 않았어야 하는 건을 걸러낸다(service의 proceed 게이트와 같은 취지).
    if peer.get("impact_direction") == "영향_없음":
        print("[중단] 영향 없음으로 판정된 건은 추천을 만들지 않는다")
        return

    n_regs = inject_regulations(peer)
    print(f"[법령] {peer.get('risk_type')} 시행 중인 조문 {n_regs}건 주입")

    if args.dry_run:
        print("\n" + "=" * 72 + "\n[SYSTEM]\n" + "=" * 72)
        print(recommend.build_system_prompt(peer))
        print("\n" + "=" * 72 + "\n[USER]\n" + "=" * 72)
        print(recommend.build_user_prompt(peer))
        return

    if not os.environ.get("DATABASE_URL"):
        # app.config.Settings가 database_url을 필수로 요구한다. 접속하지는 않으므로
        # 형식만 맞으면 되지만, 없으면 여기서 알려주는 편이 낫다.
        sys.exit(
            "[중단] DATABASE_URL이 필요합니다. DB에 접속하지는 않지만 설정 로딩에 필요합니다.\n"
            '  예: $env:DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/d"'
        )

    usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    def add(u: dict | None) -> None:
        for key in usage:
            usage[key] += (u or {}).get(key, 0)

    rec, gen_usage = recommend.recommend(peer)
    add(gen_usage)
    violations = recommend.verify_recommendation(rec, peer)
    for attempt in range(1, MAX_FEEDBACK_RETRIES + 1):
        if not violations:
            break
        print(f"[재생성 {attempt}/{MAX_FEEDBACK_RETRIES}] 검증 실패 {len(violations)}건:")
        for v in violations:
            print(f"  - {v}")
        rec, retry_usage = recommend.regenerate_with_feedback(peer, rec, violations)
        add(retry_usage)
        violations = recommend.verify_recommendation(rec, peer)

    print_report(rec, peer)
    if violations:
        print("[검증 실패] 재생성 후에도 남은 위반:")
        for v in violations:
            print(f"  - {v}")
        print("  -> 검증실패 표시를 달아 사람에게 올린다\n")
    else:
        print("[검증 통과] 6규칙\n")
    print(f"토큰: 입력 {usage['input_tokens']} / 출력 {usage['output_tokens']} "
          f"/ 호출 {usage['calls']}회")

    if args.out:
        payload = {
            "peer_company_name": peer.get("company_name"),
            "main_company_name": peer.get("main_company_name"),
            "risk_type": peer.get("risk_type"),
            "recommendation": rec,
            "regulations": peer.get("regulations"),
            "verification": {"passed": not violations, "violations": violations},
            "usage": usage,
            "recommender_version": recommend.RECOMMENDER_VERSION,
            "status": "검증실패" if violations else "생성완료",
        }
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"저장: {args.out}")


if __name__ == "__main__":
    main()
