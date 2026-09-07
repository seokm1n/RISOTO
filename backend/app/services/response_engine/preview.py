"""DB 없이 대응방안 생성을 돌려보는 확인용 도구.

라우터를 바꾸기 전에 이 엔진의 산출물을 눈으로 보려면 이걸 쓴다. RiskEvent를 읽지
않고 JSON 페이로드를 직접 받으므로 DB·마이그레이션 없이 실행된다.

    python -m app.services.response_engine.preview --input <알림.json> --dry-run
    python -m app.services.response_engine.preview --input <알림.json> --out result.json

--dry-run은 LLM을 호출하지 않고 조립된 프롬프트만 출력한다. 원칙·법령·RAG 보충이
실제로 어떻게 들어가는지 비용 없이 확인할 수 있다.

**프런트에서는 아직 안 보인다**: frontend가 schema_version 2 구조(risk_summary,
scenario.rationale, recommended_actions)를 직접 읽고 있어서, 이 엔진의 v3 구조는
화면에 그대로 렌더링되지 않는다. 프런트 대응 전까지는 이 도구로 확인한다.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from . import classify, evidence, generate, tier, verify
from .retrieval import KoreanRegulationMapper
from .risk_types import get as get_type
from .schema import AlertPayload


def _detection_of(payload: AlertPayload) -> str:
    """페이로드의 탐지 유형. 없으면 전체에서 자유 분류한다.

    AlertPayload에 detection_type 속성은 없다 - 예전에 그 이름으로 읽고 있어서 항상
    기본값으로 떨어졌다. 상단 유형은 risk_type_scores(멀티라벨)나 primary_type으로 온다.
    """
    scores = getattr(payload, "risk_type_scores", None)
    if isinstance(scores, dict) and scores:
        return max(scores.items(), key=lambda kv: kv[1])[0]
    return getattr(payload, "primary_type", None) or "reputation_consumer"


def preview(payload: AlertPayload, detection: str, dry_run: bool, use_search: bool):
    from .rag import RagPrincipleProvider

    provider = RagPrincipleProvider()
    cls = classify.refine(payload, detection, allow_llm=not dry_run)
    code = cls["risk_type"]
    rt = get_type(code)
    decision = tier.decide(code, payload)

    retriever = None
    if use_search and not dry_run:
        from .case_search import TeamCaseRetriever

        retriever = TeamCaseRetriever(company_name=payload.company_name)
    ev = evidence.build(
        payload, code, case_retriever=retriever, regulation_mapper=KoreanRegulationMapper()
    )

    header = [
        f"분류: {code} {rt.label}  (상위 {rt.parent} / 경로 {cls['route']} / 확신도 {cls['confidence']:.2f})",
        f"담당 주체: {rt.stakeholder.value}",
        f"대응 등급: {decision.tier} — {decision.policy}",
        f"근거: 원문 {len(ev.mentions)}건 · 사례 {len(ev.cases)}건 · 법령 {len(ev.regulations)}건",
        f"원칙: {provider.version}",
    ]
    if cls.get("needs_review"):
        header.append("검토 필요 플래그 켜짐")
    for n in decision.notes + cls.get("notes", []):
        header.append(f"비고: {n}")

    if dry_run:
        return "\n".join([
            "=== 파이프라인 상태 ===", *header,
            "\n=== SYSTEM 프롬프트 ===",
            generate.build_system_prompt(code, payload, ev, provider=provider),
            "\n=== USER 프롬프트 ===",
            generate.build_user_prompt(payload, ev),
        ]), None

    stances = (
        generate.DEFAULT_STANCES if decision.tier == "T3_긴급" else generate.DEFAULT_STANCES[:2]
    )
    drafts, usage = generate.generate_scenarios(payload, code, ev, stances=stances, provider=provider)
    checked = [(d, verify.verify(d, ev, code)) for d in drafts]
    kept_drafts, notes, _ = generate.dedupe_scenarios([d for d, _ in checked])
    kept_ids = {id(d) for d in kept_drafts}
    kept = [(d, v) for d, v in checked if id(d) in kept_ids]

    result = {
        "risk_type": code, "risk_type_label": rt.label, "detection_type": rt.parent,
        "stakeholder": rt.stakeholder.value, "tier": decision.tier,
        "classification": cls, "scenario_notes": notes,
        "scenarios": [
            {"stance": d.get("scenario_stance", "?"), "tradeoff": d.get("scenario_tradeoff", ""),
             "merged_stances": d.get("merged_stances", []), "report": d,
             "verification": {"passed": v.passed, "summary": v.summary(),
                              "rules": [asdict(r) for r in v.results],
                              "violations": v.violations, "skipped": v.skipped}}
            for d, v in kept
        ],
        "usage": usage,
    }
    lines = ["=== 결과 ===", *header, f"시나리오 {len(kept)}개"]
    for i, (d, v) in enumerate(kept, 1):
        merged = f" +{','.join(d.get('merged_stances', []))} 합침" if d.get("merged_stances") else ""
        lines.append(f"  {i}. {d.get('scenario_stance','?')}{merged} — {v.summary()}")
        for viol in v.violations:
            lines.append(f"       위반: {viol}")
    for n in notes:
        lines.append(f"  · {n}")
    lines.append(f"토큰: 입력 {usage['input_tokens']:,} / 출력 {usage['output_tokens']:,} / 호출 {usage['calls']}회")
    return "\n".join(lines), result


def main() -> None:
    ap = argparse.ArgumentParser(description="대응방안 생성 확인용 (DB 불필요)")
    ap.add_argument("--input", type=Path, required=True, help="알림 JSON 경로")
    ap.add_argument("--detection", default=None, help="탐지 유형(팀 8개 코드). 없으면 페이로드에서 추정")
    ap.add_argument("--out", type=Path, help="결과 JSON 저장 경로")
    ap.add_argument("--dry-run", action="store_true", help="LLM 호출 없이 프롬프트만 출력")
    ap.add_argument("--search", action="store_true", help="유사 사례 검색 사용(네이버·Tavily 키 필요)")
    args = ap.parse_args()

    payload = AlertPayload.from_dict(json.loads(args.input.read_text(encoding="utf-8")))
    detection = args.detection or _detection_of(payload)
    text, result = preview(payload, detection, args.dry_run, args.search)
    print(text)
    if args.out and result is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장 -> {args.out}")


if __name__ == "__main__":
    main()
