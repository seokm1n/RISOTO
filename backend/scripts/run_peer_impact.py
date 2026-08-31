"""동종 기업 영향 판단을 DB 없이 돌려보는 단독 실행 스크립트 (추천 앞 단계).

    cd backend
    python -m scripts.run_peer_impact ../fixtures/alert_peer_view_musinsa.json --dry-run
    python -m scripts.run_peer_impact ../fixtures/alert_peer_view_*.json --out-dir out/

`run_peer_recommend.py`의 **앞 단계**다. 저쪽이 "영향 판단이 끝난 결과 -> 추천"이라면
이쪽은 "알림 페이로드 -> 영향 판단"이고, 이 스크립트의 `--out-dir` 산출이 그대로
`run_peer_recommend.py`의 입력이 된다. 둘을 이으면 DB 없이 동종 경로 전체를 돈다.

**왜 필요한가**: `impact.analyze`는 `service._build_peer_content` 안에서만 불려서
지금까지 DB 없이 한 번 돌려볼 방법이 없었다. 그런데 이 함수가 동종 경로의 비용 통제
지점(`proceed` 게이트)이라 판별력을 측정해 두어야 한다.

**페이로드를 여러 개 주면 비교표가 나온다**: 같은 사건에 우리 기업만 바꾼 픽스처들을
한 번에 돌려 "우리가 누구냐"가 판정을 얼마나 흔드는지 본다(관점 교체 실험).
`_provenance.peer_mention_ratio`가 실린 픽스처는 그 값도 표에 같이 세운다.

**--dry-run이 프롬프트를 실제 조립 경로로 뽑는 방법**: 프롬프트 조립을 여기 옮겨 적으면
`impact.analyze`가 바뀔 때 조용히 어긋난다. 그래서 베끼지 않고 **LLM 호출 지점만 가로챈다**
(`impact.structured_call` 자리에 출력용 함수를 끼움). 조립은 진짜 코드가 하고 비용만 0이다.

**설정**: `--dry-run`은 아무 설정 없이 돌아간다. 실제 호출은 `app.config.Settings`가
`DATABASE_URL`을 요구하므로(접속하지는 않는다) 형식만 맞으면 되고, `OPENAI_API_KEY`가 있어야 한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app.services.response_engine import impact
from app.services.response_engine._llm import response_model
from app.services.response_engine.risk_types import get as get_type
from app.services.response_engine.schema import AlertPayload

# service._build_peer_content가 analyze 결과를 담는 peer dict와 같은 모양으로 낸다.
# 이 모양이어야 run_peer_recommend.py와 tests/data의 골든이 그대로 읽는다.


def _print_prompts(payload: AlertPayload) -> None:
    """LLM 호출 지점을 가로채 조립된 프롬프트만 출력한다(비용 0)."""
    original = impact.structured_call

    # structured_call과 같은 시그니처로 받는다 - 키워드 전용으로 좁히면 호출 쪽이
    # 위치 인자로 바뀌거나 model=을 넘길 때 TypeError로 죽는다.
    def spy(system, user, schema, schema_name, model=None):
        print("\n" + "=" * 72 + f"\n[SYSTEM] schema={schema_name}\n" + "=" * 72)
        print(system)
        print("\n" + "=" * 72 + "\n[USER]\n" + "=" * 72)
        print(user)
        # analyze의 뒷부분이 읽는 키만 채운 자리표시자. 값은 의미 없다 - dry-run은
        # 여기까지만 보고 표도 저장도 하지 않는다.
        return ({"risk_type": "R04", "impact_direction": "영향_없음", "impact_level": "없음",
                 "impact_channels": [], "reason": "(dry-run)", "watch_points": [],
                 "confidence": 0.0},
                {"input_tokens": 0, "output_tokens": 0, "calls": 0})

    impact.structured_call = spy
    try:
        impact.analyze(payload)
    finally:
        impact.structured_call = original


def _peer_dict(raw: dict, payload: AlertPayload, analysis: dict, model_name: str) -> dict:
    """service._build_peer_content의 peer dict를 그대로 옮긴 것. 저쪽이 바뀌면 여기도."""
    rt = get_type(analysis["risk_type"])
    return {
        "company_name": payload.company_name,
        "main_company_name": payload.main_company_name,
        "alert_id": payload.alert_id,
        "role": "peer",
        "risk_type": analysis["risk_type"],
        "risk_type_label": rt.label,
        "impact_direction": analysis["impact_direction"],
        "impact_level": analysis["impact_level"],
        "impact_channels": analysis["impact_channels"],
        "reason": analysis["reason"],
        "watch_points": analysis["watch_points"],
        "confidence": analysis["confidence"],
        "needs_review": analysis["needs_review"],
        "proceed": analysis["proceed"],
        "status": "추천대기" if analysis["proceed"] else "영향없음_종료",
        "missing_input_fields": payload.missing_fields(),
        "cases": [],
        "regulations": [],
        "usage": analysis["usage"],
        "versions": {
            "impact": impact.IMPACT_VERSION,
            # 어느 모델이 낸 판정인지 산출에 남긴다. 실측의 목적 자체가 "배포 모델에서
            # 어떻게 나오는가"라, 모델명이 없으면 나중에 산출끼리 비교가 안 된다
            # (기존 골든의 versions.model이 gpt-5.4인 것도 이 기록 덕에 구분된다).
            "model": model_name,
            "upstream_model": raw.get("model_version"),
        },
        "_provenance": {
            "note": "scripts/run_peer_impact.py로 impact.analyze를 1회 실행한 실측 산출.",
            "input_fixture": raw.get("_alert_source"),
            "peer_mention_ratio": (raw.get("_provenance") or {}).get("peer_mention_ratio"),
        },
    }


def _ratio_text(raw: dict) -> str:
    r = ((raw.get("_provenance") or {}).get("peer_mention_ratio") or {}).get("ratio")
    return f"{r * 100:3.0f}%" if isinstance(r, (int, float)) else "  -"


def print_table(rows: list[tuple[dict, dict]]) -> None:
    """우리 기업별 판정을 한 눈에. 언급 비율 높은 순 = 사건과 가까울 것으로 기대되는 순."""
    print("\n" + "=" * 96)
    print(f"{'우리 기업':<9}{'쿠팡언급':>8}  {'유형':<6}{'방향':<10}{'노출':<6}{'확신':>5}  {'게이트':<7}영향 경로")
    print("-" * 96)
    def _sort_key(rp):
        r = ((rp[0].get("_provenance") or {}).get("peer_mention_ratio") or {}).get("ratio")
        # 비율 미상은 맨 뒤로. `or -1`을 쓰면 0.0(유효한 값)이 미상과 같은 자리에 간다.
        return -r if isinstance(r, (int, float)) else 1

    for raw, peer in sorted(rows, key=_sort_key):
        gate = "통과" if peer["proceed"] else "차단(영향없음)"
        review = "*" if peer["needs_review"] else " "
        print(f"{peer['main_company_name']:<9}{_ratio_text(raw):>8}  {peer['risk_type']:<6}"
              f"{peer['impact_direction']:<10}{peer['impact_level']:<6}"
              f"{peer['confidence']:>5.2f}{review} {gate:<7}"
              f"{', '.join(peer['impact_channels']) or '-'}")
    print("=" * 96)
    print("* = confidence < 0.6 (needs_review, 사람 확인 대상)")


def main() -> None:
    parser = argparse.ArgumentParser(description="동종 기업 영향 판단 단독 실행")
    parser.add_argument("payloads", nargs="+", help="알림 페이로드 JSON 경로(여러 개면 비교표)")
    parser.add_argument("--dry-run", action="store_true",
                        help="조립된 프롬프트만 출력하고 끝낸다 (LLM 호출 없음)")
    parser.add_argument("--out-dir", help="판정 결과 JSON을 저장할 디렉터리")
    args = parser.parse_args()

    if not args.dry_run:
        # app.config.Settings는 SettingsConfigDict에 env_file이 없어 **환경변수만** 읽는다
        # (.env는 docker compose가 컨테이너에 주입하는 용도). 로컬에서 스크립트로 돌릴 때는
        # 직접 넣어야 하고, 없으면 LLM 호출 직전에야 알게 되므로 여기서 먼저 막는다.
        missing = [k for k in ("DATABASE_URL", "OPENAI_API_KEY") if not os.environ.get(k)]
        if missing:
            sys.exit(
                """[중단] 환경변수가 없습니다: """ + ", ".join(missing) + """

