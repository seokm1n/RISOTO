// 메인 기업 대응방안(schema_version 3)을 그린다.
//
// 화면은 세 영역만 둔다. 담당자가 위에서 아래로 읽으면서 "지금 상황이 무엇이고 → 무엇을
// 언제 하고 → 밖에 어떻게 말할 것인가"를 순서대로 이해하게 하려는 것이다. 작은 카드가
// 반복되면 그 흐름이 끊긴다.
//
//   1. 현재 상황 및 대응 방향   summary_points · judgment_basis · strategies(대외 발화 외)
//   2. 우선 대응 계획           checklist를 시간대로 묶은 타임라인
//   3. 커뮤니케이션 방향        strategies(소통·정정) · monitoring_metrics · limitations
//   + 근거 자료                 법령·사례. 실행할 때가 아니라 따질 때 보는 것이라 접어 둔다
//
// 시나리오가 여럿이면 위쪽 탭으로 갈아 끼운다. 세 관점을 동시에 펼치면 같은 구조가 세 번
// 반복돼 비교가 안 된다. 어느 것을 보고 있는지는 화면에서만 쓰는 상태라 저장하지 않는다 -
// 담당자가 하나를 확정하는 기능이 아니다.
//
// 화면에서 뺀 값도 content에는 그대로 남는다(risk_assessment, scenario_tradeoff 등).
// 생성 로직과 저장 구조는 건드리지 않았다.
//
// v2와 키가 하나도 겹치지 않아 파일을 나눴다. 동종 경로(peer_recommendation)는
// PeerRecommendationContent가 그린다.

import { useState } from "react";

const STANCE_LABELS = {
  선제_공개: "선제 공개",
  사실확인_우선: "사실 확인 우선",
  피해구제_중심: "피해 구제 중심",
};

// 대외 발화에 관한 전략은 3번 영역으로 보낸다. 나머지는 1번의 대응 방향에 남는다.
// 한 전략이 두 곳에 겹쳐 나오면 같은 내용을 두 번 읽게 된다.
const COMMUNICATION_TYPES = new Set(["소통강화", "사실관계_정정"]);

// 체크리스트를 시간대로 묶는다. 마감을 시간 숫자로 나열하면 급한 정도가 안 잡힌다.
const TIME_BANDS = [
  { label: "즉시", limit: 6 },
  { label: "24시간 이내", limit: 24 },
  { label: "72시간 이내", limit: 72 },
  { label: "이후", limit: Infinity },
];

function bandsOf(checklist) {
  const buckets = TIME_BANDS.map((b) => ({ ...b, items: [] }));
  for (const item of checklist ?? []) {
    const hours = typeof item.deadline_hours === "number" ? item.deadline_hours : Infinity;
    const bucket = buckets.find((b) => hours <= b.limit) ?? buckets[buckets.length - 1];
    bucket.items.push(item);
  }
  return buckets.filter((b) => b.items.length > 0);
}

