import { useState } from "react";

import { formatDate } from "../../shared/presentation";

const STANCE_LABELS = {
  선제_공개: "선제 공개",
  사실확인_우선: "사실 확인 우선",
  피해구제_중심: "피해 구제 중심",
};

const STRATEGY_LABELS = {
  사실관계_정정: "사실관계 정정",
  사과_시정: "사과·시정",
  보상: "보상",
  재발방지: "재발 방지",
  소통강화: "소통 강화",
  법적대응: "법적 대응",
  모니터링_유지: "모니터링 유지",
  부인_반박: "부인·반박",
};

const SEVERITY_LABELS = {
  critical: "긴급",
  warning: "주의",
  normal: "일반",
};

const TIME_BANDS = [
  { label: "즉시", limit: 6 },
  { label: "24시간 이내", limit: 24 },
  { label: "72시간 이내", limit: 72 },
  { label: "후속 조치", limit: Infinity },
];

function humanize(value) {
  return typeof value === "string" ? value.replaceAll("_", " ") : value ?? "";
}

function deadlineLabel(value) {
  const hours = Number(value);
  if (!Number.isFinite(hours)) return "기한 확인";
  if (hours <= 1) return "1시간 이내";
  return `${hours}시간 이내`;
}

function bandsOf(checklist) {
  const buckets = TIME_BANDS.map((band) => ({ ...band, items: [] }));
  const ordered = [...(checklist ?? [])].sort((left, right) => {
    const leftHours = Number.isFinite(Number(left.deadline_hours))
      ? Number(left.deadline_hours)
      : Infinity;
    const rightHours = Number.isFinite(Number(right.deadline_hours))
      ? Number(right.deadline_hours)
      : Infinity;
    return leftHours - rightHours;
  });

  for (const item of ordered) {
    const hours = Number.isFinite(Number(item.deadline_hours))
      ? Number(item.deadline_hours)
      : Infinity;
    const bucket = buckets.find((band) => hours <= band.limit) ?? buckets[buckets.length - 1];
    bucket.items.push(item);
  }
  return buckets.filter((band) => band.items.length > 0);
}

// 실행 계획 아래로는 전부 접어 둔다. 매번 읽는 것은 상황과 할 일이고, 나머지는
// 따질 때만 펼친다.
export function FoldSection({ title, children }) {
  return (
    <details className="response-fold">
      <summary><strong>{title}</strong></summary>
      <div className="response-fold-body">{children}</div>
    </details>
  );
}

// 모델이 쓴 줄글을 문장 단위로 끊는다. 종결부호로 끝나는 낱말에서만 자르므로
// "3.5%" 같은 숫자 가운데 마침표에는 걸리지 않는다.
function toBullets(text) {
  if (typeof text !== "string") return [];
  const sentences = [];
  let buffer = "";
  for (const chunk of text.split(/\s+/)) {
    if (!chunk) continue;
    buffer = buffer ? `${buffer} ${chunk}` : chunk;
    if (/[.!?]$/.test(chunk)) {
      sentences.push(buffer);
      buffer = "";
    }
  }
  if (buffer) sentences.push(buffer);
  return sentences;
}

function ScenarioSelector({ scenarios, active, onChange }) {
  if (scenarios.length <= 1) return null;
  return (
    <section className="response-option-panel" aria-label="대응안 선택">
      <span className="response-ui-kicker">대응 방향 선택</span>
      <div className="response-option-tabs" role="tablist">
        {scenarios.map((scenario, index) => (
          <button
            type="button"
            role="tab"
            key={`${scenario.stance ?? "scenario"}-${index}`}
            className={`response-option-tab${index === active ? " active" : ""}`}
            aria-selected={index === active}
            onClick={() => onChange(index)}
          >
            <strong>
              {scenario.report?.scenario_headline ||
                STANCE_LABELS[scenario.stance] ||
                humanize(scenario.stance) ||
                `${index + 1}번째 대응안`}
            </strong>
          </button>
        ))}
      </div>
    </section>
  );
}

