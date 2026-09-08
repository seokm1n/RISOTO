import { useState } from "react";

import { formatDate, formatRiskProbability } from "../../shared/presentation";

const HOLD_STATUS = {
  근거부족_보류: {
    kicker: "대응안 생성 보류",
    headline: "연결된 근거 기사가 없습니다",
    guide: "위험 근거 기사를 연결한 뒤 대응안을 다시 생성해 주세요.",
  },
  // 아래 둘은 지금 구조에서 새로 생기지 않는다(13개 전체에서 고르므로 상위 유형이
  // 없거나 틀려도 진행한다). 그 전에 저장된 초안을 위해 남겨 둔다.
  유형불명_보류: {
    kicker: "대응안 생성 보류",
    headline: "탐지 유형이 비어 있습니다",
    guide: "다시 생성하면 원문만으로 유형을 판정합니다.",
  },
  유형불일치_보류: {
    kicker: "대응안 생성 보류",
    headline: "탐지 유형이 사안과 맞지 않습니다",
    guide: "다시 생성하면 맞는 유형으로 초안을 만듭니다.",
  },
  대응불필요_종료: {
    kicker: "대응 불필요",
    headline: "이 기업이 대응할 사안이 아닙니다",
    guide: "판단이 틀렸다면 다시 생성해 주세요.",
  },
};

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
  // 줄바꿈이 있으면 그것이 작성자가 의도한 경계다. 보고서 문체("~음", "~됨")는 마침표를
  // 안 찍는 경우가 많아 문장부호만으로는 갈리지 않는다.
  const byLine = text.split(/\r?\n+/).map((line) => line.trim()).filter(Boolean);
  if (byLine.length > 1) return byLine;

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

function ScenarioSelector({ scenarios, active, onChange, brief }) {
  if (scenarios.length <= 1) return brief ? <section className="response-option-panel">{brief}</section> : null;
  return (
    <section className="response-option-panel" aria-label="대응안 선택">
      <span className="response-ui-kicker">대응 방향 선택</span>
      <div className="response-option-split">
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
            {/* 세 카드를 나란히 놓는 이유는 비교하기 위해서다. 제목만으로는 무엇이
                다른지 알 수 없어 한 줄짜리 대비를 붙인다. */}
            {scenario.report?.scenario_contrast && (
              <small>{scenario.report.scenario_contrast}</small>
            )}
            </button>
          ))}
        </div>
        {brief}
      </div>
    </section>
  );
}

// summary_points를 "라벨 - 문장"으로 읽게 한다. 모델이 라벨을 붙여 오면 그대로 쓰고,
// 없으면 자리로 채운다(프롬프트가 첫 항목은 상황, 나머지는 그래서 왜 중요한지로 쓰게 한다).
function summaryRows(points, fallbackLabel) {
  return points.map((point, index) => {
    const labelled = /^\s*([^:：]{2,14})\s*[:：]\s*([\s\S]+)$/.exec(point);
    if (labelled) return { label: labelled[1].trim(), text: labelled[2].trim() };
    if (fallbackLabel) return { label: index === 0 ? fallbackLabel : "", text: point };
    return { label: index === 0 ? "핵심 이슈" : "왜 중요한가", text: point };
  });
}

// tier.py의 3단계 등급 코드(TIER_ORDER)를 화면 표시용 라벨·색조로 옮긴다.
const TIER_LABELS = { T1_관찰: "관찰", T2_주시: "주시", T3_긴급: "긴급" };
const TIER_TONES = { T1_관찰: "watch", T2_주시: "caution", T3_긴급: "urgent" };

