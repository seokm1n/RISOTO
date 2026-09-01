// 메인 기업 대응방안(schema_version 3)을 그린다.
//
// v2와 키가 하나도 겹치지 않는다. v2는 scenarios[].title/assumption/recommended_actions를
// 읽는데, v3는 scenarios[]가 {stance, tradeoff, merged_stances, report, verification}이고
// 실제 내용은 report 안에 들어 있다. 그래서 기존 렌더러에 분기를 얹는 대신 파일을 나눴다.
//
// 동종 경로(content_kind: "peer_recommendation")와 근거부족 보류는 여기서 다루지 않는다.

const RESPONSIBILITY_LABELS = {
  피해자: "우리도 피해자에 가까움",
  사고: "관리 범위에서 벌어진 사고",
  예방가능: "예방할 수 있었던 사안",
};

const STANCE_LABELS = {
  선제_공개: "선제 공개",
  사실확인_우선: "사실 확인 우선",
  피해구제_중심: "피해 구제 중심",
};

// 담당자가 마감을 시간 숫자로 읽으면 감이 안 온다. 익숙한 단위로 바꿔 준다.
function deadlineLabel(hours) {
  if (typeof hours !== "number" || Number.isNaN(hours)) return null;
  if (hours <= 1) return "즉시";
  if (hours < 24) return `${hours}시간 이내`;
  const days = Math.round(hours / 24);
  return days <= 1 ? "24시간 이내" : `${days}일 이내`;
}

function VerificationBadge({ verification }) {
  if (!verification) return null;
  const violations = verification.violations ?? [];
  // 통과했으면 굳이 알릴 것이 없다. 담당자가 봐야 하는 건 걸린 항목뿐이다.
  if (verification.passed && violations.length === 0) return null;
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

function ScenarioReport({ report }) {
  if (!report) return null;
  const risk = report.risk_assessment ?? {};
  return (
    <>
      {report.summary_points?.length > 0 && (
        <section className="draft-summary">
          <h5>요약</h5>
          <ul>
            {report.summary_points.map((point, i) => (
              <li key={`s-${i}`}>{point}</li>
            ))}
          </ul>
        </section>
      )}

      {report.judgment_basis && (
        <section className="draft-basis">
          <h5>판단 근거</h5>
          <p>{report.judgment_basis}</p>
        </section>
      )}

      {(risk.responsibility || risk.primary_risks?.length > 0) && (
        <section className="draft-risk">
          <h5>위험 평가</h5>
          {risk.responsibility && (
            <p className="responsibility">
              {RESPONSIBILITY_LABELS[risk.responsibility] ?? risk.responsibility}
            </p>
          )}
          {risk.primary_risks?.length > 0 && (
            <>
              <strong>주요 위험</strong>
              <ul>
                {risk.primary_risks.map((r, i) => (
                  <li key={`pr-${i}`}>{r}</li>
                ))}
              </ul>
            </>
          )}
          {risk.secondary_risks?.length > 0 && (
            <>
              <strong>파생 위험</strong>
              <ul>
                {risk.secondary_risks.map((r, i) => (
                  <li key={`sr-${i}`}>{r}</li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      {report.strategies?.length > 0 && (
        <section className="draft-strategies">
          <h5>대응 전략</h5>
          {report.strategies.map((s, i) => (
            <article className="draft-strategy" key={`st-${i}`}>
              <header>
                <strong>{s.title}</strong>
                {s.target_stakeholder && <span className="target">{s.target_stakeholder}</span>}
              </header>
              <p>{s.detail}</p>
            </article>
          ))}
        </section>
      )}

      {report.checklist?.length > 0 && (
        <section className="draft-checklist">
          <h5>실행 체크리스트</h5>
          <ul>
            {report.checklist.map((item, i) => {
              const due = deadlineLabel(item.deadline_hours);
              return (
                <li key={`c-${i}`}>
                  <span className="task">{item.task}</span>
                  {item.owner && <span className="owner">{item.owner}</span>}
                  {due && <span className="due">{due}</span>}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {report.monitoring_metrics?.length > 0 && (
        <section className="draft-monitoring">
          <h5>관찰 지표</h5>
          <ul>
            {report.monitoring_metrics.map((m, i) => (
              <li key={`m-${i}`}>{m}</li>
            ))}
          </ul>
        </section>
      )}

      {report.case_insights?.length > 0 && (
        <section className="draft-cases">
          <h5>참고 사례</h5>
          {report.case_insights.map((c, i) => (
            <div className="draft-case" key={`ci-${i}`}>
              <strong>{c.case_title}</strong>
              <p>{c.insight}</p>
            </div>
          ))}
        </section>
      )}

      {report.limitations && (
        <p className="draft-limitations">
          <strong>한계</strong>
          {report.limitations}
        </p>
      )}
    </>
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
  // 시나리오가 비어 있다고 다 같은 상태가 아니다. 근거부족 보류는 LLM을 부르지 않고
  // 사람이 확인할 사유만 담아 저장하므로, 빈 목록으로 뭉뚱그리면 그 사유가 화면에서
  // 사라진다. 생성 실패와 구분해서 전용 화면으로 보낸다.
  if (content.status === "근거부족_보류") {
    return <NoEvidenceNotice content={content} />;
  }
  const scenarios = Array.isArray(content.scenarios) ? content.scenarios : [];
  // 담당자가 고를 수 있게 여러 관점을 만든다. 결론이 사실상 같으면 엔진이 접어서
  // 하나만 올 수도 있으므로 개수를 가정하지 않는다.
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

      {content.stakeholder && (
        <p className="draft-stakeholder">주요 이해관계자: {content.stakeholder}</p>
      )}

      {scenarios.length === 0 && <p className="draft-empty">생성된 시나리오가 없습니다.</p>}

      <div className="response-scenario-list">
        {scenarios.map((scenario, index) => {
          const stance = scenario.stance;
          const label = STANCE_LABELS[stance] ?? stance ?? `${index + 1}번째 대응안`;
          return (
            <article
              className={`response-scenario${stance && stance === selected ? " selected" : ""}`}
              key={`${stance ?? "scenario"}-${index}`}
            >
              <header>
                <span>안 {String(index + 1).padStart(2, "0")}</span>
                <h4>{label}</h4>
                {stance && stance === selected && <span className="selected-mark">기본 선택</span>}
              </header>
              {scenario.tradeoff && (
                <p className="draft-tradeoff">
                  <strong>이 관점의 득실</strong>
                  {scenario.tradeoff}
                </p>
              )}
              {scenario.merged_stances?.length > 0 && (
                <p className="draft-merged">
                  결론이 같아 합쳐진 관점: {scenario.merged_stances.join(", ")}
                </p>
              )}
              <ScenarioReport report={scenario.report} />
              <VerificationBadge verification={scenario.verification} />
            </article>
          );
        })}
      </div>

      {content.regulations?.length > 0 && (
        <section className="draft-regulations">
          <h5>적용 법령</h5>
          <ul>
            {content.regulations.map((r, i) => (
              <li key={`rg-${i}`}>
                <strong>
                  {r.law_name} {r.article}
                </strong>
                <span>{r.requirement}</span>
                {r.is_upcoming && <em className="upcoming">시행 예정</em>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {content.precedents?.length > 0 && (
        <section className="draft-precedents">
          <h5>인용 근거</h5>
          <ul>
            {content.precedents.map((p, i) => (
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
        </section>
      )}
    </div>
  );
}