// summary_points를 "라벨 - 문장"으로 읽게 한다. 모델이 라벨을 붙여 오면 그대로 쓰고,
// 없으면 자리로 채운다(프롬프트가 첫 항목은 상황, 나머지는 그래서 왜 중요한지로 쓰게 한다).
function summaryRows(points) {
  return points.map((point, index) => {
    const labelled = /^\s*([^:：]{2,14})\s*[:：]\s*([\s\S]+)$/.exec(point);
    if (labelled) return { label: labelled[1].trim(), text: labelled[2].trim() };
    return { label: index === 0 ? "핵심 이슈" : "왜 중요한가", text: point };
  });
}

function SituationSection({ scenario }) {
  const report = scenario?.report ?? {};
  const points = report.summary_points ?? [];
  const assessment = report.risk_assessment ?? {};
  const primaryRisks = assessment.primary_risks ?? [];
  const secondaryRisks = assessment.secondary_risks ?? [];

  if (!points.length && !primaryRisks.length && !secondaryRisks.length) return null;

  const rows = summaryRows(points);

  return (
    <section className="response-overview-panel bare">
      <div className="response-overview-grid">
        {rows.length > 0 && (
          <article className="response-summary-card">
            <h5>핵심 상황</h5>
            <dl className="response-summary-rows">
              {rows.map((row, index) => (
                <div key={`summary-${index}`}>
                  <dt>{index > 0 && rows[index - 1].label === row.label ? "" : row.label}</dt>
                  <dd>{row.text}</dd>
                </div>
              ))}
            </dl>
          </article>
        )}

        {(primaryRisks.length > 0 || secondaryRisks.length > 0) && (
          <article className="response-risk-card">
            <h5>우선 확인할 위험</h5>
            {primaryRisks.length > 0 && (
              <ul className="primary">
                {primaryRisks.map((risk, index) => (
                  <li key={`primary-risk-${index}`}>{risk}</li>
                ))}
              </ul>
            )}
            {secondaryRisks.length > 0 && (
              <details className="response-minor-disclosure">
                <summary>추가 위험 {secondaryRisks.length}건</summary>
                <ul>
                  {secondaryRisks.map((risk, index) => (
                    <li key={`secondary-risk-${index}`}>{risk}</li>
                  ))}
                </ul>
              </details>
            )}
          </article>
        )}
      </div>
    </section>
  );
}

function PlanSection({ report }) {
  const bands = bandsOf(report?.checklist);
  if (!bands.length) return null;

  let order = 0;
  return (
    <section className="response-workboard">
      <header className="response-section-heading">
        <div>
          <span>실행 계획</span>
          <h4>지금부터 해야 할 일</h4>
        </div>
      </header>
      <div className="response-time-groups">
        {bands.map((band) => (
          <article className="response-time-group" key={band.label}>
            <header>
              <strong>{band.label}</strong>
              <span>{band.items.length}개</span>
            </header>
            <ol>
              {band.items.map((item, index) => {
                order += 1;
                return (
                  <li key={`${band.label}-${index}`}>
                    <span className="response-task-number">{String(order).padStart(2, "0")}</span>
                    <div className="response-task-copy">
                      <strong>{item.task}</strong>
                      <small>{item.owner ? `담당 · ${item.owner}` : "담당 부서 확인 필요"}</small>
                    </div>
                    <span className="response-task-due">{deadlineLabel(item.deadline_hours)}</span>
                  </li>
                );
              })}
            </ol>
          </article>
        ))}
      </div>
    </section>
  );
}

function StrategySection({ report }) {
  const strategies = report?.strategies ?? [];
  if (!strategies.length) return null;
  return (
    <FoldSection title={`대응 전략 ${strategies.length}건`}>
      <div className="response-strategy-grid">
        {strategies.map((strategy, index) => {
          const bullets = toBullets(strategy.detail);
          return (
            <article key={`${strategy.title ?? "strategy"}-${index}`}>
              <div className="response-strategy-meta">
                <span>{STRATEGY_LABELS[strategy.strategy_type] ?? humanize(strategy.strategy_type)}</span>
                {strategy.target_stakeholder && <small>대상 · {strategy.target_stakeholder}</small>}
              </div>
              <h5>{strategy.title}</h5>
              {bullets.length > 1 ? (
                <ul className="response-strategy-points">
                  {bullets.map((sentence, order) => (
                    <li key={`strategy-${index}-${order}`}>{sentence}</li>
                  ))}
                </ul>
              ) : (
                <p>{strategy.detail}</p>
              )}
            </article>
          );
        })}
      </div>
    </FoldSection>
  );
}