// 동종 기업 화면(PeerRecommendationContent)의 위험 요약 박스와 같은 자리에,
// 우리 기업 사건에서도 현재 위험 내용을 한눈에 보여준다.
function RiskSummaryHeader({ content, risk, scenario }) {
  if (!content.risk_type_label && !content.tier) return null;
  const report = scenario?.report ?? {};
  // 한 문장만 뽑으면 무슨 일인지 알 수 없다. 라벨을 떼고 앞의 두세 항목을 이어 붙여
  // 담당자가 이 카드만 읽고도 사안을 파악할 수 있게 한다.
  const rows = summaryRows(report.summary_points ?? []);
  const headline = rows.length
    ? rows.slice(0, 3).map((row) => row.text.replace(/[.\s]+$/, "")).join(". ") + "."
    : report.risk_assessment?.primary_risks?.[0]
      || "생성된 대응안의 상황 요약을 확인해 주세요.";
  const tone = TIER_TONES[content.tier] ?? "caution";
  const tierLabel = TIER_LABELS[content.tier] ?? humanize(content.tier);
  const facts = [
    ["위험 유형", content.risk_type_label],
    ["대응 등급", tierLabel],
    ["위험도", Number.isFinite(risk?.risk_probability) ? formatRiskProbability(risk.risk_probability) : null],
    ["대응 대상", "우리 기업 사건"],
  ].filter(([, value]) => value);

  return (
    <section className={`response-command-card ${tone}`}>
      <div className="response-command-copy">
        <span className="response-ui-kicker">위험 요약</span>
        <div className="response-command-title">
          <span className={`response-priority-pill ${tone}`}>{tierLabel || "확인 필요"}</span>
          <h4>{content.risk_type_label ? `${content.risk_type_label} 위험` : "위험 사건 대응"}</h4>
        </div>
        <p>{headline}</p>
      </div>
      {facts.length > 0 && (
        <dl className="response-command-facts">
          {facts.map(([name, value]) => (
            <div key={name}><dt>{name}</dt><dd>{value}</dd></div>
          ))}
        </dl>
      )}
    </section>
  );
}

// 고른 안이 무엇을 하자는 것인지 오른쪽에 붙여 준다. 동종 경로 화면의 "현재 권고"와
// 같은 자리·같은 카드라, 두 경로를 오가는 담당자가 같은 것으로 읽는다.
function RecommendationCard({ scenario }) {
  const report = scenario?.report ?? {};
  // 1~2문장짜리 권고를 우선 쓴다. 그 필드가 없던 시절의 초안은 전략 제목·관점 이름으로
  // 내려간다(짧지만 빈 카드보다는 낫다).
  const headline =
    report.scenario_recommendation ||
    (Array.isArray(report.strategies) && report.strategies[0]?.detail) ||
    report.scenario_headline ||
    STANCE_LABELS[scenario?.stance] ||
    humanize(scenario?.stance);
  if (!headline) return null;
  return (
    <article className="recommended">
      <h5>현재 권고</h5>
      <p>{headline}</p>
    </article>
  );
}

function SituationSection({ scenario }) {
  const report = scenario?.report ?? {};
  const points = report.summary_points ?? [];
  const assessment = report.risk_assessment ?? {};
  const primaryRisks = assessment.primary_risks ?? [];
  const secondaryRisks = assessment.secondary_risks ?? [];

  const rows = summaryRows(points);
  if (!rows.length && !primaryRisks.length && !secondaryRisks.length) {
    // 섹션을 감추면 담당자가 "없는 것"과 "안 나온 것"을 구별하지 못한다.
    return (
      <section className="response-overview-panel bare">
        <p className="response-empty-note">상황 요약이 생성되지 않았습니다.</p>
      </section>
    );
  }

  return (
    <section className="response-overview-panel">
      <header className="response-section-heading">
        <div><h4>핵심 상황과 우선 확인할 위험</h4></div>
      </header>
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

        <article className="response-risk-card">
          <h5>우선 확인할 위험</h5>
          {primaryRisks.length === 0 && secondaryRisks.length === 0 && (
            <p className="response-empty-note">별도로 지목된 위험이 없습니다.</p>
          )}
          {primaryRisks.length > 0 && (
            <dl className="response-summary-rows">
              {summaryRows(primaryRisks, "주요 위험").map((row, index) => (
                <div key={`primary-risk-${index}`}>
                  <dt>{row.label}</dt>
                  <dd>{row.text}</dd>
                </div>
              ))}
            </dl>
          )}
          {/* 접지 않는다. 두 건뿐인 목록을 펼치게 하는 것은 클릭만 늘린다. */}
          {secondaryRisks.length > 0 && (
            <dl className="response-summary-rows response-minor-rows">
              {summaryRows(secondaryRisks, "추가 위험").map((row, index) => (
                <div key={`secondary-risk-${index}`}>
                  <dt>{row.label}</dt>
                  <dd>{row.text}</dd>
                </div>
              ))}
            </dl>
          )}
        </article>
      </div>
    </section>
  );
}