// 1. 현재 상황 및 대응 방향
function SituationSection({ report }) {
  const points = report?.summary_points ?? [];
  const directions = (report?.strategies ?? []).filter(
    (s) => !COMMUNICATION_TYPES.has(s.strategy_type)
  );
  if (!points.length && !report?.judgment_basis && !directions.length) return null;

  return (
    <section className="draft-block">
      <h4>현재 상황의 핵심</h4>
      {points.length > 0 && <p className="draft-lead">{points[0]}</p>}
      {points.length > 1 && (
        <ul className="draft-points">
          {points.slice(1).map((p, i) => (
            <li key={`sp-${i}`}>{p}</li>
          ))}
        </ul>
      )}
      {report?.judgment_basis && <p className="draft-body">{report.judgment_basis}</p>}

      {directions.length > 0 && (
        <div className="draft-directions">
          <h5>대응 방향</h5>
          {directions.map((s, i) => (
            <div className="draft-direction" key={`dir-${i}`}>
              <strong>{s.title}</strong>
              <p>{s.detail}</p>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// 2. 우선 대응 계획 - 과제와 일정을 한 흐름으로 합친다. 따로 두면 같은 내용을 두 번 읽는다.
function PlanSection({ report }) {
  const bands = bandsOf(report?.checklist);
  if (!bands.length) return null;
  return (
    <section className="draft-block">
      <h4>우선 대응 계획</h4>
      <ol className="draft-timeline">
        {bands.map((band) => (
          <li className="draft-step" key={band.label}>
            <span className="draft-step-time">{band.label}</span>
            <ul className="draft-step-items">
              {band.items.map((item, i) => (
                <li key={`t-${i}`}>{item.task}</li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </section>
  );
}

// 3. 커뮤니케이션 방향
function CommunicationSection({ report }) {
  const comms = (report?.strategies ?? []).filter((s) =>
    COMMUNICATION_TYPES.has(s.strategy_type)
  );
  const watch = report?.monitoring_metrics ?? [];
  if (!comms.length && !report?.limitations && !watch.length) return null;

  return (
    <section className="draft-block">
      <h4>커뮤니케이션 방향</h4>
      {comms.map((s, i) => (
        <div className="draft-direction" key={`cm-${i}`}>
          <strong>{s.title}</strong>
          <p>{s.detail}</p>
        </div>
      ))}
      {watch.length > 0 && (
        <p className="draft-body">
          대응 이후 <strong>{watch.join(" · ")}</strong>의 변화를 보며 수위를 조정합니다.
        </p>
      )}
      {report?.limitations && (
        <div className="draft-caution">
          <span className="draft-caution-label">이 초안을 쓸 때 유의할 점</span>
          <p>{report.limitations}</p>
        </div>
      )}
    </section>
  );
}

function VerificationBadge({ verification }) {
  const violations = verification?.violations ?? [];
  // 통과했으면 알릴 것이 없다. 담당자가 봐야 하는 건 걸린 항목뿐이다.
  if (!verification || (verification.passed && violations.length === 0)) return null;
  return (
    <div className="draft-verification">
      <strong>검증에서 걸린 항목</strong>
      <ul>
        {violations.map((v, i) => (
          <li key={`${v}-${i}`}>{typeof v === "string" ? v : v.message ?? JSON.stringify(v)}</li>
        ))}
      </ul>
    </div>
  );
}

// 근거 자료. 대응을 실행할 때가 아니라 근거를 따질 때 보는 것이라 맨 아래에 접어 둔다.
// 초안 전체에 공통이므로 시나리오 밖에 한 번만 놓는다.
function DraftAppendix({ content }) {
  const [open, setOpen] = useState(false);
  const regulations = content.regulations ?? [];
  // 사례는 두 갈래다. precedents는 수집된 원본이고, case_insights는 그 사례에서 뽑은
  // 해석이다. 같은 자리에 놓아야 어느 사례가 어떻게 쓰였는지 읽힌다.
  const insights = (content.scenarios ?? []).flatMap((sc) => sc.report?.case_insights ?? []);
  const precedents = content.precedents ?? [];
  if (!regulations.length && !precedents.length && !insights.length) return null;

  return (
    <section className="draft-appendix">
      <button
        type="button"
        className="scenario-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open
          ? "근거 자료 접기"
          : `근거 자료 보기 (법령 ${regulations.length} · 사례 ${precedents.length + insights.length})`}
      </button>

      {open && (
        <div className="draft-appendix-body">
          {regulations.length > 0 && (
            <div className="draft-regulations">
              <h5>적용 법령</h5>
              <ul>
                {regulations.map((r, i) => (
                  <li key={`rg-${i}`}>
                    <strong>
                      {r.law_name} {r.article}
                    </strong>
                    <span>{r.requirement}</span>
                    {r.is_upcoming && <em className="upcoming">시행 예정</em>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {insights.length > 0 && (
            <div className="draft-cases">
              <h5>참고 사례</h5>
              {insights.map((c, i) => (
                <div className="draft-case" key={`ci-${i}`}>
                  <strong>{c.case_title}</strong>
                  <p>{c.insight}</p>
                </div>
              ))}
            </div>
          )}

          {precedents.length > 0 && (
            <div className="draft-precedents">
              <h5>인용 근거</h5>
              <ul>
                {precedents.map((p, i) => (
                  <li key={`pc-${i}`}>
                    {p.url ? (
                      <a href={p.url} target="_blank" rel="noreferrer">
                        {p.title}
                      </a>
                    ) : (
                      <span>{p.title}</span>
                    )}
                    {p.verification_status === "candidate" && (
                      <em className="unverified">미검수 · 참고용</em>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// 근거 기사가 없어 생성을 보류한 상태. 두 경로(메인·동종) 모두에서 나온다.
// 담당자가 봐야 하는 건 "왜 안 만들어졌고 무엇을 확인해야 하는가"뿐이라 그것만 그린다.
export function NoEvidenceNotice({ content }) {
  const detection = content.detection ?? {};
  const probability =
    typeof detection.risk_probability === "number"
      ? `${Math.round(detection.risk_probability * 100)}%`
      : null;
  return (
    <div className="response-draft response-draft-hold">
      <div className="response-draft-head">
        <div>
          <span className="eyebrow">대응방안 생성 보류</span>
          <strong>근거 기사 없음</strong>
        </div>
        <span className="draft-kind hold">확인 필요</span>
      </div>

      <p className="hold-reason">{content.review_reason}</p>

      <dl className="hold-detection">
        {probability && (
          <div>
            <dt>탐지 위험도</dt>
            <dd>{probability}</dd>
          </div>
        )}
        {detection.severity && (
          <div>
            <dt>심각도</dt>
            <dd>{detection.severity}</dd>
          </div>
        )}
        {detection.model_version && (
          <div>
            <dt>탐지 모델</dt>
            <dd>{detection.model_version}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}

export default function MainResponseContent({ content }) {
  const scenarios = Array.isArray(content.scenarios) ? content.scenarios : [];
  const [active, setActive] = useState(0);

  // 시나리오가 비어 있다고 다 같은 상태가 아니다. 근거부족 보류는 LLM을 부르지 않고
  // 사람이 확인할 사유만 담아 저장하므로, 빈 목록으로 뭉뚱그리면 그 사유가 사라진다.
  if (content.status === "근거부족_보류") {
    return <NoEvidenceNotice content={content} />;
  }

  const current = scenarios[active] ?? scenarios[0];
  const selected = content.selected_stance;

  return (
    <div className="response-draft response-draft-v3">
      <div className="response-draft-head">
        <div>
          <span className="eyebrow">대응방안 초안 · 검토 필요</span>
          <strong>
            {content.risk_type_label ?? "위기 유형 미상"}
            {content.tier ? ` · ${content.tier}` : ""}
          </strong>
        </div>
        <span className="draft-kind main">나의 기업 직접 대응</span>
      </div>

      {scenarios.length > 1 && (
        <div className="draft-stance-tabs" role="tablist">
          {scenarios.map((sc, i) => {
            const label = STANCE_LABELS[sc.stance] ?? sc.stance ?? `${i + 1}번째 안`;
            return (
              <button
                type="button"
                role="tab"
                key={`${sc.stance ?? "s"}-${i}`}
                className={`draft-stance-tab${i === active ? " active" : ""}`}
                aria-selected={i === active}
                onClick={() => setActive(i)}
              >
                {label}
                {sc.stance && sc.stance === selected && <em>기본</em>}
              </button>
            );
          })}
        </div>
      )}

      {current ? (
        <>
          <SituationSection report={current.report} />
          <PlanSection report={current.report} />
          <CommunicationSection report={current.report} />
          <VerificationBadge verification={current.verification} />
        </>
      ) : (
        <p className="draft-empty">생성된 시나리오가 없습니다.</p>
      )}

      <DraftAppendix content={content} />
    </div>
  );
}