function FollowUpSection({ report }) {
  const metrics = report?.monitoring_metrics ?? [];
  if (!metrics.length && !report?.limitations) return null;
  return (
    <FoldSection title="대응 후 점검">
      <div className="response-followup-grid">
        {metrics.length > 0 && (
          <article>
            <h5>모니터링 지표</h5>
            <div className="response-metric-chips">
              {metrics.map((metric, index) => (
                <span key={`${metric}-${index}`}>{metric}</span>
              ))}
            </div>
          </article>
        )}
        {report?.limitations && (
          <article className="response-caution-card">
            <h5>사용 전 확인</h5>
            <p>{report.limitations}</p>
          </article>
        )}
      </div>
    </FoldSection>
  );
}

// 판단에 쓴 자료를 모아 둔다. 일상 화면의 주인공은 실행 계획이고 근거는 따질 때만
// 열어 보는 것이라 전부 접은 채로 시작한다.
function AppendixSection({ content, report }) {
  const basis = report?.judgment_basis;
  const regulations = content.regulations ?? [];
  const precedents = content.precedents ?? [];
  const insights = report?.case_insights ?? [];
  const evidence = content.evidence ?? [];
  const cited = new Set((report?.cited_mention_ids ?? []).map((id) => String(id)));

  if (!basis && !regulations.length && !precedents.length && !insights.length && !evidence.length) {
    return null;
  }

  // 사례별 시사점은 해당 사례 밑에 붙인다. 짝이 없는 시사점만 따로 남긴다.
  const insightByCase = new Map(insights.map((item) => [item.case_id, item]));
  const orphanInsights = insights.filter(
    (item) => !precedents.some((precedent) => precedent.case_id === item.case_id)
  );
  const caseCount = precedents.length + orphanInsights.length;

  return (
    <>
      {basis && (
        <FoldSection title="판단 근거 (수치)">
          <p>{basis}</p>
        </FoldSection>
      )}

      {regulations.length > 0 && (
        <FoldSection title={`관련 법령 ${regulations.length}건`}>
          <ul className="response-appendix-list">
            {regulations.map((regulation, index) => (
              <li key={`${regulation.law_name}-${regulation.article}-${index}`}>
                <div className="response-appendix-head">
                  <strong>
                    {[regulation.law_name, regulation.article].filter(Boolean).join(" ")}
                  </strong>
                  {regulation.is_upcoming && <span className="response-appendix-flag upcoming">시행 예정</span>}
                  {Number.isFinite(Number(regulation.deadline_hours)) && (
                    <span className="response-appendix-flag">{deadlineLabel(regulation.deadline_hours)}</span>
                  )}
                </div>
                {regulation.requirement && <p>{regulation.requirement}</p>}
                {regulation.source_url && (
                  <a href={regulation.source_url} target="_blank" rel="noreferrer">조문 원문</a>
                )}
              </li>
            ))}
          </ul>
        </FoldSection>
      )}

      {caseCount > 0 && (
        <FoldSection title={`유사 사례 ${caseCount}건`}>
          <ul className="response-appendix-list">
            {precedents.map((precedent, index) => {
              const insight = insightByCase.get(precedent.case_id);
              return (
                <li key={`${precedent.case_id ?? "case"}-${index}`}>
                  <div className="response-appendix-head">
                    <strong>{precedent.title}</strong>
                    <span
                      className={`response-appendix-flag${
                        precedent.verification_status === "verified" ? " verified" : ""
                      }`}
                    >
                      {precedent.verification_status === "verified" ? "검수 사례" : "검색 결과"}
                    </span>
                  </div>
                  {precedent.summary && <p>{precedent.summary}</p>}
                  {precedent.lesson && <p className="response-appendix-note">교훈 · {precedent.lesson}</p>}
                  {insight && <p className="response-appendix-note">이 사건에 주는 시사점 · {insight.insight}</p>}
                  {precedent.url && (
                    <a href={precedent.url} target="_blank" rel="noreferrer">원문 보기</a>
                  )}
                </li>
              );
            })}
            {orphanInsights.map((item, index) => (
              <li key={`insight-${item.case_id ?? index}`}>
                <div className="response-appendix-head">
                  <strong>{item.case_title}</strong>
                </div>
                <p className="response-appendix-note">시사점 · {item.insight}</p>
              </li>
            ))}
          </ul>
        </FoldSection>
      )}

      {evidence.length > 0 && (
        <FoldSection title={`근거 기사 ${evidence.length}건`}>
          <ul className="response-appendix-list response-appendix-articles">
            {evidence.map((article, index) => (
              <li key={`${article.mention_id ?? "mention"}-${index}`}>
                <div className="response-appendix-head">
                  {article.url ? (
                    <a href={article.url} target="_blank" rel="noreferrer">{article.title}</a>
                  ) : (
                    <strong>{article.title}</strong>
                  )}
                  {cited.has(String(article.mention_id)) && (
                    <span className="response-appendix-flag cited">본문 인용</span>
                  )}
                </div>
                <small>
                  {[article.source, article.published_at ? formatDate(article.published_at) : null]
                    .filter(Boolean)
                    .join(" · ") || "출처 미상"}
                </small>
              </li>
            ))}
          </ul>
        </FoldSection>
      )}
    </>
  );
}