function PlanSection({ report }) {
  const bands = bandsOf(report?.checklist);

  let order = 0;
  return (
    <section className="response-workboard response-timeline-board">
      <header className="response-section-heading">
        <div><h4>실행 계획</h4></div>
      </header>
      {bands.length === 0 && (
        <p className="response-empty-note">생성된 실행 과제가 없습니다.</p>
      )}
      <ol className="response-timeline">
        {bands.map((band) => (
          <li className="response-timeline-band" key={band.label}>
            <div className="response-timeline-marker">
              <span className="response-timeline-dot" aria-hidden="true" />
              <strong>{band.label}</strong>
              <span>{band.items.length}개</span>
            </div>
            <div className="response-timeline-tasks">
              {band.items.map((item, index) => {
                order += 1;
                return (
                  <article className="response-timeline-task" key={`${band.label}-${index}`}>
                    <span className="response-task-number">{String(order).padStart(2, "0")}</span>
                    <div className="response-task-copy">
                      <strong>{item.task}</strong>
                      <small>{item.owner ? `담당 : ${item.owner}` : "담당 부서 확인 필요"}</small>
                    </div>
                    <span className="response-task-due">{deadlineLabel(item.deadline_hours)}</span>
                  </article>
                );
              })}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

// 대응전략 생성이 이 화면의 핵심 산출물이라 접어 두지 않고 메인으로 바로 보여준다.
function StrategySection({ report }) {
  const strategies = report?.strategies ?? [];
  if (!strategies.length) return null;
  return (
    <section className="response-workboard response-strategy-main">
      <header className="response-section-heading">
        <div><h4>대응전략</h4></div>
      </header>
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
    </section>
  );
}

function FollowUpSection({ report }) {
  const metrics = report?.monitoring_metrics ?? [];
  if (!metrics.length) return null;
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
          {/* 줄글로 오면 수치가 문장에 묻힌다. 문장 단위로 끊어 항목으로 세운다. */}
          {toBullets(basis).length > 1 ? (
            <ol className="response-basis-list">
              {summaryRows(toBullets(basis), "판단 근거").map((row, index) => (
                <li key={`basis-${index}`}>
                  {row.label && <strong>{row.label}</strong>}
                  <span>{row.text}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p>{basis}</p>
          )}
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

// 한계 고지는 접지 않는다. 이 초안이 법률 자문이 아니라는 사실과 확인이 필요한 항목은
// 펼쳐야 보이면 안 되는 정보다.
function LimitationsNotice({ report }) {
  if (!report?.limitations) return null;
  return (
    <aside className="response-limitations" role="note">
      <strong>사용 전 확인</strong>
      <p>{report.limitations}</p>
    </aside>
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
  const copy = HOLD_STATUS[content.status] ?? HOLD_STATUS["근거부족_보류"];
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
          <span className="response-ui-kicker">{copy.kicker}</span>
          <strong>{copy.headline}</strong>
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
      <small className="response-hold-guide">{copy.guide}</small>
    </div>
  );
}

export default function MainResponseContent({ content, risk }) {
  const scenarios = Array.isArray(content.scenarios) ? content.scenarios : [];
  const initialIndex = Math.max(
    scenarios.findIndex((scenario) => scenario.stance === content.selected_stance),
    0
  );
  const [active, setActive] = useState(initialIndex);

  if (HOLD_STATUS[content.status]) {
    return <NoEvidenceNotice content={content} />;
  }

  const current = scenarios[active] ?? scenarios[0];
  const report = current?.report;

  return (
    <div className="response-draft response-draft-v3 response-operations-view">
      <RiskSummaryHeader content={content} risk={risk} scenario={current} />
      <ScenarioSelector
        scenarios={scenarios}
        active={active}
        onChange={setActive}
        brief={current ? <RecommendationCard scenario={current} /> : null}
      />

      {current ? (
        <details className="response-detail-fold" role="tabpanel">
          <summary>
            <strong>대응 상세</strong>
            <span>대응전략 · 핵심 상황 · 실행 계획</span>
          </summary>
          <div className="response-plan-columns">
            <div className="response-plan-main">
              <StrategySection report={report} />
              <SituationSection scenario={current} />
            </div>
            <div className="response-plan-side">
              <PlanSection report={report} />
              <div className="response-fold-stack">
                <FollowUpSection report={report} />
                <AppendixSection content={content} report={report} />
                <VerificationNotice verification={current.verification} />
              </div>
            </div>
          </div>
        </details>
      ) : (
        <p className="response-empty-state">생성된 대응안이 없습니다.</p>
      )}
      {current && <LimitationsNotice report={report} />}

    </div>
  );
}
