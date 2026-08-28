# response_engine 연결 안내

기존 `services/response_generation.py`를 대체하는 대응방안 생성 엔진입니다.
**아직 연결하지 않았습니다** — 저장소 어디에서도 `response_engine`을 임포트하지 않습니다.

## 전환 방법

**호출 지점이 두 곳입니다. 둘을 같이 바꿔야 합니다.**

`app/routers/governance.py` (담당자가 버튼으로 생성하는 수동 경로)

```python
# 기존
from app.services.response_generation import generate_response_draft
# 전환
from app.services.response_engine import generate_response_draft
```

`app/services/risk_analysis.py` (위험 이벤트 발생 시 자동으로 큐에 넣는 경로, 함수 안 지연 임포트)

```python
# 기존
from app.services.response_generation import enqueue_response_draft
# 전환
from app.services.response_engine import enqueue_response_draft
```

한쪽만 바꾸면 **같은 `response_drafts` 테이블에 v2와 v3 초안이 섞입니다.** 수동 생성은
새 형식, 자동 생성은 옛 형식이 되어 프런트가 두 구조를 동시에 감당해야 하므로, 전환은
한 번에 하는 편이 낫습니다.

반환 타입(`ResponseDraft`)과 저장 컬럼은 같습니다. `schema_version`만 2 → 3으로 달라집니다.

## 프런트 대응 (전환과 함께 가야 함)

`frontend/src/features/realtime/RealtimePage.jsx`의 `ResponseDraftContent` 한 컴포넌트가
초안 내용을 그리는 유일한 지점입니다. 조회·생성·승인 API 호출은 `content`를 들여다보지
않으므로(`ResponseDraftRead.content`가 `dict`) 손대지 않아도 됩니다.

다만 **v2 키를 v3가 하나도 물려받지 않습니다.** 분기만 추가하는 수준이 아니라 렌더러를
새로 써야 합니다.

| v2가 읽는 것 | v3에서의 위치 |
|---|---|
| `content.risk_summary` | `content.scenarios[i].report.summary_points[]` (배열) |
| `content.scenarios[i].title` | `content.scenarios[i].stance` / `.tradeoff` |
| `scenario.recommended_actions` | `scenarios[i].report.strategies[]` + `.checklist[]` |
| `content.uncertainty` | `scenarios[i].verification` (규칙별 통과·스킵 결과) |

`ActionGroups`는 `{immediate, within_24h, within_7d}` 형태를 전제하는데, v3의 `checklist[]`는
`{task, owner, deadline_hours}` 평면 목록이라 그대로 못 씁니다.

동종 기업 초안(`content_kind: "peer_recommendation"`)은 **또 다른 구조**입니다. `scenarios`가
아예 없고 `content.recommendation`(`headline`, `recommendations[]`, `avoid[]`)과
`content.impact`를 읽어야 하며, `status: "영향없음_종료"`면 `recommendation`이 `null`입니다.

## content 구조 (schema_version=3)

```jsonc
{
  "risk_type": "R11", "risk_type_label": "정산·거래조건",
  "detection_type": "supply_operations",   // 팀 8개 유형 중 상위
  "stakeholder": "판매자·입점사",
  "tier": "T3_긴급", "tier_policy": {...},
  "selected_stance": "선제_공개",           // 기본 선택 시나리오
  "scenarios": [                            // 담당자가 고를 1~3개
    {"stance": "...", "tradeoff": "...", "merged_stances": [],
     "report": {...}, "verification": {...}}
  ],
  "evidence": [...], "precedents": [...], "regulations": [...]
}
```

## 기존 구현과 달라지는 점

| | 기존 response_generation | response_engine |
|---|---|---|
| 유형 | 탐지 8개를 그대로 사용 | 8개 → 세부 13개로 좁힘 (엄격한 하위) |
| 대응 원칙 | 없음 | 유형별 원칙 + 주체별 지침 + RAG 보충 |
| 법령 | 없음 | 국내 법령 매핑, 시행 중인 조문만 의무로 |
| 대응 등급 | 없음 | 3단 매트릭스 (확률 × 유형 민감도) |
| 산출물 | 시나리오 2~5개 (한 번에 생성) | 관점이 다른 시나리오 1~3개 + 담당자 선택 |
| 검증 | 계약 형식 검사 | 자동 검증 10규칙 + 실패 시 1회 재생성 |
| 사례 검색 | 네이버·Tavily 직접 | 검수 사례 우선, 부족분만 검색 + LLM 인사이트 |

## 의존성

- `numpy` — RAG 벡터 검색 (이미 requirements에 있음)
- `pypdf` — **색인 구축 스크립트에만** 필요. 런타임에는 불필요하므로 requirements에
  넣지 않아도 됩니다. 색인을 다시 만들 때만 설치하세요.

## RAG 색인

`rag/index/`에 2,705청크(약 21MB)가 들어 있습니다. 자료를 추가하면 재색인이 필요합니다.
색인 구축 스크립트는 아직 이 저장소에 옮기지 않았습니다.

색인이 없어도 동작합니다 — 정적 원칙만 프롬프트에 들어가고 보충이 빠질 뿐입니다.

## 아직 사람 확인이 필요한 것

- 법령 매핑 77건 중 **검증 완료 22건**만 서빙됩니다(R01 4건, R02 2건, R04 3건, R05 2건, R10 2건, R11 6건, R12 3건). 미검증 조문은 `KoreanRegulationMapper`가 아예 반환하지 않습니다.
  나머지는 `verified: false`라 나가지 않습니다.
- 대응 원칙 13개 중 **11개가 근거 기반**, R07(노무·고용)·R11(정산·거래조건)은 초안입니다.