function VerificationNotice({ verification }) {
  const violations = verification?.violations ?? [];
  if (!verification || (verification.passed && violations.length === 0)) return null;
  return (
    <FoldSection title={`자동 검증에서 확인이 필요한 항목 ${violations.length}건`}>
      <ul className="response-appendix-list">
        {violations.map((violation, index) => (
          <li key={`verification-${index}`}>
            {typeof violation === "string"
              ? violation
              : violation.message ?? "세부 검증 결과를 확인해 주세요."}
          </li>
        ))}
      </ul>
    </FoldSection>
  );
}

export function NoEvidenceNotice({ content }) {
  const detection = content.detection ?? {};
  const probability =
    typeof detection.risk_probability === "number"
      ? `${Math.round(detection.risk_probability * 100)}%`
      : null;
  const facts = [
    ["탐지 위험도", probability],
    ["심각도", SEVERITY_LABELS[detection.severity] ?? humanize(detection.severity)],
  ].filter(([, value]) => value);

  return (
    <div className="response-draft response-draft-hold">
      <div className="response-hold-heading">
        <div>
          <span className="response-ui-kicker">대응안 생성 보류</span>
          <strong>연결된 근거 기사가 없습니다</strong>
        </div>
        <span>확인 필요</span>
      </div>
      <p>{content.review_reason || "위험 판단에 사용된 기사를 확인할 수 없습니다."}</p>
      {facts.length > 0 && (
        <dl>
          {facts.map(([name, value]) => (
            <div key={name}>
              <dt>{name}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      <small className="response-hold-guide">
        위험 근거 기사를 연결한 뒤 대응안을 다시 생성해 주세요.
      </small>
    </div>
  );
}

export default function MainResponseContent({ content }) {
  const scenarios = Array.isArray(content.scenarios) ? content.scenarios : [];
  const initialIndex = Math.max(
    scenarios.findIndex((scenario) => scenario.stance === content.selected_stance),
    0
  );
  const [active, setActive] = useState(initialIndex);

  if (content.status === "근거부족_보류") {
    return <NoEvidenceNotice content={content} />;
  }

  const current = scenarios[active] ?? scenarios[0];
  const report = current?.report;

  return (
    <div className="response-draft response-draft-v3 response-operations-view">
      <ScenarioSelector scenarios={scenarios} active={active} onChange={setActive} />

      {current ? (
        <div className="response-plan-content" role="tabpanel">
          <SituationSection scenario={current} />
          <PlanSection report={report} />
          <div className="response-fold-stack">
            <StrategySection report={report} />
            <FollowUpSection report={report} />
            <AppendixSection content={content} report={report} />
            <VerificationNotice verification={current.verification} />
          </div>
        </div>
      ) : (
        <p className="response-empty-state">생성된 대응안이 없습니다.</p>
      )}

    </div>
  );
}