app.config.Settings는 .env 파일을 읽지 않고 환경변수만 봅니다. DATABASE_URL은 설정
로딩에만 쓰이고 실제로 접속하지는 않으므로 형식만 맞으면 됩니다.

  PowerShell에서 저장소 루트 .env를 그대로 올리려면:
    Get-Content .env | Where-Object { $_ -match '^[A-Z_]+=' } | ForEach-Object {
        $k, $v = $_ -split '=', 2; Set-Item -Path "env:$k" -Value $v }
  또는 직접:
    $env:DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/d"
    $env:OPENAI_API_KEY="sk-..." """
            )

    rows: list[tuple[dict, dict]] = []
    failed: list[tuple[str, str]] = []
    total = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    model_name = "" if args.dry_run else response_model()

    for path in args.payloads:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        raw["_alert_source"] = Path(path).name
        payload = AlertPayload.from_dict(raw)
        print(f"\n### {Path(path).name} — {payload.company_name} 사안을 "
              f"{payload.main_company_name} 관점으로")

        if args.dry_run:
            _print_prompts(payload)
            continue

        # 한 건이 실패해도 스윕 전체를 버리지 않는다. 이미 지불한 호출의 결과와
        # 비교표가 통째로 사라지는 것을 막기 위한 것으로, 크레딧이 빠듯할수록 중요하다.
        try:
            analysis = impact.analyze(payload)
        except Exception as exc:
            failed.append((Path(path).name, f"{type(exc).__name__}: {exc}"))
            print(f"  -> [실패] {type(exc).__name__}: {str(exc)[:160]}")
            continue

        for k in total:
            total[k] += (analysis.get("usage") or {}).get(k, 0)
        peer = _peer_dict(raw, payload, analysis, model_name)
        rows.append((raw, peer))
        print(f"  -> {peer['risk_type']}({peer['risk_type_label']}) / "
              f"{peer['impact_direction']} / 노출 {peer['impact_level']} / "
              f"확신 {peer['confidence']:.2f} / 게이트 "
              f"{'통과' if peer['proceed'] else '차단'}")
        print(f"     근거: {peer['reason'][:100]}")

        if args.out_dir:
            out_dir = Path(args.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = Path(path).stem
            # 입력과 같은 디렉터리를 지정해도 원본을 덮어쓰지 않게 접두사를 보장한다.
            stem = (stem.replace("alert_peer_", "impact_peer_") if stem.startswith("alert_peer_")
                    else f"impact_{stem}")
            out = out_dir / f"{stem}.json"
            out.write_text(json.dumps(peer, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            print(f"     저장: {out}")

    if rows:
        if len(rows) > 1:
            print_table(rows)
        print(f"\n모델: {model_name}")
        print(f"토큰: 입력 {total['input_tokens']} / 출력 {total['output_tokens']} "
              f"/ 호출 {total['calls']}회")
    if failed:
        print(f"\n[실패 {len(failed)}건] 나머지 결과는 위에 그대로 남아 있다:")
        for name, why in failed:
            print(f"  - {name}: {why[:160]}")


if __name__ == "__main__":
    main()
