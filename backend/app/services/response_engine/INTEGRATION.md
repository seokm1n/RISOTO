# response_engine 연결 안내

기존 `services/response_generation.py`를 대체하는 대응방안 생성 엔진입니다.
수동 생성 API와 위험 이벤트 자동 생성 경로 모두 `response_engine`에 연결되어 있습니다.

## 연결 지점

**호출 지점은 두 곳이며 항상 같은 엔진을 가리켜야 합니다.**

`app/routers/governance.py` (담당자가 버튼으로 생성하는 수동 경로)

```python
from app.services.response_engine import generate_response_draft
```

`app/services/risk_analysis.py` (위험 이벤트 발생 시 자동으로 큐에 넣는 경로, 함수 안 지연 임포트)

```python
from app.services.response_engine import enqueue_response_draft
```

한쪽만 바꾸면 **같은 `response_drafts` 테이블에 v2와 v3 초안이 섞입니다.** 수동 생성은
새 형식, 자동 생성은 옛 형식이 될 수 있으므로 두 연결을 회귀 테스트로 함께 확인합니다.

반환 타입(`ResponseDraft`)과 저장 컬럼은 같습니다. `schema_version`만 2 → 3으로 달라집니다.

## 프런트 대응 (전환과 함께 가야 함)

`ResponseDraftContent` 컴포넌트가 초안 내용을 그리는 유일한 지점입니다. **파일 경로는 여기
적지 않습니다** — 프런트 재구성으로 세 번 옮겨졌습니다(RealtimePage.jsx → RealtimePanels.jsx →
features/analysis/AnalysisStatisticsPage.jsx). 위치는 컴포넌트 이름으로 찾으세요.

```
grep -rn "function ResponseDraftContent" frontend/src
```

조회·생성·승인 API 호출은 `content`를 들여다보지 않으므로(`ResponseDraftRead.content`가
`dict`) 손대지 않아도 됩니다.

**v2 키를 v3가 하나도 물려받지 않습니다.** 그래서 분기를 얹고 렌더러를 따로 둡니다.

| v2가 읽는 것 | v3에서의 위치 |
|---|---|
| `content.risk_summary` | `content.scenarios[i].report.summary_points[]` (배열) |
| `content.scenarios[i].title` | `content.scenarios[i].stance` / `.tradeoff` |
| `scenario.recommended_actions` | `scenarios[i].report.strategies[]` + `.checklist[]` |
| `content.uncertainty` | `scenarios[i].verification` (규칙별 통과·스킵 결과) |

`ActionGroups`는 `{immediate, within_24h, within_7d}` 형태를 전제하는데, v3의 `checklist[]`는
`{task, owner, deadline_hours}` 평면 목록이라 그대로 못 씁니다.

**현재 진행 상황**

- 메인 경로 v3: `MainResponseContent.jsx`로 분리했고 `ResponseDraftContent`에서
  `schema_version === 3 && generation_kind !== "competitor_impact"`일 때 갈라집니다.
  기존 v2 초안도 계속 조회할 수 있고, 위험관리 페이지에서 v3로 새로 생성할 수 있습니다.
- 동종 경로(`content_kind: "peer_recommendation"`)는 **또 다른 구조**입니다. `scenarios`가
  아예 없고 `content.recommendation`(`headline`, `recommendations[]`, `avoid[]`)과
  `content.impact`를 읽어야 하며, `status: "영향없음_종료"`면 `recommendation`이 `null`입니다.
- 근거 기사가 없는 이벤트는 `status: "근거부족_보류"`로 저장됩니다(LLM 미호출).
  `review_reason`과 `detection`이 사람이 봐야 할 정보입니다.

위험관리 페이지는 v3 초안을 우선 표시하며, v3가 없으면 기존 v2를 보여 주면서 새 엔진
생성 버튼을 제공합니다. 근거가 없는 이벤트는 보류 사유를 표시하고 승인할 수 없습니다.

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
- `pypdf`, `python-docx` — **색인 구축 스크립트에만** 필요. hwpx는 표준 zipfile로 읽으므로 추가 설치가 없습니다. 런타임에는 불필요하므로
  requirements에 넣지 않아도 됩니다. 색인을 다시 만들 때만 설치하세요.

## RAG 색인

`rag/index/`에 36개 문서 3,847청크(약 28MB)가 들어 있습니다. 자료를 추가하면 재색인합니다.

```
cd backend
pip install pypdf python-docx
python -m scripts.build_rag_index --dry-run   # 청크 구성만 확인
python -m scripts.build_rag_index             # 임베딩까지 (약 188만 토큰)
```

원문(`sources/*`)은 용량 때문에 저장소에서 제외돼 있습니다. 출처 목록은
`sources/README.md`에 있습니다. 어느 문서가 어느 유형의 근거인지는
`principles_data.json`의 `sources`가 정하고, `file` 키로 파일명을 고정합니다 —
파일명 유사도에 맡기면 제목이 겹치는 자료끼리 뒤바뀝니다.

색인이 없어도 동작합니다 — 정적 원칙만 프롬프트에 들어가고 보충이 빠질 뿐입니다.

## 아직 사람 확인이 필요한 것

- 법령 매핑 104건 중 **검증 완료 62건**만 서빙됩니다(R01 4 · R02 2 · R03 5 · R04 3 · R05 7 · R06 6 · R07 18 · R08 4 · R09 3 · R10 2 · R11 6 · R12 2). 미검증 조문은 `KoreanRegulationMapper`가 아예 반환하지 않습니다.
- R13(평판·루머)은 법령 0건이 의도한 결과입니다. 언론중재법·명예훼손은 회사의 의무가 아니라 회사가 행사하는 권리라 이 표의 성격과 맞지 않습니다.
- 대응 원칙은 **13개 유형 전부 근거 기반**입니다(`grounded: true`).
- `case_records`가 0행이라 유사 사례가 100% 웹검색 경로로만 만들어집니다. 검수 사례 경로를 살릴지는 미정입니다.
